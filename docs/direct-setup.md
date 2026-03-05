# Direct Setup Guide

This guide covers installing ClawStrike directly on your host machine alongside an existing AI agent (e.g., OpenClaw). For Docker-based setups, see the [Docker setup guide](docker-setup.md).

## Prerequisites

- Python 3.12+
- pip or [uv](https://docs.astral.sh/uv/)
- A [Hugging Face](https://huggingface.co) account with access to your chosen Prompt Guard model (see the [README](../README.md#prerequisites-prompt-guard-model-access) for model setup)

## Step 1 — Install ClawStrike

```bash
pip install clawstrike
# or
uv add clawstrike
```

Verify the binary is available:

```bash
clawstrike --help
```

## Step 2 — Set Up the Prompt Guard Model

ClawStrike downloads the model automatically on first run, but it needs Hugging Face credentials to access the gated model weights.

Install the Hugging Face CLI and authenticate:

```bash
pip install huggingface_hub[cli]
hf auth login --token $HF_TOKEN --add-to-git-credential
```

See the [Hugging Face CLI guide](https://huggingface.co/docs/huggingface_hub/en/guides/cli) for alternative CLI installation methods and authentication methods.

Make sure you have accepted Meta's license on the model page before proceeding — the token alone is not sufficient:

- [Llama-Prompt-Guard-2-86M](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M) (multilingual)
- [Llama-Prompt-Guard-2-22M](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-22M) (english-only)

## Step 3 — Configure ClawStrike

Bootstrap a config file with secure defaults:

```bash
clawstrike init
```

This creates `clawstrike.yaml` in the current directory with `600` permissions (owner read/write only) and a `data/` directory with `700` permissions for the audit database.

For agents that use CLI integration (like OpenClaw), the default config sets `mcp.enabled: false`, which is correct — no MCP server is needed. If your agent connects via MCP instead, run `clawstrike init --mcp`, or set `mcp.enabled: true` in the config.

Review and adjust the config as needed. See the [configuration reference](configuration.md) for all options.

## Step 4 — Install the ClawStrike Skill

The skill files are included in the [ClawStrike repository](https://github.com/yogur/ClawStrike). If you installed via `pip`, clone the repo to get them:

```bash
git clone https://github.com/yogur/ClawStrike /path/to/clawstrike-repo
```

Copy the appropriate skill into your agent's skills directory. For OpenClaw, this is typically `~/.openclaw/skills/` (see [OpenClaw skills documentation](https://docs.openclaw.ai/tools/skills) for details):

```bash
# For CLI-based agents (e.g., OpenClaw)
cp -r /path/to/clawstrike-repo/skills/clawstrike-cli ~/.openclaw/skills/clawstrike

# For MCP-capable agents
cp -r /path/to/clawstrike-repo/skills/clawstrike-mcp /path/to/your-agent/skills/clawstrike
```

The skill file instructs the agent to call ClawStrike's `classify` and `gate` tools before acting on any input or executing risky actions. The agent picks up new skills automatically — no restart required.

## Step 5 — Verify

Check that ClawStrike can load its config:

```bash
clawstrike health
# {"status": "ok", "mode": "skill", "classifier": "multilingual", "mcp_enabled": false}
```

Test a classification call:

```bash
clawstrike classify --json '{
  "text": "Hello, what is the weather today?",
  "source_id": "test@example.com",
  "channel_type": "owner_dm",
  "session_id": "test-session"
}'
```

Expected output (a benign message should pass with a low score):

```json
{
  "decision": "pass",
  "score": 0.0001,
  "label": "benign",
  "trust_level": "untrusted",
  "is_first_contact": true,
  "threshold_applied": {"block": 0.82, "flag": 0.5},
  "content_source_mismatch": false
}
```

The first call for any `source_id` returns `is_first_contact: true` with `untrusted` trust (stricter thresholds). Subsequent calls from the same source will reflect the channel's configured trust level.

## Security Recommendations

When running ClawStrike alongside an AI agent, the agent has shell access by design — that's how it calls `clawstrike classify` and `clawstrike gate`. This means the agent *could* also read or modify ClawStrike's files if permissions allow it. A few measures to limit this:

**Protect the config file.** `clawstrike.yaml` controls the security policy. If the agent can modify it, a prompt injection could weaken thresholds, add trusted contacts, or disable gating. `clawstrike init` sets `600` permissions by default. If you run the agent under a separate user account, ensure that account has read access but not write access to the config:

```bash
# Config owned by admin, readable by agent's group
chown admin:openclaw-svc clawstrike.yaml
chmod 640 clawstrike.yaml
```

**Keep allowlist learning off.** The default `allowlist_learning: false` means the agent cannot create persistent "always allow" rules, even if a prompt injection tries to use the `confirm` tool with `always_allow`. Enable it only if you need dynamic rule creation and understand the risk.

**Use a dedicated service account.** Running the agent under a separate OS user limits what a compromised agent can access beyond its intended scope.

**Review the audit log.** ClawStrike records every security decision. Periodically check for anomalies:

```bash
clawstrike logs --export csv --output audit-review.csv
```
