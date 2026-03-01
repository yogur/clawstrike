"""Tests for the classify MCP tool.

Covers classify basic functionality, block/flag/pass decision pipeline, trust
resolution and threshold modulation, first contact detection, interaction
tracking and auto-promotion, classify audit events, and content-source
mismatch detection.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clawstrike.classifier import ClassifierResult
from clawstrike.config import ClawStrikeConfig, load_config

from .helpers import (
    get_audit_events,
    get_contact_from_db,
    make_cfg_with_trust,
    minimal_config,
    write_yaml,
)

# ---------------------------------------------------------------------------
# Shared classify args and score constants
# ---------------------------------------------------------------------------

# email_body is LOW trust. On first contact the trust is forced to UNTRUSTED:
#   eff_block = 0.92 - 0.10 = 0.82,  eff_flag = 0.70 - 0.20 = 0.50
# Scores are chosen safely away from all boundaries.
_SCORE_BLOCK = 0.95  # > any possible eff_block
_SCORE_FLAG = 0.80  # >= eff_flag but < eff_block for all trust tiers
_SCORE_PASS = 0.30  # < eff_flag for all trust tiers

_CLASSIFY_ARGS = {
    "text": "test input",
    "source_id": "user@example.com",
    "channel_type": "email_body",
    "session_id": "test-session",
}


# ---------------------------------------------------------------------------
# Classify tool basic response shape
# ---------------------------------------------------------------------------


async def test_classify_returns_expected_fields(cfg: ClawStrikeConfig) -> None:
    """classify returns all required fields and echoes source metadata."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "some text",
            "source_id": "discord:12345",
            "channel_type": "public_group",
            "session_id": "test-session",
        },
    )
    data = result.structured_content
    assert "decision" in data
    assert data["decision"] in ("pass", "flag", "block")
    assert isinstance(data["score"], float)
    assert data["label"] in ("benign", "injection", "jailbreak")
    assert "model" in data
    assert "latency_ms" in data
    assert data["source_id"] == "discord:12345"
    assert data["channel_type"] == "public_group"


# ---------------------------------------------------------------------------
# Block / flag / pass decision pipeline
# ---------------------------------------------------------------------------


async def test_classify_block_decision(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Score >= eff_block → decision=block with reason and standard classifier fields."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_BLOCK, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert data["decision"] == "block"
    assert data["reason"] == "prompt_injection_detected"
    assert data["score"] == _SCORE_BLOCK
    assert data["label"] == "injection"
    assert data["model"] == "mock-model"
    assert data["latency_ms"] > 0


async def test_classify_flag_decision(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Score in [eff_flag, eff_block) → decision=flag with elevated_scrutiny, no reason."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert data["decision"] == "flag"
    assert data["elevated_scrutiny"] is True
    assert "reason" not in data


async def test_classify_pass_decision(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Score < eff_flag → decision=pass with no reason or elevated_scrutiny."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert data["decision"] == "pass"
    assert data["score"] == _SCORE_PASS
    assert "reason" not in data
    assert data.get("elevated_scrutiny") is not True


async def test_classify_session_tagged_on_flag(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Flag classify for session X → gate for X reports elevated_scrutiny=True."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", {**_CLASSIFY_ARGS, "session_id": "session-001"})
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


async def test_classify_no_session_tag_on_pass(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Pass classify with session_id does NOT tag the session as elevated."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", {**_CLASSIFY_ARGS, "session_id": "session-002"})
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


async def test_classify_elevated_scrutiny_scoped_to_session(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Flag classify for session X does not elevate an unrelated session Y."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=2.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)  # tags "test-session"
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
# Trust resolution and threshold modulation
# ---------------------------------------------------------------------------


async def test_classify_response_includes_trust_and_threshold_fields(
    cfg: ClawStrikeConfig,
) -> None:
    """classify always returns trust_level and threshold_applied (effective thresholds)."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    # First contact forces UNTRUSTED regardless of channel.
    assert data["trust_level"] == "untrusted"
    assert "threshold_applied" in data
    assert "block" in data["threshold_applied"]
    assert "flag" in data["threshold_applied"]


async def test_classify_unknown_channel_trust_level_is_untrusted(
    cfg: ClawStrikeConfig,
) -> None:
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "hello",
            "source_id": "u",
            "channel_type": "unknown_channel",
            "session_id": "test-session",
        },
    )
    assert result.structured_content["trust_level"] == "untrusted"


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
        {
            "text": "t",
            "source_id": "s",
            "channel_type": "webhook",
            "session_id": "test-session",
        },
    )
    data = result.structured_content
    # webhook → UNTRUSTED, modifier block=-0.10 → eff_block=0.82 < 0.83 → block
    assert data["decision"] == "block"
    assert pytest.approx(data["threshold_applied"]["block"], abs=1e-9) == 0.82


async def test_classify_high_trust_raises_block_threshold(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Score 0.93 blocks at base (0.92) but only flags at owner_dm HIGH trust (eff_block=0.97).

    Pre-condition: seed contact so first-contact UNTRUSTED override does not apply.
    """
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)

    # Seed: register 'owner' as a known contact.
    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "hello",
            "source_id": "owner",
            "channel_type": "owner_dm",
            "session_id": "seed-session",
        },
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.93, label="benign", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "t",
            "source_id": "owner",
            "channel_type": "owner_dm",
            "session_id": "test-session",
        },
    )
    data = result.structured_content
    # owner_dm → HIGH, eff_block=0.97 > 0.93 → not blocked; 0.93 >= eff_flag=0.80 → flag
    assert data["decision"] == "flag"
    assert pytest.approx(data["threshold_applied"]["block"], abs=1e-9) == 0.97


# ---------------------------------------------------------------------------
# Contact Registry: First Contact Detection
# ---------------------------------------------------------------------------


async def test_classify_first_contact_is_first_contact_true(
    cfg: ClawStrikeConfig,
) -> None:
    """First classify call for a new source_id returns is_first_contact=True."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    assert result.structured_content["is_first_contact"] is True


async def test_classify_known_contact_is_first_contact_false(
    cfg: ClawStrikeConfig,
) -> None:
    """Second classify call for the same source_id returns is_first_contact=False."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    assert result.structured_content["is_first_contact"] is False


async def test_classify_first_contact_trust_level_is_untrusted(
    cfg: ClawStrikeConfig,
) -> None:
    """First contact trust_level is UNTRUSTED even for a high-trust channel."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "hi",
            "source_id": "new-owner",
            "channel_type": "owner_dm",
            "session_id": "test-session",
        },
    )
    assert result.structured_content["trust_level"] == "untrusted"


async def test_classify_known_contact_uses_channel_trust_level(
    cfg: ClawStrikeConfig,
) -> None:
    """Second call for the same source_id resolves trust from channel defaults."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    # email_body → LOW (no first-contact override on second call)
    assert result.structured_content["trust_level"] == "low"


async def test_classify_first_contact_uses_untrusted_thresholds(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Score 0.83 > UNTRUSTED eff_block (0.82) → block, even though email_body LOW eff_block is 0.87."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.83, label="injection", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    result = await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    data = result.structured_content
    assert data["decision"] == "block"
    assert pytest.approx(data["threshold_applied"]["block"], abs=1e-9) == 0.82


async def test_classify_two_source_ids_each_first_contact(
    cfg: ClawStrikeConfig,
) -> None:
    """Two distinct source_ids each get their own independent first-contact event."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    r_a = await srv.mcp.call_tool(
        "classify",
        {
            "text": "hi",
            "source_id": "a@example.com",
            "channel_type": "email_body",
            "session_id": "session-a",
        },
    )
    r_b = await srv.mcp.call_tool(
        "classify",
        {
            "text": "hi",
            "source_id": "b@example.com",
            "channel_type": "email_body",
            "session_id": "session-b",
        },
    )
    assert r_a.structured_content["is_first_contact"] is True
    assert r_b.structured_content["is_first_contact"] is True


# ---------------------------------------------------------------------------
# Interaction Tracking & Auto-Promotion
# ---------------------------------------------------------------------------


async def test_classify_increments_interaction_on_pass(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Each non-blocked (pass) call for a known contact increments interaction_count."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)  # creates contact, count=1
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)  # known contact → count=2

    record = await get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.interaction_count == 2


async def test_classify_increments_interaction_on_flag(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Flag decisions (non-blocked) also increment interaction_count."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.interaction_count == 2


async def test_classify_no_increment_on_block(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Block decisions do NOT increment interaction_count."""
    import clawstrike.mcpserver as srv

    srv.init_server(cfg)
    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)  # creates contact, count=1

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_BLOCK, label="injection", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)  # block → count stays 1

    record = await get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.interaction_count == 1


async def test_classify_auto_promote_after_threshold(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """After auto_promote_after (5) non-blocked interactions, trust_level is promoted
    and a trust_update audit event is written with the expected fields.
    """
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    for _ in range(5):
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    # DB record: email_body channel default is LOW → promoted trust_level = 'low'
    record = await get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.trust_level == "low"

    # Audit event check
    events = await get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    assert len(events) == 1
    ev = events[0]
    assert ev["source_id"] == "user@example.com"
    details = json.loads(ev["details_json"])
    assert details["previous_trust"] == "auto"
    assert details["reason"] == "auto_promote"
    assert details["interaction_count"] == 5


async def test_classify_no_auto_promote_before_threshold(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """No promotion occurs before interaction_count reaches auto_promote_after."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    for _ in range(4):  # count=4 (1 creation + 3 increments); threshold is 5
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.trust_level == "auto"
    events = await get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    assert len(events) == 0


@pytest.mark.parametrize("override_trust", ["trusted", "blocked"])
async def test_classify_no_auto_promote_for_manual_override(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock, override_trust: str
) -> None:
    """Contacts with manual trust override ('trusted' or 'blocked') are never auto-promoted."""
    import clawstrike.mcpserver as srv
    from clawstrike.db import open_db, set_contact_trust_level

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)
    async with open_db(str(cfg.audit.db_path)) as conn:
        await set_contact_trust_level(conn, "user@example.com", override_trust)

    for _ in range(4):
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    record = await get_contact_from_db(str(cfg.audit.db_path), "user@example.com")
    assert record.trust_level == override_trust
    events = await get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    assert len(events) == 0


async def test_classify_auto_promote_only_once(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """After promotion, further calls don't produce additional trust_update events."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)

    for _ in range(8):  # 5 to trigger promotion, 3 more
        await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
    assert len(events) == 1  # exactly one promotion event


# ---------------------------------------------------------------------------
# Classify audit events
# ---------------------------------------------------------------------------


async def test_classify_audit_event_has_label_model_and_thresholds(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """classify audit event includes label, model, and threshold_applied in details."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await get_audit_events(str(cfg.audit.db_path), event_type="classify")
    assert len(events) == 1
    ev = events[0]
    assert ev["label"] == "benign"
    assert ev["score"] == _SCORE_PASS
    details = json.loads(ev["details_json"])
    assert details["model"] == "mock-model"
    assert "block" in details["threshold_applied"]
    assert "flag" in details["threshold_applied"]


async def test_classify_pass_audit_event(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Pass classify writes audit with decision='pass' and elevated_scrutiny=False."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await get_audit_events(str(cfg.audit.db_path), event_type="classify")
    assert len(events) == 1
    ev = events[0]
    assert ev["decision"] == "pass"
    assert json.loads(ev["details_json"])["elevated_scrutiny"] is False


async def test_classify_block_audit_event_written_with_hash(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Block classify audit event includes raw_input_hash and label='injection'."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_BLOCK, label="injection", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", {**_CLASSIFY_ARGS, "text": "inject me"})

    events = await get_audit_events(str(cfg.audit.db_path), event_type="classify")
    ev = events[0]
    assert ev["decision"] == "block"
    assert ev["label"] == "injection"
    assert ev["raw_input_hash"] is not None


async def test_classify_flag_audit_event_has_elevated_scrutiny_in_details(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """Flag classify audit event includes elevated_scrutiny=True in details."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_FLAG, label="injection", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    await srv.mcp.call_tool("classify", _CLASSIFY_ARGS)

    events = await get_audit_events(str(cfg.audit.db_path), event_type="classify")
    assert json.loads(events[0]["details_json"])["elevated_scrutiny"] is True


async def test_classify_audit_stores_raw_input_snippet_when_log_raw_input_true(
    cfg: ClawStrikeConfig, reset_server_config: MagicMock
) -> None:
    """raw_input_snippet stored when log_raw_input=True (default)."""
    import clawstrike.mcpserver as srv

    reset_server_config.classify.return_value = ClassifierResult(
        score=_SCORE_PASS, label="benign", model="mock-model", latency_ms=1.0
    )
    srv.init_server(cfg)
    text = "hello world"
    await srv.mcp.call_tool(
        "classify",
        {
            "text": text,
            "source_id": "s",
            "channel_type": "email_body",
            "session_id": "test-session",
        },
    )

    events = await get_audit_events(str(cfg.audit.db_path), event_type="classify")
    assert events[0]["raw_input_snippet"] == text


async def test_classify_audit_stores_only_hash_when_log_raw_input_false(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Only SHA-256 hash stored when log_raw_input=False."""
    import hashlib

    import clawstrike.mcpserver as srv

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
        {
            "text": text,
            "source_id": "s",
            "channel_type": "email_body",
            "session_id": "test-session",
        },
    )

    events = await get_audit_events(
        str(cfg_no_raw.audit.db_path), event_type="classify"
    )
    ev = events[0]
    assert ev["raw_input_snippet"] is None
    assert ev["raw_input_hash"] == hashlib.sha256(text.encode()).hexdigest()


async def test_classify_audit_snippet_truncated_to_max_chars(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """raw_input_snippet is truncated to raw_input_max_chars."""
    import clawstrike.mcpserver as srv

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
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "a" * 50,
            "source_id": "s",
            "channel_type": "email_body",
            "session_id": "test-session",
        },
    )

    events = await get_audit_events(str(cfg_trunc.audit.db_path), event_type="classify")
    assert events[0]["raw_input_snippet"] == "a" * 10


# ---------------------------------------------------------------------------
# Content-Source Mismatch Detection (classify side)
#
# Mismatch fires when: trust_level in (HIGH, MEDIUM) AND score >= base_flag (0.70).
# HIGH trust: eff_flag=0.80, eff_block=0.97
# MEDIUM trust: eff_flag=0.70, eff_block=0.92
# ---------------------------------------------------------------------------


async def test_classify_mismatch_detected_high_trust_pass_decision(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """HIGH trust + score in (base_flag, eff_flag) → pass decision AND mismatch.

    HIGH trust raises eff_flag to 0.80. Score 0.75 is above base_flag (0.70) but
    below eff_flag, so the message passes classification — yet mismatch fires.
    """
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "hello",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
            "session_id": "seed-session",
        },
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious content",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
            "session_id": "test-session",
        },
    )
    data = result.structured_content
    assert data["decision"] == "pass"  # 0.75 < eff_flag 0.80
    assert data["content_source_mismatch"] is True
    assert data["trust_level"] == "high"


async def test_classify_mismatch_detected_medium_trust(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """MEDIUM trust contact + score >= base_flag → mismatch detected."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "medium")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "hello",
            "source_id": "medium-user",
            "channel_type": "test_chan",
            "session_id": "seed-session",
        },
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious content",
            "source_id": "medium-user",
            "channel_type": "test_chan",
            "session_id": "test-session",
        },
    )
    data = result.structured_content
    assert data["decision"] == "flag"  # 0.75 >= MEDIUM eff_flag 0.70
    assert data["content_source_mismatch"] is True
    assert data["trust_level"] == "medium"


async def test_classify_no_mismatch_score_below_base_flag(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Score below base_flag → no mismatch even for HIGH trust contacts."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "hi",
            "source_id": "user",
            "channel_type": "test_chan",
            "session_id": "seed-session",
        },
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.50, label="benign", model="mock-model", latency_ms=1.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "normal text",
            "source_id": "user",
            "channel_type": "test_chan",
            "session_id": "test-session",
        },
    )
    assert result.structured_content["content_source_mismatch"] is False


async def test_classify_no_mismatch_low_trust_above_base_flag(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """LOW trust contacts above base_flag → no mismatch (only HIGH/MEDIUM trigger it)."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "low")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "hi",
            "source_id": "low-user",
            "channel_type": "test_chan",
            "session_id": "seed-session",
        },
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
            "session_id": "test-session",
        },
    )
    assert result.structured_content["content_source_mismatch"] is False


async def test_classify_no_mismatch_first_contact(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """First-contact forces UNTRUSTED trust → no mismatch even if HIGH channel."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
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
    assert data["is_first_contact"] is True
    assert data["content_source_mismatch"] is False
    assert "new-session" not in srv._mismatch_sessions


async def test_classify_mismatch_tags_session(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Mismatch with non-empty session_id adds session to _mismatch_sessions."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "seed",
            "source_id": "user",
            "channel_type": "test_chan",
            "session_id": "seed-session",
        },
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


async def test_classify_mismatch_empty_session_id_no_tag(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Mismatch with session_id="" sets response flag but does not tag any session."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "seed",
            "source_id": "user",
            "channel_type": "test_chan",
            "session_id": "seed-session",
        },
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious",
            "source_id": "user",
            "channel_type": "test_chan",
            "session_id": "",
        },
    )
    assert result.structured_content["content_source_mismatch"] is True
    assert len(srv._mismatch_sessions) == 0


async def test_classify_mismatch_audit_event_written(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Mismatch writes a trust_update audit event with reason=content_source_mismatch."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
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

    events = await get_audit_events(str(cfg.audit.db_path), event_type="trust_update")
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
    assert details["score"] == pytest.approx(0.75)


async def test_classify_mismatch_e2e_gate_uses_low_trust(
    tmp_path: Path, reset_server_config: MagicMock
) -> None:
    """Full flow: classify detects mismatch → gate uses LOW effective trust."""
    import clawstrike.mcpserver as srv

    cfg = make_cfg_with_trust(tmp_path, "test_chan", "high")
    srv.init_server(cfg)

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    await srv.mcp.call_tool(
        "classify",
        {
            "text": "normal message",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
            "session_id": "seed-session",
        },
    )

    reset_server_config.classify.return_value = ClassifierResult(
        score=0.75, label="injection", model="mock-model", latency_ms=2.0
    )
    c = await srv.mcp.call_tool(
        "classify",
        {
            "text": "suspicious injection attempt",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
            "session_id": "e2e-mismatch-session",
        },
    )
    assert c.structured_content["decision"] == "pass"
    assert c.structured_content["content_source_mismatch"] is True
    assert "e2e-mismatch-session" in srv._mismatch_sessions

    g = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "send email",
            "action_type": "send_email",
            "session_id": "e2e-mismatch-session",
            "source_id": "trusted-user",
            "channel_type": "test_chan",
        },
    )
    gd = g.structured_content
    assert gd["trust_level"] == "high"
    assert gd["effective_trust_level"] == "low"
    assert gd["content_source_mismatch"] is True
    assert gd["recommendation"] == "block"  # send_email HIGH risk + LOW trust → block
