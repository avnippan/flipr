import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

for key, value in {
    "AWS_DEFAULT_REGION": "us-east-1", "DYNAMODB_TABLE_NAME": "jobs-test",
    "S3_BUCKET_NAME": "bucket-test", "EBAY_APP_ID": "x", "EBAY_CERT_ID": "x",
    "TOKENS_TABLE_NAME": "t", "RATE_LIMITS_TABLE_NAME": "r", "BEDROCK_MODEL_ID": "m",
}.items():
    os.environ.setdefault(key, value)

from app.lambda_handlers import process_job  # noqa: E402

EVENT = {"Records": [{"body": json.dumps({"job_id": "job-1", "s3_key": "uploads/job-1.jpg"})}]}
CONTEXT = SimpleNamespace(aws_request_id="req-1")


def _metrics(capsys):
    """Names of the EMF metrics printed, in order."""
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return [line["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"] for line in lines if "_aws" in line]


@pytest.fixture
def table(monkeypatch):
    table = MagicMock()
    monkeypatch.setattr(process_job, "dynamodb", MagicMock(Table=MagicMock(return_value=table)))
    return table


def test_failed_job_emits_started_and_failed_metrics(monkeypatch, table, capsys):
    monkeypatch.setattr(process_job, "_get_image_bytes", MagicMock(side_effect=RuntimeError("boom")))

    process_job.handler(EVENT, CONTEXT)

    assert _metrics(capsys) == ["JobsStarted", "JobsFailed"]


def test_oversized_photo_counts_as_failed_job(monkeypatch, table, capsys):
    big = b"x" * (process_job.MAX_IMAGE_BYTES + 1)
    monkeypatch.setattr(process_job, "_get_image_bytes", MagicMock(return_value=big))

    process_job.handler(EVENT, CONTEXT)

    assert _metrics(capsys) == ["JobsStarted", "JobsFailed"]


def test_successful_job_emits_started_and_completed_metrics(monkeypatch, table, capsys):
    model = MagicMock()
    model.model_dump.return_value = {}
    item = MagicMock(item_type="shirt", brand="x", search_query="q")
    item.model_dump.return_value = {}
    comps = MagicMock(median_price=10.0)
    comps.model_dump.return_value = {}

    async def fake_analyze(*_):
        return item

    async def fake_comps(_):
        return comps

    async def fake_drafts(*_):
        return [model]

    monkeypatch.setattr(process_job, "_get_image_bytes", MagicMock(return_value=b"jpeg"))
    monkeypatch.setattr(process_job, "analyze_image", fake_analyze)
    monkeypatch.setattr(process_job, "fetch_comps", fake_comps)
    monkeypatch.setattr(process_job, "draft_listings", fake_drafts)

    process_job.handler(EVENT, CONTEXT)

    assert _metrics(capsys) == ["JobsStarted", "JobsCompleted"]
