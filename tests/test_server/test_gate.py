"""Tests for the gate MCP tool.

Covers gate basic functionality, trust resolution, action taxonomy, decision
matrix, elevated scrutiny, content-source mismatch detection, and allowlist
bypass.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clawstrike.config import ClawStrikeConfig

from .helpers import get_audit_events, make_cfg_with_static_rules, make_cfg_with_trust

# ---------------------------------------------------------------------------
# Gate tool basic response shape
# ---------------------------------------------------------------------------


async def test_gate_returns_expected_fields(cfg: ClawStrikeConfig) -> None:
    """gate returns all required fields and echoes session/source metadata."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Read the calendar for today",
            "action_type": "calendar_read",
            "session_id": "session-abc-123",
            "source_id": "owner@example.com",
            "channel_type": "owner_dm",
        },
    )
    data = result.structured_content
    assert data["risk_level"] in ("critical", "high", "medium", "low")
    assert data["recommendation"] in ("allow", "block", "prompt_user")
    assert "trust_level" in data
    assert "reason" in data
    assert data["session_id"] == "session-abc-123"
    assert data["source_id"] == "owner@example.com"
    assert data["channel_type"] == "owner_dm"
    assert data["action_type"] == "calendar_read"


# ---------------------------------------------------------------------------
# Channel trust resolution in gate
# ---------------------------------------------------------------------------


async def test_gate_trust_level_resolved_from_known_channel(
    cfg: ClawStrikeConfig,
) -> None:
    """gate resolves trust_level from channel_type via config defaults."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "check calendar",
            "action_type": "calendar_read",
            "session_id": "s1",
            "source_id": "owner@example.com",
            "channel_type": "owner_dm",
        },
    )
    assert result.structured_content["trust_level"] == "high"  # owner_dm → HIGH


async def test_gate_unknown_channel_trust_level_is_untrusted(
    cfg: ClawStrikeConfig,
) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "do something",
            "action_type": "shell_exec",
            "session_id": "s2",
            "source_id": "x",
            "channel_type": "telegram",
        },
    )
    assert result.structured_content["trust_level"] == "untrusted"


# ---------------------------------------------------------------------------
# Action taxonomy classification
# ---------------------------------------------------------------------------


async def test_gate_known_action_type_taxonomy_and_reason(
    cfg: ClawStrikeConfig,
) -> None:
    """Known action types return the correct risk_level and reason=taxonomy_match."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Run a shell command",
            "action_type": "shell_exec",
            "session_id": "s",
            "source_id": "x",
            "channel_type": "owner_dm",
        },
    )
    data = result.structured_content
    assert data["risk_level"] == "critical"
    assert data["reason"] == "taxonomy_match"


async def test_gate_unknown_action_type_defaults_to_high(
    cfg: ClawStrikeConfig,
) -> None:
    """Unrecognised action_type defaults to 'high' risk (fail-safe) with descriptive reason."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Mystery action",
            "action_type": "completely_unknown_action",
            "session_id": "s",
            "source_id": "x",
            "channel_type": "owner_dm",
        },
    )
    data = result.structured_content
    assert data["risk_level"] == "high"
    assert data["reason"] == "unknown_action_type_defaulted_to_high"


# ---------------------------------------------------------------------------
# Gating decision matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action_type,channel_type,expected_risk,expected_trust,expected_recommendation",
    [
        ("shell_exec", "owner_dm", "critical", "high", "prompt_user"),
        ("send_email", "owner_dm", "high", "high", "allow"),
        ("web_browse", "email_body", "medium", "low", "prompt_user"),
        ("list_directory", "webhook", "low", "untrusted", "prompt_user"),
    ],
)
async def test_gate_decision_matrix_integration(
    cfg: ClawStrikeConfig,
    action_type: str,
    channel_type: str,
    expected_risk: str,
    expected_trust: str,
    expected_recommendation: str,
) -> None:
    """channel_type → trust resolution → taxonomy → matrix → recommendation."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "test action",
            "action_type": action_type,
            "session_id": "s",
            "source_id": "test-source",
            "channel_type": channel_type,
        },
    )
    data = result.structured_content
    assert data["risk_level"] == expected_risk
    assert data["trust_level"] == expected_trust
    assert data["recommendation"] == expected_recommendation


async def test_gate_audit_event_written(cfg: ClawStrikeConfig) -> None:
    """gate writes an action_gate audit event with the gating decision."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Execute shell command",
            "action_type": "shell_exec",
            "session_id": "audit-test-session",
            "source_id": "attacker@evil.com",
            "channel_type": "email_body",
        },
    )

    events = await get_audit_events(str(cfg.audit.db_path), event_type="action_gate")
    assert len(events) == 1
    ev = events[0]
    assert ev["source_id"] == "attacker@evil.com"
    assert ev["session_id"] == "audit-test-session"
    assert ev["decision"] == "block"
    assert ev["trust_level"] == "low"
    details = json.loads(ev["details_json"])
    assert details["action_type"] == "shell_exec"
    assert details["risk_level"] == "critical"
    assert details["recommendation"] == "block"


# ---------------------------------------------------------------------------
# Elevated Scrutiny Tightens Gating Recommendations
# ---------------------------------------------------------------------------


async def test_gate_no_elevated_scrutiny_uses_original_trust(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Without elevated scrutiny, trust_level == effective_trust_level."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "medium")
    srv.init_server(cfg)

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send an email",
            "action_type": "send_email",
            "session_id": "no-elev-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["elevated_scrutiny"] is False
    assert data["trust_level"] == "medium"
    assert data["effective_trust_level"] == "medium"
    assert data["recommendation"] == "prompt_user"  # send_email HIGH + MEDIUM


@pytest.mark.parametrize(
    "initial_trust,action_type,expected_effective_trust,expected_recommendation",
    [
        ("high", "send_email", "medium", "prompt_user"),  # HIGH→MEDIUM, was allow
        ("medium", "send_email", "low", "block"),  # MEDIUM→LOW, was prompt_user
        ("untrusted", "calendar_read", "untrusted", "prompt_user"),  # floor
    ],
)
async def test_gate_elevated_scrutiny_downgrade(
    tmp_path: Path,
    reset_server_config: MagicMock,
    initial_trust: str,
    action_type: str,
    expected_effective_trust: str,
    expected_recommendation: str,
) -> None:
    """Elevated scrutiny downgrades trust by one tier, tightening recommendations."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", initial_trust)
    srv.init_server(cfg)
    srv._elevated_sessions.add("elev-session")

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "test action",
            "action_type": action_type,
            "session_id": "elev-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["elevated_scrutiny"] is True
    assert data["trust_level"] == initial_trust
    assert data["effective_trust_level"] == expected_effective_trust
    assert data["recommendation"] == expected_recommendation


async def test_gate_elevated_scrutiny_audit_records_both_trust_tiers(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Audit log stores effective trust as top-level column and original in details."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "medium")
    srv.init_server(cfg)
    srv._elevated_sessions.add("audit-elev-session")

    await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "audit-elev-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )

    events = await get_audit_events(str(cfg.audit.db_path), event_type="action_gate")
    assert len(events) == 1
    ev = events[0]
    assert ev["trust_level"] == "low"  # effective (post-downgrade)
    details = json.loads(ev["details_json"])
    assert details["original_trust_level"] == "medium"
    assert details["elevated_scrutiny"] is True


async def test_gate_elevated_scrutiny_only_for_tagged_session(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Elevation only applies to the tagged session_id, not others."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)
    srv._elevated_sessions.add("flagged-session")

    # Non-elevated session: HIGH trust → allow for send_email
    r_normal = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "clean-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    d_normal = r_normal.structured_content
    assert d_normal["elevated_scrutiny"] is False
    assert d_normal["effective_trust_level"] == "high"
    assert d_normal["recommendation"] == "allow"

    # Elevated session: HIGH→MEDIUM → prompt_user for send_email
    r_elev = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "flagged-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    d_elev = r_elev.structured_content
    assert d_elev["elevated_scrutiny"] is True
    assert d_elev["effective_trust_level"] == "medium"
    assert d_elev["recommendation"] == "prompt_user"


async def test_gate_elevated_via_classify_flag_end_to_end(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Full flow: classify flags a session → gate uses the downgraded trust tier."""
    import clawstrike.mcpserver as srv
    from clawstrike.classifier import ClassifierResult

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    # score between flag (0.70) and block (0.92) → flag decision
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.80, label="injection", model="mock-model", latency_ms=2.0
    )
    c = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious input",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
            "session_id": "e2e-session",
        },
    )
    assert c.structured_content["decision"] == "flag"
    assert "e2e-session" in srv._elevated_sessions

    # gate should see HIGH→MEDIUM downgrade
    g = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "e2e-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    gd = g.structured_content
    assert gd["trust_level"] == "high"
    assert gd["effective_trust_level"] == "medium"
    assert gd["elevated_scrutiny"] is True
    assert gd["recommendation"] == "prompt_user"  # send_email HIGH + MEDIUM


# ---------------------------------------------------------------------------
# Content-Source Mismatch (gate side)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("initial_trust", ["high", "medium"])
async def test_gate_mismatch_forces_effective_trust_to_low(
    tmp_path: Path, reset_server_config: MagicMock, initial_trust: str
) -> None:
    """Mismatch session always forces effective_trust to LOW regardless of starting tier."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", initial_trust)
    srv.init_server(cfg)
    srv._mismatch_sessions.add("mismatch-session")

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send an email",
            "action_type": "send_email",
            "session_id": "mismatch-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["content_source_mismatch"] is True
    assert data["trust_level"] == initial_trust
    assert data["effective_trust_level"] == "low"
    assert data["recommendation"] == "block"  # send_email HIGH + LOW → block


async def test_gate_mismatch_stacks_with_elevated_scrutiny(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Both mismatch (→ LOW) and elevated_scrutiny (→ one tier down) → UNTRUSTED."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)
    srv._mismatch_sessions.add("stacked-session")
    srv._elevated_sessions.add("stacked-session")

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "read calendar",
            "action_type": "calendar_read",
            "session_id": "stacked-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["content_source_mismatch"] is True
    assert data["elevated_scrutiny"] is True
    assert data["trust_level"] == "high"
    # mismatch → LOW, then downgrade_trust(LOW) → UNTRUSTED
    assert data["effective_trust_level"] == "untrusted"
    assert data["recommendation"] == "prompt_user"  # LOW risk + UNTRUSTED → prompt_user


async def test_gate_mismatch_only_for_tagged_session(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Mismatch only affects the tagged session; other sessions are unaffected."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)
    srv._mismatch_sessions.add("mismatch-session")

    # Clean session: HIGH trust → allow for send_email
    r_clean = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "clean-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    d_clean = r_clean.structured_content
    assert d_clean["content_source_mismatch"] is False
    assert d_clean["effective_trust_level"] == "high"
    assert d_clean["recommendation"] == "allow"

    # Mismatch session: forced to LOW → send_email HIGH+LOW → block
    r_mismatch = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "mismatch-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    d_mismatch = r_mismatch.structured_content
    assert d_mismatch["content_source_mismatch"] is True
    assert d_mismatch["effective_trust_level"] == "low"
    assert d_mismatch["recommendation"] == "block"


async def test_gate_mismatch_audit_records_content_source_mismatch(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Gate audit event details include content_source_mismatch=True."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)
    srv._mismatch_sessions.add("mismatch-audit-session")

    await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "mismatch-audit-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )

    events = await get_audit_events(str(cfg.audit.db_path), event_type="action_gate")
    assert len(events) == 1
    details = json.loads(events[0]["details_json"])
    assert details["content_source_mismatch"] is True
    assert details["original_trust_level"] == "high"
    assert events[0]["trust_level"] == "low"  # effective trust stored at top level


# ---------------------------------------------------------------------------
# Action allowlist bypass in gate
# ---------------------------------------------------------------------------


_CONFIRM_BASE = {
    "action_type": "send_email",
    "action_description": "send email to team@company.com",
    "session_id": "confirm-sess",
    "source_id": "user@example.com",
    "channel_type": "email_body",
}


async def test_gate_allowlisted_action_returns_allow(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """After always_allow, subsequent gate calls for the same action return allow."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": "always_allow"})

    g = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email to team@company.com",
            "action_type": "send_email",
            "session_id": "confirm-sess",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    data = g.structured_content
    assert data["recommendation"] == "allow"
    assert data["allowlisted"] is True
    assert data["allowlist_rule_id"] is not None


async def test_gate_no_allowlist_returns_false(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Without an allowlist rule, gate returns allowlisted=False."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "read file",
            "action_type": "file_read",
            "session_id": "sess-1",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    data = result.structured_content
    assert data["allowlisted"] is False
    assert data["allowlist_rule_id"] is None


async def test_gate_global_allowlist_matches_any_source(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """A global allowlist rule matches any source_id."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool(
        "confirm", {**_CONFIRM_BASE, "decision": "always_allow_global"}
    )

    g = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "other-sess",
            "source_id": "other@example.com",  # different source
            "channel_type": "email_body",
        },
    )
    data = g.structured_content
    assert data["recommendation"] == "allow"
    assert data["allowlisted"] is True


async def test_gate_source_scoped_rule_does_not_match_other_source(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """A source-scoped allowlist rule does not match a different source_id."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": "always_allow"})

    g = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "other-sess",
            "source_id": "other@example.com",  # different source
            "channel_type": "email_body",
        },
    )
    assert g.structured_content["allowlisted"] is False


async def test_gate_allowlist_audit_includes_rule_id(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Auto-allowed gate audit event references the allowlist rule ID."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    confirm_result = await srv.mcp.call_tool(
        "confirm", {**_CONFIRM_BASE, "decision": "always_allow"}
    )
    rule_id = confirm_result.structured_content["allowlist_rule_id"]

    await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "confirm-sess",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )

    events = await get_audit_events(str(cfg.audit.db_path), event_type="action_gate")
    details = json.loads(events[-1]["details_json"])
    assert details["allowlisted"] is True
    assert details["allowlist_rule_id"] == rule_id


# ---------------------------------------------------------------------------
# allowlist_source field — DB vs config rule distinction
# ---------------------------------------------------------------------------


async def test_gate_db_allowlist_source_is_db(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """A rule created via confirm tool shows allowlist_source='db'."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": "always_allow"})

    g = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "src-sess",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    data = g.structured_content
    assert data["allowlisted"] is True
    assert data["allowlist_source"] == "db"


async def test_gate_no_allowlist_source_is_none(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """When not allowlisted, allowlist_source is None."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "read file",
            "action_type": "file_read",
            "session_id": "sess-x",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    assert result.structured_content["allowlist_source"] is None


# ---------------------------------------------------------------------------
# Static config rule matching in gate
# ---------------------------------------------------------------------------


async def test_gate_static_global_rule_allows_any_source(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """A global static config rule allows the action for any source_id."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_static_rules(
        tmp_path, [{"action_type": "file_read", "source_scope": "global"}]
    )
    srv.init_server(cfg)

    for source in ("user@example.com", "other@example.com", "anyone"):
        result = await srv.mcp.call_tool(
            "gate",
            {
                "action_description": "read a file",
                "action_type": "file_read",
                "session_id": "s",
                "source_id": source,
                "channel_type": "email_body",
            },
        )
        data = result.structured_content
        assert data["allowlisted"] is True
        assert data["allowlist_source"] == "config"
        assert data["allowlist_rule_id"] is None
        assert data["recommendation"] == "allow"


async def test_gate_static_source_scoped_rule_matches_own_source(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """A source-scoped static config rule only matches its own source_id."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_static_rules(
        tmp_path,
        [{"action_type": "send_email", "source_scope": "owner@example.com"}],
    )
    srv.init_server(cfg)

    # Matching source → allowlisted
    r_match = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "s1",
            "source_id": "owner@example.com",
            "channel_type": "owner_dm",
        },
    )
    assert r_match.structured_content["allowlisted"] is True
    assert r_match.structured_content["allowlist_source"] == "config"

    # Different source → not allowlisted
    r_no = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "s2",
            "source_id": "other@example.com",
            "channel_type": "owner_dm",
        },
    )
    assert r_no.structured_content["allowlisted"] is False
    assert r_no.structured_content["allowlist_source"] is None


async def test_gate_static_rule_wrong_action_type_no_match(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Static config rule does not match a different action_type."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_static_rules(
        tmp_path, [{"action_type": "file_read", "source_scope": "global"}]
    )
    srv.init_server(cfg)

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "s",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    assert result.structured_content["allowlisted"] is False


async def test_gate_static_rule_db_rule_takes_priority(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """When both DB and config rules match, DB rule is used (checked first)."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_static_rules(
        tmp_path,
        [{"action_type": "send_email", "source_scope": "global"}],
        db_name="priority_test.db",
    )
    srv.init_server(cfg)

    # Create a DB rule via confirm
    await srv.mcp.call_tool(
        "confirm",
        {
            "action_type": "send_email",
            "action_description": "send email",
            "session_id": "sess",
            "source_id": "user@example.com",
            "channel_type": "email_body",
            "decision": "always_allow",
        },
    )

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "sess",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    data = result.structured_content
    assert data["allowlisted"] is True
    # DB rule takes priority over config rule
    assert data["allowlist_source"] == "db"
    assert data["allowlist_rule_id"] is not None


async def test_gate_static_rule_audit_includes_allowlist_source_config(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Audit event details include allowlist_source='config' for config rule matches."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_static_rules(
        tmp_path, [{"action_type": "file_read", "source_scope": "global"}]
    )
    srv.init_server(cfg)

    await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "read a file",
            "action_type": "file_read",
            "session_id": "audit-s",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )

    events = await get_audit_events(str(cfg.audit.db_path), event_type="action_gate")
    assert len(events) == 1
    details = json.loads(events[0]["details_json"])
    assert details["allowlisted"] is True
    assert details["allowlist_source"] == "config"
    assert details["allowlist_rule_id"] is None
