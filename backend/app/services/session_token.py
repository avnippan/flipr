"""Short-lived session tokens, so the invite code is typed once and never stored.

A token looks like "<expiry unix seconds>.<signature>". The signature is an
HMAC-SHA256 of the expiry, made with a key derived from the invite code. That
means:
  - nobody can forge or extend a token without knowing the invite code,
  - the invite code cannot be worked out from a token (HMAC is one-way),
  - rotating the invite code in SSM instantly invalidates every token.
"""
import hashlib
import hmac
import os
import time

INVITE_CODE_PARAM = os.environ.get("INVITE_CODE_PARAM", "/flipr/invite-code")
TOKEN_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "43200"))  # 12 hours

CODE_CACHE_SECONDS = int(os.environ.get("INVITE_CODE_CACHE_SECONDS", "300"))

_invite_code = None  # cached copy of the SSM value
_fetched_at = 0.0  # when _invite_code was last read from SSM


def get_invite_code() -> str:
    # Re-read SSM every few minutes so a rotated code takes effect without a restart.
    # (Lazy client: created only when needed.)
    global _invite_code, _fetched_at
    if _invite_code is None or time.time() - _fetched_at > CODE_CACHE_SECONDS:
        import boto3

        ssm = boto3.client("ssm")
        _invite_code = ssm.get_parameter(Name=INVITE_CODE_PARAM)["Parameter"]["Value"]
        _fetched_at = time.time()
    return _invite_code


def invite_code_matches(provided: str) -> bool:
    # Encode so compare_digest accepts non-ASCII input instead of raising
    return hmac.compare_digest(provided.encode(), get_invite_code().encode())


def _sign(expiry: str) -> str:
    key = hmac.new(get_invite_code().encode(), b"flipr-session-v1", hashlib.sha256).digest()
    return hmac.new(key, expiry.encode(), hashlib.sha256).hexdigest()


def issue_token(now: float | None = None) -> str:
    expiry = str(int((now if now is not None else time.time()) + TOKEN_TTL_SECONDS))
    return f"{expiry}.{_sign(expiry)}"


def token_is_valid(token: str, now: float | None = None) -> bool:
    expiry, _, signature = token.partition(".")
    if not expiry.isdigit() or not signature:
        return False
    if int(expiry) < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(signature.encode(), _sign(expiry).encode())
