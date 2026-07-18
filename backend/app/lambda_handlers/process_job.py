import asyncio
import json
import os
import time

import boto3
import structlog

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

            image_bytes = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)["Body"].read()
            log.info("process_job_image_downloaded", size_bytes=len(image_bytes))

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
            log.error("job_failed", error=str(e))
            _update_job(table, job_id, {
                "status": "failed",
                "error": str(e),
            })
