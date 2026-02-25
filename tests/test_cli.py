"""Tests for the ClawStrike CLI commands (US-002 and CLI integration story)."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from clawstrike.classifier import ClassifierResult
from clawstrike.cli import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
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
# AC: missing config file — explicit path → exit 1; default path → defaults
# ---------------------------------------------------------------------------


def test_start_missing_explicit_config_exits_1(tmp_path: Path) -> None:
    """Passing an explicit --config to a missing file must exit 1."""
    result = runner.invoke(app, ["start", "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 1
    assert "missing.yaml" in result.output


# ---------------------------------------------------------------------------
# AC: invalid config → exit 1
# ---------------------------------------------------------------------------


def test_start_invalid_config_exits_1(tmp_path: Path) -> None:
    cfg_file = write_yaml(
        tmp_path, {"clawstrike": {"classifier": {"model": "bad-model"}}}
    )
    result = runner.invoke(app, ["start", "--config", str(cfg_file)])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# AC: proxy mode → exit 1 with a clear error (proxy not available in MVP)
# ---------------------------------------------------------------------------


def test_start_proxy_mode_exits_1(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config({"mode": "proxy"}))
    result = runner.invoke(app, ["start", "--config", str(cfg_file)])
    assert result.exit_code == 1
    assert "proxy" in result.output.lower() or "skill" in result.output.lower()


# ---------------------------------------------------------------------------
# AC: skill mode → startup banner in output, mcp.run called with stdio
# ---------------------------------------------------------------------------


def _mock_classifier() -> MagicMock:
    clf = MagicMock()
    clf.classify.return_value = ClassifierResult(
        score=0.0, label="benign", model="mock-model", latency_ms=1.0
    )
    return clf


def test_start_skill_mode_logs_banner_and_runs(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())

    with (
        patch("clawstrike.mcpserver.mcp.run") as mock_run,
        patch(
            "clawstrike.mcpserver.create_classifier", return_value=_mock_classifier()
        ),
    ):
        result = runner.invoke(app, ["start", "--config", str(cfg_file)])

    assert result.exit_code == 0
    assert "skill mode" in result.output.lower()
    assert "stdio" in result.output.lower()
    mock_run.assert_called_once_with(transport="stdio")


@pytest.mark.parametrize("model", ["multilingual", "english-only"])
def test_start_banner_includes_classifier_model(tmp_path: Path, model: str) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config({"classifier": {"model": model}}))

    with (
        patch("clawstrike.mcpserver.mcp.run"),
        patch(
            "clawstrike.mcpserver.create_classifier", return_value=_mock_classifier()
        ),
    ):
        result = runner.invoke(app, ["start", "--config", str(cfg_file)])

    assert result.exit_code == 0
    assert model in result.output


# ---------------------------------------------------------------------------
# AC: classifier load failure → exit 1 with descriptive error
# ---------------------------------------------------------------------------


def test_start_classifier_load_failure_exits_1(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())

    with (
        patch("clawstrike.mcpserver.mcp.run"),
        patch(
            "clawstrike.mcpserver.create_classifier",
            side_effect=RuntimeError("Failed to load classifier 'x': no such file"),
        ),
    ):
        result = runner.invoke(app, ["start", "--config", str(cfg_file)])

    assert result.exit_code == 1
    assert "Failed to load classifier" in result.output


# ---------------------------------------------------------------------------
# AC: init_server is called before the server runs
# ---------------------------------------------------------------------------


def test_start_calls_init_server(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())

    with (
        patch("clawstrike.mcpserver.mcp.run"),
        patch("clawstrike.mcpserver.init_server") as mock_init,
    ):
        result = runner.invoke(app, ["start", "--config", str(cfg_file)])

    assert result.exit_code == 0
    mock_init.assert_called_once()


# ---------------------------------------------------------------------------
# AC: `start` with no config file falls back to defaults and starts server
# ---------------------------------------------------------------------------


def test_start_no_config_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the default clawstrike.yaml is absent, start uses all defaults."""
    # Run from a directory that has no clawstrike.yaml
    monkeypatch.chdir(tmp_path)

    with (
        patch("clawstrike.mcpserver.mcp.run") as mock_run,
        patch(
            "clawstrike.mcpserver.create_classifier", return_value=_mock_classifier()
        ),
    ):
        result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with(transport="stdio")


# ---------------------------------------------------------------------------
# AC: `start` with mcp.enabled: false exits 0 with message
# ---------------------------------------------------------------------------


def test_start_mcp_disabled_exits_0(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config({"mcp": {"enabled": False}}))

    result = runner.invoke(app, ["start", "--config", str(cfg_file)])

    assert result.exit_code == 0
    assert "mcp.enabled" in result.output
    assert "disabled" in result.output.lower()


# ---------------------------------------------------------------------------
# AC: `clawstrike health` — config-only, no model load
# ---------------------------------------------------------------------------


def test_health_outputs_json(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())

    result = runner.invoke(app, ["health", "--config", str(cfg_file)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["mode"] == "skill"
    assert data["classifier"] == "multilingual"
    assert data["mcp_enabled"] is True


def test_health_reflects_mcp_disabled(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config({"mcp": {"enabled": False}}))

    result = runner.invoke(app, ["health", "--config", str(cfg_file)])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["mcp_enabled"] is False


def test_health_no_config_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["health"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["classifier"] == "multilingual"


# ---------------------------------------------------------------------------
# AC: `clawstrike classify` — JSON in, JSON out
# ---------------------------------------------------------------------------

_CLASSIFY_PARAMS = json.dumps(
    {
        "text": "hello world",
        "source_id": "user@example.com",
        "channel_type": "email_body",
    }
)


def test_classify_returns_json(tmp_path: Path) -> None:
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(tmp_path / "test.db")}})
    )

    with patch(
        "clawstrike.mcpserver.create_classifier", return_value=_mock_classifier()
    ):
        result = runner.invoke(
            app, ["classify", "--json", _CLASSIFY_PARAMS, "--config", str(cfg_file)]
        )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "decision" in data
    assert data["decision"] == "pass"
    assert "score" in data


def test_classify_invalid_json_exits_1(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())

    result = runner.invoke(
        app, ["classify", "--json", "not-valid-json", "--config", str(cfg_file)]
    )

    assert result.exit_code == 1
    assert "Invalid JSON" in result.output


def test_classify_classifier_load_failure_exits_1(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())

    with patch(
        "clawstrike.mcpserver.create_classifier",
        side_effect=RuntimeError("model not found"),
    ):
        result = runner.invoke(
            app, ["classify", "--json", _CLASSIFY_PARAMS, "--config", str(cfg_file)]
        )

    assert result.exit_code == 1
    assert "model not found" in result.output


# ---------------------------------------------------------------------------
# AC: `clawstrike gate` — JSON in, JSON out
# ---------------------------------------------------------------------------

_GATE_PARAMS = json.dumps(
    {
        "action_description": "run a bash script",
        "action_type": "shell_exec",
        "session_id": "session-1",
        "source_id": "user@example.com",
        "channel_type": "email_body",
    }
)


def test_gate_returns_json(tmp_path: Path) -> None:
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(tmp_path / "test.db")}})
    )

    with patch(
        "clawstrike.mcpserver.create_classifier", return_value=_mock_classifier()
    ):
        result = runner.invoke(
            app, ["gate", "--json", _GATE_PARAMS, "--config", str(cfg_file)]
        )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "recommendation" in data
    # shell_exec + email_body (low trust) → block
    assert data["recommendation"] == "block"
    assert data["action_type"] == "shell_exec"


def test_gate_invalid_json_exits_1(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())

    result = runner.invoke(
        app, ["gate", "--json", "{bad json}", "--config", str(cfg_file)]
    )

    assert result.exit_code == 1
    assert "Invalid JSON" in result.output


# ---------------------------------------------------------------------------
# US-023 AC4 — startup logs audit DB status
# ---------------------------------------------------------------------------


def test_start_logs_audit_db_created(tmp_path: Path) -> None:
    """start logs '(created)' when the audit DB is new."""
    cfg_file = write_yaml(
        tmp_path,
        minimal_config({"audit": {"db_path": str(tmp_path / "new.db")}}),
    )

    with (
        patch("clawstrike.mcpserver.mcp.run"),
        patch(
            "clawstrike.mcpserver.create_classifier", return_value=_mock_classifier()
        ),
    ):
        result = runner.invoke(app, ["start", "--config", str(cfg_file)])

    assert result.exit_code == 0
    assert "(created)" in result.output


def test_start_logs_audit_db_ready_with_event_count(tmp_path: Path) -> None:
    """start logs '(ready, X events)' for an existing audit DB."""
    from clawstrike.db import setup_audit_db

    db_path = tmp_path / "existing.db"
    setup_audit_db(db_path)  # pre-create the DB

    cfg_file = write_yaml(
        tmp_path,
        minimal_config({"audit": {"db_path": str(db_path)}}),
    )

    with (
        patch("clawstrike.mcpserver.mcp.run"),
        patch(
            "clawstrike.mcpserver.create_classifier", return_value=_mock_classifier()
        ),
    ):
        result = runner.invoke(app, ["start", "--config", str(cfg_file)])

    assert result.exit_code == 0
    assert "ready" in result.output.lower()
    assert "events" in result.output


# ---------------------------------------------------------------------------
# US-019 — `clawstrike confirm` CLI command
# ---------------------------------------------------------------------------

_CONFIRM_PARAMS = json.dumps(
    {
        "action_type": "send_email",
        "action_description": "send email to team",
        "session_id": "cli-sess",
        "source_id": "user@example.com",
        "channel_type": "email_body",
        "decision": "approve",
    }
)


def test_confirm_returns_json(tmp_path: Path) -> None:
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(tmp_path / "test.db")}})
    )

    with patch(
        "clawstrike.mcpserver.create_classifier", return_value=_mock_classifier()
    ):
        result = runner.invoke(
            app, ["confirm", "--json", _CONFIRM_PARAMS, "--config", str(cfg_file)]
        )

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "recorded"
    assert data["decision"] == "allow"
    assert data["user_decision"] == "approve"


def test_confirm_invalid_json_exits_1(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())

    result = runner.invoke(
        app, ["confirm", "--json", "not-json", "--config", str(cfg_file)]
    )

    assert result.exit_code == 1
    assert "Invalid JSON" in result.output


# ---------------------------------------------------------------------------
# US-028 — `clawstrike logs --export csv --output <path>`
# ---------------------------------------------------------------------------


def _seed_db(db_path: Path, events: list[dict]) -> None:
    """Create the audit DB and insert synthetic events for testing."""
    from clawstrike.db import setup_audit_db

    setup_audit_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        for ev in events:
            conn.execute(
                "INSERT INTO audit_events "
                "(timestamp, event_type, session_id, source_id, channel_type, "
                "decision, score, is_first_contact, trust_level, details_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ev.get("timestamp", "2026-02-26T12:00:00+00:00"),
                    ev.get("event_type", "input_classification"),
                    ev.get("session_id", "sess-1"),
                    ev.get("source_id", "user@example.com"),
                    ev.get("channel_type", "email_body"),
                    ev.get("decision", "pass"),
                    ev.get("score", 0.1),
                    ev.get("is_first_contact", 0),
                    ev.get("trust_level", "auto"),
                    ev.get("details_json", "{}"),
                ),
            )
        conn.commit()


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    """Return (headers, rows) from a CSV file."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return headers, rows


def test_logs_export_csv_creates_file(tmp_path: Path) -> None:
    """--export csv --output creates the output file with audit events."""
    db_path = tmp_path / "audit.db"
    _seed_db(db_path, [{"event_type": "input_classification", "decision": "pass"}])

    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"

    result = runner.invoke(
        app,
        ["logs", "--export", "csv", "--output", str(out), "--config", str(cfg_file)],
    )

    assert result.exit_code == 0, result.output
    assert out.exists()


def test_logs_export_csv_headers_match_audit_fields(tmp_path: Path) -> None:
    """CSV headers must match the AUDIT_EVENT_FIELDS constant."""
    from clawstrike.db import AUDIT_EVENT_FIELDS

    db_path = tmp_path / "audit.db"
    _seed_db(db_path, [{}])
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"

    runner.invoke(
        app,
        ["logs", "--export", "csv", "--output", str(out), "--config", str(cfg_file)],
    )

    headers, _ = _read_csv(out)
    assert headers == AUDIT_EVENT_FIELDS


def test_logs_export_csv_prints_event_count(tmp_path: Path) -> None:
    """On completion, stdout includes the count of exported events."""
    db_path = tmp_path / "audit.db"
    _seed_db(db_path, [{}, {}, {}])  # 3 events
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"

    result = runner.invoke(
        app,
        ["logs", "--export", "csv", "--output", str(out), "--config", str(cfg_file)],
    )

    assert result.exit_code == 0
    assert "3" in result.output
    assert str(out) in result.output


def test_logs_export_csv_overwrites_after_confirm(tmp_path: Path) -> None:
    """When the output file already exists and the user confirms, it is overwritten."""
    db_path = tmp_path / "audit.db"
    _seed_db(db_path, [{"event_type": "action_gate"}])
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"
    out.write_text("old content")  # pre-existing file

    result = runner.invoke(
        app,
        ["logs", "--export", "csv", "--output", str(out), "--config", str(cfg_file)],
        input="y\n",  # confirm overwrite
    )

    assert result.exit_code == 0
    # File should now contain CSV, not the old content.
    content = out.read_text()
    assert "id" in content  # CSV header present


def test_logs_export_csv_aborts_when_overwrite_denied(tmp_path: Path) -> None:
    """When the user declines the overwrite prompt, the command exits 0 and file is unchanged."""
    db_path = tmp_path / "audit.db"
    _seed_db(db_path, [{}])
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"
    out.write_text("original")

    result = runner.invoke(
        app,
        ["logs", "--export", "csv", "--output", str(out), "--config", str(cfg_file)],
        input="n\n",  # deny overwrite
    )

    assert result.exit_code == 0
    assert out.read_text() == "original"
    assert "Aborted" in result.output


def test_logs_no_export_flag_exits_1(tmp_path: Path) -> None:
    """Calling logs without --export should exit 1 with a helpful message."""
    cfg_file = write_yaml(tmp_path, minimal_config())

    result = runner.invoke(app, ["logs", "--config", str(cfg_file)])

    assert result.exit_code == 1
    assert "csv" in result.output.lower()


def test_logs_invalid_export_format_exits_1(tmp_path: Path) -> None:
    """An unsupported --export format exits 1."""
    cfg_file = write_yaml(tmp_path, minimal_config())
    out = tmp_path / "export.json"

    result = runner.invoke(
        app,
        ["logs", "--export", "json", "--output", str(out), "--config", str(cfg_file)],
    )

    assert result.exit_code == 1
    assert "json" in result.output.lower()


def test_logs_missing_output_exits_1(tmp_path: Path) -> None:
    """--export without --output exits 1."""
    cfg_file = write_yaml(tmp_path, minimal_config())

    result = runner.invoke(app, ["logs", "--export", "csv", "--config", str(cfg_file)])

    assert result.exit_code == 1
    assert "--output" in result.output


def test_logs_invalid_last_duration_exits_1(tmp_path: Path) -> None:
    """Invalid --last value exits 1 with an error message."""
    cfg_file = write_yaml(tmp_path, minimal_config())
    out = tmp_path / "export.csv"

    result = runner.invoke(
        app,
        [
            "logs",
            "--export",
            "csv",
            "--output",
            str(out),
            "--last",
            "bad",
            "--config",
            str(cfg_file),
        ],
    )

    assert result.exit_code == 1
    assert "bad" in result.output


def test_logs_filter_by_source(tmp_path: Path) -> None:
    """--source filters events to only those matching the source ID."""
    db_path = tmp_path / "audit.db"
    _seed_db(
        db_path,
        [
            {"source_id": "alice@example.com"},
            {"source_id": "bob@example.com"},
        ],
    )
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"

    runner.invoke(
        app,
        [
            "logs",
            "--export",
            "csv",
            "--output",
            str(out),
            "--source",
            "alice@example.com",
            "--config",
            str(cfg_file),
        ],
    )

    _, rows = _read_csv(out)
    assert all(r["source_id"] == "alice@example.com" for r in rows)
    assert len(rows) == 1


def test_logs_filter_by_event_type(tmp_path: Path) -> None:
    """--event-type filters events to only those with matching event_type."""
    db_path = tmp_path / "audit.db"
    _seed_db(
        db_path,
        [
            {"event_type": "input_classification"},
            {"event_type": "action_gate"},
            {"event_type": "input_classification"},
        ],
    )
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"

    runner.invoke(
        app,
        [
            "logs",
            "--export",
            "csv",
            "--output",
            str(out),
            "--event-type",
            "action_gate",
            "--config",
            str(cfg_file),
        ],
    )

    _, rows = _read_csv(out)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "action_gate"


def test_logs_filter_by_decision(tmp_path: Path) -> None:
    """--decision filters events to only those with the given decision."""
    db_path = tmp_path / "audit.db"
    _seed_db(
        db_path,
        [
            {"decision": "pass"},
            {"decision": "block"},
            {"decision": "pass"},
        ],
    )
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"

    runner.invoke(
        app,
        [
            "logs",
            "--export",
            "csv",
            "--output",
            str(out),
            "--decision",
            "block",
            "--config",
            str(cfg_file),
        ],
    )

    _, rows = _read_csv(out)
    assert len(rows) == 1
    assert rows[0]["decision"] == "block"


def test_logs_filter_by_last(tmp_path: Path) -> None:
    """--last filters out events older than the specified duration."""
    db_path = tmp_path / "audit.db"
    _seed_db(
        db_path,
        [
            # Old event — outside any recent window.
            {"timestamp": "2020-01-01T00:00:00+00:00", "event_type": "old_event"},
            # Recent event — within 1 hour.
            {"timestamp": "2026-02-26T12:00:00+00:00", "event_type": "recent_event"},
        ],
    )
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"

    runner.invoke(
        app,
        [
            "logs",
            "--export",
            "csv",
            "--output",
            str(out),
            "--last",
            "1h",
            "--config",
            str(cfg_file),
        ],
    )

    _, rows = _read_csv(out)
    assert all(r["event_type"] == "recent_event" for r in rows)


def test_logs_export_empty_db_creates_headers_only(tmp_path: Path) -> None:
    """Exporting from an empty (no events) DB writes just the header row."""
    db_path = tmp_path / "audit.db"
    from clawstrike.db import setup_audit_db

    setup_audit_db(db_path)
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(db_path)}})
    )
    out = tmp_path / "export.csv"

    result = runner.invoke(
        app,
        ["logs", "--export", "csv", "--output", str(out), "--config", str(cfg_file)],
    )

    assert result.exit_code == 0
    assert "0" in result.output
    headers, rows = _read_csv(out)
    assert headers  # headers are present
    assert rows == []  # no data rows


def test_logs_nonexistent_db_exports_zero_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the configured DB file does not exist, export succeeds with 0 events."""
    monkeypatch.chdir(tmp_path)  # ensure no stray clawstrike.yaml is picked up
    missing_db = tmp_path / "nonexistent.db"
    cfg_file = write_yaml(
        tmp_path, minimal_config({"audit": {"db_path": str(missing_db)}})
    )
    out = tmp_path / "export.csv"

    result = runner.invoke(
        app,
        ["logs", "--export", "csv", "--output", str(out), "--config", str(cfg_file)],
    )

    assert result.exit_code == 0
    assert "0" in result.output
