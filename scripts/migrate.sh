#!/usr/bin/env bash
# Apply Alembic migrations to this checkout's Core Postgres.
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" migrate "$@"
