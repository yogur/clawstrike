"""ClawStrike CLI — entry point for all command-line interactions."""

from __future__ import annotations

import asyncio
import csv
import json
import re
import sys
from datetime import UTC, datetime, timedelta
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

    # Initialize audit DB and log status.
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
def confirm(
    json_input: Annotated[
        str, typer.Option("--json", help="JSON-encoded confirm parameters.")
    ],
    config: _ConfigOption = _DEFAULT_CONFIG_PATH,
) -> None:
    """Record a user confirmation decision for a gated action. Accepts and outputs JSON."""
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

    result = asyncio.run(srv.confirm(**params))
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


_DURATION_RE = re.compile(r"^(\d+)([mhd])$")


def _parse_last_duration(value: str) -> timedelta:
    """Parse a duration string like '24h', '7d', or '30m' into a timedelta."""
    m = _DURATION_RE.match(value.strip())
    if not m:
        raise ValueError(
            f"Invalid duration '{value}'. Use format: <N>m, <N>h, or <N>d "
            "(e.g. 30m, 24h, 7d)."
        )
    amount, unit = int(m.group(1)), m.group(2)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


@app.command()
def logs(
    export: Annotated[
        str | None,
        typer.Option(
            "--export", help="Export format. Currently only 'csv' is supported."
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output file path for --export."),
    ] = None,
    last: Annotated[
        str | None,
        typer.Option(
            "--last", help="Filter to events in the last N units (e.g. 24h, 7d, 30m)."
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option("--source", help="Filter by source ID (exact match)."),
    ] = None,
    event_type: Annotated[
        str | None,
        typer.Option("--event-type", help="Filter by event type."),
    ] = None,
    decision: Annotated[
        str | None,
        typer.Option("--decision", help="Filter by decision value."),
    ] = None,
    config: _ConfigOption = _DEFAULT_CONFIG_PATH,
) -> None:
    """Export audit log events to CSV."""
    if export is None:
        typer.echo(
            "Specify --export csv --output <path> to export audit events.", err=True
        )
        raise typer.Exit(code=1)
    if export.lower() != "csv":
        typer.echo(
            f"Unsupported export format '{export}'. Only 'csv' is supported.", err=True
        )
        raise typer.Exit(code=1)
    if output is None:
        typer.echo("--output is required when using --export.", err=True)
        raise typer.Exit(code=1)

    cfg = _load_cfg_or_defaults(config)

    # Parse --last duration.
    since_dt: datetime | None = None
    if last is not None:
        try:
            since_dt = datetime.now(UTC) - _parse_last_duration(last)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    # Prompt for overwrite when the output file already exists.
    if output.exists():
        confirmed = typer.confirm(f"File '{output}' already exists. Overwrite?")
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(code=0)

    from clawstrike.db import AUDIT_EVENT_FIELDS, query_audit_events

    events = query_audit_events(
        cfg.audit.db_path,
        since=since_dt,
        source_id=source,
        event_type=event_type,
        decision=decision,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=AUDIT_EVENT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        # Replace None with "" so the CSV doesn't contain literal "None" strings.
        for row in events:
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})

    count = len(events)
    typer.echo(f"Exported {count} events to {output}")


@app.command()
def trust() -> None:
    """Manually trust a contact."""
    typer.echo("Not yet implemented.", err=True)
    raise typer.Exit(code=1)


@app.command()
def block() -> None:
    """Manually block a contact."""
    typer.echo("Not yet implemented.", err=True)
    raise typer.Exit(code=1)


_allowlist_app = typer.Typer(help="Manage the action allowlist (read-only).")
app.add_typer(_allowlist_app, name="allowlist")


@_allowlist_app.command("list")
def allowlist_list(
    config: _ConfigOption = _DEFAULT_CONFIG_PATH,
) -> None:
    """List all allowlist rules (static config rules and dynamic DB rules)."""
    cfg = _load_cfg_or_defaults(config)

    from clawstrike.db import list_allowlist_rules

    db_rules = list_allowlist_rules(cfg.audit.db_path)
    static_rules = cfg.action_gating.static_rules

    rows: list[dict[str, str]] = []
    for rule in static_rules:
        rows.append(
            {
                "source": "config",
                "id": "-",
                "action_type": rule.action_type,
                "source_scope": rule.source_scope,
                "created": "(static)",
            }
        )
    for rule in db_rules:
        rows.append(
            {
                "source": "db",
                "id": str(rule["id"]),
                "action_type": rule["action_type"],
                "source_scope": rule["source_scope"],
                "created": rule["created_at"] or "",
            }
        )

    if not rows:
        typer.echo("No allowlist rules found.")
        return

    headers = ["Source", "ID", "Action Type", "Source Scope", "Created"]
    keys = ["source", "id", "action_type", "source_scope", "created"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, key in enumerate(keys):
            widths[i] = max(widths[i], len(row[key]))

    typer.echo("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    typer.echo("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        typer.echo("  ".join(row[key].ljust(widths[i]) for i, key in enumerate(keys)))
