#!/usr/bin/env bash
# Generate a new Alembic migration by diffing the SQLAlchemy models against
# Core's dev Postgres. Edit the generated file before committing — autogen
# is a starting point, not the truth.
#
# Usage: scripts/migrate-new.sh "describe the change"

source "$(dirname "$0")/_lib.sh"

[[ $# -lt 1 ]] && die "Usage: scripts/migrate-new.sh \"message\""

require_docker
log "Autogenerating migration: $1"
(cd "$HMS_ROOT/core" && uv run alembic revision --autogenerate -m "$1")
log "Generated. Review the new file under core/alembic/versions/ before committing."
