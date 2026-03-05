# ClawStrike

**Security guardrails for AI agents — prompt injection detection, trust-aware input scanning, and advisory action gating.**

<!-- TODO: badges — PyPI version, Python 3.12+, MIT license -->

---

## The Problem

AI agents like [OpenClaw](TODO) now have real system access — shell execution, email, calendars, file systems — and users grant this willingly because autonomy is the whole point. OpenClaw alone has surpassed 200k GitHub stars in weeks, signaling massive demand for agents that act on your behalf. But the security model hasn't kept up.

The primary risk isn't the agent itself — it's the **inputs**. Every email body, group chat message, webhook payload, and skill data feed is a potential prompt injection vector. A carefully crafted message from any of these channels can instruct the agent to take actions the user never intended — sending emails, exfiltrating files, modifying system configuration — all while operating within its granted permissions.

Most emerging security approaches focus on **sandboxing**: isolating the agent, limiting blast radius, restricting what it *can* do. That matters, but it doesn't address the input layer. A sandboxed agent can still be manipulated into misusing every permission it legitimately has. If an agent is allowed to send emails, a sandbox won't stop a prompt injection from composing and sending one.

What's missing is **input-layer defense**: scanning content *before* it reaches the agent, differentiating trust based on *where* input comes from (an owner's DM is not the same threat as an unsolicited email body), and gating risky actions with a review step before they execute. These need to work together — without trust-aware scanning, action gating is flying blind; without action gating, a bypassed classifier has no safety net.

Even OpenClaw's own documentation acknowledges there is no "perfectly secure" setup. ClawStrike doesn't claim to be one either. What it provides is a **layered defense** that makes attacks harder, catches the common cases, and gives you a forensic trail when something does go wrong.

## What ClawStrike Does

ClawStrike is a security layer you install as a skill in your AI agent. It instructs the agent to check with ClawStrike before acting on any input — scanning content, evaluating trust, and gating risky actions.

### The three pillars

| | What it does | Why it matters |
|---|---|---|
| **Classify** | Scans every inbound message for prompt injection using Meta's Llama Prompt Guard 2 models | Catches injection attempts before they reach the agent |
| **Trust** | Assigns a trust level based on the input channel (owner DM → high, email body → low, webhook → untrusted) and tracks contacts over time | A message from your own account isn't treated the same as an unsolicited email |
| **Gate** | Evaluates planned actions against a risk taxonomy and recommends allow, block, or prompt the user | Shell execution from an untrusted source gets blocked; a calendar read from the owner gets auto-allowed |

These three work together. The classifier's sensitivity adjusts based on trust — untrusted sources face stricter thresholds. The gating engine uses both the action's risk level and the session's trust level to decide what to recommend. And if the classifier flags something suspicious, the gating engine automatically tightens for the rest of that session.

Every decision — classification, trust change, gating recommendation, user approval — is written to a local audit log for forensic review.

### A typical flow

```
External input arrives (email, group chat, webhook, ...)
         │
         ▼
   ┌───────────┐
   │  Classify │──── Score ≥ block threshold? ──► Block. Notify owner.
   └───────────┘
         │ pass or flag
         ▼
   ┌───────────┐
   │   Trust   │──── Resolve trust from channel + contact history
   └───────────┘     Adjust classifier thresholds accordingly
         │
         ▼
     Agent acts
         │
         ▼
   ┌───────────┐
   │   Gate    │──── Risk level + trust level → allow / block / prompt user
   └───────────┘
         │
         ▼
   ┌───────────┐
   │ Audit Log │──── Every decision recorded
   └───────────┘
```

### It learns over time

ClawStrike starts strict and relaxes as it learns your patterns. New contacts begin as untrusted and earn trust through repeated safe interactions, eventually reaching their channel's default trust level. Actions you approve can be added to an allowlist so you aren't prompted for the same routine operation twice. The system adapts to your workflows while staying vigilant for novel or untrusted activity.

### Advisory mode (MVP)

In the current release, ClawStrike operates in **skill mode** — it advises the agent, and the agent is instructed to comply. This is effective against unsophisticated attacks and provides full visibility, but a sufficiently advanced injection could instruct the agent to ignore the skill. Enforcement-grade interception (where blocked content never reaches the agent at all) ships in a future release.

ClawStrike integrates via two methods:

| Method | How it works | Best for |
|---|---|---|
| **MCP** | Persistent process, agent calls ClawStrike tools directly | MCP-capable agents (full feature set including session tracking) |
| **CLI** | One-shot shell commands (`clawstrike classify`, `clawstrike gate`) | Any agent with shell access (e.g., OpenClaw) |

## Getting Started

### Prerequisites: Prompt Guard model access

ClawStrike uses Meta's Llama Prompt Guard 2 for prompt injection detection. Before installing, you need to grant access to the model weights:

1. Create a [Hugging Face](https://huggingface.co) account if you don't have one
2. Visit the model page for your chosen model and accept Meta's license:
   - [Llama-Prompt-Guard-2-86M](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M) — Multilingual (~1.13 GB) — **recommended**
   - [Llama-Prompt-Guard-2-22M](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-22M) — English only (~300 MB)
3. Generate a read-only access token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)

### Option A: Docker (recommended for new setups)

Best if you're setting up OpenClaw and ClawStrike together from scratch.

```bash
git clone https://github.com/yogur/ClawStrike && cd ClawStrike
cp clawstrike.example.yaml clawstrike.yaml    # edit to taste
cp .env.example .env                           # add HF_TOKEN + LLM credentials
bash docker-setup.sh
```

The setup script builds the image, downloads the model, runs OpenClaw onboarding, and starts the gateway. First run takes a few minutes for the model download; subsequent starts are fast.

See the [full Docker setup guide](docs/docker-setup.md) for details, volume reference, and troubleshooting.

### Option B: Direct install (pip / uv)

Best if you already have OpenClaw running and want to add ClawStrike alongside it.

```bash
pip install clawstrike                         # or: uv add clawstrike

# Install Hugging Face CLI and authenticate
# See: https://huggingface.co/docs/huggingface_hub/en/guides/cli
pip install huggingface_hub[cli]
hf auth login --token $HF_TOKEN --add-to-git-credential

# Bootstrap config with secure defaults
clawstrike init

# Copy the ClawStrike skill into your OpenClaw skills directory
cp -r skills/clawstrike-cli /path/to/openclaw/skills/clawstrike
```

See the [full direct setup guide](docs/direct-setup.md) for OpenClaw configuration, file permissions, and security recommendations.

### Verify

```bash
clawstrike health
# {"status": "ok", "mode": "skill", "classifier": "multilingual", "mcp_enabled": false}
```

## Configuration

`clawstrike init` generates a `clawstrike.yaml` with secure defaults — see [clawstrike.example.yaml](clawstrike.example.yaml) for a fully annotated starting point.

The settings most users will want to review:

| Setting | What to decide | Default |
|---|---|---|
| `classifier.model` | `"multilingual"` (86M, multiple languages) or `"english-only"` (22M, lower memory) | `multilingual` |
| `classifier.threshold.block` / `.flag` | How aggressive detection should be — lower values catch more but risk false positives | `0.92` / `0.70` |
| `trust.channel_defaults` | Which input channels you consider high, medium, low, or untrusted trust | owner_dm=high, email=low, webhook=untrusted |
| `trust.contacts` | Specific senders to always trust or always block, regardless of channel | `{}` (none) |
| `action_gating.allowlist_learning` | Whether approving an action can create a permanent "always allow" rule | `false` (off) |
| `audit.log_raw_input` | Whether input text snippets are stored in the audit log, or only a hash | `true` (snippets stored) |

**Example** — tighten detection and block a known bad sender:

```yaml
clawstrike:
  classifier:
    model: "english-only"
    threshold:
      block: 0.85          # lower = more aggressive blocking
      flag: 0.60

  trust:
    contacts:
      "attacker@evil.com": "blocked"
      "colleague@company.com": "trusted"
```

**Security note:** The config file controls ClawStrike's security policy. It should be owned by you (not the agent's service account) and not writable by the agent process. `clawstrike init` sets file permissions to `600` (owner read/write only) by default.

See the [full configuration reference](docs/configuration.md) for all options.

## Project Status

ClawStrike is in **MVP (Skill Mode)**. The core guardrails — prompt injection detection, trust tiers, action gating, and audit logging — are functional and tested.

In this release, all guardrail decisions are **advisory**: the agent is instructed to comply via the skill's system prompt, but is not mechanically prevented from ignoring recommendations. This is effective against common attacks and provides full visibility through the audit log, but is not a hard security boundary.

**Coming next:**

- **Enforcement mode (Proxy Mode)** — ClawStrike intercepts LLM API calls directly, blocking dangerous tool calls before they reach the agent. No LLM cooperation required.
- **LLM-as-Judge** — Semantic intent verification for ambiguous cases.
- **Skill scanner** — Static analysis of agent skills before installation.
- **Output guardrails** — PII and credential detection in outbound content.

## Contributing

Contributions are welcome. Please open an issue to discuss before submitting large changes.

## License

MIT License
