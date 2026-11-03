import hmac
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

configure_logging()
logger = structlog.get_logger(__name__)

# DynamoDB: JobsTable (DYNAMODB_TABLE_NAME)
# Write: PK=JOB#{job_id}  SK=META
#        Fields: job_id, status, marketplace, created_at, updated_at, ttl
#
# DynamoDB: RateLimitsTable (RATE_LIMITS_TABLE_NAME) — daily job creation cap
# Write: date=JOBS#{UTC date, ISO 8601}
#        Fields: count (atomic ADD, conditional on count < DAILY_JOB_LIMIT)
#
# SSM: /flipr/invite-code (String) — speed bump against casual abuse, not real auth

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
sqs = boto3.client("sqs")

TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
S3_BUCKET = os.environ["S3_BUCKET_NAME"]
ANALYSIS_QUEUE_URL = os.environ["ANALYSIS_QUEUE_URL"]
RATE_LIMITS_TABLE = os.environ["RATE_LIMITS_TABLE_NAME"]
INVITE_CODE_PARAM = os.environ.get("INVITE_CODE_PARAM", "/flipr/invite-code")
DAILY_JOB_LIMIT = int(os.environ.get("DAILY_JOB_LIMIT", "50"))
# Same ceiling as process_job.MAX_IMAGE_BYTES; S3 enforces it at upload time
MAX_UPLOAD_BYTES = 3_670_016

_invite_code = None  # cached for the life of the execution environment


def handler(event, context):
    start_request(context)
    try:
        if not _invite_code_ok(event):
            logger.warning("create_job_bad_invite_code")
            return _response(401, {"error": "Missing or invalid invite code."})

        if not _check_and_increment_daily_limit():
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


def _get_invite_code() -> str:
    # Lazy client and one read per cold start (SSM client is created only when needed)
    global _invite_code
    if _invite_code is None:
        ssm = boto3.client("ssm")
        _invite_code = ssm.get_parameter(Name=INVITE_CODE_PARAM)["Parameter"]["Value"]
    return _invite_code


def _invite_code_ok(event: dict) -> bool:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    provided = headers.get("x-invite-code", "")
    # Encode so compare_digest accepts non-ASCII input instead of raising
    return hmac.compare_digest(provided.encode(), _get_invite_code().encode())


def _check_and_increment_daily_limit() -> bool:
    """
    Atomically check and increment today's (UTC) job count.
    Returns True if under the limit (and increments), False if the limit is reached.
    Uses a conditional write to avoid a read-then-write race condition.
    """
    table = dynamodb.Table(RATE_LIMITS_TABLE)
    today = datetime.now(timezone.utc).date().isoformat()

    try:
        table.update_item(
            Key={"date": f"JOBS#{today}"},
            UpdateExpression="ADD #count :one",
            ConditionExpression="attribute_not_exists(#count) OR #count < :limit",
            ExpressionAttributeNames={"#count": "count"},
            ExpressionAttributeValues={":one": Decimal("1"), ":limit": Decimal(str(DAILY_JOB_LIMIT))},
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
