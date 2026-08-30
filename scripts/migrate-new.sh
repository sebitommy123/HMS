#!/usr/bin/env bash
# Autogenerate a new Alembic migration. Usage: scripts/migrate-new.sh "message"
#
# Thin shim onto the real implementation in scripts/hms.py, kept so the
# familiar entry point kept working. `scripts/hms.py --help` lists everything.
exec python3 "$(dirname "$0")/hms.py" migrate-new "$@"
