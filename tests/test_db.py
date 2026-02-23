"""Unit tests for US-012: db.py — Contact Registry and Audit Events."""

from __future__ import annotations

from pathlib import Path

import pytest

from clawstrike.db import (
    ContactRecord,
    get_or_create_contact,
    insert_audit_event,
    open_db,
)

# ---------------------------------------------------------------------------
# open_db — schema and directory creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_db_creates_contacts_table(tmp_path: Path) -> None:
    """open_db creates the contacts table on first use."""
    async with open_db(tmp_path / "test.db") as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='contacts'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_open_db_creates_audit_events_table(tmp_path: Path) -> None:
    """open_db creates the audit_events table on first use."""
    async with open_db(tmp_path / "test.db") as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_open_db_creates_parent_dirs(tmp_path: Path) -> None:
    """open_db auto-creates missing parent directories."""
    db_path = tmp_path / "nested" / "dir" / "test.db"
    async with open_db(db_path):
        pass
    assert db_path.exists()


@pytest.mark.asyncio
async def test_open_db_idempotent(tmp_path: Path) -> None:
    """Calling open_db twice on the same path does not error (IF NOT EXISTS guard)."""
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await get_or_create_contact(conn, "x@example.com", "webhook")
    async with open_db(db_path) as conn:
        await get_or_create_contact(conn, "y@example.com", "webhook")


# ---------------------------------------------------------------------------
# get_or_create_contact — first contact behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_contact_is_first_contact_true(tmp_path: Path) -> None:
    """New source_id → is_first_contact=True, trust_level='auto', count=1."""
    async with open_db(tmp_path / "test.db") as conn:
        record, is_first = await get_or_create_contact(
            conn, "alice@example.com", "email_body"
        )
    assert is_first is True
    assert isinstance(record, ContactRecord)
    assert record.source_id == "alice@example.com"
    assert record.channel_type == "email_body"
    assert record.trust_level == "auto"
    assert record.interaction_count == 1


@pytest.mark.asyncio
async def test_known_contact_is_first_contact_false(tmp_path: Path) -> None:
    """Existing source_id → is_first_contact=False."""
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await get_or_create_contact(conn, "alice@example.com", "email_body")
    async with open_db(db_path) as conn:
        _, is_first = await get_or_create_contact(
            conn, "alice@example.com", "email_body"
        )
    assert is_first is False


@pytest.mark.asyncio
async def test_first_contact_row_persists_across_connections(tmp_path: Path) -> None:
    """Row created on first contact is retrievable in a subsequent open_db call."""
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await get_or_create_contact(conn, "bob@example.com", "webhook")
    async with open_db(db_path) as conn:
        record, _ = await get_or_create_contact(conn, "bob@example.com", "webhook")
    assert record.source_id == "bob@example.com"
    assert record.channel_type == "webhook"


@pytest.mark.asyncio
async def test_second_call_preserves_channel_type(tmp_path: Path) -> None:
    """Calling get_or_create_contact for an existing source with a different
    channel_type does not overwrite the original channel_type."""
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await get_or_create_contact(conn, "carol@example.com", "email_body")
    async with open_db(db_path) as conn:
        record, _ = await get_or_create_contact(conn, "carol@example.com", "owner_dm")
    assert record.channel_type == "email_body"


@pytest.mark.asyncio
async def test_different_source_ids_are_independent(tmp_path: Path) -> None:
    """Two distinct source_ids each trigger their own first-contact event."""
    async with open_db(tmp_path / "test.db") as conn:
        _, is_first_a = await get_or_create_contact(conn, "a@example.com", "email_body")
        _, is_first_b = await get_or_create_contact(conn, "b@example.com", "email_body")
    assert is_first_a is True
    assert is_first_b is True


# ---------------------------------------------------------------------------
# insert_audit_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_audit_event_writes_row(tmp_path: Path) -> None:
    """insert_audit_event persists a row retrievable via a SELECT."""
    async with open_db(tmp_path / "test.db") as conn:
        await insert_audit_event(
            conn,
            event_type="classify",
            session_id="sess-1",
            source_id="user@example.com",
            channel_type="email_body",
            decision="pass",
            score=0.10,
            is_first_contact=True,
            trust_level="untrusted",
        )
        async with conn.execute(
            "SELECT event_type, source_id, is_first_contact, decision "
            "FROM audit_events WHERE source_id = ?",
            ("user@example.com",),
        ) as cur:
            row = await cur.fetchone()

    assert row is not None
    assert row["event_type"] == "classify"
    assert row["is_first_contact"] == 1
    assert row["decision"] == "pass"


@pytest.mark.asyncio
async def test_insert_audit_event_is_first_contact_false(tmp_path: Path) -> None:
    """is_first_contact=False is stored as integer 0."""
    async with open_db(tmp_path / "test.db") as conn:
        await insert_audit_event(
            conn,
            event_type="classify",
            source_id="known@example.com",
            is_first_contact=False,
        )
        async with conn.execute(
            "SELECT is_first_contact FROM audit_events WHERE source_id = ?",
            ("known@example.com",),
        ) as cur:
            row = await cur.fetchone()

    assert row["is_first_contact"] == 0
