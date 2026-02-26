# ClawStrike OpenClaw Skill

Security guardrails for OpenClaw. This skill integrates ClawStrike's prompt
injection detection, source trust policies, and action gating into your
OpenClaw agent sessions.

---

## Prerequisites

ClawStrike must be installed on the machine running your OpenClaw agent.

```bash
# Using pip
pip install clawstrike

# Or using uv (recommended)
uv add clawstrike
```

Verify the installation:

```bash
clawstrike health
```

---

## Configuration

Copy the example configuration file and edit it for your environment:

```bash
cp clawstrike.example.yaml clawstrike.yaml
```

**For OpenClaw deployments, set `mcp.enabled: false`** in `clawstrike.yaml`.
OpenClaw invokes ClawStrike as a one-shot shell command, not as a persistent
MCP server, so the MCP listener is not needed.

```yaml
clawstrike:
  mcp:
    enabled: false # <-- required for OpenClaw / CLI mode
```

Key settings to review:

| Setting                            | Default           | Description                                                                |
| ---------------------------------- | ----------------- | -------------------------------------------------------------------------- |
| `classifier.model`                 | `multilingual`    | `multilingual` (86M, multi-language) or `english-only` (22M, lower memory) |
| `classifier.threshold.block`       | `0.92`            | Scores at or above this trigger a block recommendation                     |
| `classifier.threshold.flag`        | `0.70`            | Scores at or above this trigger elevated scrutiny                          |
| `trust.channel_defaults`           | (see example)     | Base trust level per channel type                                          |
| `trust.auto_promote_after`         | `5`               | Safe interactions before auto-promoting a contact's trust                  |
| `action_gating.allowlist_learning` | `true`            | Offer "always allow" option on confirmations                               |
| `audit.db_path`                    | `./data/audit.db` | Path to the SQLite audit database                                          |
| `audit.log_raw_input`              | `true`            | Store first 200 chars of each classified message                           |

---

## Installation into OpenClaw

### Option A — Manual file copy

```bash
cp -r skills/clawstrike ~/.openclaw/skills/clawstrike
```

### Option B — ClawHub install command

```bash
openclaw skill install clawstrike
```

---

## MCP mode (non-OpenClaw agents)

If your agent supports native MCP connections, you can run ClawStrike as a
persistent server instead of invoking it as a shell command. The persistent
server enables session-level elevation tracking across multiple calls.

1. Keep `mcp.enabled: true` in `clawstrike.yaml`.
1. Start the server: `clawstrike start`
1. Register the server with your agent's MCP client (stdio transport).
1. The skill's mode detection will automatically switch to MCP mode when the
   `classify`, `gate`, and `confirm` tools appear in the active tool list.

---

## Troubleshooting

**`clawstrike health` exits with an error**
Check that `clawstrike.yaml` exists in the working directory (or pass
`--config /path/to/clawstrike.yaml`).

**Classifier model download fails**
The first run downloads the Llama Prompt Guard 2 model from Hugging Face.
This requires `HF_TOKEN` to be set in the environment and Meta's licence to
be accepted on the Hugging Face model page.

**High latency in CLI mode**
Each call cold-starts the classifier (~1–2s). This is expected. For
lower-latency deployments, switch to MCP mode with `clawstrike start`.

**Audit database not found**
The `data/` directory is created automatically on first run. Ensure the
process has write access to the directory specified in `audit.db_path`.
