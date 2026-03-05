# Docker Setup Guide

This guide covers running ClawStrike + OpenClaw together in Docker. For installing ClawStrike directly on your host machine, see the [direct setup guide](direct-setup.md).

## Overview

ClawStrike runs as a CLI tool on the same container as OpenClaw. The custom image extends the official OpenClaw image, adding Python 3.12 and the `clawstrike` binary. One container, no inter-process communication complexity.

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- A [Hugging Face](https://huggingface.co) account
- Meta license accepted for your chosen Prompt Guard model (see below)

## Step 1 — Accept the Model License

ClawStrike uses Meta's Llama Prompt Guard 2. You have to accept Meta's license on Hugging Face before the model can be downloaded.

Choose one:

| Model | Size | Languages | License page |
|-------|------|-----------|--------------|
| Llama-Prompt-Guard-2-22M | ~300 MB | English only | [Hugging Face](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-22M) |
| Llama-Prompt-Guard-2-86M | ~1.13 GB | Multilingual | [Hugging Face](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M) |

After accepting, generate a read-only token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).

This step cannot be automated. **The `HF_TOKEN` alone is not sufficient — you should also accept the license on the model page.**

## Step 2 — Clone and Configure

```bash
# Clone the repo
git clone https://github.com/yogur/ClawStrike && cd ClawStrike

# Create ClawStrike config (choose one)
cp clawstrike.example.yaml clawstrike.yaml   # then edit to taste
# OR (if uv is installed locally):
# uv run clawstrike init                      # generates defaults

# Create environment file and fill in HF_TOKEN and LLM credentials
cp .env.example .env
```

Edit `.env` and fill in:
- `HF_TOKEN` — your Hugging Face read-only token
- LLM session credentials (`CLAUDE_AI_SESSION_KEY`, etc.) for whichever LLM provider you use

`OPENCLAW_GATEWAY_TOKEN` is generated automatically by the setup script if not set.

## Step 3 — Run the Setup Script

```bash
bash docker-setup.sh
```

The script will:
1. Create required data directories
2. Generate a gateway token (or reuse one if already configured)
3. Build the Docker image
4. Fix bind-mount directory permissions
5. Run the interactive OpenClaw onboarding
6. Start the gateway

**Do not Ctrl+C during the first run.** The Prompt Guard model is being downloaded to the `hf-cache` named volume; subsequent starts skip the download entirely.

## Step 4 — Open the OpenClaw CLI

In a separate terminal:

```bash
docker compose run --rm openclaw-cli
```

## Subsequent Starts

```bash
# No rebuild needed unless ClawStrike or OpenClaw version changes
docker compose up -d openclaw-gateway
```

## Updating

### Update ClawStrike

Pull the latest code and rebuild:

```bash
git pull
docker compose build --no-cache
docker compose up
```

### Update the OpenClaw base image

The `Dockerfile` pins the OpenClaw version (`FROM ghcr.io/openclaw/openclaw:2026.3.2`). To upgrade:

1. Change the `FROM` tag in `Dockerfile` to the new version
2. Run `docker compose build --no-cache`
3. Test before using in production

Pinning is intentional — OpenClaw updates may change the skill API or directory structure.

## Volume Reference

| Volume | Purpose | Survives rebuild? |
|--------|---------|-------------------|
| `hf-cache` | Hugging Face model cache | Yes (named volume) |
| `clawstrike-data` | Audit DB, contact registry | Yes (named volume) |
| `OPENCLAW_CONFIG_DIR` | OpenClaw config, conversations | Yes (host bind-mount) |

## Troubleshooting

### Container exits immediately with "Config file not found"

`clawstrike.yaml` is missing or not bind-mounted. Create it first:

```bash
cp clawstrike.example.yaml clawstrike.yaml
```

Then ensure `docker-compose.yml` has the correct bind-mount path (it does by default: `./clawstrike.yaml:/clawstrike/clawstrike.yaml:ro`).

### Model warmup fails

Check that:
1. `HF_TOKEN` is set and valid
2. You have accepted the Meta license on the model's Hugging Face page
3. The token has read access (not write-only)

The entrypoint prints the exact model URLs when warmup fails. OpenClaw still starts — classification requests will return errors until the model is available.

## Verify

Once the gateway is running, open a shell in the container and check that ClawStrike is working:

```bash
docker compose exec openclaw-gateway clawstrike health
# {"status": "ok", "mode": "skill", "classifier": "multilingual", "mcp_enabled": false}
```

## Security & Architecture Notes

- `clawstrike.yaml` is mounted read-only (`:ro`). The container cannot modify the security policy.
- The gateway binds to `127.0.0.1` on the host by default — reachable from the host machine only, not from the LAN or internet. To expose it to the LAN or a tailnet, add `OPENCLAW_GATEWAY_HOST=0.0.0.0` to `.env`. If you do, generate a strong `OPENCLAW_GATEWAY_TOKEN` (e.g. `openssl rand -hex 32`) and apply additional network controls so port 18789 is not reachable from the internet.
- The image runs as the non-root `node` user (uid 1000), matching the upstream OpenClaw security posture.
- PyTorch is installed from the CPU-only index — both Docker and local dev installs use CPU inference. GPU is unnecessary for models of this size (22M / 86M parameters).
