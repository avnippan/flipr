import os
import time

import boto3
import structlog

logger = structlog.get_logger(__name__)

_TOKENS_TABLE = os.environ["TOKENS_TABLE_NAME"]
_TOKEN_SK_PREFIX = "TOKEN#"
_EXPIRY_WARNING_WINDOW = 7 * 86400  # warn 7 days before refresh token expires


def handler(event, context):
    logger.info("refresh_token_monitor_start")

    dynamodb = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
    table = dynamodb.Table(_TOKENS_TABLE)

    scan_kwargs = {
        "FilterExpression": "begins_with(#sk, :prefix)",
        "ExpressionAttributeNames": {"#sk": "SK"},
        "ExpressionAttributeValues": {":prefix": _TOKEN_SK_PREFIX},
    }

    items: list[dict] = []
    response = table.scan(**scan_kwargs)
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        response = table.scan(**scan_kwargs, ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    warning_threshold = time.time() + _EXPIRY_WARNING_WINDOW
    warnings = 0

    for item in items:
        user_id = item["PK"].removeprefix("USER#")
        marketplace = item["SK"].removeprefix("TOKEN#")
        rt_expires_at = item.get("refresh_token_expires_at")

        if rt_expires_at and int(rt_expires_at) < warning_threshold:
            days_left = (int(rt_expires_at) - time.time()) / 86400
            logger.critical(
                "refresh_token_expiring_soon",
                user_id=user_id,
                marketplace=marketplace,
                days_remaining=round(days_left, 1),
            )
            # TODO: send SNS alert for refresh token expiry
            warnings += 1
        else:
            logger.info("refresh_token_ok", user_id=user_id, marketplace=marketplace)

    logger.info(
        "refresh_token_monitor_complete",
        tokens_checked=len(items),
        warnings_issued=warnings,
    )
    return {
        "statusCode": 200,
        "body": f"Checked {len(items)} token(s), {warnings} warning(s) issued",
    }
