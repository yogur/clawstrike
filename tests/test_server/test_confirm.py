"""Tests for confirm tool and allowlist creation via confirm."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastmcp.exceptions import ToolError

from clawstrike.config import ClawStrikeConfig, load_config

from .helpers import get_audit_events, make_cfg_with_trust, minimal_config, write_yaml

_CONFIRM_BASE = {
    "action_type": "send_email",
    "action_description": "send email to team@company.com",
    "session_id": "confirm-sess",
    "source_id": "user@example.com",
    "channel_type": "email_body",
}


# ---------------------------------------------------------------------------
# Confirm tool basic behavior
# ---------------------------------------------------------------------------


async def test_confirm_approve_returns_recorded(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """confirm with decision='approve' returns status=recorded, decision=allow."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "confirm", {**_CONFIRM_BASE, "decision": "approve"}
    )
    data = result.structured_content
    assert data["status"] == "recorded"
    assert data["decision"] == "allow"
    assert data["user_decision"] == "approve"
    assert data["allowlist_created"] is False
    assert data["allowlist_rule_id"] is None


async def test_confirm_deny_returns_deny(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """confirm with decision='deny' returns decision=deny."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": "deny"})
    data = result.structured_content
    assert data["decision"] == "deny"
    assert data["user_decision"] == "deny"


async def test_confirm_short_aliases(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """confirm accepts short aliases: 'a' for approve, 'd' for deny."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)

    r1 = await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": "a"})
    assert r1.structured_content["user_decision"] == "approve"

    r2 = await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": "d"})
    assert r2.structured_content["user_decision"] == "deny"


async def test_confirm_case_insensitive(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """confirm normalizes decision case-insensitively."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "confirm", {**_CONFIRM_BASE, "decision": " APPROVE "}
    )
    assert result.structured_content["user_decision"] == "approve"


async def test_confirm_invalid_decision_raises(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """confirm with an invalid decision raises ToolError."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    with pytest.raises(ToolError, match="Invalid decision"):
        await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": "maybe"})


@pytest.mark.parametrize(
    "decision,expected_decision,expected_user_decision",
    [
        ("approve", "allow", "approve"),
        ("deny", "deny", "deny"),
    ],
)
async def test_confirm_audit_event(
    cfg: ClawStrikeConfig,
    reset_server_config: MagicMock,
    decision: str,
    expected_decision: str,
    expected_user_decision: str,
) -> None:
    """confirm writes an action_confirm audit event with the correct decision fields."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": decision})

    events = await get_audit_events(str(cfg.audit.db_path), event_type="action_confirm")
    assert len(events) == 1
    assert events[0]["decision"] == expected_decision
    details = json.loads(events[0]["details_json"])
    assert details["user_decision"] == expected_user_decision
    if decision == "approve":
        assert details["allowlist_created"] is False


# ---------------------------------------------------------------------------
# Allowlist creation via confirm
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["always_allow", "aa"])
async def test_confirm_always_allow_creates_allowlist_rule(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock, decision: str
) -> None:
    """confirm with always_allow (or 'aa' alias) creates a source-scoped allowlist rule."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": decision})
    data = result.structured_content
    assert data["allowlist_created"] is True
    assert data["allowlist_rule_id"] is not None
    assert data["user_decision"] == "always_allow"


async def test_confirm_always_allow_global_creates_global_rule(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """confirm with always_allow_global ('aag') creates a rule with source_scope='global'."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": "aag"})
    data = result.structured_content
    assert data["allowlist_created"] is True
    assert data["user_decision"] == "always_allow_global"

    from clawstrike.db import check_allowlist, open_db

    async with open_db(str(cfg.audit.db_path)) as conn:
        rule = await check_allowlist(conn, "send_email", "any-source")
    assert rule is not None
    assert rule["source_scope"] == "global"


async def test_confirm_always_allow_writes_allowlist_creation_audit(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """confirm always_allow writes an allowlist_creation audit event."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool("confirm", {**_CONFIRM_BASE, "decision": "always_allow"})

    events = await get_audit_events(
        str(cfg.audit.db_path), event_type="allowlist_creation"
    )
    assert len(events) == 1
    details = json.loads(events[0]["details_json"])
    assert details["action_type"] == "send_email"
    assert details["source_scope"] == "user@example.com"
    assert details["allowlist_rule_id"] is not None


async def test_confirm_always_allow_disabled_downgrades_to_approve(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """When allowlist_learning=false, always_allow is silently downgraded to approve."""
    import clawstrike.mcpserver as srv

    data = minimal_config(
        {
            "audit": {"db_path": str(tmp_path / "test.db")},
            "action_gating": {"allowlist_learning": False},
        }
    )
    cfg = load_config(write_yaml(tmp_path, data))
    srv.init_server(cfg)

    result = await srv.mcp.call_tool(
        "confirm", {**_CONFIRM_BASE, "decision": "always_allow"}
    )
    data = result.structured_content
    assert data["allowlist_created"] is False
    assert data["allowlist_rule_id"] is None
    assert data["user_decision"] == "approve"


# ---------------------------------------------------------------------------
# E2E: gate → confirm → gate flow
# ---------------------------------------------------------------------------


async def test_e2e_gate_prompt_user_then_always_allow_then_gate_allow(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Full E2E: gate returns prompt_user → confirm always_allow → gate returns allow."""
    import clawstrike.mcpserver as srv

    # send_email from medium trust → prompt_user (decision matrix)
    cfg = make_cfg_with_trust(tmp_path, "trusted_group", "medium")
    srv.init_server(cfg)

    # 1. Gate returns prompt_user for send_email from medium trust.
    g1 = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email to team",
            "action_type": "send_email",
            "session_id": "e2e-sess",
            "source_id": "user@example.com",
            "channel_type": "trusted_group",
        },
    )
    assert g1.structured_content["recommendation"] == "prompt_user"

    # 2. User confirms with always_allow — creates a source-scoped allowlist rule.
    c = await srv.mcp.call_tool(
        "confirm",
        {
            "action_type": "send_email",
            "action_description": "send email to team",
            "session_id": "e2e-sess",
            "source_id": "user@example.com",
            "channel_type": "trusted_group",
            "decision": "always_allow",
        },
    )
    assert c.structured_content["allowlist_created"] is True

    # 3. Subsequent gate returns allow with allowlisted=True.
    g2 = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email to team",
            "action_type": "send_email",
            "session_id": "e2e-sess",
            "source_id": "user@example.com",
            "channel_type": "trusted_group",
        },
    )
    g2_data = g2.structured_content
    assert g2_data["recommendation"] == "allow"
    assert g2_data["allowlisted"] is True

    # 4. Audit trail: all expected event types are present.
    all_events = await get_audit_events(str(cfg.audit.db_path))
    event_types = [e["event_type"] for e in all_events]
    assert "action_gate" in event_types
    assert "action_confirm" in event_types
    assert "allowlist_creation" in event_types

    # Auto-allowed gate event has allowlist info in details.
    gate_events = [e for e in all_events if e["event_type"] == "action_gate"]
    details = json.loads(gate_events[-1]["details_json"])
    assert details["allowlisted"] is True
