#!/usr/bin/env bash
# Run the UI dev server in the foreground so its log is visible. Stop with Ctrl+C.

source "$(dirname "$0")/_lib.sh"

cd "$HMS_ROOT/ui"

if [[ ! -d node_modules ]]; then
  log "node_modules missing — running pnpm install..."
  pnpm install
fi

log "Starting UI dev server on port $UI_PORT (Core: $CORE_URL, AI: $AI_URL)..."
VITE_CORE_URL="$CORE_URL" VITE_AI_URL="$AI_URL" pnpm exec vite --port "$UI_PORT" --host 127.0.0.1
