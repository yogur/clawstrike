"""Action risk taxonomy and gating decision matrix for ClawStrike.

Implements PRD Section 4.3.1 (taxonomy) and Section 4.3.2 (decision matrix).
Both public functions are pure and require no I/O or mocking in tests.
"""

from __future__ import annotations

from clawstrike.config import TrustLevel

# ---------------------------------------------------------------------------
# Hardcoded action risk taxonomy (PRD Section 4.3.1)
# ---------------------------------------------------------------------------

_TAXONOMY: dict[str, str] = {
    # Critical — Shell execution
    "exec": "critical",
    "spawn": "critical",
    "system": "critical",
    "child_process": "critical",
    "shell_exec": "critical",
    # Critical — Outbound network to unknown hosts
    "outbound_network_unknown": "critical",
    "curl": "critical",
    "wget": "critical",
    "fetch": "critical",
    # Critical — Skill installation / modification
    "skill_install": "critical",
    "skill_modify": "critical",
    # Critical — Cron job creation / modification
    "cron_create": "critical",
    "cron_modify": "critical",
    # High — Email / message sending
    "send_email": "high",
    "send_message": "high",
    # High — File system writes outside sandbox
    "file_write": "high",
    # High — Calendar / contact modification
    "calendar_modify": "high",
    "contact_modify": "high",
    # Medium — File system reads of sensitive files
    "file_read_sensitive": "medium",
    # Medium — Web browsing / navigation
    "web_browse": "medium",
    "web_navigate": "medium",
    "form_submit": "medium",
    # Low — Read-only operations
    "file_read": "low",
    "calendar_read": "low",
    "list_directory": "low",
}

# Unknown action types default to HIGH (fail-safe).
_DEFAULT_RISK = "high"


def classify_action(action_type: str) -> tuple[str, str]:
    """Return ``(risk_level, reason)`` for the given *action_type*.

    *risk_level* is one of ``"critical"``, ``"high"``, ``"medium"``, ``"low"``.
    *reason* is a short machine-readable string explaining the classification:
    ``"taxonomy_match"`` when the action_type is in the hardcoded taxonomy, or
    ``"unknown_action_type_defaulted_to_high"`` for anything not recognised.

    Unknown action types default to ``"high"`` (fail-safe).
    """
    risk = _TAXONOMY.get(action_type)
    if risk is not None:
        return risk, "taxonomy_match"
    return _DEFAULT_RISK, "unknown_action_type_defaulted_to_high"


# ---------------------------------------------------------------------------
# Gating decision matrix (PRD Section 4.3.2)
# ---------------------------------------------------------------------------
#
#              | High Trust   | Medium Trust | Low Trust | Untrusted  |
# Critical     | prompt_user  | block        | block     | block      |
# High         | allow        | prompt_user  | block     | block      |
# Medium       | allow        | allow        | prompt_user | block    |
# Low          | allow        | allow        | allow     | prompt_user|

_MATRIX: dict[str, dict[TrustLevel, str]] = {
    "critical": {
        TrustLevel.HIGH: "prompt_user",
        TrustLevel.MEDIUM: "block",
        TrustLevel.LOW: "block",
        TrustLevel.UNTRUSTED: "block",
    },
    "high": {
        TrustLevel.HIGH: "allow",
        TrustLevel.MEDIUM: "prompt_user",
        TrustLevel.LOW: "block",
        TrustLevel.UNTRUSTED: "block",
    },
    "medium": {
        TrustLevel.HIGH: "allow",
        TrustLevel.MEDIUM: "allow",
        TrustLevel.LOW: "prompt_user",
        TrustLevel.UNTRUSTED: "block",
    },
    "low": {
        TrustLevel.HIGH: "allow",
        TrustLevel.MEDIUM: "allow",
        TrustLevel.LOW: "allow",
        TrustLevel.UNTRUSTED: "prompt_user",
    },
}


def apply_decision_matrix(risk_level: str, trust_level: TrustLevel) -> str:
    """Return the gating recommendation for *(risk_level, trust_level)*.

    Returns one of: ``"allow"``, ``"block"``, ``"prompt_user"``.
    """
    return _MATRIX[risk_level][trust_level]


# ---------------------------------------------------------------------------
# Trust-level downgrade for elevated scrutiny
# ---------------------------------------------------------------------------

_TRUST_DOWNGRADE: dict[TrustLevel, TrustLevel] = {
    TrustLevel.HIGH: TrustLevel.MEDIUM,
    TrustLevel.MEDIUM: TrustLevel.LOW,
    TrustLevel.LOW: TrustLevel.UNTRUSTED,
    TrustLevel.UNTRUSTED: TrustLevel.UNTRUSTED,
}


def downgrade_trust(trust_level: TrustLevel) -> TrustLevel:
    """Downgrade *trust_level* by one tier for elevated-scrutiny sessions.

    Ordering: HIGH → MEDIUM → LOW → UNTRUSTED (floor; stays at UNTRUSTED).
    Called once per elevation source; stack multiple calls for stacked downgrades
    (e.g. elevated scrutiny + content-source mismatch).
    """
    return _TRUST_DOWNGRADE[trust_level]
