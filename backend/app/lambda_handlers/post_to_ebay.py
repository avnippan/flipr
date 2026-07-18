import asyncio
import json
import os
import time
import uuid
from datetime import date
from decimal import Decimal

import boto3
import structlog
from botocore.exceptions import ClientError

from app.config import settings
from app.services.ebay_poster import EbayPoster
from app.services.ebay_token import get_valid_token

logger = structlog.get_logger(__name__)

# DynamoDB: JobsTable (DYNAMODB_TABLE_NAME) — idempotency records for eBay postings
# Read/Write: PK=POSTING#{idempotency_key}  SK=META
#             Fields: status, ebay_url, item_id, created_at, completed_at, ttl
#
# DynamoDB: RateLimitsTable (RATE_LIMITS_TABLE_NAME) — daily posting rate limit
# Write: PK=date (ISO 8601)
#        Fields: count (atomic ADD, conditional on count < 50)

_RATE_LIMITS_TABLE = os.environ["RATE_LIMITS_TABLE_NAME"]
_DAILY_POST_LIMIT = 50

# Idempotency pattern:
# 1. Client generates UUID before first attempt
# 2. Conditional DynamoDB write (fails if key already exists)
# 3. If write fails: return cached result or 409
# 4. If write succeeds: call eBay API, store result
# 5. Retries with same key always return same result
# Why: eBay penalizes duplicate listings at the account level


def handler(event, context):
    logger.info("post_to_ebay_start")

    try:
        body = json.loads(event.get("body") or "{}")

        user_id = body.get("user_id", "default")
        marketplace = body.get("marketplace", "ebay")
        listing = body.get("listing")
        metadata = body.get("metadata")
        if not listing or not metadata:
            return _response(400, {"error": "Request body must include 'listing' and 'metadata'"})

        idempotency_key = body.get("idempotency_key")
        if not idempotency_key:
            logger.warning("no_idempotency_key_provided", marketplace=marketplace)
            idempotency_key = str(uuid.uuid4())

        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(os.environ["DYNAMODB_TABLE_NAME"])
        now = int(time.time())
        pk = f"POSTING#{idempotency_key}"

        # Try to claim the idempotency slot
        try:
            table.put_item(
                Item={
                    "PK": pk,
                    "SK": "META",
                    "status": "processing",
                    "created_at": now,
                    "ttl": now + 86400,
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

            existing = table.get_item(Key={"PK": pk, "SK": "META"}).get("Item", {})
            status = existing.get("status")

            if status == "complete":
                logger.info(
                    "post_to_ebay_cache_hit",
                    idempotency_key=idempotency_key,
                    marketplace=marketplace,
                )
                return _response(200, {
                    "ebay_url": existing["ebay_url"],
                    "item_id": existing["item_id"],
                    "idempotency_key": idempotency_key,
                    "cached": True,
                })

            if status == "processing":
                return _response(409, {
                    "error": "Posting already in progress",
                    "idempotency_key": idempotency_key,
                })

            # status == "failed" — delete and re-claim for retry
            table.delete_item(Key={"PK": pk, "SK": "META"})
            table.put_item(Item={
                "PK": pk,
                "SK": "META",
                "status": "processing",
                "created_at": now,
                "ttl": now + 86400,
            })
            logger.info(
                "post_to_ebay_retry_after_failure",
                idempotency_key=idempotency_key,
                marketplace=marketplace,
            )

        # We own the slot — check rate limit before calling eBay
        if not _check_and_increment_rate_limit():
            logger.warning("post_to_ebay_rate_limit_reached", marketplace=marketplace)
            _update_slot(table, pk, {"status": "failed", "error": "rate_limit_exceeded"})
            return _response(429, {"error": f"Daily posting limit ({_DAILY_POST_LIMIT}) reached"})

        token = get_valid_token(user_id, marketplace)

        raw_condition = metadata.get("condition")
        listing_data = {
            "title": listing["title"],
            "description": listing.get("description", ""),
            "price": listing.get("suggested_price", 0.0),
            "condition": raw_condition.title() if raw_condition else None,
            "brand": metadata.get("brand"),
            "size": metadata.get("size"),
            "color": metadata.get("color"),
            "material": metadata.get("material"),
            "item_type": metadata.get("item_type"),
        }

        poster = EbayPoster(
            user_token=token,
            fulfillment_policy_id=settings.ebay_fulfillment_policy_id,
            payment_policy_id=settings.ebay_payment_policy_id,
            return_policy_id=settings.ebay_return_policy_id,
            merchant_location_key=settings.ebay_merchant_location_key,
        )

        try:
            result = asyncio.run(poster.post_listing(listing_data))
        except Exception as e:
            logger.error(
                "post_to_ebay_exception",
                error=str(e),
                idempotency_key=idempotency_key,
                marketplace=marketplace,
            )
            _update_slot(table, pk, {"status": "failed", "error": str(e)})
            return _response(500, {"error": "Internal server error"})

        if not result.success:
            logger.error(
                "post_to_ebay_api_error",
                error=result.error,
                idempotency_key=idempotency_key,
                marketplace=marketplace,
            )
            _update_slot(table, pk, {"status": "failed", "error": result.error})
            return _response(500, {"error": f"eBay API error: {result.error}"})

        _update_slot(table, pk, {
            "status": "complete",
            "ebay_url": result.listing_url,
            "item_id": result.listing_id,
            "completed_at": int(time.time()),
        })
        logger.info(
            "post_to_ebay_complete",
            listing_id=result.listing_id,
            listing_url=result.listing_url,
            idempotency_key=idempotency_key,
            marketplace=marketplace,
        )
        return _response(200, {
            "ebay_url": result.listing_url,
            "item_id": result.listing_id,
            "idempotency_key": idempotency_key,
            "cached": False,
        })

    except RuntimeError as e:
        logger.error("post_to_ebay_config_error", error=str(e))
        return _response(500, {"error": str(e)})
    except Exception as e:
        logger.error("post_to_ebay_error", error=str(e))
        return _response(500, {"error": "Internal server error"})


def _update_slot(table, pk: str, updates: dict):
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
    table.update_item(
        Key={"PK": pk, "SK": "META"},
        UpdateExpression=expr,
        ExpressionAttributeNames={f"#{k}": k for k in updates},
        ExpressionAttributeValues={f":{k}": v for k, v in updates.items()},
    )


def _check_and_increment_rate_limit() -> bool:
    """
    Atomically check and increment today's post count.
    Returns True if under the limit (and increments), False if the limit is reached.
    Uses a conditional write to avoid a read-then-write race condition.
    """
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(_RATE_LIMITS_TABLE)
    today = date.today().isoformat()

    try:
        table.update_item(
            Key={"date": today},
            UpdateExpression="ADD #count :one",
            ConditionExpression="attribute_not_exists(#count) OR #count < :limit",
            ExpressionAttributeNames={"#count": "count"},
            ExpressionAttributeValues={":one": Decimal("1"), ":limit": Decimal(str(_DAILY_POST_LIMIT))},
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
