import json

import structlog

from app.core.observability import configure_logging, start_request
from app.services import session_token

configure_logging()
logger = structlog.get_logger(__name__)


def handler(event, context):
    """POST /session: trade the invite code for a short-lived token.

    The code goes in the request body (not a URL or stored header) and is never logged.
    """
    start_request(context)
    try:
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            logger.warning("create_session_malformed_json")
            return _response(400, {"error": "Request body must be valid JSON."})
        if not isinstance(body, dict):
            return _response(400, {"error": "Request body must be a JSON object."})
        code = body.get("invite_code")
        if not isinstance(code, str) or not session_token.invite_code_matches(code):
            logger.warning("create_session_bad_invite_code")
            return _response(401, {"error": "Missing or invalid invite code."})
        return _response(200, {
            "token": session_token.issue_token(),
            "expires_in": session_token.TOKEN_TTL_SECONDS,
        })
    except Exception as e:
        logger.error("create_session_error", error=str(e))
        return _response(500, {"error": "Internal server error"})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body),
    }
