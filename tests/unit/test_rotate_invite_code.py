import os
import time
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from app.lambda_handlers import rotate_invite_code  # noqa: E402
from app.services import session_token  # noqa: E402


def test_rotation_writes_a_long_random_string_parameter(monkeypatch):
    ssm = MagicMock()
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: ssm)
    rotate_invite_code.handler({}, None)
    kwargs = ssm.put_parameter.call_args.kwargs
    assert kwargs["Name"] == "/flipr/invite-code"
    assert kwargs["Type"] == "String" and kwargs["Overwrite"] is True
    assert len(kwargs["Value"]) >= 24


def test_each_rotation_produces_a_different_code(monkeypatch):
    ssm = MagicMock()
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: ssm)
    rotate_invite_code.handler({}, None)
    rotate_invite_code.handler({}, None)
    first, second = (c.kwargs["Value"] for c in ssm.put_parameter.call_args_list)
    assert first != second


def test_new_code_is_never_logged(monkeypatch, capsys):
    ssm = MagicMock()
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: ssm)
    rotate_invite_code.handler({}, None)
    code = ssm.put_parameter.call_args.kwargs["Value"]
    out = capsys.readouterr()
    assert code not in out.out + out.err


def test_code_is_reread_from_ssm_after_the_cache_expires(monkeypatch):
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "fresh"}}
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: ssm)
    monkeypatch.setattr(session_token, "_invite_code", "stale")
    monkeypatch.setattr(session_token, "_fetched_at", time.time() - session_token.CODE_CACHE_SECONDS - 1)
    assert session_token.get_invite_code() == "fresh"


def test_code_is_not_reread_while_the_cache_is_fresh(monkeypatch):
    ssm = MagicMock()
    monkeypatch.setattr("boto3.client", lambda name, *a, **k: ssm)
    monkeypatch.setattr(session_token, "_invite_code", "cached")
    monkeypatch.setattr(session_token, "_fetched_at", time.time())
    assert session_token.get_invite_code() == "cached"
    ssm.get_parameter.assert_not_called()
