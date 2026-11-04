import json
import os
import time

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from app.lambda_handlers import create_session  # noqa: E402
from app.services import session_token  # noqa: E402


@pytest.fixture(autouse=True)
def code(monkeypatch):
    monkeypatch.setattr(session_token, "_invite_code", "let-me-in")
    monkeypatch.setattr(session_token, "_fetched_at", time.time())


def _call(body):
    return create_session.handler({"body": json.dumps(body)}, None)


def test_right_code_returns_a_valid_token():
    resp = _call({"invite_code": "let-me-in"})
    token = json.loads(resp["body"])["token"]
    assert resp["statusCode"] == 200
    assert session_token.token_is_valid(token)


def test_wrong_missing_or_non_string_code_returns_401():
    for body in ({"invite_code": "nope"}, {}, {"invite_code": 123}, {"invite_code": "café"}):
        assert _call(body)["statusCode"] == 401


def test_token_does_not_contain_the_invite_code():
    token = json.loads(_call({"invite_code": "let-me-in"})["body"])["token"]
    assert "let-me-in" not in token


def test_token_expires_and_cannot_be_tampered_with():
    token = session_token.issue_token(now=1000)
    expiry, sig = token.split(".")
    assert session_token.token_is_valid(token, now=1001)
    assert not session_token.token_is_valid(token, now=1000 + session_token.TOKEN_TTL_SECONDS + 1)
    assert not session_token.token_is_valid(f"{int(expiry) + 99999}.{sig}", now=1001)


def test_rotating_the_invite_code_invalidates_old_tokens(monkeypatch):
    token = session_token.issue_token()
    monkeypatch.setattr(session_token, "_invite_code", "new-code")
    monkeypatch.setattr(session_token, "_fetched_at", time.time())
    assert not session_token.token_is_valid(token)


def test_malformed_json_returns_400_not_500():
    # What PowerShell's curl.exe sends after stripping the inner quotes
    resp = create_session.handler({"body": "{invite_code:WRONG}"}, None)
    assert resp["statusCode"] == 400
    assert "valid JSON" in json.loads(resp["body"])["error"]


def test_json_that_is_not_an_object_returns_400():
    for raw in ('"let-me-in"', "[1, 2]", "42"):
        assert create_session.handler({"body": raw}, None)["statusCode"] == 400


def test_empty_body_is_still_401_not_400():
    # No body means no code given, which is the same as a missing code
    assert create_session.handler({"body": None}, None)["statusCode"] == 401
