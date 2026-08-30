#!/usr/bin/env bash
# Start the full local dev stack for this checkout (Trino, both Postgres DBs, Core, AI).
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" up "$@"
