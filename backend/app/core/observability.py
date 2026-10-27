"""Structured JSON logs and CloudWatch metrics for the Lambda handlers.

Logs: one JSON object per line on stdout, which CloudWatch Logs indexes by field.
Handlers call `start_request` first, so every line carries request_id and job_id.

Metrics: written as CloudWatch Embedded Metric Format (EMF) lines on stdout.
CloudWatch turns them into metrics on its own, so there is no extra AWS client,
no cold-start cost and no IAM permission to add.
"""
import json
import logging
import os
import time

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, get_contextvars

METRICS_NAMESPACE = "Flipr"


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def start_request(context=None, **fields) -> None:
    """Reset per-request log context. Call at the top of every handler (or SQS record)."""
    clear_contextvars()
    request_id = getattr(context, "aws_request_id", None)
    if request_id:
        fields["request_id"] = request_id
    bind_contextvars(**fields)


def emit_metric(name: str, value: float, unit: str = "Count", **dimensions: str) -> None:
    """Print one EMF line. Every metric gets an Environment dimension; extra
    dimensions (e.g. Operation="vision") are added to it. job_id and request_id
    ride along as plain fields so a data point can be traced back to its logs."""
    dims = {"Environment": os.environ.get("ENVIRONMENT", "dev"), **dimensions}
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRICS_NAMESPACE,
                "Dimensions": [list(dims)],
                "Metrics": [{"Name": name, "Unit": unit}],
            }],
        },
        **get_contextvars(),
        **dims,
        name: value,
    }
    print(json.dumps(record), flush=True)
