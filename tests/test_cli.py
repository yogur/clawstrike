"""Tests for the ClawStrike CLI commands (US-002 and CLI integration story)."""

from __future__ import annotations

import json
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
