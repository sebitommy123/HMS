#!/usr/bin/env bash
# Common helpers sourced by the other dev scripts. Don't run this directly.

set -euo pipefail

# Resolve the HMS root no matter where the caller invoked the script from.
HMS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export HMS_ROOT

# Local dev runs Docker through colima on macOS. Set DOCKER_HOST so anything
# that shells out to `docker` (testcontainers included) hits the right socket.
if [[ -z "${DOCKER_HOST:-}" ]] && [[ -S "$HOME/.colima/default/docker.sock" ]]; then
  export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
fi

# Make sure uv is on PATH for non-login shells.
if ! command -v uv >/dev/null 2>&1; then
  if [[ -x "$HOME/.local/bin/uv" ]]; then
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi

# Default ports — override via env if you need to run something on a different
# port (e.g. AirTunes on macOS sometimes binds port 5000).
export CORE_PORT="${CORE_PORT:-5001}"
export AI_PORT="${AI_PORT:-5002}"
export UI_PORT="${UI_PORT:-5174}"
export CORE_URL="${CORE_URL:-http://127.0.0.1:${CORE_PORT}}"
export AI_URL="${AI_URL:-http://127.0.0.1:${AI_PORT}}"

# Ryuk (testcontainers' cleanup sidecar) doesn't reliably work under colima.
# Disable it so testcontainers don't hang waiting for Ryuk to come up.
export TESTCONTAINERS_RYUK_DISABLED="${TESTCONTAINERS_RYUK_DISABLED:-true}"

# Pick up the Anthropic API key from a local-only file if the user hasn't
# already set it in their shell environment. ai/key.sh is gitignored (or
# should be — see below); contributors without one just won't have the key
# auto-loaded, and AI features will return 503 until they set it.
if [[ -z "${ANTHROPIC_API_KEY:-}" ]] && [[ -f "$HMS_ROOT/ai/key.sh" ]]; then
  # shellcheck disable=SC1091
  source "$HMS_ROOT/ai/key.sh"
fi

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

require_docker() {
  docker info >/dev/null 2>&1 || die "Docker not reachable at DOCKER_HOST=${DOCKER_HOST:-default}. Start colima with: colima start"
}

# Wait until $1 (an HTTP URL) responds 2xx-or-503 (503 is acceptable — it
# means the service is up but a dependency is down, which we'll surface via
# the status bar). Times out after $2 seconds (default 30).
wait_for_http() {
  local url="$1"; local timeout="${2:-30}"; local start code
  start=$(date +%s)
  while true; do
    # Don't pass -f: it makes curl exit non-zero on 5xx, hiding the status
    # we actually want to inspect. -sS suppresses progress, prints errors.
    code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
    case "$code" in
      2*|503) return 0 ;;
    esac
    if (( $(date +%s) - start > timeout )); then
      die "Timed out waiting for $url (last HTTP code: $code)"
    fi
    sleep 0.5
  done
}
