#!/usr/bin/env bash
# Start the full local dev stack: Trino, both Postgres DBs, Core, AI.
# Idempotent — re-running won't error if things are already up. Doesn't start
# the UI dev server (run `scripts/ui-dev.sh` in a separate terminal for that
# so its log isn't tangled with Core/AI output).
#
# Order matters: Core needs Trino + Postgres; AI needs Core + its own Postgres.
# Each step waits on /health before moving on.

source "$(dirname "$0")/_lib.sh"

require_docker

# Build the flex Trino plugin JAR if missing or stale. Maven-in-Docker
# avoids host-level Java + Maven prereqs. Cheap when the .m2 cache is
# warm (a couple of seconds); the first ever build is slow (~30s).
FLEX_JAR="$HMS_ROOT/flex/connector/target/trino-flex-connector-0.1.0.jar"
flex_sources_newer_than_jar() {
  [[ ! -f "$FLEX_JAR" ]] && return 0
  # Compare against the youngest source file under flex/connector/src.
  local newest
  newest=$(find "$HMS_ROOT/flex/connector/src" "$HMS_ROOT/flex/connector/pom.xml" -type f -newer "$FLEX_JAR" 2>/dev/null | head -n1)
  [[ -n "$newest" ]]
}
if flex_sources_newer_than_jar; then
  log "Building flex connector JAR..."
  (
    cd "$HMS_ROOT/flex/connector" && docker run --rm \
      -v "$(pwd):/work" \
      -v "$(pwd)/.m2:/root/.m2" \
      -w /work \
      maven:3.9-eclipse-temurin-25-alpine \
      mvn -B -DskipTests package >/dev/null
  )
fi

log "Bringing up Postgres + Trino containers (docker compose up -d)..."
# --build ensures the custom trino-flex image is rebuilt whenever the
# Dockerfile, the Python runtime, or the flex JAR has changed. Docker's
# layer cache makes the common case (no changes) near-instant.
(cd "$HMS_ROOT/datapro" && docker compose up -d --build)

log "Waiting for Trino to be reachable..."
wait_for_http "http://127.0.0.1:8080/v1/info" 120

# Run migrations against Core's Postgres so the schema is fresh.
log "Running Core Alembic migrations..."
(cd "$HMS_ROOT/core" && uv run alembic upgrade head)

if pgrep -f "datapro_core.app" >/dev/null; then
  warn "Core appears to already be running. Skipping launch."
else
  log "Starting Core on port $CORE_PORT..."
  mkdir -p "$HMS_ROOT/.dev-logs"
  (
    cd "$HMS_ROOT/core"
    nohup uv run flask --app datapro_core.app run \
      --host 0.0.0.0 --port "$CORE_PORT" \
      > "$HMS_ROOT/.dev-logs/core.log" 2>&1 &
  )
fi

log "Waiting for Core /health..."
wait_for_http "$CORE_URL/health" 30

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  warn "ANTHROPIC_API_KEY not set — AI will return 503 from /messages endpoints."
  warn "  Set it in your shell with: export ANTHROPIC_API_KEY=sk-ant-..."
fi

if pgrep -f "datapro_ai.app" >/dev/null; then
  warn "AI service appears to already be running. Skipping launch."
else
  log "Starting AI on port $AI_PORT..."
  mkdir -p "$HMS_ROOT/.dev-logs"
  (
    cd "$HMS_ROOT/ai"
    nohup uv run flask --app datapro_ai.app run \
      --host 0.0.0.0 --port "$AI_PORT" \
      > "$HMS_ROOT/.dev-logs/ai.log" 2>&1 &
  )
fi

log "Waiting for AI /health..."
wait_for_http "$AI_URL/health" 30

log "Stack is up:"
printf "  Core: %s/health\n" "$CORE_URL"
printf "  AI:   %s/health\n" "$AI_URL"
printf "  UI dev server: run \033[1mscripts/ui-dev.sh\033[0m in a separate terminal.\n"
printf "  Logs: tail -f %s/.dev-logs/{core,ai}.log\n" "$HMS_ROOT"
