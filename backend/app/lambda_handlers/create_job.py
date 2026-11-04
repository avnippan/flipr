import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import structlog
from botocore.exceptions import ClientError
from structlog.contextvars import bind_contextvars

from app.core.observability import configure_logging, start_request
from app.services import session_token

configure_logging()
logger = structlog.get_logger(__name__)

# DynamoDB: JobsTable (DYNAMODB_TABLE_NAME)
# Write: PK=JOB#{job_id}  SK=META
#        Fields: job_id, status, marketplace, created_at, updated_at, ttl
#
# DynamoDB: RateLimitsTable (RATE_LIMITS_TABLE_NAME) — daily job creation cap
# Two counters are bumped together in one transaction (both or neither):
#   date=JOBS#{UTC date}              global ceiling, count < DAILY_JOB_LIMIT
#   date=JOBS#{UTC date}#{client_id}  per-client cap, count < PER_CLIENT_DAILY_LIMIT
#   Fields: count (atomic ADD), ttl (row expires on its own after a few days)
#
# Auth: Authorization: Bearer <token> from POST /session (see services/session_token.py).
# The invite code itself is never sent to this endpoint.

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
sqs = boto3.client("sqs")

TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
S3_BUCKET = os.environ["S3_BUCKET_NAME"]
ANALYSIS_QUEUE_URL = os.environ["ANALYSIS_QUEUE_URL"]
RATE_LIMITS_TABLE = os.environ["RATE_LIMITS_TABLE_NAME"]
DAILY_JOB_LIMIT = int(os.environ.get("DAILY_JOB_LIMIT", "50"))  # global ceiling
PER_CLIENT_DAILY_LIMIT = int(os.environ.get("PER_CLIENT_DAILY_LIMIT", "10"))
# Same ceiling as process_job.MAX_IMAGE_BYTES; S3 enforces it at upload time
MAX_UPLOAD_BYTES = 3_670_016


def handler(event, context):
    start_request(context)
    try:
        if not _session_ok(event):
            logger.warning("create_job_bad_session")
            return _response(401, {"error": "Session missing or expired. Enter your invite code again."})

        client_id = _client_id(event)
        limit_hit = _check_and_increment_daily_limit(client_id)
        if limit_hit == "client":
            logger.warning("create_job_client_limit_reached", limit=PER_CLIENT_DAILY_LIMIT, client_id=client_id)
            return _response(429, {"error": "You've reached your daily limit. Try again tomorrow."})
        if limit_hit == "global":
            logger.warning("create_job_daily_limit_reached", limit=DAILY_JOB_LIMIT)
            return _response(429, {"error": "Daily limit reached. Try again tomorrow."})

        body = json.loads(event.get("body") or "{}")
        marketplace = body.get("marketplace", "ebay")

        job_id = str(uuid.uuid4())
        bind_contextvars(job_id=job_id)
        now = int(time.time())
        s3_key = f"uploads/{job_id}/image.jpg"

        table = dynamodb.Table(TABLE_NAME)
        table.put_item(Item={
            "PK": f"JOB#{job_id}",
            "SK": "META",
            "job_id": job_id,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "marketplace": marketplace,
            "ttl": now + 604800,
        })

        # Presigned POST (not PUT) so S3 itself rejects files outside the size range
        presigned = s3.generate_presigned_post(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Fields={"Content-Type": "image/jpeg"},
            Conditions=[
                {"Content-Type": "image/jpeg"},
                ["content-length-range", 1, MAX_UPLOAD_BYTES],
            ],
            ExpiresIn=300,
        )

        sqs.send_message(
            QueueUrl=ANALYSIS_QUEUE_URL,
            MessageBody=json.dumps({"job_id": job_id, "s3_key": s3_key}),
            DelaySeconds=3,
        )

        logger.info("job_created", job_id=job_id, marketplace=marketplace)

        return _response(202, {
            "job_id": job_id,
            "upload_url": presigned["url"],
            "upload_fields": presigned["fields"],
            "status_url": f"/jobs/{job_id}",
            "expires_in": 300,
        })

    except Exception as e:
        logger.error("create_job_error", error=str(e))
        return _response(500, {"error": "Internal server error"})


def _session_ok(event: dict) -> bool:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    scheme, _, token = headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and session_token.token_is_valid(token.strip())


def _client_id(event: dict) -> str:
    """
    Who is making this request? This is the one place to change when real user
    login arrives: return the user id from the authorizer instead of the IP.

    Today it is the caller's IP address as API Gateway saw it (clients cannot
    forge it, unlike the X-Forwarded-For header). It is hashed so raw IPs are
    not stored in DynamoDB or logs. Weakness: people behind one shared network
    (office, school, phone carrier) share a quota.
    """
    ctx = event.get("requestContext") or {}
    ip = (ctx.get("identity") or {}).get("sourceIp") or "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _check_and_increment_daily_limit(client_id: str):
    """
    Count this job against today's (UTC) global ceiling and this client's own cap.
    Returns None if allowed, or "client" / "global" for whichever limit was hit.
    Both counters change in one DynamoDB transaction, so a rejected request never
    uses up a slot on the other counter, and there is no read-then-write race.
    """
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    expires = Decimal(str(int(now.timestamp()) + 3 * 86400))

    def bump(key: str, limit: int) -> dict:
        return {"Update": {
            "TableName": RATE_LIMITS_TABLE,
            "Key": {"date": key},
            "UpdateExpression": "ADD #count :one SET #ttl = :ttl",
            "ConditionExpression": "attribute_not_exists(#count) OR #count < :limit",
            "ExpressionAttributeNames": {"#count": "count", "#ttl": "ttl"},
            "ExpressionAttributeValues": {":one": Decimal("1"), ":limit": Decimal(str(limit)), ":ttl": expires},
        }}

    try:
        dynamodb.meta.client.transact_write_items(TransactItems=[
            bump(f"JOBS#{today}", DAILY_JOB_LIMIT),
            bump(f"JOBS#{today}#{client_id}", PER_CLIENT_DAILY_LIMIT),
        ])
        return None
    except ClientError as e:
        if e.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
        if len(reasons) == 2 and reasons[1] == "ConditionalCheckFailed":
            return "client"  # checked first so a throttled user sees their own limit
        if len(reasons) == 2 and reasons[0] == "ConditionalCheckFailed":
            return "global"
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
