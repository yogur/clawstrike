"""Tests for Skill Mode MCP Server Startup — health, init_server, tool registration."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from clawstrike.config import ClawStrikeConfig, load_config

from .helpers import minimal_config, write_yaml

# ---------------------------------------------------------------------------
# health tool
# ---------------------------------------------------------------------------


async def test_health_returns_ok(cfg: ClawStrikeConfig) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("health", {})
    data = result.structured_content
    assert data["status"] == "ok"
    assert data["mode"] == "skill"
    assert data["classifier"] == "multilingual"


async def test_health_raises_if_not_initialized() -> None:
    import clawstrike.mcpserver as srv

    with pytest.raises(ToolError, match="not configured"):
        await srv.mcp.call_tool("health", {})


async def test_health_reflects_english_only_model(tmp_path: Path) -> None:
    import clawstrike.mcpserver as srv

    cfg = load_config(
        write_yaml(tmp_path, minimal_config({"classifier": {"model": "english-only"}}))
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("health", {})
    assert result.structured_content["classifier"] == "english-only"


# ---------------------------------------------------------------------------
# classify / gate raise before init
# ---------------------------------------------------------------------------


async def test_classify_raises_if_not_initialized() -> None:
    import clawstrike.mcpserver as srv

    with pytest.raises(ToolError, match="not configured"):
        await srv.mcp.call_tool(
            "classify",
            {
                "text": "x",
                "source_id": "s",
                "channel_type": "webhook",
                "session_id": "s",
            },
        )


async def test_gate_raises_if_not_initialized() -> None:
    import clawstrike.mcpserver as srv

    with pytest.raises(ToolError, match="not configured"):
        await srv.mcp.call_tool(
            "gate",
            {
                "action_description": "do something",
                "action_type": "shell_exec",
                "session_id": "s",
                "source_id": "x",
                "channel_type": "webhook",
            },
        )


# ---------------------------------------------------------------------------
# init_server wires config
# ---------------------------------------------------------------------------


def test_init_server_sets_config(cfg: ClawStrikeConfig) -> None:
    import clawstrike.mcpserver as srv

    assert srv._config is None
    srv.init_server(cfg)
    assert srv._config is cfg


def test_init_server_overrides_previous_config(
    tmp_path: Path, cfg: ClawStrikeConfig
) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    cfg2 = load_config(
        write_yaml(tmp_path, minimal_config({"classifier": {"model": "english-only"}}))
    )
    srv.init_server(cfg2)
    assert srv._config is cfg2


def test_init_server_resets_mismatch_sessions(cfg: ClawStrikeConfig) -> None:
    """init_server() clears _mismatch_sessions to ensure clean state on restart."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    srv._mismatch_sessions.add("stale-session")
    assert "stale-session" in srv._mismatch_sessions

    srv.init_server(cfg)
    assert len(srv._mismatch_sessions) == 0


# ---------------------------------------------------------------------------
# tool registration
# ---------------------------------------------------------------------------


async def test_all_three_tools_are_registered(cfg: ClawStrikeConfig) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)

    from fastmcp import Client

    async with Client(srv.mcp) as client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}

    assert "health" in tool_names
    assert "classify" in tool_names
    assert "gate" in tool_names
    assert "confirm" in tool_names
