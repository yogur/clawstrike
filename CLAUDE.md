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
- **MCP server pattern:** `src/clawstrike/mcpserver.py` holds a module-level `mcp = FastMCP(...)` instance with `_config: ClawStrikeConfig | None`, `_classifier: BaseClassifier | None`, and `_elevated_sessions: set[str]` globals. Call `init_server(cfg)` to inject config, load the classifier, and reset session state. Tests mock `create_classifier` via `patch("clawstrike.mcpserver.create_classifier", ...)` in the `autouse` fixture, which **yields the mock classifier** so individual tests can override `mock_clf.classify.return_value`. Teardown resets all three globals (`_config = None`, `_classifier = None`, `_elevated_sessions.clear()`).
- **Session elevation tracking:** When `classify` returns `"flag"` and a non-empty `session_id` is provided, the session is inserted into `_elevated_sessions`. The `gate` tool reads this set to include `elevated_scrutiny: bool` in its response. `init_server()` resets the set to a fresh empty set.
- **classify response shapes:** block → adds `reason: "prompt_injection_detected"`; flag → adds `elevated_scrutiny: True`; pass → no extra fields. The optional `session_id: str = ""` parameter on `classify` controls session tagging (empty string disables it).
- **Classifier module:** `src/clawstrike/classifier.py` contains `BaseClassifier` ABC, `PromptGuardClassifier` implementation, `ClassifierResult` dataclass, and `create_classifier(model)` factory. HF model IDs are in `_MODEL_IDS`. The real models require `HF_TOKEN` and Meta license acceptance; tests always mock `create_classifier` to avoid downloads.
- **Classifier chunking:** `PromptGuardClassifier.classify()` handles texts longer than `_MAX_TOKENS` (512) via a sliding-window strategy: tokenize the full text without truncation, split token IDs into non-overlapping 512-token chunks, decode each chunk back to text, then run a **single batched forward pass** via `_classify_chunks()`. The final score is `max(probs[:, 1])` across all chunks (pessimistic/fail-closed). Short texts (≤ 512 tokens) take a fast path with no chunking.
- **Classifier test helper:** `_make_classifier_with_logits(logits_list, *, body_token_count=5)` in `tests/test_classifier.py`. `body_token_count` controls whether classify() takes the fast path (≤ 512) or the chunked path. The mock tokenizer uses `side_effect = [count_call_result, batch_call_result]` to handle the two distinct `__call__` invocations; `mock_tokenizer.decode.return_value` covers chunk decoding.
- **Classifier `init_server` failure:** If `create_classifier()` raises `RuntimeError` (model not found, bad token, etc.), `init_server()` propagates it. The CLI `start` command catches `RuntimeError` and exits with code 1.
- **FastMCP v3 tool decorator:** Use `@mcp.tool` (no parentheses) for simple tools. Tool return types are `dict[str, Any]` for flexible schemas.
- **FastMCP v3 testing:** Use `result = await mcp.call_tool("tool_name", {...})` for direct in-process testing. Access results via `result.structured_content` (typed dict). `RuntimeError` raised inside tools is wrapped in `fastmcp.exceptions.ToolError` at the protocol boundary — match on `ToolError` in tests, not `RuntimeError`.
- **fastmcp run support:** Set `CLAWSTRIKE_CONFIG=/path/to/clawstrike.yaml` env var, then run `fastmcp run src/clawstrike/mcpserver.py`. The module auto-initializes via the env var on import.
- **Trust engine:** `src/clawstrike/trust.py` exposes two pure functions: `resolve_trust_level(channel_type, trust_cfg) -> TrustLevel` (looks up `trust_cfg.channel_defaults`, defaults to `UNTRUSTED` for unknown channels) and `compute_effective_thresholds(base_block, base_flag, trust_level, modifiers) -> tuple[float, float]` (applies additive modifier from `trust_cfg.threshold_modifiers`, clamps to [0.0, 1.0]). Both functions are tested in `tests/test_trust.py` without any MCP/server mocking.
- **classify response fields (post US-011/015):** All `classify` responses now include `trust_level` (str value of resolved `TrustLevel`) and `threshold_applied: {block: float, flag: float}` (effective thresholds after modulation). The decision logic uses effective thresholds, not base thresholds. When writing tests that check block/flag/pass decisions, account for the channel_type modifier (e.g., `email_body` is LOW trust → block threshold drops from 0.92 to 0.87).
