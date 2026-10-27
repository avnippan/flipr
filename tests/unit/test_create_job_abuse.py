import json
import os
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("DYNAMODB_TABLE_NAME", "jobs-test")
os.environ.setdefault("S3_BUCKET_NAME", "bucket-test")
os.environ.setdefault("ANALYSIS_QUEUE_URL", "https://sqs.test/queue")
os.environ.setdefault("RATE_LIMITS_TABLE_NAME", "limits-test")

from app.lambda_handlers import create_job  # noqa: E402

INVITE_CODE = "let-me-in"


def _event(code=INVITE_CODE):
    headers = {} if code is None else {"X-Invite-Code": code}
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
    monkeypatch.setattr(create_job, "_invite_code", INVITE_CODE)
    return MagicMock(table=table, s3=s3, sqs=sqs)


def test_missing_invite_code_returns_401(aws):
    resp = create_job.handler(_event(code=None), None)
    assert resp["statusCode"] == 401
    aws.table.update_item.assert_not_called()
    aws.sqs.send_message.assert_not_called()


def test_wrong_invite_code_returns_401(aws):
    resp = create_job.handler(_event(code="nope"), None)
    assert resp["statusCode"] == 401
    aws.sqs.send_message.assert_not_called()


def test_non_ascii_invite_code_returns_401_not_500(aws):
    resp = create_job.handler(_event(code="café"), None)
    assert resp["statusCode"] == 401


def test_header_name_is_case_insensitive(aws):
    event = {"headers": {"x-invite-code": INVITE_CODE}, "body": "{}"}
    assert create_job.handler(event, None)["statusCode"] == 202


def test_daily_limit_reached_returns_429(aws):
    aws.table.update_item.side_effect = _limit_error()
    resp = create_job.handler(_event(), None)
    assert resp["statusCode"] == 429
    assert "Daily limit" in json.loads(resp["body"])["error"]
    aws.sqs.send_message.assert_not_called()


def test_wrong_code_does_not_use_up_the_daily_cap(aws):
    create_job.handler(_event(code="nope"), None)
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
