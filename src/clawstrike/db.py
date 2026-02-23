"""SQLite connection manager, schema init, and CRUD for ClawStrike."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

# ---------------------------------------------------------------------------
# Schema DDL — contacts (US-012) and audit_events (US-012 AC5 / US-023)
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
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TIMESTAMP NOT NULL,
    event_type       TEXT NOT NULL DEFAULT '',
    session_id       TEXT NOT NULL DEFAULT '',
    source_id        TEXT NOT NULL DEFAULT '',
    channel_type     TEXT NOT NULL DEFAULT '',
    decision         TEXT,
    score            REAL,
    is_first_contact INTEGER NOT NULL DEFAULT 0,
    trust_level      TEXT,
    details_json     TEXT NOT NULL DEFAULT '{}'
);
"""

# ---------------------------------------------------------------------------
# Connection manager
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
) -> None:
    """Write one audit event row to ``audit_events``."""
    now = datetime.now(UTC).isoformat()
    await conn.execute(
        "INSERT INTO audit_events "
        "(timestamp, event_type, session_id, source_id, channel_type, "
        "decision, score, is_first_contact, trust_level, details_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        ),
    )
    await conn.commit()
