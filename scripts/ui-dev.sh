#!/usr/bin/env bash
# Run the UI dev server in the foreground so its log is visible. Stop with Ctrl+C.

source "$(dirname "$0")/_lib.sh"

cd "$HMS_ROOT/ui"

if [[ ! -d node_modules ]]; then
  log "node_modules missing — running pnpm install..."
  pnpm install
fi

# Same-origin reverse proxy: the app talks to /api/core and /api/ai on its own
# origin, and Vite proxies those to Core/AI (targets from CORE_URL/AI_URL). This
# means the whole stack is reachable from a single URL with no CORS config — set
# UI_HOST=0.0.0.0 to make the dev server LAN-reachable.
log "Starting UI dev server on port $UI_PORT (proxying /api/core→$CORE_URL, /api/ai→$AI_URL)..."
VITE_CORE_URL="/api/core" VITE_AI_URL="/api/ai" \
  pnpm exec vite --port "$UI_PORT" --host "${UI_HOST:-127.0.0.1}"
