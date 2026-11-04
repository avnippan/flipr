import json
import os
import time
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("DYNAMODB_TABLE_NAME", "jobs-test")
os.environ.setdefault("S3_BUCKET_NAME", "bucket-test")
os.environ.setdefault("ANALYSIS_QUEUE_URL", "https://sqs.test/queue")
os.environ.setdefault("RATE_LIMITS_TABLE_NAME", "limits-test")

from app.lambda_handlers import create_job  # noqa: E402
from app.services import session_token  # noqa: E402

INVITE_CODE = "let-me-in"


def _event(token="valid"):
    if token == "valid":
        token = session_token.issue_token()
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return {"headers": headers, "body": json.dumps({"marketplace": "ebay"})}


def _limit_error():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")


@pytest.fixture
def aws(monkeypatch):
    """Replace every AWS client in create_job with a mock; invite code is INVITE_CODE."""
    table = MagicMock()
    dynamodb = MagicMock()
    dynamodb.Table.return_value = table
    s3 = MagicMock()
    s3.generate_presigned_post.return_value = {"url": "https://s3.test", "fields": {"key": "k"}}
    sqs = MagicMock()
    monkeypatch.setattr(create_job, "dynamodb", dynamodb)
    monkeypatch.setattr(create_job, "s3", s3)
    monkeypatch.setattr(create_job, "sqs", sqs)
    monkeypatch.setattr(session_token, "_invite_code", INVITE_CODE)
    monkeypatch.setattr(session_token, "_fetched_at", time.time())
    return MagicMock(table=table, s3=s3, sqs=sqs)


def test_missing_token_returns_401(aws):
    resp = create_job.handler(_event(token=None), None)
    assert resp["statusCode"] == 401
    aws.table.update_item.assert_not_called()
    aws.sqs.send_message.assert_not_called()


def test_forged_token_returns_401(aws):
    resp = create_job.handler(_event(token="9999999999.deadbeef"), None)
    assert resp["statusCode"] == 401
    aws.sqs.send_message.assert_not_called()


def test_expired_token_returns_401(aws):
    old = session_token.issue_token(now=0)
    assert create_job.handler(_event(token=old), None)["statusCode"] == 401


def test_raw_invite_code_is_not_accepted_as_a_token(aws):
    assert create_job.handler(_event(token=INVITE_CODE), None)["statusCode"] == 401


def test_non_ascii_token_returns_401_not_500(aws):
    assert create_job.handler(_event(token="café.é"), None)["statusCode"] == 401


def test_header_name_is_case_insensitive(aws):
    event = {"headers": {"authorization": f"Bearer {session_token.issue_token()}"}, "body": "{}"}
    assert create_job.handler(event, None)["statusCode"] == 202


def test_daily_limit_reached_returns_429(aws):
    aws.table.update_item.side_effect = _limit_error()
    resp = create_job.handler(_event(), None)
    assert resp["statusCode"] == 429
    assert "Daily limit" in json.loads(resp["body"])["error"]
    aws.sqs.send_message.assert_not_called()


def test_bad_token_does_not_use_up_the_daily_cap(aws):
    create_job.handler(_event(token="9999999999.deadbeef"), None)
    aws.table.update_item.assert_not_called()


def test_valid_request_returns_202_and_presigned_post(aws):
    resp = create_job.handler(_event(), None)
    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])
    assert body["upload_url"] == "https://s3.test"
    assert body["upload_fields"] == {"key": "k"}
    aws.sqs.send_message.assert_called_once()


def test_upload_is_capped_at_bedrock_image_limit(aws):
    create_job.handler(_event(), None)
    conditions = aws.s3.generate_presigned_post.call_args.kwargs["Conditions"]
    assert ["content-length-range", 1, 3_670_016] in conditions


def test_other_dynamodb_errors_are_not_reported_as_429(aws):
    aws.table.update_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem"
    )
    resp = create_job.handler(_event(), None)
    assert resp["statusCode"] == 500
