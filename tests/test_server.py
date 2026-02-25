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
    srv._mismatch_sessions.clear()
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


# ---------------------------------------------------------------------------
# US-024 — Classify audit events: full field coverage (US-008/009/010 ACs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_audit_event_has_label_model_and_thresholds(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """classify audit event includes label, model, and threshold_applied in details."""
    import json

    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="classify")
    assert len(events) == 1
    ev = events[0]
    assert ev["label"] == "benign"
    assert ev["score"] == _SCORE_PASS
    details = json.loads(ev["details_json"])
    assert details["model"] == "mock-model"
    assert "threshold_applied" in details
    assert "block" in details["threshold_applied"]
    assert "flag" in details["threshold_applied"]


@pytest.mark.asyncio
async def test_classify_pass_audit_event_written(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """US-010 AC: pass classify writes an audit event with decision='pass'."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="classify")
    assert len(events) == 1
    assert events[0]["decision"] == "pass"


@pytest.mark.asyncio
async def test_classify_block_audit_event_written_with_hash(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """US-008 AC: block classify audit event includes raw_input_hash."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_BLOCK, label="injection", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool(
        "classify",
        {**_CLASSIFY_ARGS, "text": "inject me"},
    )

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="classify")
    assert len(events) == 1
    ev = events[0]
    assert ev["decision"] == "block"
    assert ev["label"] == "injection"
    assert ev["raw_input_hash"] is not None


@pytest.mark.asyncio
async def test_classify_flag_audit_event_has_elevated_scrutiny_in_details(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """US-009 AC: flag classify audit event includes elevated_scrutiny=True in details."""
    import json

    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="classify")
    assert len(events) == 1
    details = json.loads(events[0]["details_json"])
    assert details["elevated_scrutiny"] is True


@pytest.mark.asyncio
async def test_classify_pass_audit_event_has_no_elevated_scrutiny(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """pass classify audit event has elevated_scrutiny=False in details."""
    import json

    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="classify")
    details = json.loads(events[0]["details_json"])
    assert details["elevated_scrutiny"] is False


@pytest.mark.asyncio
async def test_classify_audit_stores_raw_input_snippet_when_log_raw_input_true(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """US-024 AC6: raw_input_snippet stored when log_raw_input=True (default)."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    text = "hello world"
    await srv.mcp.call_tool(
        "classify",
        {"text": text, "source_id": "s", "channel_type": "email_body"},
    )

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="classify")
    assert events[0]["raw_input_snippet"] == text


@pytest.mark.asyncio
async def test_classify_audit_stores_only_hash_when_log_raw_input_false(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """US-024 AC6: only SHA-256 hash stored when log_raw_input=False."""
    import hashlib

    import clawstrike.mcpserver as srv
    from clawstrike.config import load_config

    data = minimal_config(
        {
            "audit": {
                "db_path": str(tmp_path / "no-raw.db"),
                "log_raw_input": False,
            }
        }
    )
    cfg_no_raw = load_config(write_yaml(tmp_path, data))

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg_no_raw)
    text = "secret text"
    await srv.mcp.call_tool(
        "classify",
        {"text": text, "source_id": "s", "channel_type": "email_body"},
    )

    events = await _get_audit_events(
        str(cfg_no_raw.audit.db_path), event_type="classify"
    )
    ev = events[0]
    assert ev["raw_input_snippet"] is None
    expected_hash = hashlib.sha256(text.encode()).hexdigest()
    assert ev["raw_input_hash"] == expected_hash


@pytest.mark.asyncio
async def test_classify_audit_snippet_truncated_to_max_chars(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """raw_input_snippet is truncated to raw_input_max_chars."""
    import clawstrike.mcpserver as srv
    from clawstrike.config import load_config

    data = minimal_config(
        {
            "audit": {
                "db_path": str(tmp_path / "trunc.db"),
                "log_raw_input": True,
                "raw_input_max_chars": 10,
            }
        }
    )
    cfg_trunc = load_config(write_yaml(tmp_path, data))

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg_trunc)
    long_text = "a" * 50
    await srv.mcp.call_tool(
        "classify",
        {"text": long_text, "source_id": "s", "channel_type": "email_body"},
    )

    events = await _get_audit_events(
        str(cfg_trunc.audit.db_path), event_type="classify"
    )
    assert events[0]["raw_input_snippet"] == "a" * 10


# ---------------------------------------------------------------------------
# US-022 — Elevated Scrutiny Tightens Gating Recommendations
# ---------------------------------------------------------------------------

# Default config has owner_dm → HIGH trust (see default channel_defaults).
# medium trust channel: email_body → LOW. Use a channel that maps to MEDIUM.
# Check config defaults: the test cfg uses minimal_config which has no explicit
# channel_defaults, so defaults apply. Let's use a channel that resolves to
# a known trust level. Looking at the default config...
# We'll inject a custom trust config to control the scenario precisely.


def _make_cfg_with_trust(tmp_path: Path, channel: str, trust: str) -> ClawStrikeConfig:
    """Return a config that maps *channel* to *trust* trust level."""
    from clawstrike.config import load_config

    data = minimal_config(
        {
            "audit": {"db_path": str(tmp_path / "us022.db")},
            "trust": {
                "channel_defaults": {channel: trust},
            },
        }
    )
    return load_config(write_yaml(tmp_path, data))


@pytest.mark.asyncio
async def test_gate_no_elevated_scrutiny_uses_original_trust(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Without elevated scrutiny, trust_level == effective_trust_level."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "medium")
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
    # send_email is HIGH risk + MEDIUM trust → prompt_user (from decision matrix)
    assert data["recommendation"] == "prompt_user"


@pytest.mark.asyncio
async def test_gate_elevated_scrutiny_downgrades_trust_by_one_tier(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Elevated scrutiny downgrades MEDIUM → LOW, changing recommendation."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "medium")
    srv.init_server(cfg)

    # Manually inject the session into elevated_sessions (simulates prior classify flag).
    srv._elevated_sessions.add("elev-session-1")

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send an email",
            "action_type": "send_email",
            "session_id": "elev-session-1",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["elevated_scrutiny"] is True
    assert data["trust_level"] == "medium"  # original
    assert data["effective_trust_level"] == "low"  # downgraded by one tier
    # send_email is HIGH risk + LOW trust → block (stricter than prompt_user)
    assert data["recommendation"] == "block"


@pytest.mark.asyncio
async def test_gate_elevated_scrutiny_high_trust_downgrades_to_medium(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """HIGH trust session with elevated scrutiny → effective trust is MEDIUM."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    srv._elevated_sessions.add("high-elev-session")

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send an email",
            "action_type": "send_email",
            "session_id": "high-elev-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["trust_level"] == "high"
    assert data["effective_trust_level"] == "medium"
    # send_email is HIGH risk + MEDIUM trust → prompt_user (was allow at high)
    assert data["recommendation"] == "prompt_user"


@pytest.mark.asyncio
async def test_gate_elevated_scrutiny_untrusted_stays_untrusted(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """UNTRUSTED trust cannot be downgraded further; stays UNTRUSTED."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "untrusted")
    srv.init_server(cfg)

    srv._elevated_sessions.add("untrusted-elev-session")

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "read calendar",
            "action_type": "calendar_read",
            "session_id": "untrusted-elev-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["trust_level"] == "untrusted"
    assert data["effective_trust_level"] == "untrusted"


@pytest.mark.asyncio
async def test_gate_elevated_scrutiny_audit_records_both_trust_tiers(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Audit log includes original_trust_level and elevated_scrutiny in details."""
    import json

    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "medium")
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

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="action_gate")
    assert len(events) == 1
    ev = events[0]
    # effective trust (post-downgrade) is stored as the top-level trust_level column
    assert ev["trust_level"] == "low"
    details = json.loads(ev["details_json"])
    assert details["original_trust_level"] == "medium"
    assert details["elevated_scrutiny"] is True


@pytest.mark.asyncio
async def test_gate_elevated_scrutiny_only_for_tagged_session(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Elevation only applies to the tagged session_id, not others."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    srv._elevated_sessions.add("flagged-session")

    # Non-elevated session: trust stays HIGH, send_email HIGH+HIGH → allow
    result_normal = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "clean-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data_normal = result_normal.structured_content
    assert data_normal["elevated_scrutiny"] is False
    assert data_normal["effective_trust_level"] == "high"
    assert data_normal["recommendation"] == "allow"

    # Elevated session: trust drops to MEDIUM, send_email HIGH+MEDIUM → prompt_user
    result_elev = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "flagged-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data_elev = result_elev.structured_content
    assert data_elev["elevated_scrutiny"] is True
    assert data_elev["effective_trust_level"] == "medium"
    assert data_elev["recommendation"] == "prompt_user"


@pytest.mark.asyncio
async def test_gate_elevated_via_classify_flag_end_to_end(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Full flow: classify sets elevated_scrutiny, gate uses downgraded trust."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    # Trigger a flag decision via classify (score between flag and block thresholds).
    # Default thresholds: flag=0.70, block=0.92. Use score=0.80.
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.80, label="injection", model="mock-model", latency_ms=2.0
    )
    classify_result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious input",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
            "session_id": "e2e-session",
        },
    )
    assert classify_result.structured_content["decision"] == "flag"
    assert "e2e-session" in srv._elevated_sessions

    # Now gate should see downgraded trust: HIGH → MEDIUM.
    gate_result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "e2e-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    gate_data = gate_result.structured_content
    assert gate_data["trust_level"] == "high"
    assert gate_data["effective_trust_level"] == "medium"
    assert gate_data["elevated_scrutiny"] is True
    # send_email HIGH risk + MEDIUM trust → prompt_user (stricter than allow at HIGH)
    assert gate_data["recommendation"] == "prompt_user"


# ---------------------------------------------------------------------------
# US-016 — Content-Source Mismatch Detection
# ---------------------------------------------------------------------------
#
# Mismatch fires when: trust_level in (HIGH, MEDIUM) AND score >= base_flag.
# Default config: base_flag=0.70, base_block=0.92.
# HIGH trust effective thresholds: flag=0.80, block=0.97
# MEDIUM trust effective thresholds: flag=0.70, block=0.92


@pytest.mark.asyncio
async def test_classify_mismatch_detected_high_trust_pass_decision(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """HIGH trust contact + score in (base_flag, eff_flag) → pass decision + mismatch.

    This is the key scenario: HIGH trust lowers the effective flag (raises it
    to 0.80), so the message passes classification.  But it still exceeds the
    *base* flag threshold (0.70), triggering content_source_mismatch detection.
    """
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    # Seed: register the contact as known.
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "hello",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
        },
    )

    # Now classify with score in (0.70, 0.80) — above base_flag, below eff_flag for HIGH.
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious content",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    # Score 0.75 < eff_flag 0.80 → pass (HIGH trust leniency)
    assert data["decision"] == "pass"
    # But score 0.75 ≥ base_flag 0.70 → mismatch
    assert data["content_source_mismatch"] is True
    assert data["trust_level"] == "high"


@pytest.mark.asyncio
async def test_classify_mismatch_detected_medium_trust(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """MEDIUM trust contact + score >= base_flag → mismatch detected."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "medium")
    srv.init_server(cfg)

    # Seed: register the contact.
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "hello",
            "source_id": "medium-user",
            "channel_type": "test_chan",
        },
    )

    # Score 0.75 ≥ effective_flag 0.70 (MEDIUM modifier=0) → flag decision + mismatch.
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious content",
            "source_id": "medium-user",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["decision"] == "flag"
    assert data["content_source_mismatch"] is True
    assert data["trust_level"] == "medium"


@pytest.mark.asyncio
async def test_classify_no_mismatch_score_below_base_flag(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Score below base_flag → no mismatch even for HIGH trust contacts."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    # Seed contact.
    await srv.mcp.call_tool(
        "classify",
        {"text": "hi", "source_id": "user", "channel_type": "test_chan"},
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.50, label="benign", model="mock-model", latency_ms=1.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {"text": "normal text", "source_id": "user", "channel_type": "test_chan"},
    )
    assert result.structured_content["content_source_mismatch"] is False


@pytest.mark.asyncio
async def test_classify_no_mismatch_low_trust_above_base_flag(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """LOW trust contacts above base_flag → no mismatch (only HIGH/MEDIUM trigger it)."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "low")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {"text": "hi", "source_id": "low-user", "channel_type": "test_chan"},
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious",
            "source_id": "low-user",
            "channel_type": "test_chan",
        },
    )
    assert result.structured_content["content_source_mismatch"] is False


@pytest.mark.asyncio
async def test_classify_no_mismatch_first_contact(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """First-contact forces UNTRUSTED trust → no mismatch even if HIGH channel."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.80, label="injection", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious",
            "source_id": "brand-new-user",
            "channel_type": "test_chan",
            "session_id": "new-session",
        },
    )
    data = result.structured_content
    # First contact → UNTRUSTED → not in (HIGH, MEDIUM) → no mismatch.
    assert data["is_first_contact"] is True
    assert data["content_source_mismatch"] is False
    assert "new-session" not in srv._mismatch_sessions


@pytest.mark.asyncio
async def test_classify_mismatch_tags_session(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Mismatch with session_id adds session to _mismatch_sessions."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {"text": "seed", "source_id": "user", "channel_type": "test_chan"},
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious",
            "source_id": "user",
            "channel_type": "test_chan",
            "session_id": "mismatch-session",
        },
    )
    assert "mismatch-session" in srv._mismatch_sessions


@pytest.mark.asyncio
async def test_classify_mismatch_no_session_id_no_tag(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Mismatch without session_id: response flag set, but no session tagged."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {"text": "seed", "source_id": "user", "channel_type": "test_chan"},
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {"text": "suspicious", "source_id": "user", "channel_type": "test_chan"},
    )
    # Mismatch is detected and returned in response.
    assert result.structured_content["content_source_mismatch"] is True
    # But no session was provided to tag.
    assert len(srv._mismatch_sessions) == 0


@pytest.mark.asyncio
async def test_classify_mismatch_audit_event_written(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Mismatch writes a trust_update audit event with reason=content_source_mismatch."""
    import json

    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "seed",
            "source_id": "audit-user",
            "channel_type": "test_chan",
            "session_id": "audit-sess",
        },
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious",
            "source_id": "audit-user",
            "channel_type": "test_chan",
            "session_id": "audit-sess",
        },
    )

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    mismatch_events = [
        e
        for e in events
        if json.loads(e["details_json"]).get("reason") == "content_source_mismatch"
    ]
    assert len(mismatch_events) == 1
    ev = mismatch_events[0]
    details = json.loads(ev["details_json"])
    assert ev["trust_level"] == "low"
    assert details["previous_trust"] == "high"
    assert details["new_trust"] == "low"
    assert details["reason"] == "content_source_mismatch"
    assert details["score"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_gate_mismatch_session_forces_effective_trust_to_low(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Injecting a session into _mismatch_sessions → gate effective_trust=low."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    srv._mismatch_sessions.add("mismatch-gate-session")

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send an email",
            "action_type": "send_email",
            "session_id": "mismatch-gate-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["content_source_mismatch"] is True
    assert data["trust_level"] == "high"  # original channel-resolved
    assert data["effective_trust_level"] == "low"  # forced to LOW by mismatch
    # send_email HIGH risk + LOW trust → block
    assert data["recommendation"] == "block"


@pytest.mark.asyncio
async def test_gate_mismatch_medium_trust_forces_to_low(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """MEDIUM trust session with mismatch → effective_trust=low (same single-tier result)."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "medium")
    srv.init_server(cfg)

    srv._mismatch_sessions.add("med-mismatch-session")

    result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send an email",
            "action_type": "send_email",
            "session_id": "med-mismatch-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    data = result.structured_content
    assert data["content_source_mismatch"] is True
    assert data["trust_level"] == "medium"
    assert data["effective_trust_level"] == "low"
    assert data["recommendation"] == "block"


@pytest.mark.asyncio
async def test_gate_mismatch_stacks_with_elevated_scrutiny(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Both mismatch (→ LOW) and elevated_scrutiny (→ one tier down) → UNTRUSTED."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    # Both flags set for the same session.
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
    assert data["trust_level"] == "high"  # original
    # mismatch → LOW, then elevated_scrutiny downgrade_trust(LOW) → UNTRUSTED
    assert data["effective_trust_level"] == "untrusted"
    # calendar_read LOW risk + UNTRUSTED trust → prompt_user
    assert data["recommendation"] == "prompt_user"


@pytest.mark.asyncio
async def test_gate_mismatch_only_for_tagged_session(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Mismatch only affects the tagged session; other sessions are unaffected."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    srv._mismatch_sessions.add("mismatch-session")

    # Clean session: HIGH trust → allow for send_email HIGH risk? No: HIGH+HIGH=allow.
    result_clean = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "clean-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    d_clean = result_clean.structured_content
    assert d_clean["content_source_mismatch"] is False
    assert d_clean["effective_trust_level"] == "high"
    assert d_clean["recommendation"] == "allow"

    # Mismatch session: forced to LOW → send_email HIGH+LOW → block.
    result_mismatch = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "mismatch-session",
            "source_id": "user@example.com",
            "channel_type": "test_chan",
        },
    )
    d_mismatch = result_mismatch.structured_content
    assert d_mismatch["content_source_mismatch"] is True
    assert d_mismatch["effective_trust_level"] == "low"
    assert d_mismatch["recommendation"] == "block"


@pytest.mark.asyncio
async def test_gate_mismatch_audit_records_content_source_mismatch(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Gate audit event details include content_source_mismatch=True."""
    import json

    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
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

    events = await _get_audit_events(str(cfg.audit.db_path), event_type="action_gate")
    assert len(events) == 1
    details = json.loads(events[0]["details_json"])
    assert details["content_source_mismatch"] is True
    assert details["original_trust_level"] == "high"
    # effective trust is LOW (mismatch), stored as top-level trust_level
    assert events[0]["trust_level"] == "low"


@pytest.mark.asyncio
async def test_classify_mismatch_e2e_gate_uses_low_trust(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Full flow: classify detects mismatch → gate uses LOW effective trust."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    # Seed: register contact so second call is not first-contact.
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "normal message",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
        },
    )

    # Classify with mismatch-triggering score: ≥ base_flag (0.70), < eff_flag (0.80).
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    classify_result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious injection attempt",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
            "session_id": "e2e-mismatch-session",
        },
    )
    c_data = classify_result.structured_content
    assert c_data["decision"] == "pass"  # passes effective threshold
    assert c_data["content_source_mismatch"] is True
    assert "e2e-mismatch-session" in srv._mismatch_sessions

    # Gate call: effective trust should be LOW due to mismatch.
    gate_result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "e2e-mismatch-session",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
        },
    )
    g_data = gate_result.structured_content
    assert g_data["trust_level"] == "high"
    assert g_data["effective_trust_level"] == "low"
    assert g_data["content_source_mismatch"] is True
    # send_email HIGH risk + LOW trust → block
    assert g_data["recommendation"] == "block"


@pytest.mark.asyncio
async def test_init_server_resets_mismatch_sessions(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """init_server() clears _mismatch_sessions to ensure clean state on restart."""
    import clawstrike.mcpserver as srv

    cfg = _make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    # Manually add a stale session.
    srv._mismatch_sessions.add("stale-session")
    assert "stale-session" in srv._mismatch_sessions

    # Re-init should clear it.
    srv.init_server(cfg)
    assert len(srv._mismatch_sessions) == 0
