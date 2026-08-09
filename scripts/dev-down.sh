#!/usr/bin/env bash
# Stop Core + AI dev processes. Leaves Docker containers alone (use
# `cd datapro && docker compose down` if you want to stop those too).

source "$(dirname "$0")/_lib.sh"

stopped_any=0

for pattern in "datapro_ai.app" "datapro_core.app"; do
  if pgrep -f "$pattern" >/dev/null; then
    log "Stopping ${pattern}..."
    pkill -f "$pattern" || true
    stopped_any=1
  fi
done

# Give the processes a moment to release their ports.
[[ $stopped_any -eq 1 ]] && sleep 1

if [[ $stopped_any -eq 0 ]]; then
  warn "Nothing running. (Containers in docker-compose stay up unless you stop them separately.)"
else
  log "Done. Containers left running — \`cd datapro && docker compose down\` to stop those."
fi
