#!/usr/bin/env bash
# Apply pending Alembic migrations to Core's dev Postgres. Run this after
# pulling changes that touched core/src/datapro_core/models.py.

source "$(dirname "$0")/_lib.sh"

require_docker
log "Applying Core migrations..."
(cd "$HMS_ROOT/core" && uv run alembic upgrade head)
