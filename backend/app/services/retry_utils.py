"""Retry predicates shared by the S3 fetch and the Bedrock services.

Only transient error codes are retried. Permanent errors (ValidationException,
AccessDenied, ...) must fail on the first attempt, so pair these with
reraise=True.
"""
from botocore.exceptions import ClientError

_TRANSIENT_BEDROCK_CODES = {
    "ThrottlingException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelNotReadyException",
}


def _error_code(exc: BaseException) -> str | None:
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code")
    return None


def _is_missing_object(exc: BaseException) -> bool:
    """S3 upload race: the object may not exist yet. Retry only NoSuchKey."""
    return _error_code(exc) == "NoSuchKey"


def _is_transient_bedrock_error(exc: BaseException) -> bool:
    return _error_code(exc) in _TRANSIENT_BEDROCK_CODES
