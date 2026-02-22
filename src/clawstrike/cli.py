"""ClawStrike CLI — entry point for all command-line interactions."""

from __future__ import annotations

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

    typer.echo(
        f"Config loaded (mode={cfg.mode}). MCP server startup not yet implemented."
    )
    raise typer.Exit(code=1)
