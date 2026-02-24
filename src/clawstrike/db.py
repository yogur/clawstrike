"""SQLite connection manager, schema init, and CRUD for ClawStrike."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

# ---------------------------------------------------------------------------
# Schema DDL — contacts (US-012) and audit_events (US-023/024)
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS contacts (
    source_id         TEXT PRIMARY KEY,
    channel_type      TEXT NOT NULL,
    display_name      TEXT,
    trust_level       TEXT NOT NULL DEFAULT 'auto',
    first_seen        TIMESTAMP NOT NULL,
    last_seen         TIMESTAMP NOT NULL,
    interaction_count INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS audit_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TIMESTAMP NOT NULL,
    event_type        TEXT NOT NULL DEFAULT '',
    session_id        TEXT NOT NULL DEFAULT '',
    source_id         TEXT NOT NULL DEFAULT '',
    channel_type      TEXT NOT NULL DEFAULT '',
    decision          TEXT,
    score             REAL,
    is_first_contact  INTEGER NOT NULL DEFAULT 0,
    trust_level       TEXT,
    details_json      TEXT NOT NULL DEFAULT '{}',
    label             TEXT,
    raw_input_hash    TEXT,
    raw_input_snippet TEXT
);
"""

# Columns added after the original schema that must be applied via ALTER TABLE
# when opening an existing database that pre-dates their addition.
_NEW_AUDIT_COLS: list[tuple[str, str]] = [
    ("label", "TEXT"),
    ("raw_input_hash", "TEXT"),
    ("raw_input_snippet", "TEXT"),
]

# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------


async def _apply_migrations(conn: aiosqlite.Connection) -> None:
    """Add any columns missing from audit_events (forward migration only)."""
    async with conn.execute("PRAGMA table_info(audit_events)") as cur:
        rows = await cur.fetchall()
    existing = {row[1] for row in rows}
    for col_name, col_type in _NEW_AUDIT_COLS:
        if col_name not in existing:
            await conn.execute(
                f"ALTER TABLE audit_events ADD COLUMN {col_name} {col_type}"
            )
    await conn.commit()


def _apply_migrations_sync(conn: sqlite3.Connection) -> None:
    """Sync version of _apply_migrations for use in setup_audit_db."""
    cursor = conn.execute("PRAGMA table_info(audit_events)")
    existing = {row[1] for row in cursor.fetchall()}
    for col_name, col_type in _NEW_AUDIT_COLS:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE audit_events ADD COLUMN {col_name} {col_type}")
    conn.commit()


# ---------------------------------------------------------------------------
# Async connection manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def open_db(path: str | Path) -> AsyncIterator[aiosqlite.Connection]:
    """Open an aiosqlite connection, ensure schema exists, yield, then close.

    Creates parent directories automatically. Schema creation is idempotent
    (``CREATE TABLE IF NOT EXISTS``), so this can be called on every request.
    Applies forward migrations to add any columns that did not exist in
    earlier schema versions.
    """
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_DDL)
        await conn.commit()
        await _apply_migrations(conn)
        yield conn


# ---------------------------------------------------------------------------
# Synchronous startup initializer (US-023)
# ---------------------------------------------------------------------------


def setup_audit_db(path: str | Path) -> tuple[bool, int]:
    """Synchronously initialize the audit database and return status.

    Uses the stdlib ``sqlite3`` module so it can be called from synchronous
    startup code without an event loop.  Creates parent directories, creates
    tables if they don't exist, and runs forward migrations on existing
    databases.

    Returns:
        ``(was_created, event_count)`` where *was_created* is ``True`` when
        the database file did not exist before this call.
    """
    db_path = Path(path)
    was_created = not db_path.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(_DDL)
        conn.commit()
        _apply_migrations_sync(conn)
        cursor = conn.execute("SELECT COUNT(*) FROM audit_events")
        event_count: int = cursor.fetchone()[0]
    return was_created, event_count


# ---------------------------------------------------------------------------
# Contact Registry
# ---------------------------------------------------------------------------


@dataclass
class ContactRecord:
    source_id: str
    channel_type: str
    trust_level: str  # 'auto' | 'trusted' | 'blocked'
    first_seen: datetime
    last_seen: datetime
    interaction_count: int


async def get_or_create_contact(
    conn: aiosqlite.Connection,
    source_id: str,
    channel_type: str,
) -> tuple[ContactRecord, bool]:
    """Look up *source_id*; create the record if absent.

    Returns:
        ``(record, is_first_contact)`` where ``is_first_contact`` is ``True``
        iff the row was created by this call (i.e., never seen before).
    """
    now = datetime.now(UTC).isoformat()

    async with conn.execute(
        "SELECT source_id, channel_type, trust_level, first_seen, last_seen, "
        "interaction_count FROM contacts WHERE source_id = ?",
        (source_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        await conn.execute(
            "INSERT INTO contacts "
            "(source_id, channel_type, trust_level, first_seen, last_seen, "
            "interaction_count) VALUES (?, ?, 'auto', ?, ?, 1)",
            (source_id, channel_type, now, now),
        )
        await conn.commit()
        return (
            ContactRecord(
                source_id=source_id,
                channel_type=channel_type,
                trust_level="auto",
                first_seen=datetime.fromisoformat(now),
                last_seen=datetime.fromisoformat(now),
                interaction_count=1,
            ),
            True,
        )

    return (
        ContactRecord(
            source_id=row["source_id"],
            channel_type=row["channel_type"],
            trust_level=row["trust_level"],
            first_seen=datetime.fromisoformat(row["first_seen"]),
            last_seen=datetime.fromisoformat(row["last_seen"]),
            interaction_count=row["interaction_count"],
        ),
        False,
    )


async def increment_interaction(
    conn: aiosqlite.Connection,
    source_id: str,
) -> ContactRecord:
    """Increment interaction_count and update last_seen for a known contact.

    Returns the updated ContactRecord after the write.
    """
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "UPDATE contacts SET interaction_count = interaction_count + 1, "
        "last_seen = ? WHERE source_id = ?",
        (now, source_id),
    )
    await conn.commit()
    async with conn.execute(
        "SELECT source_id, channel_type, trust_level, first_seen, last_seen, "
        "interaction_count FROM contacts WHERE source_id = ?",
        (source_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return ContactRecord(
        source_id=row["source_id"],
        channel_type=row["channel_type"],
        trust_level=row["trust_level"],
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_seen=datetime.fromisoformat(row["last_seen"]),
        interaction_count=row["interaction_count"],
    )


async def set_contact_trust_level(
    conn: aiosqlite.Connection,
    source_id: str,
    trust_level: str,
) -> None:
    """Update the stored trust_level for a contact."""
    await conn.execute(
        "UPDATE contacts SET trust_level = ? WHERE source_id = ?",
        (trust_level, source_id),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Audit Events
# ---------------------------------------------------------------------------


async def insert_audit_event(
    conn: aiosqlite.Connection,
    *,
    event_type: str,
    session_id: str = "",
    source_id: str = "",
    channel_type: str = "",
    decision: str | None = None,
    score: float | None = None,
    is_first_contact: bool = False,
    trust_level: str | None = None,
    details: dict | None = None,
    label: str | None = None,
    raw_input_hash: str | None = None,
    raw_input_snippet: str | None = None,
) -> None:
    """Write one audit event row to ``audit_events``."""
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "INSERT INTO audit_events "
        "(timestamp, event_type, session_id, source_id, channel_type, "
        "decision, score, is_first_contact, trust_level, details_json, "
        "label, raw_input_hash, raw_input_snippet) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now,
            event_type,
            session_id,
            source_id,
            channel_type,
            decision,
            score,
            1 if is_first_contact else 0,
            trust_level,
            json.dumps(details or {}),
            label,
            raw_input_hash,
            raw_input_snippet,
        ),
    )
    await conn.commit()
