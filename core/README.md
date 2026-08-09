# DataPro Core

The data plane of DataPro (see `../datapro_revised_v2.md` § DataPro Core, AI, and UI).

A Flask HTTP API backed by Postgres that manages the desired set of Trino catalogs
and reconciles Trino against that desired state. The Trino instance in `../datapro/`
uses `catalog.management=dynamic` + `catalog.store=memory`, so Trino loses all
catalogs on restart. Core is what makes that survivable — Postgres holds the source
of truth; the reconciler replays it.

## Deployment model: single instance, by design

Core runs as **one process per deployment.** Not two, not a load-balanced fleet.
This is intentional:

- Core does no heavy lifting. Trino does the federation, execution, joins, and
  aggregation; Core is a thin coordinator that manages catalogs, dispatches
  queries, and shapes results. None of those need horizontal scale.
- Catalog management is intrinsically low-throughput — operators register
  catalogs occasionally, not thousands of times per second.
- Trino itself scales horizontally (coordinator + workers). When you outgrow a
  deployment, you scale Trino, not Core.

Concrete consequences:

- No `pg_advisory_lock` or other cross-process coordination in the reconciler.
- The 60-second heartbeat reconcile and an in-flight `POST /catalogs` *can*
  race within a single process; the worst case is a transient
  "catalog already exists" / "catalog not found" error that the next reconcile
  resolves. No data corruption.
- High availability comes from Postgres + Trino being independently HA, plus
  Core being fast to restart (it holds no in-memory state that isn't in Postgres).
- If a customer ever genuinely needs two Cores (multi-region active-active),
  that becomes a real distributed-systems redesign, not a replica-count toggle.

This assumption is load-bearing for several Phase-0 design choices. Don't add
multi-instance support speculatively — first prove the workload demands it.

See `../datapro_revised_v2.md` § DataPro Core > Single-instance by design.

## Vocabulary

This codebase mirrors Trino's strict distinction
(see `../datapro_revised_v2.md` § Vocabulary):

- **Connector** — a Trino plugin (`postgresql`, `tpch`, the future `logs` stock
  connector). One connector serves many catalogs.
- **Catalog** — a named, configured instance that picks a connector and binds it to
  a backend with specific properties. What Core manages.

## What lives here

```
core/
├── src/datapro_core/
│   ├── app.py                # Flask app factory, /health, heartbeat
│   ├── config.py             # env-driven config
│   ├── db.py                 # SQLAlchemy engine + session
│   ├── models.py             # Catalog ORM model
│   ├── schemas.py            # Pydantic request validation
│   ├── trino_client.py       # live Trino client (DDL + state read)
│   ├── reconciler.py         # diff (desired, actual) + apply
│   └── api/
│       ├── catalogs.py       # /catalogs CRUD
│       ├── reconcile.py      # /reconcile
│       └── trino_state.py    # /trino/state
├── tests/
│   ├── unit/                 # pure-function tests, milliseconds
│   └── integration/          # real Postgres + real Trino, no mocks
├── alembic/                  # Postgres migrations
├── scripts/demo.sh           # end-to-end curl demo
├── docker-compose.yml        # Core's Postgres (and Core itself)
├── Dockerfile
├── Makefile
└── pyproject.toml
```

## Quickstart

Prereqs: Docker (the codebase was developed against colima), Python 3.12.

```bash
# 1. Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. From this directory: install deps
make install

# 3. Start Core's Postgres
make up

# 4. Apply migrations
make migrate

# 5. Make sure Trino is running too (one directory up)
cd ../datapro && docker compose up -d && cd -

# 6. Run Core (dev server, port 5001 to avoid macOS AirTunes on 5000)
uv run flask --app datapro_core.app run --host 0.0.0.0 --port 5001

# 7. In another shell: run the end-to-end demo
CORE=http://127.0.0.1:5001 bash scripts/demo.sh
```

The demo:
1. Registers a `tpch_demo` catalog via `POST /catalogs`
2. Verifies Trino has it
3. Drops it out-of-band in Trino (simulating a Trino restart wipe)
4. Calls `POST /reconcile`
5. Verifies the catalog is back

## API

| Method  | Path                  | What                                                       |
|---------|-----------------------|------------------------------------------------------------|
| GET     | `/health`             | Pings Postgres + Trino. 200 if both reachable, 503 else.    |
| GET     | `/catalogs`           | All registered catalogs from Postgres.                      |
| GET     | `/catalogs/<name>`    | One catalog, or 404.                                        |
| POST    | `/catalogs`           | Register a catalog. Validates, persists, reconciles. 201 / 409 / 400 / 502. |
| DELETE  | `/catalogs/<name>`    | Soft-delete + reconcile (drops from Trino).                 |
| POST    | `/reconcile`          | Diff Postgres vs Trino and apply CREATE/DROP actions.       |
| GET     | `/trino/state`        | Live snapshot of Trino's actual catalogs.                   |

`POST /catalogs` body:

```json
{
  "name": "tpch_demo",
  "connector": "tpch",
  "properties": { "connection-url": "jdbc:postgresql://h/db" }
}
```

## Tests

```bash
make test-unit          # ~50ms, no Docker needed
make test-integration   # ~10s, real Postgres + real Trino (testcontainers)
make test-slow          # real Trino container restart
make test-all           # everything
```

Integration tests use **live Postgres + live Trino, no mocks**. The Trino test
container mounts `../datapro/trino/etc/config.properties` so it has the same
`catalog.store=memory` behavior as production.

Tests cover the six Phase-0 invariants:

1. Happy path: POST → 201, row persisted, Trino has it
2. Idempotent reconcile: second `POST /reconcile` produces zero actions
3. Convergence after drift (the headline invariant): out-of-band drop in Trino →
   reconcile → catalog returns
4. Broken catalog: bad properties → row marked `broken` with `last_error`
5. DELETE drops from Trino
6. Persistence across Core restart: new Core instance reconciles back to desired state

Plus a slow test that actually `docker restart`s the Trino container and verifies
end-to-end recovery (`pytest -m slow`).

Plus a real-backend test (`tests/integration/test_postgresql_catalog.py`) that:

- Spins up its own Postgres testcontainer on a shared docker network with Trino
- Seeds it with SEC-EDGAR-shaped data (a small `companies` table)
- Registers a `postgresql` catalog via `POST /catalogs` with real credentials
- Runs `SHOW SCHEMAS`, `SHOW TABLES`, `SELECT`, `COUNT(*)`, and a cross-catalog
  query against `tpch` through Trino
- Verifies real rows come back from the real backend

Plus partial-sync coverage (`tests/integration/test_partial_sync.py`):

- **Extra catalog in Trino → reconcile drops it.** Someone created a catalog
  out-of-band; Core asserts itself as source of truth and removes it.
- **Name match but wrong connector → reconcile drops + re-creates.** Postgres
  says `foo: tpch`, Trino actually has `foo: memory`. Reconcile emits the
  correct DROP-then-CREATE pair.
- **Partial failure → other actions still run.** When one CREATE fails, the
  others complete. Failed row is marked `broken` with the actual Trino error;
  successful rows are `enabled`.
- **Trino unreachable → clean failure.** Reconcile returns 5xx without partial
  Postgres mutation.
- **The boss test: missing + extra + connector-mismatch all at once → fixed in
  one pass.** Three independent drift classes converged by a single reconcile.

What's **deliberately not detected** (Phase-0 known limit):

- **Same name + same connector + different WITH-clause properties.** Trino's
  `system.metadata` doesn't expose catalog properties, so we can't see drift on
  the body. A future PATCH /catalogs API will need to force DROP+CREATE on
  property changes explicitly. Until then, if someone changes properties out of
  band in Trino, Core stays oblivious.

## Colima notes

The codebase was tested against colima. Two things to know:

1. `DOCKER_HOST=unix:///Users/sebi/.colima/default/docker.sock` — testcontainers
   doesn't auto-detect this. The Makefile sets it.
2. `TESTCONTAINERS_RYUK_DISABLED=true` — Ryuk's socket mount fails on colima.
   The Makefile sets this too. Orphan containers won't auto-clean; `docker ps -a`
   and clean manually if needed.

## What's deliberately out of scope (Phase 0)

- The DataPro semantic layer that bolts on top of a catalog (object types, trait
  mappings, safety contracts, link store) — Phases 1b+.
- AI, MCP, UI, time series — later milestones.
- Auth, RBAC, multi-customer, multi-Trino.
- Property-level drift detection on reconcile (Trino doesn't expose catalog
  properties via introspection). For now, an updated row triggers DROP + CREATE
  through the API; pure reconcile only catches existence-level drift.
- Production deployment story (the dev server is the dev server).

## Status

Phase 0 — catalog reconciler is **done and tested**. 30/30 tests pass against
live Postgres + live Trino, including the slow restart-recovery test.

Next: extend with the semantic layer (object types, traits) or with stock connector
support (logs first). Both are independent expansions of the same foundation.
