from app.services.errors import (
    GUARDRAIL_MODERATION,
    GUARDRAIL_OTHER,
    GUARDRAIL_SENSITIVE_INFO,
    guardrail_category,
)


def _trace(**policies):
    return {"guardrail": {"inputAssessment": {"gr-1": policies}}}


def test_blocked_content_filter_is_moderation():
    trace = _trace(contentPolicy={"filters": [{"type": "VIOLENCE", "action": "BLOCKED"}]})
    assert guardrail_category(trace) == GUARDRAIL_MODERATION


def test_blocked_pii_is_sensitive_info():
    trace = _trace(sensitiveInformationPolicy={"piiEntities": [{"type": "EMAIL", "action": "BLOCKED"}]})
    assert guardrail_category(trace) == GUARDRAIL_SENSITIVE_INFO


def test_unblocked_pii_does_not_hide_a_blocked_content_filter():
    trace = _trace(
        sensitiveInformationPolicy={"piiEntities": [{"type": "EMAIL", "action": "ANONYMIZED"}]},
        contentPolicy={"filters": [{"type": "VIOLENCE", "action": "BLOCKED"}]},
    )
    assert guardrail_category(trace) == GUARDRAIL_MODERATION


def test_unblocked_content_finding_does_not_win_over_blocked_pii():
    trace = _trace(
        contentPolicy={"filters": [{"type": "INSULTS", "action": "NONE"}]},
        sensitiveInformationPolicy={"piiEntities": [{"type": "EMAIL", "action": "BLOCKED"}]},
    )
    assert guardrail_category(trace) == GUARDRAIL_SENSITIVE_INFO


def test_detected_but_not_blocked_is_other():
    trace = _trace(contentPolicy={"filters": [{"type": "INSULTS", "action": "NONE"}]})
    assert guardrail_category(trace) == GUARDRAIL_OTHER


def test_missing_or_unknown_trace_is_other():
    assert guardrail_category(None) == GUARDRAIL_OTHER
    assert guardrail_category({"guardrail": {}}) == GUARDRAIL_OTHER
