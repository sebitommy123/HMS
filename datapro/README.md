# HMS · datapro — empty Trino for DataPro

Minimal **single-node Trino** in Docker: nothing to federate until **DataPro Core** (or you, manually) runs **`CREATE CATALOG`**. This is the process boundary DataPro builds on — not the product itself.

## What you get

- **`trinodb/trino:latest`** on `http://localhost:$TRINO_PORT` (per checkout — `scripts/hms.py ls`; the main clone's is 5004)
- **`catalog.management=dynamic`** + **`catalog.store=memory`** — same shape as production-oriented control-plane replay: catalogs are defined in SQL, live in RAM, and **disappear on coordinator restart** until replayed.
- **`SHOW CATALOGS`** after a fresh `docker compose up` → **`system` only** (plus anything you `CREATE` in that session). With **`catalog.store=memory`**, Trino does **not** load catalogs from the image’s `*.properties` files on startup — only `CREATE CATALOG` matters, so you do **not** need an empty `trino/etc/catalog/` folder or volume.

A sample custom connector for experiments lives under **`../examples/log-connector/`**; it is **not** wired into this compose file.

See **`datapro_revised_v2.md`** for how DataPro owns metadata and replays catalogs against a customer Trino.

## Bring it up

```bash
cd "HMS/datapro"
docker compose up -d
```

Wait until healthy (~30–60 s on first pull):

```bash
docker compose ps
docker inspect -f '{{.State.Health.Status}}' hms-$HMS_STACK-trino
```

## Tear it down

```bash
docker compose down
```

## Sanity check

```bash
docker exec hms-$HMS_STACK-trino trino --execute "SHOW CATALOGS"
# expect: "system"
```

Web UI: `http://localhost:$TRINO_PORT` (dev image; user `trino`).

> Container names and ports are per checkout — every worktree runs its own
> Trino. `eval "$(scripts/hms.py env)"` fills in `$HMS_STACK` / `$TRINO_PORT`
> for the checkout you're in; `scripts/hms.py ls` shows them all.

## Manual smoke: add `tpch` without DataPro

```sql
CREATE CATALOG tpch USING tpch;
SELECT COUNT(*) FROM tpch.tiny.nation;
DROP CATALOG tpch;
```

([`CREATE CATALOG`](https://trino.io/docs/current/sql/create-catalog.html) is experimental; full statements are logged — use [secrets](https://trino.io/docs/current/security/secrets.html) for real credentials.)

## Layout

```
datapro/
├── docker-compose.yml
└── trino/
    └── etc/
        └── config.properties
```

## Troubleshooting

- **Docker** — e.g. `colima start --cpu 4 --memory 6` if the daemon is down.
- **OOM** — `docker logs hms-$HMS_STACK-trino`; give the VM more RAM. Note that
  each running worktree stack has its own Trino, so several at once add up.
