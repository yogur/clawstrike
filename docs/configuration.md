# Configuration Reference

All ClawStrike configuration lives in a single YAML file: `clawstrike.yaml`.

## Overview

**Generating the config file:**

```bash
clawstrike init               # secure defaults, MCP disabled (for CLI agents)
clawstrike init --mcp          # secure defaults, MCP enabled
clawstrike init --force        # overwrite an existing config
```

The generated file has `600` permissions (owner read/write only). See [clawstrike.example.yaml](../clawstrike.example.yaml) for a fully annotated version.

**Where to put the config file:**

OpenClaw executes CLI commands (`clawstrike classify`, `clawstrike gate`, etc.) from within its workspace directory. ClawStrike's CLI looks for `clawstrike.yaml` in the current working directory by default, so the config must live there for the agent to find it automatically.

- **Direct install:** run `clawstrike init` from inside `~/.openclaw/workspace/` (or your configured workspace path).
- **Docker:** `clawstrike.yaml` is bind-mounted read-only into `/home/node/.openclaw/workspace/clawstrike.yaml` automatically — no manual placement needed.
- **Override:** pass `--config /path/to/clawstrike.yaml` to any CLI command to use a config at an arbitrary path.

**File structure:**

All settings live under a top-level `clawstrike:` key:

```yaml
clawstrike:
  mode: "skill"
  mcp:
    enabled: false
  classifier:
    ...
  trust:
    ...
  action_gating:
    ...
  audit:
    ...
```

Every field has a default — an empty `clawstrike.yaml` (or even an empty file) produces a valid configuration with all defaults applied. Unknown fields are ignored with a warning to stderr.

---

## Mode

```yaml
mode: "skill"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `mode` | `"skill"` | `"skill"` | Operating mode. Only `"skill"` (advisory) is supported in the MVP. Enforcement-grade `"proxy"` mode ships in a future release. |

---

## Classifier

Controls the prompt injection detection model and its sensitivity.

```yaml
classifier:
  model: "multilingual"
  run_mode: "local"
  threshold:
    block: 0.92
    flag: 0.70
```

| Field | Type | Default | Description |
|---|---|---|---|
| `model` | `"multilingual"` \| `"english-only"` | `"multilingual"` | Which Prompt Guard model to use. `"multilingual"` is Llama Prompt Guard 2 86M (multiple languages). `"english-only"` is Llama Prompt Guard 2 22M (lower memory). |
| `run_mode` | `"local"` | `"local"` | Inference mode. Only `"local"` is supported in the MVP. |
| `threshold.block` | float (0.0–1.0) | `0.92` | Classifier scores at or above this value produce a **block** recommendation. Lower values are more aggressive (catch more, but higher false positive risk). |
| `threshold.flag` | float (0.0–1.0) | `0.70` | Scores at or above this value (but below `block`) produce a **flag** recommendation with elevated scrutiny. Lower values flag more content for review. |

**How thresholds interact with trust:** These are *base* thresholds. The effective thresholds for each message are adjusted by the source's trust level (see `trust.threshold_modifiers` below). For example, an untrusted source with modifier `-0.10` hits the block threshold at `0.82` instead of `0.92`.

---

## Trust

Controls how ClawStrike differentiates input sources by trust level and adjusts behavior accordingly.

```yaml
trust:
  channel_defaults:
    owner_dm:      high
    trusted_group: medium
    public_group:  low
    email_body:    low
    webhook:       untrusted
    skill_input:   untrusted
  threshold_modifiers:
    high:      { block: +0.05, flag: +0.10 }
    medium:    { block:  0.00, flag:  0.00 }
    low:       { block: -0.05, flag: -0.10 }
    untrusted: { block: -0.10, flag: -0.20 }
  auto_promote_after: 5
  contacts: {}
```

### channel_defaults

A mapping from input channel type to a base trust level.

| Field | Type | Default | Description |
|---|---|---|---|
| `channel_defaults` | dict (string → trust level) | See above | Maps each channel type to `high`, `medium`, `low`, or `untrusted`. Any channel not listed defaults to `untrusted`. |

You can add your own channel types here — the keys are arbitrary strings that your agent's skill passes as the `channel_type` parameter to `classify` and `gate`.

### threshold_modifiers

Additive adjustments applied to the base `block` and `flag` thresholds based on the source's resolved trust level.

| Trust Level | Block modifier | Flag modifier | Effect |
|---|---|---|---|
| `high` | `+0.05` | `+0.10` | More lenient — fewer false positives for trusted sources |
| `medium` | `0.00` | `0.00` | Baseline — no adjustment |
| `low` | `-0.05` | `-0.10` | Stricter — lower threshold to trigger block/flag |
| `untrusted` | `-0.10` | `-0.20` | Most strict — catches more at the cost of more false positives |

Effective thresholds are clamped to the range [0.0, 1.0].

**Example:** Base block = `0.92`. An untrusted source has effective block = `0.92 + (-0.10)` = `0.82`. A high-trust owner DM has effective block = `0.92 + 0.05` = `0.97`.

### auto_promote_after

| Field | Type | Default | Description |
|---|---|---|---|
| `auto_promote_after` | int | `5` | Number of safe interactions (no blocks or flags) before a new contact is automatically promoted from untrusted to their channel's default trust level. |

New contacts always start as untrusted regardless of their channel. After this many benign interactions, they are promoted to the trust level configured for their channel type in `channel_defaults`.

### contacts

Static trust overrides for specific senders, defined in config.

```yaml
contacts:
  "attacker@evil.com": "blocked"
  "colleague@company.com": "trusted"
```

| Value | Effect |
|---|---|
| `"trusted"` | Source is treated as `high` trust regardless of channel defaults or contact history. The classifier still runs — but with high-trust (lenient) thresholds. |
| `"blocked"` | All input from this source is immediately rejected without running the classifier. |

Config overrides take precedence over the dynamic contact registry. The contact's stored trust level in the database is not modified — the override is applied at read time only. Removing a contact from this section restores automatic behavior.

**Security note:** Trust overrides are deliberately config-file-only. There is no CLI command to add trusted or blocked contacts. This prevents a compromised agent from persistently weakening the security policy. Keep `clawstrike.yaml` non-writable by the agent process.

---

## Action Gating

Controls how ClawStrike evaluates and gates planned actions before execution.

```yaml
action_gating:
  enabled: true
  confirmation_channel: "owner_dm"
  allowlist_learning: false
  guard_allowlist_on_flag: true
  static_rules: []
```

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Whether action gating is active. |
| `confirmation_channel` | string | `"owner_dm"` | Channel used to send approval prompts to the owner when an action requires confirmation. |
| `allowlist_learning` | bool | `false` | When `true`, approving an action with "always allow" creates a persistent allowlist rule in the database. When `false`, "always allow" decisions are silently downgraded to a one-time approval — no rule is created. |
| `guard_allowlist_on_flag` | bool | `true` | When `true`, "always allow" decisions are blocked in sessions with active security signals (see below). This prevents a compromised session from creating persistent rules even when `allowlist_learning` is enabled. |

**Elevated scrutiny:** When the classifier scores a message between the `flag` and `block` thresholds, the session is marked as suspicious. For the remainder of that session, the gating engine uses a trust level one tier stricter than normal (e.g., medium → low). This makes it harder for a borderline injection to escalate into risky actions.

**Content-source mismatch:** When a normally trusted contact (high or medium trust) sends content that scores above the base `flag` threshold, this is treated as anomalous — potentially a compromised account or relayed malicious content. The session's effective trust is forced to low for gating purposes. The contact's stored trust level is not permanently changed.

### static_rules

Pre-approved action rules defined in config. These are checked alongside database-created allowlist rules when the `gate` tool evaluates an action.

```yaml
static_rules:
  - action_type: "send_email"
    source_scope: "global"              # allow from any source
  - action_type: "file_read"
    source_scope: "colleague@company.com"  # allow only from this source
```

| Field | Type | Default | Description |
|---|---|---|---|
| `action_type` | string | *(required)* | The action type to allow (must match exactly). |
| `source_scope` | string | `"global"` | `"global"` matches any source. A specific `source_id` matches only that sender. |

Static rules cannot be modified by the agent at runtime. This is the recommended way to define standing allowlist policies.

**Action types recognized by the built-in risk taxonomy:**

| Risk Level | Action Types |
|---|---|
| **Critical** | `exec`, `spawn`, `system`, `child_process`, `shell_exec`, `outbound_network_unknown`, `curl`, `wget`, `fetch`, `skill_install`, `skill_modify`, `cron_create`, `cron_modify` |
| **High** | `send_email`, `send_message`, `file_write`, `calendar_modify`, `contact_modify` |
| **Medium** | `file_read_sensitive`, `web_browse`, `web_navigate`, `form_submit` |
| **Low** | `file_read`, `calendar_read`, `list_directory` |

Unrecognized action types default to **high** risk (fail-safe).

### Decision matrix

The `gate` tool combines the action's risk level with the session's effective trust level to produce a recommendation:

|  | High Trust | Medium Trust | Low Trust | Untrusted |
|---|---|---|---|---|
| **Critical** | prompt user | block | block | block |
| **High** | allow | prompt user | block | block |
| **Medium** | allow | allow | prompt user | block |
| **Low** | allow | allow | allow | prompt user |

If the session has **elevated scrutiny** (the classifier flagged the input as suspicious), the effective trust level is downgraded by one tier for gating purposes — e.g., medium → low. If a **content-source mismatch** was detected (a normally trusted contact sent content that scored above the flag threshold), the effective trust is forced to low. Both downgrades can stack.

---

## Audit

Controls the audit log — a local SQLite database that records every security-relevant decision.

```yaml
audit:
  enabled: true
  retention_days: 90
  log_raw_input: true
  raw_input_max_chars: 200
  db_path: "./data/audit.db"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Whether audit logging is active. |
| `retention_days` | int | `90` | Events older than this many days are purged on startup. Set to `0` for infinite retention. |
| `log_raw_input` | bool | `true` | When `true`, the first `raw_input_max_chars` characters of each input are stored in the audit log. When `false`, only a SHA-256 hash of the input is stored — useful for privacy-sensitive deployments. The hash is always stored regardless of this setting. |
| `raw_input_max_chars` | int | `200` | Maximum number of characters to store when `log_raw_input` is `true`. |
| `db_path` | string | `"./data/audit.db"` | Path to the SQLite audit database. Parent directories are created automatically. |

**Exporting audit data:**

```bash
clawstrike logs --export csv --output audit-export.csv
```

---

## MCP

Controls the MCP (Model Context Protocol) server for agents that connect to ClawStrike as a persistent process.

```yaml
mcp:
  enabled: false
```

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | When `true`, `clawstrike start` launches an MCP server (stdio transport). When `false`, `clawstrike start` prints an informational message and exits. CLI commands (`classify`, `gate`, `health`) work regardless of this setting. |

Set to `true` for MCP-capable agents, or use `clawstrike init --mcp`.
