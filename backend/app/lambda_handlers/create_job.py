import json
import os
import time
import uuid

import boto3
import structlog

logger = structlog.get_logger(__name__)

# DynamoDB: JobsTable (DYNAMODB_TABLE_NAME)
# Write: PK=JOB#{job_id}  SK=META
#        Fields: job_id, status, marketplace, created_at, updated_at, ttl

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
sqs = boto3.client("sqs")

TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
S3_BUCKET = os.environ["S3_BUCKET_NAME"]
ANALYSIS_QUEUE_URL = os.environ["ANALYSIS_QUEUE_URL"]


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        marketplace = body.get("marketplace", "ebay")

        job_id = str(uuid.uuid4())
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

        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": s3_key,
                "ContentType": "image/jpeg",
            },
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
            "upload_url": upload_url,
            "status_url": f"/jobs/{job_id}",
            "expires_in": 300,
        })

    except Exception as e:
        logger.error("create_job_error", error=str(e))
        return _response(500, {"error": "Internal server error"})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
