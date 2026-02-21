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
