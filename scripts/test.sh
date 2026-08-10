#!/usr/bin/env bash
# Run the full test suite — Core integration, AI tools, UI components.
# Pass an arg to limit scope: core | ai | ui | perf (default: all).
# `perf` is the query performance suite; it's excluded from `all`.

source "$(dirname "$0")/_lib.sh"

scope="${1:-all}"

# Wipe orphan testcontainers FIRST. With Ryuk disabled (required under colima)
# old containers leak between runs, and stale port mappings make new test
# runs fail with "Connection refused" errors against ports nothing's
# listening on. The hms-* containers are the persistent dev DBs — keep those.
log "Clearing orphan testcontainers..."
require_docker
docker ps -a --format '{{.Names}}' \
  | grep -vE '^(hms-)' \
  | xargs -r docker rm -f >/dev/null 2>&1 || true

run_core() {
  log "Running Core tests..."
  (cd "$HMS_ROOT/core" && uv run pytest "$@")
}

run_ai() {
  log "Running AI tests..."
  # AI tool tests need a live Core; integration tests also need ANTHROPIC_API_KEY.
  if ! curl -fsS "$CORE_URL/health" >/dev/null 2>&1; then
    warn "Core isn't running at $CORE_URL — AI tool tests will skip. Start with scripts/dev-up.sh."
  fi
  (cd "$HMS_ROOT/ai" && CORE_URL="$CORE_URL" uv run pytest "$@")
}

run_ui() {
  log "Running UI tests..."
  (cd "$HMS_ROOT/ui" && pnpm exec tsc --noEmit && pnpm exec vitest run "$@")
}

run_perf() {
  log "Running Core query-perf suite (tracked baselines, non-blocking)..."
  # Seeds a large Postgres and measures the semantic query path. `-s` so the
  # baseline report prints. Excluded from `all` — run it explicitly.
  (cd "$HMS_ROOT/core" && uv run pytest -m perf -s "$@")
}

case "$scope" in
  core)  shift || true; run_core "$@" ;;
  ai)    shift || true; run_ai   "$@" ;;
  ui)    shift || true; run_ui   "$@" ;;
  perf)  shift || true; run_perf "$@" ;;
  all)
    run_core
    run_ai
    run_ui
    ;;
  *) die "Unknown scope: $scope. Use one of: core, ai, ui, perf, all." ;;
esac
