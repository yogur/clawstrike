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
- **All config fields have defaults** — `classifier.model` defaults to `MULTILINGUAL`. `ClawStrikeConfig()` with no args works. `_RootConfig.clawstrike` has `default_factory=ClawStrikeConfig` so empty/absent YAML files produce all-defaults rather than a validation error.
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
- **DB layer:** `src/clawstrike/db.py` provides `open_db(path)` async context manager (creates schema + parent dirs), `get_or_create_contact(conn, source_id, channel_type) -> (ContactRecord, bool)`, and `insert_audit_event(conn, ...)`. Tables: `contacts` and `audit_events`. `_db_path: str | None` module global in `mcpserver.py` is set by `init_server()` from `cfg.audit.db_path`; `None` means DB disabled (no-op). `init_server()` remains synchronous — no async DB init at startup.
- **First-contact trust override (US-012):** `classify` calls `get_or_create_contact()` on every request. If `is_first_contact=True`, trust level is forced to `UNTRUSTED` regardless of channel defaults. `is_first_contact: bool` is always present in `classify` responses. Tests that check `trust_level` in classify responses must account for this: first call to any source_id returns `"untrusted"`.
- **Test DB isolation:** The `cfg` fixture in `tests/test_server.py` injects `audit.db_path = str(tmp_path / "test.db")` so each test gets a fresh SQLite file. The `reset_server_config` autouse fixture resets `srv._db_path = None` in teardown. Tests that test threshold modulation with high-trust channels (like `owner_dm`) must pre-register the contact with a seed classify call before testing.
- **Interaction tracking (US-013):** `increment_interaction(conn, source_id)` and `set_contact_trust_level(conn, source_id, trust_level)` in `db.py`. `classify` calls `increment_interaction` for non-first-contact, non-blocked decisions only. Auto-promotion fires when `contact.trust_level == "auto"` AND `updated.interaction_count >= cfg.trust.auto_promote_after`; writes a `trust_update` audit event with `details.reason = "auto_promote"`. Contacts with stored trust_level `'trusted'` or `'blocked'` are skipped (the `== "auto"` guard handles this). `contact` (the `ContactRecord`) is captured from phase-1 `get_or_create_contact` and used in phase-2 to check the pre-increment trust_level.
- **DB helper pattern in tests:** `_get_contact_from_db(db_path, source_id)` and `_get_audit_events(db_path, event_type=None)` are local async helpers in `test_server.py` for querying the SQLite DB directly to verify post-classify state.
- **Gating engine:** `src/clawstrike/gating.py` exposes two pure functions: `classify_action(action_type) -> (risk_level, reason)` (matches action_type against `_TAXONOMY`, defaults to `"high"` for unknowns — fail-safe) and `apply_decision_matrix(risk_level, trust_level) -> recommendation` (returns `"allow"`, `"block"`, or `"prompt_user"` from the PRD Section 4.3.2 matrix). Both are tested in `tests/test_gating.py` without any mocking. The `gate` tool in `mcpserver.py` calls both functions and writes an `action_gate` audit event with `details: {action_type, action_description, risk_level, recommendation}`.
- **gate tool audit event:** Uses `event_type="action_gate"`, stores `decision` as the recommendation string, `trust_level` as channel-resolved trust, and `details_json` for action_type/risk_level/recommendation. Does NOT do contact lookup (unlike `classify`) — trust is resolved purely from channel_type.
- **CLI integration pattern:** `src/clawstrike/cli.py` exposes `classify --json '...'`, `gate --json '...'`, and `health` as one-shot JSON commands. They call `srv.init_server(cfg)` then `asyncio.run(srv.classify(**params))` directly — `@mcp.tool` functions are plain async callables. `health` is config-only (no `init_server`, no model load). The `_load_cfg_or_defaults(path)` helper falls back to `ClawStrikeConfig()` when the default `clawstrike.yaml` is absent; explicit `--config` to a missing file exits 1.
- **`McpConfig.enabled: bool = True`** — `TransportMode` enum was removed. Set `mcp.enabled: false` to have `clawstrike start` exit 0 with a message instead of starting a listener. CLI commands (`classify`, `gate`, `health`) are unaffected by this flag.
- **CLI test isolation:** Use `monkeypatch.chdir(tmp_path)` in tests that verify "no config file" fallback behavior — prevents tests from accidentally picking up the real project `clawstrike.yaml`. CLI tests for `classify`/`gate` still mock `clawstrike.mcpserver.create_classifier` to avoid model downloads.
- **Audit schema (US-023/024):** `audit_events` has `label TEXT`, `raw_input_hash TEXT`, `raw_input_snippet TEXT` columns (added in US-023). `_apply_migrations(conn)` (async) and `_apply_migrations_sync(conn)` (stdlib sqlite3) use `PRAGMA table_info` to add missing columns on older DBs. `open_db()` calls `_apply_migrations` after DDL. `setup_audit_db(path) -> (was_created, event_count)` is the sync startup initializer used by `clawstrike start`.
- **Classify audit event (US-024):** `insert_audit_event` accepts optional `label`, `raw_input_hash`, `raw_input_snippet`. `classify` always writes `raw_input_hash = SHA-256(text)`. `raw_input_snippet = text[:max_chars]` when `cfg.audit.log_raw_input=True` (default), `None` otherwise. `details` dict includes `model`, `threshold_applied: {block, flag}`, and `elevated_scrutiny: bool`.
- **Startup audit log (US-023 AC4):** `clawstrike start` calls `setup_audit_db` after `init_server` (when `cfg.audit.enabled`) and logs `"Audit log: <path> (created)"` or `"(ready, N events)"` to stderr. Default `audit.db_path = ./data/audit.db` — `data/` is gitignored.
