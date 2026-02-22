# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ClawStrike** is a security guardrails layer for OpenClaw (an open-source personal AI agent). It detects prompt injection attacks, enforces source-aware trust policies, gates high-risk actions before execution, and provides a full audit trail — integrated into the OpenClaw runtime via an MCP server.

The MVP ships **Skill Mode only**: ClawStrike runs as a local MCP server (stdio transport via `fastmcp`). An OpenClaw skill instructs the LLM to call ClawStrike's `classify`, `gate`, and `health` MCP tools before acting. All guardrail decisions in this phase are **advisory** — the LLM is instructed to comply but not mechanically forced. Enforcement-grade interception (Proxy Mode) ships in Phase 1.5.

The full product specification is in `tasks/clawstrike-prd.md` and user stories in `tasks/clawstrike-user-stories.md`.

## Technical Stack

- **Language:** Python 3.12+
- **Package manager:** `uv`
- **MCP server:** `fastmcp` v3 (stdio transport)
- **Classifier inference:** Hugging Face Transformers + PyTorch
- **Storage:** SQLite via `aiosqlite`
- **CLI:** Typer
- **Config:** Pydantic v2 + PyYAML
- **Logging:** structlog
- **Testing:** pytest + pytest-asyncio
- **Lint/format:** ruff
- **Project layout:** `src/clawstrike/` (src layout)

## Development Commands

```bash
uv run pytest                # run all tests
uv run ruff check --fix      # lint
uv run ruff format           # format (always use this, never manually reformat)
```

## CLI Structure

The CLI entry point is `src/clawstrike/cli.py`. It exports `app` (a Typer instance), which is registered as the `clawstrike` console script in `pyproject.toml`.

- All commands accept `--config / -c PATH` via the shared `_ConfigOption` alias.
- Commands call `load_config()` and surface `FileNotFoundError`/`ValueError` to stderr with exit code 1.

## Codebase Patterns

- **Enum classes:** Use `StrEnum` (Python 3.11+) — ruff UP042 will flag `(str, Enum)` style.
- **Pydantic models:** Use `ConfigDict(extra="allow")` on all config models so unknown fields land in `model_extra` for warning logic. Use `extra="ignore"` only if warnings are not needed.
- **Config loading:** `load_config(path)` in `src/clawstrike/config.py` is the single entry point. It returns `ClawStrikeConfig`.
- **Unknown field warnings:** Implemented via `_collect_extra_paths()` which recursively compares raw YAML dict against model field names using the `_NESTED` registry. When adding new nested config models, add them to `_NESTED`.
- **`classifier.model` is the only required field** — all others have defaults.
- **ruff line length:** 88 chars. Use `ruff format` to fix; never manually wrap lines.
