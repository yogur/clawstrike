"""Tests for US-002: Skill Mode MCP Server Startup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastmcp.exceptions import ToolError

from clawstrike.classifier import ClassifierResult
from clawstrike.config import ClawStrikeConfig, load_config

# ---------------------------------------------------------------------------
# Helpers (mirrored from test_config.py)
# ---------------------------------------------------------------------------


def write_yaml(tmp_path: Path, data: dict) -> Path:
    cfg_file = tmp_path / "clawstrike.yaml"
    cfg_file.write_text(yaml.dump(data))
    return cfg_file


def minimal_config(extra: dict | None = None) -> dict:
    base: dict = {"clawstrike": {"classifier": {"model": "multilingual"}}}
    if extra:
        base["clawstrike"].update(extra)
    return base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg(tmp_path: Path) -> ClawStrikeConfig:
    """Return a minimal validated config with a per-test isolated DB path."""
    data = minimal_config()
    data["clawstrike"]["audit"] = {"db_path": str(tmp_path / "test.db")}
    return load_config(write_yaml(tmp_path, data))


@pytest.fixture(autouse=True)
def reset_server_config():
    """Reset module globals and mock create_classifier for each test.

    Patching create_classifier prevents any attempt to download real HF models.
    The mock classifier returns a fixed benign ClassifierResult by default;
    individual tests may override mock_clf.classify.return_value to set a
    specific score.

    Yields the mock classifier so tests can configure score/label per-scenario.
    """
    import clawstrike.mcpserver as srv

    mock_clf = MagicMock()
    mock_clf.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )

    with patch("clawstrike.mcpserver.create_classifier", return_value=mock_clf):
        yield mock_clf

    srv._config = None
    srv._classifier = None
    srv._elevated_sessions.clear()
    srv._db_path = None


# ---------------------------------------------------------------------------
# AC: server exposes health tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_ok(cfg: ClawStrikeConfig) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("health", {})
    data = result.structured_content
    assert data["status"] == "ok"
    assert data["mode"] == "skill"
    assert data["classifier"] == "multilingual"


@pytest.mark.asyncio
async def test_health_raises_if_not_initialized() -> None:
    import clawstrike.mcpserver as srv

    # _config is None (reset by autouse fixture).
    # FastMCP wraps RuntimeError in ToolError at the protocol boundary.
    with pytest.raises(ToolError, match="not configured"):
        await srv.mcp.call_tool("health", {})


# ---------------------------------------------------------------------------
# AC: server exposes classify tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_returns_result(cfg: ClawStrikeConfig) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "Hello, what is the weather today?",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    data = result.structured_content
    assert "decision" in data
    assert data["decision"] in ("pass", "flag", "block")
    assert "score" in data
    assert isinstance(data["score"], float)
    assert "label" in data
    assert data["label"] in ("benign", "injection", "jailbreak")
    assert "model" in data
    assert "latency_ms" in data


@pytest.mark.asyncio
async def test_classify_echoes_source_metadata(cfg: ClawStrikeConfig) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "some text",
            "source_id": "discord:12345",
            "channel_type": "public_group",
        },
    )
    data = result.structured_content
    assert data["source_id"] == "discord:12345"
    assert data["channel_type"] == "public_group"


@pytest.mark.asyncio
async def test_classify_raises_if_not_initialized() -> None:
    import clawstrike.mcpserver as srv

    with pytest.raises(ToolError, match="not configured"):
        await srv.mcp.call_tool(
            "classify",
            {"text": "x", "source_id": "s", "channel_type": "webhook"},
        )


# ---------------------------------------------------------------------------
# AC: server exposes gate tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_returns_recommendation(cfg: ClawStrikeConfig) -> None:
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
    assert "risk_level" in data
    assert data["risk_level"] in ("critical", "high", "medium", "low")
    assert "recommendation" in data
    assert data["recommendation"] in ("allow", "block", "prompt_user")
    assert "trust_level" in data
    assert "reason" in data


@pytest.mark.asyncio
async def test_gate_echoes_session_and_source(cfg: ClawStrikeConfig) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Send an email",
            "action_type": "send_email",
            "session_id": "s-xyz",
            "source_id": "contact@example.com",
            "channel_type": "email_body",
        },
    )
    data = result.structured_content
    assert data["session_id"] == "s-xyz"
    assert data["source_id"] == "contact@example.com"
    assert data["channel_type"] == "email_body"
    assert data["action_type"] == "send_email"


@pytest.mark.asyncio
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
# AC: init_server wires config correctly
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


# ---------------------------------------------------------------------------
# AC: health reflects the configured classifier model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_reflects_english_only_model(tmp_path: Path) -> None:
    import clawstrike.mcpserver as srv

    cfg = load_config(
        write_yaml(tmp_path, minimal_config({"classifier": {"model": "english-only"}}))
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("health", {})
    assert result.structured_content["classifier"] == "english-only"


# ---------------------------------------------------------------------------
# AC: tool listing confirms all three tools are registered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
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


# ---------------------------------------------------------------------------
# US-008 / US-009 / US-010 — Classifier Decision Pipeline
# ---------------------------------------------------------------------------

# Default threshold defaults: block=0.92, flag=0.70
_SCORE_BLOCK = 0.95  # ≥ 0.92 → block
_SCORE_FLAG = 0.80  # ≥ 0.70 and < 0.92 → flag
_SCORE_PASS = 0.30  # < 0.70 → pass

_CLASSIFY_ARGS = {
    "text": "test input",
    "source_id": "user@example.com",
    "channel_type": "email_body",
}


@pytest.mark.asyncio
async def test_classify_block_includes_reason(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_BLOCK, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert data["decision"] == "block"
    assert data["reason"] == "prompt_injection_detected"


@pytest.mark.asyncio
async def test_classify_block_includes_standard_fields(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_BLOCK, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert data["score"] == _SCORE_BLOCK
    assert data["label"] == "injection"
    assert data["model"] == "mock-model"
    assert data["latency_ms"] > 0


@pytest.mark.asyncio
async def test_classify_flag_includes_elevated_scrutiny(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert data["decision"] == "flag"
    assert data["elevated_scrutiny"] is True


@pytest.mark.asyncio
async def test_classify_flag_has_no_reason_field(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    assert "reason" not in result.structured_content


@pytest.mark.asyncio
async def test_classify_pass_decision_and_score(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert data["decision"] == "pass"
    assert data["score"] == _SCORE_PASS


@pytest.mark.asyncio
async def test_classify_pass_has_no_reason_or_elevated_scrutiny(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert "reason" not in data
    assert data.get("elevated_scrutiny") is not True


@pytest.mark.asyncio
async def test_classify_session_tagged_on_flag(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Flag classify for session X → gate for X reports elevated_scrutiny=True."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool(
        "classify",
        {**_CLASSIFY_ARGS, "session_id": "session-001"},
    )
    gate_result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "read file",
            "action_type": "file_read",
            "session_id": "session-001",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    assert gate_result.structured_content["elevated_scrutiny"] is True


@pytest.mark.asyncio
async def test_classify_no_session_tag_on_pass(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Pass classify with session_id does NOT tag the session as elevated."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool(
        "classify",
        {**_CLASSIFY_ARGS, "session_id": "session-002"},
    )
    gate_result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "read file",
            "action_type": "file_read",
            "session_id": "session-002",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    assert gate_result.structured_content["elevated_scrutiny"] is False


@pytest.mark.asyncio
async def test_classify_session_not_tagged_without_session_id(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Flag classify without session_id does not pollute an arbitrary session."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    # classify without session_id (defaults to empty string — no tagging)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    gate_result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "read file",
            "action_type": "file_read",
            "session_id": "unrelated-session",
            "source_id": "user@example.com",
            "channel_type": "email_body",
        },
    )
    assert gate_result.structured_content["elevated_scrutiny"] is False


# ---------------------------------------------------------------------------
# US-011 — Channel Trust Level Resolution (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_response_includes_trust_level(
    cfg: ClawStrikeConfig,
) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert "trust_level" in data
    # US-012: first contact is always UNTRUSTED regardless of channel.
    # email_body would normally resolve to LOW, but user@example.com is a new
    # contact in the fresh per-test DB, so UNTRUSTED override applies.
    assert data["trust_level"] == "untrusted"


@pytest.mark.asyncio
async def test_classify_response_includes_threshold_applied(
    cfg: ClawStrikeConfig,
) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert "threshold_applied" in data
    assert "block" in data["threshold_applied"]
    assert "flag" in data["threshold_applied"]


@pytest.mark.asyncio
async def test_classify_unknown_channel_trust_level_is_untrusted(
    cfg: ClawStrikeConfig,
) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "classify",
        {"text": "hello", "source_id": "u", "channel_type": "unknown_channel"},
    )
    assert result.structured_content["trust_level"] == "untrusted"


@pytest.mark.asyncio
async def test_gate_trust_level_resolved_from_known_channel(
    cfg: ClawStrikeConfig,
) -> None:
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
    # owner_dm → HIGH
    assert result.structured_content["trust_level"] == "high"


@pytest.mark.asyncio
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
# US-015 — Trust-Modulated Classifier Thresholds (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_untrusted_channel_blocks_at_effective_threshold(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Score 0.83 with untrusted channel (eff_block=0.82) → block decision."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.83, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "classify",
        {"text": "t", "source_id": "s", "channel_type": "webhook"},
    )
    data = result.structured_content
    # webhook → UNTRUSTED, modifier block=-0.10 → eff_block=0.82 < 0.83 → block
    assert data["decision"] == "block"
    assert pytest.approx(data["threshold_applied"]["block"], abs=1e-9) == 0.82


@pytest.mark.asyncio
async def test_classify_high_trust_raises_block_threshold(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Score 0.93 would block at base thresholds (0.92) but only flags at owner_dm
    HIGH trust (eff_block=0.97, eff_flag=0.80), demonstrating the raised threshold.

    Pre-condition: 'owner' must be a known contact so the US-012 first-contact
    UNTRUSTED override does not apply. A seed call registers the contact first.
    """
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)

    # Seed: register 'owner' as a known contact (score=0.0 → pass, no side effects).
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {"text": "hello", "source_id": "owner", "channel_type": "owner_dm"},
    )

    # Now test threshold modulation on the second (known-contact) call.
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.93, label="benign", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {"text": "t", "source_id": "owner", "channel_type": "owner_dm"},
    )
    data = result.structured_content
    # owner_dm → HIGH, modifier block=+0.05 → eff_block=0.97 > 0.93 → not blocked
    # but 0.93 ≥ eff_flag=0.80 → flag (not pass)
    assert data["decision"] == "flag"
    assert pytest.approx(data["threshold_applied"]["block"], abs=1e-9) == 0.97


# ---------------------------------------------------------------------------
# US-012 — Contact Registry: First Contact Detection (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_first_contact_is_first_contact_true(
    cfg: ClawStrikeConfig,
) -> None:
    """First classify call for a new source_id returns is_first_contact=True."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    assert result.structured_content["is_first_contact"] is True


@pytest.mark.asyncio
async def test_classify_known_contact_is_first_contact_false(
    cfg: ClawStrikeConfig,
) -> None:
    """Second classify call for the same source_id returns is_first_contact=False."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    assert result.structured_content["is_first_contact"] is False


@pytest.mark.asyncio
async def test_classify_first_contact_trust_level_is_untrusted(
    cfg: ClawStrikeConfig,
) -> None:
    """First contact trust_level is UNTRUSTED even for a high-trust channel."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "classify",
        {"text": "hi", "source_id": "new-owner", "channel_type": "owner_dm"},
    )
    # owner_dm is normally HIGH, but first contact overrides to UNTRUSTED.
    assert result.structured_content["trust_level"] == "untrusted"


@pytest.mark.asyncio
async def test_classify_known_contact_uses_channel_trust_level(
    cfg: ClawStrikeConfig,
) -> None:
    """Second call for the same source_id resolves trust from channel defaults."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    # Seed: register contact.
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    # Second call: email_body → LOW (no first-contact override).
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    assert result.structured_content["trust_level"] == "low"


@pytest.mark.asyncio
async def test_classify_first_contact_uses_untrusted_thresholds(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """First contact applies UNTRUSTED thresholds (eff_block=0.82).

    Score 0.83 would be below email_body LOW block threshold (0.87) but
    exceeds UNTRUSTED eff_block (0.82), so the decision is block.
    """
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.83, label="injection", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert data["decision"] == "block"
    assert pytest.approx(data["threshold_applied"]["block"], abs=1e-9) == 0.82


@pytest.mark.asyncio
async def test_classify_two_source_ids_each_first_contact(
    cfg: ClawStrikeConfig,
) -> None:
    """Two distinct source_ids each get their own independent first-contact event."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result_a = await srv.mcp.call_tool(
        "classify",
        {"text": "hi", "source_id": "a@example.com", "channel_type": "email_body"},
    )
    result_b = await srv.mcp.call_tool(
        "classify",
        {"text": "hi", "source_id": "b@example.com", "channel_type": "email_body"},
    )
    assert result_a.structured_content["is_first_contact"] is True
    assert result_b.structured_content["is_first_contact"] is True
