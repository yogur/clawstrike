"""Unit tests for the action risk taxonomy and gating decision matrix.

These tests are pure — no mocking, no async, no server fixtures needed.
"""

from __future__ import annotations

import pytest

from clawstrike.config import TrustLevel
from clawstrike.gating import apply_decision_matrix, classify_action, downgrade_trust

# ---------------------------------------------------------------------------
# Action Risk Taxonomy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action_type,expected_risk",
    [
        ("shell_exec", "critical"),
        ("exec", "critical"),
        ("skill_install", "critical"),
        ("cron_create", "critical"),
        ("send_email", "high"),
        ("file_write", "high"),
        ("calendar_modify", "high"),
        ("file_read_sensitive", "medium"),
        ("web_browse", "medium"),
        ("calendar_read", "low"),
        ("file_read", "low"),
        ("list_directory", "low"),
    ],
)
def test_taxonomy_action_risk(action_type: str, expected_risk: str) -> None:
    risk, _ = classify_action(action_type)
    assert risk == expected_risk


def test_known_action_type_reason_is_taxonomy_match() -> None:
    _, reason = classify_action("shell_exec")
    assert reason == "taxonomy_match"


def test_unknown_action_type_defaults_to_high() -> None:
    risk, _ = classify_action("completely_unknown_action")
    assert risk == "high"


def test_unknown_action_type_reason_explains_default() -> None:
    _, reason = classify_action("totally_made_up")
    assert reason == "unknown_action_type_defaulted_to_high"


def test_empty_action_type_defaults_to_high() -> None:
    risk, _ = classify_action("")
    assert risk == "high"


# ---------------------------------------------------------------------------
# Gating Decision Matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "risk_level,trust_level,expected",
    [
        # Critical row
        ("critical", TrustLevel.HIGH, "prompt_user"),
        ("critical", TrustLevel.MEDIUM, "block"),
        ("critical", TrustLevel.LOW, "block"),
        ("critical", TrustLevel.UNTRUSTED, "block"),
        # High row
        ("high", TrustLevel.HIGH, "allow"),
        ("high", TrustLevel.MEDIUM, "prompt_user"),
        ("high", TrustLevel.LOW, "block"),
        ("high", TrustLevel.UNTRUSTED, "block"),
        # Medium row
        ("medium", TrustLevel.HIGH, "allow"),
        ("medium", TrustLevel.MEDIUM, "allow"),
        ("medium", TrustLevel.LOW, "prompt_user"),
        ("medium", TrustLevel.UNTRUSTED, "block"),
        # Low row
        ("low", TrustLevel.HIGH, "allow"),
        ("low", TrustLevel.MEDIUM, "allow"),
        ("low", TrustLevel.LOW, "allow"),
        ("low", TrustLevel.UNTRUSTED, "prompt_user"),
    ],
)
def test_decision_matrix(
    risk_level: str, trust_level: TrustLevel, expected: str
) -> None:
    assert apply_decision_matrix(risk_level, trust_level) == expected


# ---------------------------------------------------------------------------
# Trust-Level Downgrade for Elevated Scrutiny
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_level,expected",
    [
        (TrustLevel.HIGH, TrustLevel.MEDIUM),
        (TrustLevel.MEDIUM, TrustLevel.LOW),
        (TrustLevel.LOW, TrustLevel.UNTRUSTED),
        (TrustLevel.UNTRUSTED, TrustLevel.UNTRUSTED),
    ],
)
def test_downgrade_trust(input_level: TrustLevel, expected: TrustLevel) -> None:
    assert downgrade_trust(input_level) == expected


def test_double_downgrade_stacks() -> None:
    """Two successive downgrades apply two tiers (HIGH → LOW)."""
    assert downgrade_trust(downgrade_trust(TrustLevel.HIGH)) == TrustLevel.LOW
