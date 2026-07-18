import os
import time

import boto3
import structlog
from botocore.exceptions import ClientError
from requests.auth import HTTPBasicAuth
import requests

logger = structlog.get_logger(__name__)

# DynamoDB: TokensTable (TOKENS_TABLE_NAME)
# Read:  PK=USER#{user_id}  SK=TOKEN#{marketplace}
# Write: PK=USER#{user_id}  SK=TOKEN#{marketplace}
#        Fields: access_token, expires_at, refresh_token, refresh_token_expires_at, refreshing (concurrency lock)

_TOKENS_TABLE = os.environ["TOKENS_TABLE_NAME"]
_EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_REFRESH_SCOPE = (
    "https://api.ebay.com/oauth/api_scope/sell.inventory "
    "https://api.ebay.com/oauth/api_scope/sell.account "
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment"
)

_dynamodb = boto3.resource("dynamodb")


def get_valid_token(user_id: str, marketplace: str = "ebay") -> str:
    table = _dynamodb.Table(_TOKENS_TABLE)
    pk = f"USER#{user_id}"
    sk = f"TOKEN#{marketplace}"

    response = table.get_item(Key={"PK": pk, "SK": sk})
    item = response.get("Item")
    if not item:
        raise RuntimeError(f"No {marketplace} token found for user {user_id!r}")

    if int(item["expires_at"]) < time.time() + 300:
        _refresh_and_store(user_id, marketplace, str(item["refresh_token"]))
        item = table.get_item(Key={"PK": pk, "SK": sk})["Item"]

    return str(item["access_token"])


def _refresh_and_store(user_id: str, marketplace: str, refresh_token: str) -> None:
    table = _dynamodb.Table(_TOKENS_TABLE)
    pk = f"USER#{user_id}"
    sk = f"TOKEN#{marketplace}"

    # Claim the refresh lock — only one Lambda should hit the marketplace API at a time
    try:
        table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression="SET refreshing = :true",
            ConditionExpression="attribute_not_exists(refreshing) OR refreshing = :false",
            ExpressionAttributeValues={":true": True, ":false": False},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            time.sleep(2)
            logger.info("token_refresh_yielded", user_id=user_id, marketplace=marketplace)
            return
        raise

    try:
        resp = requests.post(
            _EBAY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": _REFRESH_SCOPE,
            },
            auth=HTTPBasicAuth(os.environ["EBAY_APP_ID"], os.environ["EBAY_CERT_ID"]),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        new_expires_at = int(time.time()) + int(data["expires_in"])
        table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression=(
                "SET access_token = :at, expires_at = :ea, "
                "refresh_token = :rt, refreshing = :false"
            ),
            ExpressionAttributeValues={
                ":at": data["access_token"],
                ":ea": new_expires_at,
                ":rt": data.get("refresh_token", refresh_token),
                ":false": False,
            },
        )
        logger.info(
            "token_refreshed",
            user_id=user_id,
            marketplace=marketplace,
            expires_at=new_expires_at,
        )
    except Exception:
        # Release the lock so the next invocation can retry
        table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression="SET refreshing = :false",
            ExpressionAttributeValues={":false": False},
        )
        raise
