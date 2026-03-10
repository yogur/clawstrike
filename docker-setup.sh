#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing dependency: $1" >&2
    exit 1
  fi
}

# Read the gateway token stored in openclaw.json (written by OpenClaw on first onboard).
read_config_gateway_token() {
  local config_path="$OPENCLAW_CONFIG_DIR/openclaw.json"
  if [[ ! -f "$config_path" ]]; then
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$config_path" <<'PY'
import json, sys
try:
    cfg = json.load(open(sys.argv[1]))
    token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
    if isinstance(token, str) and token.strip():
        print(token.strip())
except Exception:
    pass
PY
  fi
}

# Replace or append KEY=VALUE in a file without sourcing it.
upsert_env() {
  local file="$1" key="$2" value="$3"
  local tmp found
  tmp="$(mktemp)"
  found=false
  if [[ -f "$file" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "${line%%=*}" == "$key" ]]; then
        printf '%s=%s\n' "$key" "$value" >>"$tmp"
        found=true
      else
        printf '%s\n' "$line" >>"$tmp"
      fi
    done <"$file"
  fi
  if [[ "$found" == false ]]; then
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  mv "$tmp" "$file"
}

# ── Prerequisites ──────────────────────────────────────────────────────────────
require_cmd docker
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose not available (try: docker compose version)" >&2
  exit 1
fi

# ── Config check ───────────────────────────────────────────────────────────────
if [[ ! -f "$ROOT_DIR/clawstrike.yaml" ]]; then
  fail "clawstrike.yaml not found. Create one first:
    cp clawstrike.example.yaml clawstrike.yaml   # then edit to taste
    # OR (if uv is installed locally):
    uv run clawstrike init

  Note: docker-compose.yml mounts this file read-only into the OpenClaw
  workspace inside the container so OpenClaw can find it when invoking
  ClawStrike CLI commands."
fi

# ── Data directories ───────────────────────────────────────────────────────────
OPENCLAW_CONFIG_DIR="${OPENCLAW_CONFIG_DIR:-$HOME/.openclaw}"
OPENCLAW_WORKSPACE_DIR="${OPENCLAW_WORKSPACE_DIR:-$HOME/.openclaw/workspace}"

echo "==> Creating data directories"
mkdir -p "$OPENCLAW_CONFIG_DIR" "$OPENCLAW_WORKSPACE_DIR"
# Pre-seed subdirectories so bind mounts work on Docker Desktop / Windows
# where containers (even as root) cannot create new host subdirectories.
mkdir -p "$OPENCLAW_CONFIG_DIR/identity"
mkdir -p "$OPENCLAW_CONFIG_DIR/agents/main/agent"
mkdir -p "$OPENCLAW_CONFIG_DIR/agents/main/sessions"

export OPENCLAW_CONFIG_DIR
export OPENCLAW_WORKSPACE_DIR

# ── Gateway token ──────────────────────────────────────────────────────────────
echo ""
echo "==> Gateway token"

if [[ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ]]; then
  EXISTING_CONFIG_TOKEN="$(read_config_gateway_token || true)"
  if [[ -n "$EXISTING_CONFIG_TOKEN" ]]; then
    OPENCLAW_GATEWAY_TOKEN="$EXISTING_CONFIG_TOKEN"
    echo "Reusing gateway token from $OPENCLAW_CONFIG_DIR/openclaw.json"
  elif command -v openssl >/dev/null 2>&1; then
    OPENCLAW_GATEWAY_TOKEN="$(openssl rand -hex 32)"
    echo "Generated new gateway token."
  else
    OPENCLAW_GATEWAY_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    echo "Generated new gateway token."
  fi
else
  echo "Using OPENCLAW_GATEWAY_TOKEN from environment."
fi

upsert_env "$ENV_FILE" OPENCLAW_GATEWAY_TOKEN "$OPENCLAW_GATEWAY_TOKEN"
upsert_env "$ENV_FILE" OPENCLAW_CONFIG_DIR "$OPENCLAW_CONFIG_DIR"
upsert_env "$ENV_FILE" OPENCLAW_WORKSPACE_DIR "$OPENCLAW_WORKSPACE_DIR"
export OPENCLAW_GATEWAY_TOKEN

# ── Build ──────────────────────────────────────────────────────────────────────
echo ""
echo "==> Building Docker image"
docker compose build

# ── Permissions ────────────────────────────────────────────────────────────────
echo ""
echo "==> Fixing data-directory permissions"
# Ensures the container's node user (uid 1000) can write to host-created dirs.
# -xdev restricts chown to the config-dir mount, avoiding a recursive chown
# across the workspace bind mount and user project files.
docker compose run --rm --user root --entrypoint sh openclaw-gateway -c \
  'find /home/node/.openclaw -xdev -exec chown node:node {} +; \
   [ -d /home/node/.openclaw/workspace/.openclaw ] && chown -R node:node /home/node/.openclaw/workspace/.openclaw || true'

# ── Onboarding ─────────────────────────────────────────────────────────────────
echo ""
echo "==> Onboarding (interactive)"
echo "Docker setup pins Gateway mode to local."
echo "Gateway token: $OPENCLAW_GATEWAY_TOKEN"
echo ""
docker compose run --rm openclaw-cli onboard --mode local --no-install-daemon

# ── Start ──────────────────────────────────────────────────────────────────────
echo ""
echo "==> Starting gateway"
docker compose up -d openclaw-gateway

echo ""
echo "Gateway is running."
echo "Token:  $OPENCLAW_GATEWAY_TOKEN"
echo "Config: $OPENCLAW_CONFIG_DIR"
echo ""
echo "Commands:"
echo "  docker compose run --rm openclaw-cli          # open the CLI"
echo "  docker compose logs -f openclaw-gateway       # view logs"
echo "  docker compose down                           # stop"
