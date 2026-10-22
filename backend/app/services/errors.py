"""Errors raised by the Bedrock services that the worker maps to user messages."""

GUARDRAIL_MODERATION = "moderation"
GUARDRAIL_SENSITIVE_INFO = "sensitive_info"
GUARDRAIL_OTHER = "other"


class GuardrailBlocked(Exception):
    """Bedrock Guardrails intervened. `category` is one of the GUARDRAIL_* values."""

    def __init__(self, category: str = GUARDRAIL_OTHER):
        super().__init__(f"guardrail_intervened:{category}")
        self.category = category


def _any_blocked(node) -> bool:
    """True if any entry under `node` has action == "BLOCKED"."""
    if isinstance(node, dict):
        return node.get("action") == "BLOCKED" or any(_any_blocked(v) for v in node.values())
    if isinstance(node, list):
        return any(_any_blocked(v) for v in node)
    return False


def _policy_blocked(node, policy: str) -> bool:
    """True if the nested trace has `policy` with at least one BLOCKED entry.

    Bedrock lists detected-but-not-blocked entries too (action "NONE" or
    "ANONYMIZED"), so a non-empty policy block does not mean it blocked.
    """
    if isinstance(node, dict):
        return any(
            (k == policy and _any_blocked(v)) or _policy_blocked(v, policy)
            for k, v in node.items()
        )
    if isinstance(node, list):
        return any(_policy_blocked(v, policy) for v in node)
    return False


def guardrail_category(trace: dict | None) -> str:
    """Pick a category from a Converse `trace.guardrail` block. Unknown shapes
    fall through to GUARDRAIL_OTHER."""
    if _policy_blocked(trace, "sensitiveInformationPolicy"):
        return GUARDRAIL_SENSITIVE_INFO
    if _policy_blocked(trace, "contentPolicy"):
        return GUARDRAIL_MODERATION
    return GUARDRAIL_OTHER
