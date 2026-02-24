"""ClawStrike CLI — entry point for all command-line interactions."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from clawstrike.config import ClawStrikeConfig, load_config

# ---------------------------------------------------------------------------
# Top-level app
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="clawstrike",
    help="Security guardrails layer for OpenClaw AI agents.",
    no_args_is_help=True,
)

# Shared type alias for the --config option used across commands.
_ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        "-c",
        help="Path to clawstrike.yaml configuration file.",
        show_default=True,
    ),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = Path("clawstrike.yaml")


def _load_cfg_or_defaults(config: Path) -> ClawStrikeConfig:
    """Load config from *config*, falling back to all-defaults if the default
    path is used and the file does not exist.  Exits with code 1 if an
    explicit path is missing or if validation fails.
    """
    try:
        return load_config(config)
    except FileNotFoundError:
        if config != _DEFAULT_CONFIG_PATH:
            typer.echo(f"Config file not found: {config}", err=True)
            raise typer.Exit(code=1)
        return ClawStrikeConfig()
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


# ---------------------------------------------------------------------------
# `clawstrike start`
# ---------------------------------------------------------------------------


@app.command()
def start(
    config: _ConfigOption = _DEFAULT_CONFIG_PATH,
) -> None:
    """Start the ClawStrike MCP server (skill mode: stdio transport)."""
    cfg = _load_cfg_or_defaults(config)

    if cfg.mode.value != "skill":
        typer.echo(
            f"Error: mode '{cfg.mode.value}' is not supported. "
            "Only 'skill' mode is available in the MVP.",
            err=True,
        )
        raise typer.Exit(code=1)

    if not cfg.mcp.enabled:
        typer.echo(
            "MCP server is disabled (mcp.enabled: false).\n"
            "Use `clawstrike classify`, `clawstrike gate`, and "
            "`clawstrike health` directly."
        )
        raise typer.Exit(code=0)

    # Import and configure the module-level MCP server.
    from clawstrike.mcpserver import init_server, mcp

    try:
        init_server(cfg)
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Initialize audit DB and log status (US-023 AC4).
    if cfg.audit.enabled:
        from clawstrike.db import setup_audit_db

        was_created, event_count = setup_audit_db(cfg.audit.db_path)
        if was_created:
            print(
                f"Audit log: {cfg.audit.db_path} (created)",
                file=sys.stderr,
            )
        else:
            print(
                f"Audit log: {cfg.audit.db_path} (ready, {event_count:,} events)",
                file=sys.stderr,
            )

    print(
        "ClawStrike MCP server started "
        f"(skill mode — advisory, stdio transport, "
        f"classifier={cfg.classifier.model.value})",
        file=sys.stderr,
    )

    # Blocks until the client disconnects (stdio transport).
    mcp.run(transport="stdio")


# ---------------------------------------------------------------------------
# `clawstrike classify` / `clawstrike gate` / `clawstrike health`
# ---------------------------------------------------------------------------


@app.command()
def classify(
    json_input: Annotated[
        str, typer.Option("--json", help="JSON-encoded classify parameters.")
    ],
    config: _ConfigOption = _DEFAULT_CONFIG_PATH,
) -> None:
    """Classify a message for prompt injection. Accepts and outputs JSON."""
    cfg = _load_cfg_or_defaults(config)

    try:
        params = json.loads(json_input)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    import clawstrike.mcpserver as srv

    try:
        srv.init_server(cfg)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    result = asyncio.run(srv.classify(**params))
    typer.echo(json.dumps(result))


@app.command()
def gate(
    json_input: Annotated[
        str, typer.Option("--json", help="JSON-encoded gate parameters.")
    ],
    config: _ConfigOption = _DEFAULT_CONFIG_PATH,
) -> None:
    """Evaluate a planned action and return a gating recommendation. Accepts and outputs JSON."""
    cfg = _load_cfg_or_defaults(config)

    try:
        params = json.loads(json_input)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    import clawstrike.mcpserver as srv

    try:
        srv.init_server(cfg)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    result = asyncio.run(srv.gate(**params))
    typer.echo(json.dumps(result))


@app.command()
def health(
    config: _ConfigOption = _DEFAULT_CONFIG_PATH,
) -> None:
    """Print server health as JSON (config-only, no model load)."""
    cfg = _load_cfg_or_defaults(config)
    result = {
        "status": "ok",
        "mode": cfg.mode.value,
        "classifier": cfg.classifier.model.value,
        "mcp_enabled": cfg.mcp.enabled,
    }
    typer.echo(json.dumps(result))


# ---------------------------------------------------------------------------
# Placeholder commands — implemented in later stories
# ---------------------------------------------------------------------------


@app.command()
def logs() -> None:
    """Query the audit log (US-025 – US-028)."""
    typer.echo("Not yet implemented.", err=True)
    raise typer.Exit(code=1)


@app.command()
def trust() -> None:
    """Manually trust a contact (US-014)."""
    typer.echo("Not yet implemented.", err=True)
    raise typer.Exit(code=1)


@app.command()
def block() -> None:
    """Manually block a contact (US-014)."""
    typer.echo("Not yet implemented.", err=True)
    raise typer.Exit(code=1)


@app.command()
def allowlist() -> None:
    """Manage the action allowlist (US-021)."""
    typer.echo("Not yet implemented.", err=True)
    raise typer.Exit(code=1)
