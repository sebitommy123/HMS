#!/usr/bin/env bash
# Wipe leftover test containers. Safe to run anytime — only touches
# containers NOT prefixed with `hms-`, so the persistent dev DBs/Trino are
# untouched. Use this when test runs fail with mysterious "Connection refused"
# on a random port that nothing's actually listening on.
#
# Background: Ryuk (testcontainers' auto-cleanup sidecar) doesn't behave well
# under colima, so we disable it (see _lib.sh). With Ryuk off, test runs that
# crash or time out leak their containers — and the stale port mappings then
# confuse the next testcontainers session.

source "$(dirname "$0")/_lib.sh"

require_docker

leftover="$(docker ps -a --format '{{.Names}}' | grep -vE '^(hms-)' || true)"
if [[ -z "$leftover" ]]; then
  log "Nothing to clean."
  exit 0
fi

log "Removing the following containers:"
printf '  %s\n' $leftover
echo "$leftover" | xargs -r docker rm -f >/dev/null
log "Done."
