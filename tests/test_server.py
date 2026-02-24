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


# ---------------------------------------------------------------------------
# US-013 — Interaction Tracking & Auto-Promotion (integration)
# ---------------------------------------------------------------------------


async def _get_contact_from_db(db_path: str, source_id: str):
    """Helper: fetch a ContactRecord directly from the SQLite DB."""
    from clawstrike.db import get_or_create_contact, open_db

    async with open_db(db_path) as conn:
        record, _ = await get_or_create_contact(conn, source_id, "email_body")
    return record


async def _get_audit_events(db_path: str, *, event_type: str | None = None):
    """Helper: fetch all audit events (optionally filtered by event_type)."""
    from clawstrike.db import open_db

    async with open_db(db_path) as conn:
        if event_type:
            async with conn.execute(
                "SELECT * FROM audit_events WHERE event_type = ?", (event_type,)
            ) as cur:
                return await cur.fetchall()
        async with conn.execute("SELECT * FROM audit_events") as cur:
            return await cur.fetchall()


@pytest.mark.asyncio
async def test_classify_increments_interaction_on_pass(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Each non-blocked (pass) call for a known contact increments interaction_count."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    # First call: creates contact, count=1.
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    # Second call: known contact, pass → count becomes 2.
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await _get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.interaction_count == 2


@pytest.mark.asyncio
async def test_classify_increments_interaction_on_flag(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Flag decisions (non-blocked) also increment interaction_count."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    # First call (first-contact, no increment on creation beyond initial 1).
    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    # Second call with flag score: should still increment.
    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await _get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.interaction_count == 2


@pytest.mark.asyncio
async def test_classify_no_increment_on_block(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Block decisions do NOT increment interaction_count."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    # First call: creates contact, count=1.
    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    # Second call: block decision — count must stay at 1.
    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_BLOCK, label="injection", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await _get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.interaction_count == 1


@pytest.mark.asyncio
async def test_classify_auto_promote_after_threshold(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """After auto_promote_after (5) non-blocked interactions, trust_level is promoted.

    email_body defaults to LOW trust. After 5 safe interactions, the stored
    trust_level should change from 'auto' to 'low'.
    """
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    # auto_promote_after default is 5. First call creates with count=1.
    # 4 more non-blocked calls → count reaches 5 → promote on the 5th call.
    for _ in range(5):
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await _get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    # email_body channel default is LOW → promoted trust_level = 'low'
    assert record.trust_level == "low"


@pytest.mark.asyncio
async def test_classify_auto_promote_writes_trust_update_audit_event(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Auto-promotion writes a trust_update audit event with the correct fields."""
    import json

    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    for _ in range(5):
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    assert len(events) == 1
    ev = events[0]
    assert ev["source_id"] == "user@example.com"
    assert ev["channel_type"] == "email_body"
    details = json.loads(ev["details_json"])
    assert details["previous_trust"] == "auto"
    assert details["reason"] == "auto_promote"
    assert details["interaction_count"] == 5


@pytest.mark.asyncio
async def test_classify_no_auto_promote_before_threshold(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """No promotion occurs before interaction_count reaches auto_promote_after."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    # 4 calls → count=4 (first creates count=1, then 3 increments → 4).
    # With auto_promote_after=5, should NOT promote.
    for _ in range(4):
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await _get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.trust_level == "auto"
    events = await _get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    assert len(events) == 0


@pytest.mark.asyncio
async def test_classify_no_auto_promote_if_manual_trusted(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Contacts with trust_level='trusted' (manual override) are never auto-promoted."""
    import clawstrike.mcpserver as srv
    from clawstrike.db import open_db, set_contact_trust_level

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    # Register contact and manually set trust_level to 'trusted'.
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    async with open_db(str(cfg.audit.db_path)) as conn:
        await set_contact_trust_level(conn, "user@example.com", "trusted")

    # Make 4 more calls to exceed auto_promote_after threshold.
    for _ in range(4):
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await _get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    # trust_level should remain 'trusted' (not overwritten by auto-promotion).
    assert record.trust_level == "trusted"
    events = await _get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    assert len(events) == 0


@pytest.mark.asyncio
async def test_classify_no_auto_promote_if_manual_blocked(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Contacts with trust_level='blocked' (manual override) are never auto-promoted."""
    import clawstrike.mcpserver as srv
    from clawstrike.db import open_db, set_contact_trust_level

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    # Register contact and manually set trust_level to 'blocked'.
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    async with open_db(str(cfg.audit.db_path)) as conn:
        await set_contact_trust_level(conn, "user@example.com", "blocked")

    # Make 4 more calls to exceed auto_promote_after threshold.
    for _ in range(4):
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await _get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.trust_level == "blocked"
    events = await _get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    assert len(events) == 0


@pytest.mark.asyncio
async def test_classify_auto_promote_only_once(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """After promotion, further calls don't produce additional trust_update events."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    # 5 calls to trigger promotion, then 3 more.
    for _ in range(8):
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    # Exactly one trust_update event (from the 5th call).
    assert len(events) == 1


# ---------------------------------------------------------------------------
# US-017 — Advisory Action Classification via API (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_risk_level_from_taxonomy(cfg: ClawStrikeConfig) -> None:
    """shell_exec maps to 'critical' via the hardcoded taxonomy."""
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
    assert result.structured_content["risk_level"] == "critical"


@pytest.mark.asyncio
async def test_gate_unknown_action_type_defaults_to_high(
    cfg: ClawStrikeConfig,
) -> None:
    """Unrecognised action_type defaults to 'high' risk (fail-safe)."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Do something unusual",
            "action_type": "completely_unknown_action",
            "session_id": "s",
            "source_id": "x",
            "channel_type": "owner_dm",
        },
    )
    assert result.structured_content["risk_level"] == "high"


@pytest.mark.asyncio
async def test_gate_reason_is_taxonomy_match_for_known_type(
    cfg: ClawStrikeConfig,
) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Read a calendar entry",
            "action_type": "calendar_read",
            "session_id": "s",
            "source_id": "x",
            "channel_type": "owner_dm",
        },
    )
    assert result.structured_content["reason"] == "taxonomy_match"


@pytest.mark.asyncio
async def test_gate_reason_explains_unknown_default(cfg: ClawStrikeConfig) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Mystery action",
            "action_type": "mystery",
            "session_id": "s",
            "source_id": "x",
            "channel_type": "owner_dm",
        },
    )
    assert (
        result.structured_content["reason"] == "unknown_action_type_defaulted_to_high"
    )


# ---------------------------------------------------------------------------
# US-018 — Gating Recommendation Matrix (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_critical_high_trust_recommends_prompt_user(
    cfg: ClawStrikeConfig,
) -> None:
    """Critical action from HIGH trust source → prompt_user."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Execute shell command",
            "action_type": "shell_exec",
            "session_id": "s",
            "source_id": "owner",
            "channel_type": "owner_dm",  # HIGH trust
        },
    )
    data = result.structured_content
    assert data["risk_level"] == "critical"
    assert data["trust_level"] == "high"
    assert data["recommendation"] == "prompt_user"


@pytest.mark.asyncio
async def test_gate_critical_low_trust_recommends_block(cfg: ClawStrikeConfig) -> None:
    """Critical action from LOW trust source → block."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Execute shell command",
            "action_type": "shell_exec",
            "session_id": "s",
            "source_id": "attacker",
            "channel_type": "email_body",  # LOW trust
        },
    )
    data = result.structured_content
    assert data["risk_level"] == "critical"
    assert data["trust_level"] == "low"
    assert data["recommendation"] == "block"


@pytest.mark.asyncio
async def test_gate_critical_untrusted_recommends_block(cfg: ClawStrikeConfig) -> None:
    """Critical action from UNTRUSTED source → block."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Execute shell command",
            "action_type": "shell_exec",
            "session_id": "s",
            "source_id": "anon",
            "channel_type": "webhook",  # UNTRUSTED
        },
    )
    assert result.structured_content["recommendation"] == "block"


@pytest.mark.asyncio
async def test_gate_high_risk_high_trust_recommends_allow(
    cfg: ClawStrikeConfig,
) -> None:
    """High risk action from HIGH trust source → allow."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Send an email",
            "action_type": "send_email",
            "session_id": "s",
            "source_id": "owner",
            "channel_type": "owner_dm",  # HIGH trust
        },
    )
    data = result.structured_content
    assert data["risk_level"] == "high"
    assert data["trust_level"] == "high"
    assert data["recommendation"] == "allow"


@pytest.mark.asyncio
async def test_gate_high_risk_medium_trust_recommends_prompt_user(
    cfg: ClawStrikeConfig,
) -> None:
    """High risk action from MEDIUM trust source → prompt_user."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Send a message",
            "action_type": "send_message",
            "session_id": "s",
            "source_id": "team",
            "channel_type": "trusted_group",  # MEDIUM trust
        },
    )
    data = result.structured_content
    assert data["risk_level"] == "high"
    assert data["trust_level"] == "medium"
    assert data["recommendation"] == "prompt_user"


@pytest.mark.asyncio
async def test_gate_high_risk_low_trust_recommends_block(
    cfg: ClawStrikeConfig,
) -> None:
    """High risk action from LOW trust source → block."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Send an email",
            "action_type": "send_email",
            "session_id": "s",
            "source_id": "newsletter",
            "channel_type": "email_body",  # LOW trust
        },
    )
    assert result.structured_content["recommendation"] == "block"


@pytest.mark.asyncio
async def test_gate_medium_risk_low_trust_recommends_prompt_user(
    cfg: ClawStrikeConfig,
) -> None:
    """Medium risk action from LOW trust source → prompt_user."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Browse a URL",
            "action_type": "web_browse",
            "session_id": "s",
            "source_id": "external",
            "channel_type": "email_body",  # LOW trust
        },
    )
    data = result.structured_content
    assert data["risk_level"] == "medium"
    assert data["recommendation"] == "prompt_user"


@pytest.mark.asyncio
async def test_gate_medium_risk_untrusted_recommends_block(
    cfg: ClawStrikeConfig,
) -> None:
    """Medium risk action from UNTRUSTED source → block."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Read sensitive file",
            "action_type": "file_read_sensitive",
            "session_id": "s",
            "source_id": "bot",
            "channel_type": "webhook",  # UNTRUSTED
        },
    )
    assert result.structured_content["recommendation"] == "block"


@pytest.mark.asyncio
async def test_gate_low_risk_low_trust_recommends_allow(
    cfg: ClawStrikeConfig,
) -> None:
    """Low risk action from LOW trust source → allow."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Read calendar",
            "action_type": "calendar_read",
            "session_id": "s",
            "source_id": "user",
            "channel_type": "email_body",  # LOW trust
        },
    )
    data = result.structured_content
    assert data["risk_level"] == "low"
    assert data["recommendation"] == "allow"


@pytest.mark.asyncio
async def test_gate_low_risk_untrusted_recommends_prompt_user(
    cfg: ClawStrikeConfig,
) -> None:
    """Low risk action from UNTRUSTED source → prompt_user."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "List files",
            "action_type": "list_directory",
            "session_id": "s",
            "source_id": "unknown",
            "channel_type": "webhook",  # UNTRUSTED
        },
    )
    assert result.structured_content["recommendation"] == "prompt_user"


@pytest.mark.asyncio
async def test_gate_audit_event_written(cfg: ClawStrikeConfig) -> None:
    """gate tool writes an action_gate audit event with the gating decision."""
    import json

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

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="action_gate")
    assert len(events) == 1
    ev = events[0]
    assert ev["source_id"] == "attacker@evil.com"
    assert ev["channel_type"] == "email_body"
    assert ev["session_id"] == "audit-test-session"
    assert ev["decision"] == "block"
    assert ev["trust_level"] == "low"
    details = json.loads(ev["details_json"])
    assert details["action_type"] == "shell_exec"
    assert details["risk_level"] == "critical"
    assert details["recommendation"] == "block"
