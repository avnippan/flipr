import json

import structlog

from app.core import observability


class _Context:
    aws_request_id = "req-123"


def _lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_logs_are_json_with_request_and_job_id(capsys):
    observability.configure_logging()
    observability.start_request(_Context(), job_id="job-1")

    structlog.get_logger("test").info("hello", extra_field=7)

    (line,) = _lines(capsys)
    assert line["event"] == "hello"
    assert line["level"] == "info"
    assert line["request_id"] == "req-123"
    assert line["job_id"] == "job-1"
    assert line["extra_field"] == 7
    assert "timestamp" in line


def test_start_request_clears_the_previous_requests_ids(capsys):
    observability.configure_logging()
    observability.start_request(_Context(), job_id="job-1")
    observability.start_request(None)

    structlog.get_logger("test").info("next")

    (line,) = _lines(capsys)
    assert "job_id" not in line
    assert "request_id" not in line


def test_emit_metric_writes_embedded_metric_format(capsys, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    observability.start_request(_Context(), job_id="job-1")

    observability.emit_metric("BedrockLatencyMs", 812.5, "Milliseconds", Operation="vision")

    (line,) = _lines(capsys)
    metrics = line["_aws"]["CloudWatchMetrics"][0]
    assert metrics["Namespace"] == "Flipr"
    assert metrics["Dimensions"] == [["Environment", "Operation"]]
    assert metrics["Metrics"] == [{"Name": "BedrockLatencyMs", "Unit": "Milliseconds"}]
    assert line["BedrockLatencyMs"] == 812.5
    assert line["Environment"] == "dev"
    assert line["Operation"] == "vision"
    assert line["job_id"] == "job-1"
    assert isinstance(line["_aws"]["Timestamp"], int)


def test_count_metric_defaults_to_count_unit(capsys):
    observability.start_request(None)

    observability.emit_metric("JobsFailed", 1)

    (line,) = _lines(capsys)
    assert line["_aws"]["CloudWatchMetrics"][0]["Metrics"] == [{"Name": "JobsFailed", "Unit": "Count"}]
    assert line["JobsFailed"] == 1
