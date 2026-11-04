import os
import secrets

import structlog

from app.core.observability import configure_logging, emit_metric, start_request

configure_logging()
logger = structlog.get_logger(__name__)

INVITE_CODE_PARAM = os.environ.get("INVITE_CODE_PARAM", "/flipr/invite-code")


def handler(event, context):
    """Scheduled (EventBridge): replace the invite code with a fresh random one.

    The new code is never logged or returned. Read it with:
      aws ssm get-parameter --name /flipr/invite-code --query Parameter.Value --output text
    Sessions signed with the old code stop working once each Lambda's short cache
    (see services/session_token.py) refreshes, so users enter the new code again.
    """
    start_request(context)
    import boto3  # lazy: no AWS client at import time

    new_code = secrets.token_urlsafe(18)  # 24 URL-safe characters
    # Type must stay String: CloudFormation parameters in this stack cannot be SecureString
    boto3.client("ssm").put_parameter(
        Name=INVITE_CODE_PARAM, Value=new_code, Type="String", Overwrite=True
    )
    logger.info("invite_code_rotated")
    emit_metric("InviteCodeRotated", 1)
    return {"rotated": True}
