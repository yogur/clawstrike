"""End-to-end scenario tests spanning the full classify → gate pipeline.

These tests exercise complete user-facing scenarios to verify that all
pipeline components integrate correctly from input classification through
action gating and audit logging.
"""

from __future__ import annotations

import time
from pathlib import Path

from clawstrike.config import ClawStrikeConfig

from .helpers import get_audit_events

# ---------------------------------------------------------------------------
# US-032: E2E — Benign Owner DM Passthrough
# ---------------------------------------------------------------------------


async def test_e2e_benign_owner_dm_passthrough(
    cfg: ClawStrikeConfig,
    tmp_path: Path,
) -> None:
    """Full classify → gate pipeline for a benign owner DM message.

    A benign message from the owner's DM channel passes classification with
    high trust, and a subsequent low-risk action is auto-approved. ClawStrike
    is effectively invisible during normal owner usage.

    Acceptance criteria verified:
    - Benign message passes classification (score < flag threshold)
    - Trust resolves to HIGH (owner_dm channel default, non-first-contact)
    - Subsequent gate call for calendar_read returns recommendation: allow
    - Full classify + gate round trip completes in <110ms (mock classifier)
    - Audit log: exactly one classify event (pass) + one action_gate event (allow)
    """
    import clawstrike.mcpserver as srv
    from clawstrike.db import get_or_create_contact, open_db

    source_id = "owner@example.com"
    session_id = "e2e-session-benign-owner"
    channel_type = "owner_dm"
    db_path = str(cfg.audit.db_path)

    # Pre-register the owner contact so classify sees them as a known contact.
    # Without this, the first call would trigger the first-contact override
    # (UNTRUSTED) instead of the channel default (owner_dm → HIGH).
    async with open_db(db_path) as conn:
        await get_or_create_contact(conn, source_id, channel_type)

    srv.init_server(cfg)

    # --- Classify: benign message from the owner's DM ---
    t_start = time.monotonic()
    classify_result = await srv.mcp.call_tool(
        "classify",
        {
            "text": "Hey, what's on my calendar today?",
            "source_id": source_id,
            "channel_type": channel_type,
            "session_id": session_id,
        },
    )
    classify_data = classify_result.structured_content

    # --- Gate: low-risk calendar read action ---
    gate_result = await srv.mcp.call_tool(
        "gate",
        {
            "action_description": "Read today's calendar events",
            "action_type": "calendar_read",
            "session_id": session_id,
            "source_id": source_id,
            "channel_type": channel_type,
        },
    )
    t_end = time.monotonic()
    gate_data = gate_result.structured_content

    elapsed_ms = (t_end - t_start) * 1000

    # Classify: benign pass with high trust (owner_dm channel default)
    assert classify_data["decision"] == "pass"
    assert classify_data["trust_level"] == "high"
    assert classify_data["is_first_contact"] is False
    assert classify_data["content_source_mismatch"] is False

    # Gate: low-risk calendar_read from high-trust owner_dm → auto-approve
    assert gate_data["recommendation"] == "allow"
    assert gate_data["risk_level"] == "low"
    assert gate_data["trust_level"] == "high"
    assert gate_data["elevated_scrutiny"] is False
    assert gate_data["allowlisted"] is False

    # Performance: full classify + gate round trip must complete under 110ms.
    # The mock classifier eliminates model inference time, so this measures
    # MCP transport + DB I/O + pipeline overhead only.
    assert elapsed_ms < 110, f"Round trip took {elapsed_ms:.1f}ms, expected <110ms"

    # Audit log: exactly one classify event (pass) and one action_gate event (allow).
    classify_events = await get_audit_events(db_path, event_type="classify")
    gate_events = await get_audit_events(db_path, event_type="action_gate")

    assert len(classify_events) == 1
    assert len(gate_events) == 1

    classify_row = dict(classify_events[0])
    assert classify_row["decision"] == "pass"
    assert classify_row["source_id"] == source_id
    assert classify_row["session_id"] == session_id
    assert classify_row["channel_type"] == channel_type

    gate_row = dict(gate_events[0])
    assert gate_row["decision"] == "allow"
    assert gate_row["source_id"] == source_id
    assert gate_row["session_id"] == session_id
    assert gate_row["channel_type"] == channel_type
