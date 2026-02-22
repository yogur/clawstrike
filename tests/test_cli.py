"""Tests for the `clawstrike start` CLI command (US-002)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

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
    base: dict = {"clawstrike": {"classifier": {"model": "prompt-guard-2"}}}
    if extra:
        base["clawstrike"].update(extra)
    return base


# ---------------------------------------------------------------------------
# AC: missing config file → exit 1, filename in output
# ---------------------------------------------------------------------------


def test_start_missing_config_exits_1(tmp_path: Path) -> None:
    result = runner.invoke(app, ["start", "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 1
    assert "missing.yaml" in result.output


# ---------------------------------------------------------------------------
# AC: invalid config → exit 1
# ---------------------------------------------------------------------------


def test_start_invalid_config_exits_1(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, {"clawstrike": {"classifier": {}}})
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


def test_start_skill_mode_logs_banner_and_runs(tmp_path: Path) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config())

    with patch("clawstrike.mcpserver.mcp.run") as mock_run:
        result = runner.invoke(app, ["start", "--config", str(cfg_file)])

    assert result.exit_code == 0
    assert "skill mode" in result.output.lower()
    assert "stdio" in result.output.lower()
    mock_run.assert_called_once_with(transport="stdio")


@pytest.mark.parametrize("model", ["prompt-guard-2", "deberta-v3"])
def test_start_banner_includes_classifier_model(tmp_path: Path, model: str) -> None:
    cfg_file = write_yaml(tmp_path, minimal_config({"classifier": {"model": model}}))

    with patch("clawstrike.mcpserver.mcp.run"):
        result = runner.invoke(app, ["start", "--config", str(cfg_file)])

    assert result.exit_code == 0
    assert model in result.output


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
