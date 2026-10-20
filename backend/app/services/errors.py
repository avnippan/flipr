"""Errors raised by the Bedrock services that the worker maps to user messages."""

GUARDRAIL_MODERATION = "moderation"
GUARDRAIL_SENSITIVE_INFO = "sensitive_info"
GUARDRAIL_OTHER = "other"


class GuardrailBlocked(Exception):
    """Bedrock Guardrails intervened. `category` is one of the GUARDRAIL_* values."""

    def __init__(self, category: str = GUARDRAIL_OTHER):
        super().__init__(f"guardrail_intervened:{category}")
        self.category = category


def _has_findings(node, key: str) -> bool:
    """True if `key` appears anywhere in the nested trace with a non-empty value."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and v:
                return True
            if _has_findings(v, key):
                return True
    elif isinstance(node, list):
        return any(_has_findings(v, key) for v in node)
    return False


def guardrail_category(trace: dict | None) -> str:
    """Pick a category from a Converse `trace.guardrail` block. Unknown shapes
    fall through to GUARDRAIL_OTHER."""
    if _has_findings(trace, "sensitiveInformationPolicy"):
        return GUARDRAIL_SENSITIVE_INFO
    if _has_findings(trace, "contentPolicy"):
        return GUARDRAIL_MODERATION
    return GUARDRAIL_OTHER
