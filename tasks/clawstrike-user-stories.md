# ClawStrike — User Stories

## Supporting Document for ClawStrike PRD v0.1

**Date:** February 2026
**Status:** Pre-MVP

---

## Epic 1: Setup & Configuration

### US-001: YAML Configuration Loading [DONE]

**Description:** As a ClawStrike user, I want to define all settings in a single `clawstrike.yaml` file so that I can configure the system without touching code.

**Acceptance Criteria:**
- [x] ClawStrike reads configuration from `clawstrike.yaml` in the working directory or a path specified via `--config` CLI flag
- [x] Missing required fields (e.g., `classifier.model`) cause a startup error with a message naming the missing field
- [x] Unknown fields are ignored with a warning logged to stderr
- [x] Default values are applied for all optional fields as specified in the PRD Configuration Reference (Section 7)
- [x] Configuration is validated at startup: invalid enum values (e.g., `model: "invalid"`) produce an error naming the field and listing valid options
- [x] Phase 1.5 fields (`proxy` block) are parsed and validated even when `mode: "skill"`, so config errors are caught early

---

### US-002: Skill Mode MCP Server Startup [DONE]

**Description:** As a ClawStrike user, I want to start ClawStrike as a local MCP server so that the ClawStrike OpenClaw skill can call it for classification and gating recommendations.

**Acceptance Criteria:**
- [x] Running `clawstrike start` with `mode: "skill"` starts a fastmcp MCP server using stdio transport
- [x] Startup logs confirm the server is running (e.g., `ClawStrike MCP server started (skill mode — advisory, stdio transport)`)
- [x] The server exposes a `classify` MCP tool that accepts `text`, `source_id`, and `channel_type` parameters and returns a classification result
- [x] The server exposes a `gate` MCP tool that accepts `action_description`, `action_type`, `session_id`, `source_id`, and `channel_type` parameters and returns a gating recommendation
- [x] The server exposes a `health` MCP tool that returns `{"status": "ok", "mode": "skill", "classifier": "<model_name>"}`
- [x] The MCP server can also be started directly via `fastmcp run` for development/testing

---

### US-003: ClawStrike OpenClaw Skill Definition

**Description:** As a ClawStrike user, I want a ready-to-install OpenClaw skill file so that I can integrate ClawStrike with my OpenClaw instance without writing skill config myself.

**Acceptance Criteria:**
- [ ] The repository includes a `skills/clawstrike/` directory containing a complete OpenClaw skill definition
- [ ] The skill's system prompt instructs the LLM to: (1) call the `classify` MCP tool with all inbound messages before acting, (2) call the `gate` MCP tool with planned actions before executing, and (3) comply with block/flag/prompt recommendations
- [ ] The skill definition configures the ClawStrike MCP server connection (stdio transport)
- [ ] The skill can be installed into OpenClaw via standard ClawHub installation or manual file copy
- [ ] A README in the skill directory documents installation steps including MCP server setup

---

### US-004: Graceful Shutdown [POSTPONED]

**Description:** As a ClawStrike user, I want ClawStrike to shut down cleanly so that in-flight requests complete and no data is lost.

**Acceptance Criteria:**
- [ ] Sending SIGTERM or SIGINT initiates graceful shutdown
- [ ] In-flight API requests are allowed to complete (up to a 10-second timeout)
- [ ] Open database connections (audit log, contact registry) are closed cleanly
- [ ] Shutdown logs confirm completion (e.g., `ClawStrike shut down. X requests drained.`)

---

## Epic 2: Prompt Injection Detection

### US-005: Classify Input with Multilingual Model [DONE]

**Description:** As a ClawStrike user, I want to run inbound messages through Llama Prompt Guard 2 86M so that prompt injection attacks are detected across multiple languages.

**Acceptance Criteria:**
- [x] When `classifier.model` is set to `"multilingual"`, the Llama Prompt Guard 2 86M model is loaded at startup
- [x] The `classify` MCP tool passes the input text to the model and returns a `ClassifierResult` containing `score` (0.0–1.0), `label` (`"benign"` | `"injection"` | `"jailbreak"`), `model` identifier, and `latency_ms`
- [x] Classification completes in <100ms p95 on the developer's test machine (measured and logged)
- [x] If the model fails to load, startup fails with a descriptive error

---

### US-006: Classify Input with English-Only Model [DONE]

**Description:** As a ClawStrike user deploying in an English-only environment, I want to use Llama Prompt Guard 2 22M so that I get a lower-memory-footprint classifier option.

**Acceptance Criteria:**
- [x] When `classifier.model` is set to `"english-only"`, the Llama Prompt Guard 2 22M model is loaded at startup
- [x] Returns the same `ClassifierResult` schema as the multilingual model
- [x] Classification completes in <100ms p95 on the developer's test machine
- [x] If the model fails to load, startup fails with a descriptive error

---

### US-007: Custom Model Support *(Deferred — Phase 2)*

**Description:** As an advanced user, I want to plug in my own fine-tuned classifier so that I can tailor detection to my specific threat profile.

> **Status: Deferred to Phase 2.** Custom model support is out of scope for the MVP. The `BaseClassifier` interface is defined in the MVP to keep the extension point open.

---

### US-008: Block Threshold — Rejection Recommendation

**Description:** As a ClawStrike user, I want messages scoring above the block threshold to be flagged for rejection so that high-confidence injection attempts are caught.

**Acceptance Criteria:**
- [x] When classifier score ≥ `threshold.block`, the `classify` MCP tool returns `{"decision": "block", "score": <float>, "label": "...", "reason": "prompt_injection_detected"}`
- [ ] The ClawStrike skill, upon receiving a block recommendation, instructs the LLM to reject the input and not act on it
- [ ] A notification is sent to the user via the originating channel stating the input was flagged and from which source
- [ ] The event is written to the audit log with all fields populated (classifier score, source metadata, decision: "block")

---

### US-009: Flag Threshold — Elevated Scrutiny

**Description:** As a ClawStrike user, I want messages scoring between the flag and block thresholds to be marked for elevated scrutiny so that suspicious-but-uncertain inputs get tighter action gating recommendations.

**Acceptance Criteria:**
- [x] When classifier score ≥ `threshold.flag` and < `threshold.block`, the `classify` MCP tool returns `{"decision": "flag", "score": <float>, "elevated_scrutiny": true}`
- [x] The session is tagged internally as `elevated_scrutiny`
- [x] Subsequent `gate` tool calls for this session use the next-stricter trust tier for gating recommendations (e.g., `medium` trust → treated as `low`) *(elevation surfaced in gate response; trust-downgrade logic deferred to US-022)*
- [ ] The audit log records the flag event with `decision: "flag"` and the elevated scrutiny tag

---

### US-010: Benign Input Passthrough

**Description:** As a ClawStrike user, I want messages scoring below the flag threshold to pass through with no interference so that normal usage is unaffected.

**Acceptance Criteria:**
- [x] When classifier score < `threshold.flag`, the `classify` MCP tool returns `{"decision": "pass", "score": <float>}`
- [x] No user notification is generated
- [ ] The event is still written to the audit log with `decision: "pass"`
- [x] The `classify` tool call completes in <110ms total (classification + MCP transport overhead)

---

## Epic 3: Source-Aware Trust Tiers

### US-011: Channel Trust Level Resolution [DONE]

**Description:** As a ClawStrike user, I want each inbound message to be assigned a base trust level based on its channel type so that inputs from different sources are treated with appropriate scrutiny.

**Acceptance Criteria:**
- [x] The `classify` and `gate` MCP tools accept a `channel_type` parameter
- [x] The channel type is matched against `trust.channel_defaults` in the config
- [x] If the channel type is not in the config, it defaults to `untrusted`
- [x] The resolved trust level is included in the tool response and available for threshold modulation and gating decisions

---

### US-012: Contact Registry — First Contact Detection ✅ DONE

**Description:** As a ClawStrike user, I want ClawStrike to detect when a message comes from a never-before-seen source so that first contacts receive maximum scrutiny.

**Acceptance Criteria:**
- [x] On each `classify` tool call, the `source_id` is looked up in the `contacts` SQLite table
- [x] If no matching row exists, a new record is created with `trust_level: 'auto'`, `interaction_count: 1`, and current timestamps
- [x] The source is treated as `untrusted` for this session regardless of channel defaults
- [x] The tool response includes `"is_first_contact": true`
- [x] The audit log records `is_first_contact: true` for this event

---

### US-013: Contact Registry — Interaction Tracking & Auto-Promotion ✅ DONE

**Description:** As a ClawStrike user, I want contacts to be automatically promoted to the channel's default trust level after repeated safe interactions so that trusted regulars aren't permanently treated as strangers.

**Acceptance Criteria:**
- [x] Each non-blocked interaction from a known contact increments `interaction_count` and updates `last_seen`
- [x] When `interaction_count` reaches `trust.auto_promote_after` (default: 5) and `trust_level` is `'auto'`, the contact's effective trust is promoted to the channel's default trust level
- [x] An audit log entry records the promotion event with `event_type: "trust_update"`
- [x] Contacts with a manual override (`'trusted'` or `'blocked'`) are never auto-promoted

---

### US-014: Manual Contact Trust Override

**Description:** As a ClawStrike user, I want to manually trust or block specific contacts so that I can override automatic trust decisions.

**Acceptance Criteria:**
- [ ] Running `/clawstrike trust <source_id>` via the configured channel sets the contact's `trust_level` to `'trusted'`
- [ ] Running `/clawstrike block <source_id>` sets the contact's `trust_level` to `'blocked'`
- [ ] Blocked contacts have all their inputs immediately returned with a block recommendation without classification
- [ ] Trusted contacts use the `high` trust tier regardless of channel defaults
- [ ] Each manual override is recorded in the audit log with `event_type: "trust_update"` and `created_by: "owner"`
- [ ] Running `/clawstrike trust` or `/clawstrike block` with a non-existent `source_id` returns an error: `"Contact not found. Source must have at least one prior interaction."`

---

### US-015: Trust-Modulated Classifier Thresholds [DONE]

**Description:** As a ClawStrike user, I want the classifier's block and flag thresholds to adjust based on the source's trust level so that untrusted sources face stricter scrutiny and trusted sources experience fewer false positives.

**Acceptance Criteria:**
- [x] After resolving the source's trust level, the effective thresholds are computed by applying the configured `threshold_modifiers` to the base thresholds
- [x] Example: base `block` = 0.92, untrusted modifier = -0.10 → effective `block` = 0.82
- [x] The effective thresholds (not the base thresholds) are used for the block/flag/pass decision
- [ ] The audit log records both the base thresholds and the effective thresholds applied *(deferred — audit log ships in a later story)*

---

### US-016: Content-Source Mismatch Detection

**Description:** As a ClawStrike user, I want ClawStrike to flag anomalous behavior when a high-trust contact sends content that looks like prompt injection so that potential account compromise is caught.

**Acceptance Criteria:**
- [ ] If a contact has effective trust level `high` or `medium` AND the classifier score exceeds the *base* `flag` threshold (before trust modulation), a content-source mismatch is detected
- [ ] The session's effective trust level is temporarily downgraded to `low` for all subsequent gating recommendations in this session
- [ ] The audit log records the mismatch event with `event_type: "trust_update"` and a `reason: "content_source_mismatch"` field
- [ ] The downgrade does not persist beyond the current session — the contact's stored trust level is unchanged

---

## Epic 4: Action Gating (Advisory)

### US-017: Advisory Action Classification via API ✅ DONE

**Description:** As a ClawStrike user, I want the `gate` MCP tool to classify LLM-reported actions by risk level so that the skill can advise the LLM on whether to proceed.

**Acceptance Criteria:**
- [x] The `gate` MCP tool accepts `action_description`, `action_type`, `session_id`, `source_id`, and `channel_type` parameters
- [x] The `action_type` is matched against the hardcoded action risk taxonomy (PRD Section 4.3.1) and assigned a risk level: `critical`, `high`, `medium`, or `low`
- [x] If `action_type` matches no taxonomy entry, it defaults to `high` (fail-safe)
- [x] The tool returns `{"risk_level": "...", "recommendation": "allow|block|prompt_user", "trust_level": "...", "reason": "..."}`

---

### US-018: Gating Recommendation Matrix ✅ DONE

**Description:** As a ClawStrike user, I want the gating recommendation to reflect both the action's risk and the session's trust level so that the skill gives appropriate advice to the LLM.

**Acceptance Criteria:**
- [x] The recommendation matrix from PRD Section 4.3.2 is implemented:
  - Critical + High Trust → `prompt_user`; Critical + Medium/Low/Untrusted → `block`
  - High + High Trust → `allow`; High + Medium → `prompt_user`; High + Low/Untrusted → `block`
  - Medium + High/Medium → `allow`; Medium + Low → `prompt_user`; Medium + Untrusted → `block`
  - Low + High/Medium/Low → `allow`; Low + Untrusted → `prompt_user`
- [x] The audit log records each gating recommendation with action type, risk level, trust level, and recommendation

---

### US-019: User Confirmation Prompt for Gated Actions

**Description:** As a ClawStrike user, I want to receive a confirmation prompt when the system recommends user approval so that I stay in control of risky actions.

**Acceptance Criteria:**
- [ ] When the `gate` MCP tool returns `recommendation: "prompt_user"`, the ClawStrike skill instructs the LLM to ask the owner for confirmation before proceeding
- [ ] The confirmation message includes: action description, source identifier, channel type, trust level, and risk level
- [ ] The user can respond with "approve" or "deny" (or single-character shortcuts "a" / "d")
- [ ] If the user denies, the skill instructs the LLM to abandon the action
- [ ] The audit log records the user's decision with `event_type: "action_gate"` and `decision: "allow"` or `decision: "deny"`

---

### US-020: Action Allowlist Creation from Approval

**Description:** As a ClawStrike user, I want the option to permanently allow a type of action when approving it so that I don't get prompted repeatedly for routine workflows.

**Acceptance Criteria:**
- [ ] When `action_gating.allowlist_learning` is `true`, the confirmation prompt includes an additional option: "always allow" (or shortcut "aa")
- [ ] Selecting "always allow" creates an entry in the `action_allowlist` table with the `action_type` and `source_scope` set to the current source
- [ ] An additional option "always allow globally" ("aag") creates an entry with `source_scope: "global"`
- [ ] On subsequent `gate` tool calls, the allowlist is checked before applying the decision matrix — allowlisted actions return `recommendation: "allow"` immediately
- [ ] The audit log records the allowlist creation event and all subsequent auto-allows that reference the rule

---

### US-021: Action Allowlist Management via CLI

**Description:** As a ClawStrike user, I want to view and remove allowlist rules via the CLI so that I can audit and revoke permissions I've previously granted.

**Acceptance Criteria:**
- [ ] `clawstrike allowlist list` prints all allowlist rules in a table format showing ID, action type, action pattern, source scope, and creation date
- [ ] `clawstrike allowlist remove <id>` deletes the rule with the given ID
- [ ] `clawstrike allowlist clear` deletes all rules after a confirmation prompt ("This will remove X rules. Confirm? y/n")
- [ ] Removals are recorded in the audit log with `event_type: "config_change"`

---

### US-022: Elevated Scrutiny Tightens Gating Recommendations

**Description:** As a ClawStrike user, I want flagged sessions (elevated scrutiny from prompt injection detection) to face stricter gating recommendations so that suspicious inputs can't easily trigger risky actions.

**Acceptance Criteria:**
- [ ] When a session is tagged `elevated_scrutiny` (from US-009), the effective trust level for gating is downgraded by one tier (high → medium, medium → low, low → untrusted)
- [ ] The downgrade stacks with content-source mismatch downgrades (US-016): if both apply, both downgrades are applied in sequence
- [ ] The effective trust tier used for gating is recorded in the audit log alongside the original trust tier

---

## Epic 5: Audit Log

### US-023: Audit Log Database Initialization

**Description:** As a ClawStrike user, I want the audit log database to be created automatically on first startup so that logging works without manual setup.

**Acceptance Criteria:**
- [ ] On startup, if the SQLite database at `audit.db_path` does not exist, it is created with the required schema
- [ ] If the database exists but has an outdated schema, a migration is applied automatically
- [ ] The audit table schema supports all event fields from PRD Section 4.4 (timestamp, event_type, session_id, source, classifier, action_gate, raw_input_hash, raw_input_snippet)
- [ ] Startup logs confirm audit database status (e.g., `Audit log: ./data/audit.db (created)` or `(ready, 1,432 events)`)

---

### US-024: Audit Event Writing

**Description:** As a ClawStrike user, I want every security-relevant decision to be recorded in the audit log so that I have a forensic trail for incident response.

**Acceptance Criteria:**
- [ ] Input classification events are logged with classifier model, score, label, effective thresholds, and decision
- [ ] Action gating events are logged with action type, risk level, trust level, recommendation, and user decision (if prompted)
- [ ] Trust update events are logged with source ID, previous trust level, new trust level, and reason
- [ ] Config change events are logged with the field changed and old/new values
- [ ] All events include timestamp (UTC), session ID, and source metadata
- [ ] When `audit.log_raw_input` is `true`, the first N characters of input are stored (N = `raw_input_max_chars`). When `false`, only the SHA-256 hash is stored.

---

### US-025: Audit Log CLI — Query by Time Range

**Description:** As a ClawStrike user, I want to query audit logs by time range so that I can review recent activity.

**Acceptance Criteria:**
- [ ] `clawstrike logs --last 24h` returns all events from the past 24 hours
- [ ] `clawstrike logs --last 7d` returns all events from the past 7 days
- [ ] Supported duration units: `m` (minutes), `h` (hours), `d` (days)
- [ ] Results are printed in reverse chronological order with one line per event showing timestamp, event type, source, and decision
- [ ] If no events match, output reads `No events found for the specified time range.`

---

### US-026: Audit Log CLI — Query by Source

**Description:** As a ClawStrike user, I want to query audit logs by source identifier so that I can investigate all activity from a specific contact.

**Acceptance Criteria:**
- [ ] `clawstrike logs --source "user@example.com"` returns all events with that source ID
- [ ] Partial matching is supported: `--source "user@"` matches all source IDs starting with `user@`
- [ ] Results include all event types (classification, action gating, trust updates) for the matched source

---

### US-027: Audit Log CLI — Query by Event Type and Decision

**Description:** As a ClawStrike user, I want to filter audit logs by event type and decision so that I can quickly find blocks, flags, or trust changes.

**Acceptance Criteria:**
- [ ] `clawstrike logs --event-type action_gate` returns only action gating events
- [ ] `clawstrike logs --decision block` returns only events where the decision was "block"
- [ ] Filters can be combined: `clawstrike logs --event-type action_gate --decision block --last 7d`
- [ ] Valid event types: `input_classification`, `action_gate`, `trust_update`, `config_change`
- [ ] Valid decisions: `pass`, `flag`, `block`, `allow`, `deny`, `prompt_user`

---

### US-028: Audit Log CLI — CSV Export

**Description:** As a ClawStrike user, I want to export audit logs to CSV so that I can analyze them in external tools or share them with a security team.

**Acceptance Criteria:**
- [ ] `clawstrike logs --export csv --output ./audit-export.csv` writes matching events to the specified file
- [ ] All query filters (time range, source, event type, decision) are applied before export
- [ ] CSV headers match the audit log field names
- [ ] If the output file already exists, ClawStrike prompts for overwrite confirmation
- [ ] On completion, logs the number of events exported (e.g., `Exported 247 events to ./audit-export.csv`)

---

### US-029: Audit Log Retention Cleanup

**Description:** As a ClawStrike user, I want old audit log entries to be automatically purged based on my configured retention period so that the database doesn't grow unbounded.

**Acceptance Criteria:**
- [ ] On each startup, events older than `audit.retention_days` are deleted from the database
- [ ] A startup log line reports the cleanup result (e.g., `Audit log: purged 312 events older than 90 days`)
- [ ] If `retention_days` is set to `0`, no automatic purging occurs (infinite retention)

---

## Epic 6: Phase 2 Interface Hooks (Defined in MVP, Implemented Later)

### US-030: LLM-as-Judge Hook Point in Gating Pipeline

**Description:** As a developer extending ClawStrike, I want the gating pipeline to include a defined hook point for an async judge so that Phase 2's LLM-as-Judge can be integrated without refactoring the gating logic.

**Acceptance Criteria:**
- [ ] The `gate` tool's internal pipeline includes an `async_judge` step between risk classification and the final recommendation
- [ ] When `llm_judge.enabled` is `false` (MVP default), the hook is a no-op passthrough that adds no latency
- [ ] The hook interface accepts the full gating context (action, source, trust level, classifier score, session history) and returns a `JudgeResult` with `alignment_score`, `rationale`, and `recommendation` (`"allow"` | `"block"` | `"defer_to_matrix"`)
- [ ] The interface is documented in code with a docstring explaining Phase 2 usage and trigger conditions

---

### US-031: Configuration Validation for Future Phase Fields

**Description:** As a ClawStrike user, I want future phase configuration fields (e.g., `llm_judge`, `proxy`) to be accepted in the config file without errors so that I can pre-configure them and enable them later.

**Acceptance Criteria:**
- [ ] The `llm_judge` config block is parsed and validated at startup even when `enabled: false`
- [ ] The `proxy` config block is parsed and validated at startup even when `mode: "skill"`
- [ ] If `mode: "proxy"` is set in the MVP, startup fails with an error: `"Proxy mode is not yet available. Set mode: 'skill' or wait for Phase 1.5."`
- [ ] If `llm_judge.enabled: true` is set, startup fails with: `"LLM Judge is not yet available. Set llm_judge.enabled: false or wait for Phase 2."`
- [ ] Invalid values in future phase fields (e.g., `trigger: "invalid"`) produce validation errors even when disabled, so users catch config issues before enabling

---

## Epic 7: End-to-End Scenarios

### US-032: E2E — Benign Owner DM Passthrough

**Description:** As a ClawStrike user, I want a normal message from my own account to flow through the entire pipeline with no interference so that ClawStrike is invisible during normal usage.

**Acceptance Criteria:**
- [ ] A benign message from the owner's DM channel passes classification (score < flag threshold)
- [ ] Trust resolves to `high` (owner DM channel default)
- [ ] A subsequent `gate` tool call for a low-risk action (e.g., calendar read) returns `recommendation: "allow"`
- [ ] The full classify + gate round trip completes with <110ms total overhead (MCP transport included)
- [ ] The audit log contains one `input_classification` event (decision: pass) and one `action_gate` event (recommendation: allow)

---

### US-033: E2E — Prompt Injection from Untrusted Email Detected

**Description:** As a ClawStrike user, I want a prompt injection embedded in an email body to be detected and flagged for rejection so that indirect injection attacks via email are caught.

**Acceptance Criteria:**
- [ ] An inbound message with channel type `email_body` and a known prompt injection payload scores above the trust-modulated block threshold
- [ ] The `classify` tool returns `decision: "block"`
- [ ] The ClawStrike skill instructs the LLM to reject the input
- [ ] The user is notified via the originating channel with the blocked source and score
- [ ] The audit log records the event with `decision: "block"`, the effective threshold (lowered for untrusted), and the source metadata

---

### US-034: E2E — Suspicious Action from Flagged Session Escalated

**Description:** As a ClawStrike user, I want a flagged session (suspicious but not blocked input) to produce stricter gating recommendations so that borderline attacks face higher scrutiny on downstream actions.

**Acceptance Criteria:**
- [ ] An inbound message scores between `flag` and `block` thresholds, triggering elevated scrutiny
- [ ] The `classify` tool returns `decision: "flag"` with `elevated_scrutiny: true`
- [ ] A subsequent `gate` tool call for a `high`-risk action from a `medium` trust source is affected by the scrutiny downgrade (medium → low), escalating the recommendation from `prompt_user` to `block`
- [ ] The audit log captures both the flag event and the gating escalation with the effective trust tier noted

---

### US-035: E2E — First Contact → Repeated Interaction → Auto-Promotion

**Description:** As a ClawStrike user, I want the trust system to progressively relax restrictions on a new contact as they interact safely over time so that the system adapts to my real communication patterns.

**Acceptance Criteria:**
- [ ] First message from a new Discord user is treated as `untrusted` (first contact)
- [ ] After 5 benign interactions (no blocks, no flags), the contact is auto-promoted to the channel's default trust level (e.g., `medium` for a trusted group)
- [ ] After auto-promotion, the same contact's messages use `medium` trust thresholds and gating recommendations
- [ ] The audit log contains 5 interaction events (with `is_first_contact: true` on the first) and one `trust_update` event recording the promotion

---

### US-036: E2E — Allowlist Reduces Prompt Fatigue Over Time

**Description:** As a ClawStrike user, I want my approval history to reduce unnecessary confirmation prompts so that ClawStrike becomes less intrusive as it learns my workflows.

**Acceptance Criteria:**
- [ ] User is prompted for a `high`-risk action (e.g., send email to `team@company.com`) from a medium-trust source
- [ ] User responds "always allow" — an allowlist rule is created for this action type + source
- [ ] The next time the same action type occurs from the same source, the `gate` tool returns `recommendation: "allow"` without prompting
- [ ] The audit log for the auto-allowed event references the allowlist rule ID that authorized it
- [ ] If the user later runs `clawstrike allowlist remove <id>`, subsequent identical actions trigger a prompt again

---

## Epic 8: Phase 1.5 — Proxy Mode & Enforcement (Post-MVP)

> **Note:** These stories are documented for planning purposes. They are not in scope for the MVP and will be refined based on learnings from Skill Mode usage.

### US-037: Proxy Mode Startup

**Description:** As a ClawStrike user, I want to start ClawStrike in proxy mode so that it intercepts all LLM API calls between OpenClaw and the upstream provider with enforcement-grade gating.

**Acceptance Criteria:**
- [ ] Running `clawstrike start` with `mode: "proxy"` starts an HTTP proxy server on the configured `api.listen_port`
- [ ] The proxy forwards requests to `proxy.upstream_llm_url` and returns responses to the caller
- [ ] Startup logs confirm the listening address, upstream URL, and enforcement mode (e.g., `ClawStrike proxy listening on :8019 → https://api.anthropic.com/v1 (enforcement mode)`)
- [ ] OpenClaw, when pointed to `http://localhost:<listen_port>`, continues to function normally for benign requests

---

### US-038: Structured Tool Call Extraction (Proxy Mode)

**Description:** As a ClawStrike user in proxy mode, I want ClawStrike to parse tool calls from the LLM response so that each planned action is deterministically evaluated before execution.

**Acceptance Criteria:**
- [ ] ClawStrike intercepts the LLM response before returning it to OpenClaw
- [ ] Tool calls are parsed from the structured JSON following OpenClaw's tool call schema
- [ ] Each extracted tool call is represented as a structured object with `action_type`, `action_name`, and `arguments`
- [ ] If the LLM response contains no tool calls (pure text), it is passed through without action gating
- [ ] Malformed tool call JSON causes the response to be blocked and an error logged

---

### US-039: Enforcement-Grade Action Blocking (Proxy Mode)

**Description:** As a ClawStrike user in proxy mode, I want blocked actions to be mechanically stripped from LLM responses so that the agent cannot execute them regardless of LLM behavior.

**Acceptance Criteria:**
- [ ] When a tool call's gating decision is "block," the tool call is removed from the LLM response before it reaches OpenClaw
- [ ] When a tool call's gating decision is "prompt user," the full response is held until the user approves or denies
- [ ] If a response contains multiple tool calls, only blocked/prompted calls are held or stripped — approved calls are forwarded immediately
- [ ] The user is notified of blocked actions with the action details and reason

---

### US-040: SSE Streaming Passthrough (Proxy Mode)

**Description:** As a ClawStrike user in proxy mode, I want non-tool-call streaming responses to pass through with minimal latency so that normal conversational responses aren't delayed.

**Acceptance Criteria:**
- [ ] Pure text SSE streams from the LLM are forwarded to OpenClaw token-by-token with no buffering
- [ ] Responses containing tool calls are buffered until the full tool call JSON is received, then gated before forwarding
- [ ] The transition from streaming passthrough to buffered mode is handled seamlessly within a single response
