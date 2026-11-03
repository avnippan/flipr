import json
import os
from decimal import Decimal

import boto3
import structlog

from app.core.observability import configure_logging, start_request

configure_logging()
logger = structlog.get_logger(__name__)


def decimal_default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError

# DynamoDB: JobsTable (DYNAMODB_TABLE_NAME)
# Read: PK=JOB#{job_id}  SK=META

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]


def handler(event, context):
    start_request(context)
    try:
        job_id = event["pathParameters"]["job_id"]
        start_request(context, job_id=job_id)
        log = logger
        log.info("get_job_start")

        table = dynamodb.Table(TABLE_NAME)
        result = table.get_item(Key={"PK": f"JOB#{job_id}", "SK": "META"})
        item = result.get("Item")

        if not item:
            log.warning("get_job_not_found")
            return _response(404, {"error": f"Job {job_id} not found"})

        log.info("get_job_complete", status=item["status"])

        return _response(200, {
            "job_id": job_id,
            "status": item["status"],
            "created_at": item["created_at"],
            "metadata": json.loads(item["metadata"]) if item.get("metadata") else None,
            "comps": json.loads(item["comps"]) if item.get("comps") else None,
            "listings": json.loads(item["listings"]) if item.get("listings") else None,
            "error": item.get("error"),
            "marketplace": item.get("marketplace", "ebay"),
        })

    except Exception as e:
        logger.error("get_job_error", error=str(e))
        return _response(500, {"error": "Internal server error"})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=decimal_default),
    }
