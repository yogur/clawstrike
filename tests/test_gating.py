"""Unit tests for the action risk taxonomy and gating decision matrix.

These tests are pure — no mocking, no async, no server fixtures needed.
"""

from __future__ import annotations

import pytest

from clawstrike.config import TrustLevel
from clawstrike.gating import apply_decision_matrix, classify_action

# ---------------------------------------------------------------------------
# US-017 — Action Risk Taxonomy
# ---------------------------------------------------------------------------


def test_shell_exec_is_critical() -> None:
    risk, _ = classify_action("shell_exec")
    assert risk == "critical"


def test_exec_is_critical() -> None:
    risk, _ = classify_action("exec")
    assert risk == "critical"


def test_skill_install_is_critical() -> None:
    risk, _ = classify_action("skill_install")
    assert risk == "critical"


def test_cron_create_is_critical() -> None:
    risk, _ = classify_action("cron_create")
    assert risk == "critical"


def test_send_email_is_high() -> None:
    risk, _ = classify_action("send_email")
    assert risk == "high"


def test_file_write_is_high() -> None:
    risk, _ = classify_action("file_write")
    assert risk == "high"


def test_calendar_modify_is_high() -> None:
    risk, _ = classify_action("calendar_modify")
    assert risk == "high"


def test_file_read_sensitive_is_medium() -> None:
    risk, _ = classify_action("file_read_sensitive")
    assert risk == "medium"


def test_web_browse_is_medium() -> None:
    risk, _ = classify_action("web_browse")
    assert risk == "medium"


def test_calendar_read_is_low() -> None:
    risk, _ = classify_action("calendar_read")
    assert risk == "low"


def test_file_read_is_low() -> None:
    risk, _ = classify_action("file_read")
    assert risk == "low"


def test_list_directory_is_low() -> None:
    risk, _ = classify_action("list_directory")
    assert risk == "low"


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
# US-018 — Gating Decision Matrix
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
