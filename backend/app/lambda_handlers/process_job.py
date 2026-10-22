import asyncio
import json
import os
import time

import boto3
import structlog
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from app.services.errors import GUARDRAIL_MODERATION, GUARDRAIL_SENSITIVE_INFO, GuardrailBlocked
from app.services.retry_utils import _is_missing_object
from app.services.vision import analyze_image
from app.services.pricing import fetch_sold_comps
from app.services.listing import draft_listings

logger = structlog.get_logger(__name__)

# DynamoDB: JobsTable (DYNAMODB_TABLE_NAME)
# Write: PK=JOB#{job_id}  SK=META
#        Status transitions: pending → processing → complete | failed
#        Fields on complete: status, metadata (JSON), comps (JSON), listings (JSON), updated_at
#        Fields on failure:  status, error, updated_at

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
S3_BUCKET = os.environ["S3_BUCKET_NAME"]

# Bedrock Converse measures the base64-encoded image, which is 4/3 of the raw
# size, against a 5 MiB (5,242,880 byte) cap. 3.5 MiB raw is about 4.67 MiB
# encoded, which leaves headroom under the ~3.75 MiB raw ceiling.
MAX_IMAGE_BYTES = 3_670_016

PHOTO_TOO_LARGE_MSG = "Photo too large. Please retry with a smaller photo."
PHOTO_UNREADABLE_MSG = "We couldn't read that photo. Please try a different one."
GENERIC_ERROR_MSG = "Something went wrong processing that photo. Please try again."
GUARDRAIL_MESSAGES = {
    GUARDRAIL_MODERATION: "This photo was flagged by content moderation. Please try a different photo.",
    GUARDRAIL_SENSITIVE_INFO: "This photo contains information we can't process. Please try a different photo.",
}
GUARDRAIL_OTHER_MSG = "This photo couldn't be processed due to a content restriction. Please try a different photo."


class PhotoTooLarge(Exception):
    pass


def _user_message(exc: Exception) -> str:
    """Readable text for the job's `error` field. Raw details stay in logs."""
    if isinstance(exc, PhotoTooLarge):
        return PHOTO_TOO_LARGE_MSG
    if isinstance(exc, GuardrailBlocked):
        return GUARDRAIL_MESSAGES.get(exc.category, GUARDRAIL_OTHER_MSG)
    if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") == "ValidationException":
        return PHOTO_UNREADABLE_MSG
    return GENERIC_ERROR_MSG


def _log_s3_retry(retry_state):
    logger.warning(
        "process_job_s3_get_retry",
        attempt=retry_state.attempt_number,
        wait_seconds=retry_state.next_action.sleep if retry_state.next_action else None,
        error=str(retry_state.outcome.exception()),
    )


@retry(
    retry=retry_if_exception(_is_missing_object),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    stop=stop_after_attempt(5),
    before_sleep=_log_s3_retry,
    reraise=True,
)
def _get_image_bytes(bucket: str, key: str) -> bytes:
    """Fetch the uploaded image from S3. Retries with backoff because the
    client's upload can still be in flight when this Lambda first fires
    (SQS DelaySeconds is a lower bound, not a guarantee the object exists)."""
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def _update_job(table, job_id: str, updates: dict):
    updates["updated_at"] = int(time.time())
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
    table.update_item(
        Key={"PK": f"JOB#{job_id}", "SK": "META"},
        UpdateExpression=expr,
        ExpressionAttributeNames={f"#{k}": k for k in updates},
        ExpressionAttributeValues={f":{k}": v for k, v in updates.items()},
    )


def handler(event, context):
    table = dynamodb.Table(TABLE_NAME)

    for record in event["Records"]:
        body = json.loads(record["body"])
        job_id = body["job_id"]
        s3_key = body["s3_key"]

        log = logger.bind(job_id=job_id)
        log.info("process_job_start")

        try:
            _update_job(table, job_id, {"status": "processing"})

            image_bytes = _get_image_bytes(S3_BUCKET, s3_key)
            log.info("process_job_image_downloaded", size_bytes=len(image_bytes))

            if len(image_bytes) > MAX_IMAGE_BYTES:
                raise PhotoTooLarge()

            item = asyncio.run(analyze_image(image_bytes, "image/jpeg"))
            log.info("process_job_vision_complete", item_type=item.item_type, brand=item.brand)

            comps = asyncio.run(fetch_sold_comps(item.search_query))
            log.info("process_job_pricing_complete", median_price=comps.median_price)

            listings = asyncio.run(draft_listings(item, comps))
            log.info("process_job_listings_complete", count=len(listings))

            _update_job(table, job_id, {
                "status": "complete",
                "metadata": json.dumps(item.model_dump()),
                "comps": json.dumps(comps.model_dump()),
                "listings": json.dumps([lst.model_dump() for lst in listings]),
            })

            log.info("process_job_complete")

        except Exception as e:
            log.error("job_failed", error=str(e), error_type=type(e).__name__)
            _update_job(table, job_id, {
                "status": "failed",
                "error": _user_message(e),
            })
