# scripts/

Common dev tasks, factored out so contributors don't have to re-derive every
command. All scripts source `_lib.sh` for shared env (DOCKER_HOST under
colima, port defaults, helpers).

## First-time setup

```bash
# Start colima if you haven't (Docker Desktop also works — DOCKER_HOST will
# auto-pick the colima socket if it's present, otherwise the default).
colima start

# Backends (Core + AI) use uv-managed venvs; UI uses pnpm.
# These come from each subproject's own install steps:
(cd core && uv sync)
(cd ai && uv sync)
(cd ui && pnpm install)
```

## Day-to-day

```bash
scripts/dev-up.sh         # Start Trino + Postgres + Core + AI. Idempotent.
scripts/ui-dev.sh         # Run the UI dev server (foreground, Ctrl+C to stop).
scripts/dev-down.sh       # Stop Core + AI. Leaves Docker containers running.
scripts/dev-restart.sh    # dev-down + dev-up. Use after code changes.
scripts/migrate.sh        # Apply Alembic migrations to Core's dev DB.
scripts/migrate-new.sh "your message"   # Autogenerate a new Alembic migration.
scripts/test.sh           # Run everything (core + ai + ui).
scripts/test.sh core      # Just one slice. Same with `ai` or `ui`.
scripts/test.sh core tests/integration/test_object_factories.py  # Pass-through args.
scripts/clean-testcontainers.sh    # Wipe leaked test containers (see below).
```

## Env vars worth knowing

| Var | Default | What it does |
|-----|---------|--------------|
| `ANTHROPIC_API_KEY` | — | Required for live AI tests + the chat feature. The AI service returns 503 from its messages endpoints if it's unset. |
| `CORE_PORT` | `5001` | Core HTTP port. |
| `AI_PORT` | `5002` | AI HTTP port. |
| `UI_PORT` | `5174` | UI dev server port. (5000 is AirTunes on macOS, 5173 is sometimes claimed.) |
| `DOCKER_HOST` | auto | Auto-set to the colima socket if present. Override if you use a different Docker. |
| `TESTCONTAINERS_RYUK_DISABLED` | `true` | Ryuk doesn't play well with colima; keep it off. |

## Known gotchas

**"Connection refused" against a port nothing's listening on, in tests.**
Ryuk is disabled (required under colima), so test runs that crash or time out
leak their containers. Stale port mappings then confuse the next testcontainers
session — the library reports a port that no live container is bound to.
Fix: `scripts/clean-testcontainers.sh`. If that doesn't help, restart colima:
`colima restart`.

**Port 5000 in use.** macOS AirTunes binds it. That's why the dev defaults are
5001 (Core), 5002 (AI), 5174 (UI), not the round numbers.

**Alembic autogen produced an empty migration.** Make sure the new model is
actually imported somewhere reachable from `datapro_core.db.Base` — autogen
only sees tables registered against `Base.metadata`.
