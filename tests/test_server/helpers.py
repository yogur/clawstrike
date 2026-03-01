"""Shared helper functions and constants for test_server/ tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from clawstrike.config import ClawStrikeConfig, load_config

# ---------------------------------------------------------------------------
# YAML utilities
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


def make_cfg_with_trust(tmp_path: Path, channel: str, trust: str) -> ClawStrikeConfig:
    """Return a config that maps *channel* to *trust* trust level."""
    data = minimal_config(
        {
            "audit": {"db_path": str(tmp_path / "trust_test.db")},
            "trust": {"channel_defaults": {channel: trust}},
        }
    )
    return load_config(write_yaml(tmp_path, data))


def make_cfg_with_static_rules(
    tmp_path: Path,
    static_rules: list[dict],
    *,
    db_name: str = "static_rules_test.db",
) -> ClawStrikeConfig:
    """Return a config with static allowlist rules and an isolated DB."""
    data = minimal_config(
        {
            "audit": {"db_path": str(tmp_path / db_name)},
            "action_gating": {"static_rules": static_rules},
        }
    )
    return load_config(write_yaml(tmp_path, data))


def make_cfg_with_contacts(
    tmp_path: Path,
    contacts: dict[str, str],
    *,
    channel: str = "email_body",
    channel_trust: str = "low",
    db_name: str = "contacts_test.db",
) -> ClawStrikeConfig:
    """Return a config with *contacts* trust overrides and an isolated DB."""
    data = minimal_config(
        {
            "audit": {"db_path": str(tmp_path / db_name)},
            "trust": {
                "channel_defaults": {channel: channel_trust},
                "contacts": contacts,
            },
        }
    )
    return load_config(write_yaml(tmp_path, data))


# ---------------------------------------------------------------------------
# Async DB helpers
# ---------------------------------------------------------------------------


async def get_contact_from_db(db_path: str, source_id: str):
    """Fetch a ContactRecord directly from the SQLite DB."""
    from clawstrike.db import get_or_create_contact, open_db

    async with open_db(db_path) as conn:
        record, _ = await get_or_create_contact(conn, source_id, "email_body")
    return record


async def get_audit_events(db_path: str, *, event_type: str | None = None):
    """Fetch all audit events (optionally filtered by event_type)."""
    from clawstrike.db import open_db

    async with open_db(db_path) as conn:
        if event_type:
            async with conn.execute(
                "SELECT * FROM audit_events WHERE event_type = ?", (event_type,)
            ) as cur:
                return await cur.fetchall()
        async with conn.execute("SELECT * FROM audit_events") as cur:
            return await cur.fetchall()
