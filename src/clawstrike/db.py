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
# Schema DDL — contacts, action_allowlist, audit_events
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
CREATE TABLE IF NOT EXISTS action_allowlist (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type    TEXT NOT NULL,
    action_pattern TEXT,
    source_scope   TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL,
    created_by     TEXT NOT NULL
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

# ---------------------------------------------------------------------------
# Async connection manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def open_db(path: str | Path) -> AsyncIterator[aiosqlite.Connection]:
    """Open an aiosqlite connection, ensure schema exists, yield, then close.

    Creates parent directories automatically. Schema creation is idempotent
    (``CREATE TABLE IF NOT EXISTS``), so this can be called on every request.
    """
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_DDL)
        await conn.commit()
        yield conn


# ---------------------------------------------------------------------------
# Synchronous startup initializer
# ---------------------------------------------------------------------------


def setup_audit_db(path: str | Path) -> tuple[bool, int]:
    """Synchronously initialize the audit database and return status.

    Uses the stdlib ``sqlite3`` module so it can be called from synchronous
    startup code without an event loop.  Creates parent directories and
    creates tables if they don't exist.

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
# Action Allowlist
# ---------------------------------------------------------------------------


async def check_allowlist(
    conn: aiosqlite.Connection,
    action_type: str,
    source_id: str,
) -> dict | None:
    """Check if an action is allowlisted for the given source.

    Matches on exact ``action_type`` AND (``source_scope = 'global'`` OR
    ``source_scope = source_id``).  Returns the first matching row as a dict
    (with ``id``, ``action_type``, ``source_scope``, etc.) or ``None``.
    """
    async with conn.execute(
        "SELECT id, action_type, action_pattern, source_scope, created_at, "
        "created_by FROM action_allowlist "
        "WHERE action_type = ? AND (source_scope = 'global' OR source_scope = ?) "
        "LIMIT 1",
        (action_type, source_id),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row["id"],
        "action_type": row["action_type"],
        "action_pattern": row["action_pattern"],
        "source_scope": row["source_scope"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
    }


async def insert_allowlist_rule(
    conn: aiosqlite.Connection,
    action_type: str,
    source_scope: str,
    created_by: str = "owner",
) -> int:
    """Insert a new allowlist rule and return its row ID."""
    now = datetime.now(UTC).isoformat()
    cursor = await conn.execute(
        "INSERT INTO action_allowlist "
        "(action_type, action_pattern, source_scope, created_at, created_by) "
        "VALUES (?, NULL, ?, ?, ?)",
        (action_type, source_scope, now, created_by),
    )
    await conn.commit()
    return cursor.lastrowid


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


# ---------------------------------------------------------------------------
# Audit Event Query
# ---------------------------------------------------------------------------

#: Column order for CSV export — matches the DDL declaration order.
AUDIT_EVENT_FIELDS: list[str] = [
    "id",
    "timestamp",
    "event_type",
    "session_id",
    "source_id",
    "channel_type",
    "decision",
    "score",
    "is_first_contact",
    "trust_level",
    "details_json",
    "label",
    "raw_input_hash",
    "raw_input_snippet",
]


def query_audit_events(
    path: str | Path,
    *,
    since: datetime | None = None,
    source_id: str | None = None,
    event_type: str | None = None,
    decision: str | None = None,
) -> list[dict]:
    """Query audit events synchronously with optional filters.

    Returns rows as plain dicts with keys matching ``AUDIT_EVENT_FIELDS``.
    If the database file does not exist, returns an empty list.
    Results are ordered by timestamp ascending.
    """
    db_path = Path(path)
    if not db_path.exists():
        return []

    conditions: list[str] = []
    params: list[object] = []

    if since is not None:
        conditions.append("timestamp >= ?")
        params.append(since.isoformat())
    if source_id is not None:
        conditions.append("source_id = ?")
        params.append(source_id)
    if event_type is not None:
        conditions.append("event_type = ?")
        params.append(event_type)
    if decision is not None:
        conditions.append("decision = ?")
        params.append(decision)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT * FROM audit_events{where} ORDER BY timestamp ASC"

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()

    return [dict(row) for row in rows]
