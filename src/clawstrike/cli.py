"""ClawStrike CLI — entry point for all command-line interactions."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from clawstrike.config import load_config

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
# `clawstrike start`
# ---------------------------------------------------------------------------


@app.command()
def start(
    config: _ConfigOption = Path("clawstrike.yaml"),
) -> None:
    """Start the ClawStrike MCP server (skill mode: stdio transport)."""
    try:
        cfg = load_config(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if cfg.mode.value != "skill":
        typer.echo(
            f"Error: mode '{cfg.mode.value}' is not supported. "
            "Only 'skill' mode is available in the MVP.",
            err=True,
        )
        raise typer.Exit(code=1)

    # Import and configure the module-level MCP server.
    from clawstrike.mcpserver import init_server, mcp

    init_server(cfg)

    print(
        "ClawStrike MCP server started "
        f"(skill mode — advisory, stdio transport, "
        f"classifier={cfg.classifier.model.value})",
        file=sys.stderr,
    )

    # Blocks until the client disconnects (stdio transport).
    mcp.run(transport="stdio")


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
