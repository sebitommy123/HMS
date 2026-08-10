"""Performance-suite fixtures: a large, seeded Postgres queried through Trino.

This is a separate pytest area (marker: `perf`, excluded from normal runs). It
reuses the session-scoped `trino` + `docker_network` fixtures from the parent
`core/tests/conftest.py` and adds:

  - `perf_postgres` — a Postgres on Trino's docker network, seeded once via
    `generate_series` to PERF_SCALE_ROWS rows across two `instruments` tables
    that share an `optiver_id` key (plus a tiny table for the fast-path floor).
  - `perf_app` / `perf_client` — a session-scoped Core app so the whole bench
    environment is built exactly once (the parent's `core_app` is per-test).
  - `bench` — registers TWO postgresql catalogs at the same Postgres and builds
    the object types + factories the scenarios query. Two catalogs is deliberate:
    a cross-catalog identity join can't be pushed into Postgres, so Trino is
    forced to execute the FULL OUTER JOIN itself — faithfully reproducing the
    production timeout.

The parent `_reset` (autouse, truncates + drops catalogs after every test) is
shadowed here with a no-op: perf scenarios are read-only queries that share one
session bench environment, and the session containers are torn down at the end.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from datapro_core.app import create_app
from datapro_core.trino_client import TrinoClient

from . import harness

PERF_PG_ALIAS = "perf-pg"
PERF_PG_B_ALIAS = "perf-pg-b"
PERF_PG_DB = "bench"
PERF_PG_USER = "bench"
PERF_PG_PASSWORD = "bench"
SMALL_ROWS = 200


# --------------------------------------------------------------------------
# Seeded Postgres
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def perf_postgres(docker_network) -> Iterator[PostgresContainer]:
    """Large seeded Postgres on Trino's network. Seeded once per session."""
    pg = (
        PostgresContainer(
            "postgres:16",
            driver="psycopg2",
            username=PERF_PG_USER,
            password=PERF_PG_PASSWORD,
            dbname=PERF_PG_DB,
        )
        .with_network(docker_network)
        .with_network_aliases(PERF_PG_ALIAS)
    )
    pg.start()
    try:
        _wait_ready(pg)
        _seed(pg, rows=harness.scale_rows())
        yield pg
    finally:
        pg.stop()


def _dsn(pg: PostgresContainer) -> str:
    host = pg.get_container_host_ip()
    port = pg.get_exposed_port(5432)
    return (
        f"host={host} port={port} dbname={PERF_PG_DB} "
        f"user={PERF_PG_USER} password={PERF_PG_PASSWORD}"
    )


def _wait_ready(pg: PostgresContainer, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with psycopg.connect(_dsn(pg), connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("perf postgres did not become ready in time")


def _seed(pg: PostgresContainer, *, rows: int) -> None:
    """Seed two large tables sharing `optiver_id`, plus a tiny table.

    Values are deterministic functions of the series index (reproducible, no
    randomness). The two large tables' key ranges half-overlap so a FULL OUTER
    JOIN yields unmatched rows on both sides — the realistic stitch shape.
    """
    half = rows // 2
    with psycopg.connect(_dsn(pg), autocommit=True) as conn, conn.cursor() as cur:
        _create_instruments_a(cur, rows)
        _create_instruments_b(cur, rows, half)
        _create_instruments_small(cur)


def _seed_b_backend(pg: PostgresContainer, *, rows: int) -> None:
    """Seed ONLY instruments_b on a second Postgres backend, for the federated
    (two genuinely separate databases) identity scenario."""
    half = rows // 2
    with psycopg.connect(_dsn(pg), autocommit=True) as conn, conn.cursor() as cur:
        _create_instruments_b(cur, rows, half)


def _create_instruments_a(cur, rows: int) -> None:
    # keys 1..rows
    cur.execute("DROP TABLE IF EXISTS instruments_a")
    cur.execute(
        """
        CREATE TABLE instruments_a (
            optiver_id BIGINT PRIMARY KEY,
            feedcode   TEXT   NOT NULL,
            symbol     TEXT   NOT NULL,
            strike     DOUBLE PRECISION
        )
        """
    )
    cur.execute(
        f"""
        INSERT INTO instruments_a (optiver_id, feedcode, symbol, strike)
        SELECT g, 'FEED' || g, 'SYM' || (g % 5000), ((g % 1000) * 0.5)::double precision
        FROM generate_series(1, {int(rows)}) AS g
        """
    )


def _create_instruments_b(cur, rows: int, half: int) -> None:
    # keys half+1 .. rows+half (half-overlapping with instruments_a's range, so a
    # FULL OUTER JOIN has unmatched rows on both sides)
    cur.execute("DROP TABLE IF EXISTS instruments_b")
    cur.execute(
        """
        CREATE TABLE instruments_b (
            optiver_id BIGINT PRIMARY KEY,
            exchange   TEXT NOT NULL,
            expiry     DATE
        )
        """
    )
    cur.execute(
        f"""
        INSERT INTO instruments_b (optiver_id, exchange, expiry)
        SELECT g, (ARRAY['CBOE','NYSE','NASDAQ','EUREX'])[1 + (g % 4)], DATE '2026-01-01' + (g % 365)
        FROM generate_series({int(half) + 1}, {int(rows) + int(half)}) AS g
        """
    )


def _create_instruments_small(cur) -> None:
    cur.execute("DROP TABLE IF EXISTS instruments_small")
    cur.execute(
        """
        CREATE TABLE instruments_small (
            optiver_id BIGINT PRIMARY KEY,
            feedcode   TEXT NOT NULL,
            symbol     TEXT NOT NULL
        )
        """
    )
    cur.execute(
        f"""
        INSERT INTO instruments_small (optiver_id, feedcode, symbol)
        SELECT g, 'FEED' || g, 'SYM' || g
        FROM generate_series(1, {SMALL_ROWS}) AS g
        """
    )


@pytest.fixture(scope="session")
def perf_postgres_b(docker_network) -> Iterator[PostgresContainer]:
    """Second Postgres backend on Trino's network — for the federated (two
    separate databases) identity scenario. Seeded with instruments_b only."""
    pg = (
        PostgresContainer(
            "postgres:16",
            driver="psycopg2",
            username=PERF_PG_USER,
            password=PERF_PG_PASSWORD,
            dbname=PERF_PG_DB,
        )
        .with_network(docker_network)
        .with_network_aliases(PERF_PG_B_ALIAS)
    )
    pg.start()
    try:
        _wait_ready(pg)
        _seed_b_backend(pg, rows=harness.scale_rows())
        yield pg
    finally:
        pg.stop()


# --------------------------------------------------------------------------
# Session-scoped Core app / client (built once — perf is read-only)
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def perf_app(config, trino):
    app = create_app(config, start_heartbeat=False, run_startup_reconcile=False)
    app.extensions["trino"] = TrinoClient(
        host=trino.get_container_host_ip(),
        port=int(trino.get_exposed_port(8080)),
        user=config.trino_user,
    )
    return app


@pytest.fixture(scope="session")
def perf_client(perf_app):
    return perf_app.test_client()


@pytest.fixture(autouse=True)
def _reset():
    """Shadow the parent conftest's per-test truncate/drop. Perf scenarios share
    one session bench environment and only read from it; the session containers
    are discarded at the end, so no per-test cleanup is needed."""
    yield


# --------------------------------------------------------------------------
# Bench environment: catalogs + object types + factories
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bench:
    small_type: str  # single factory over the tiny table
    large_type: str  # single factory over a large table (UNION path)
    joined_type: str  # identity, two factories across two catalogs, same backend
    federated_type: str  # identity, two factories across two separate backends
    table_a: str  # fully-qualified path for raw-SQL scenarios


@pytest.fixture(scope="session")
def bench(perf_client, perf_postgres, perf_postgres_b) -> Iterator[Bench]:
    c = perf_client

    def register_pg_catalog(name: str, alias: str) -> None:
        r = c.post(
            "/catalogs",
            json={
                "name": name,
                "connector": "postgresql",
                "properties": {
                    "connection-url": f"jdbc:postgresql://{alias}:5432/{PERF_PG_DB}",
                    "connection-user": PERF_PG_USER,
                    "connection-password": PERF_PG_PASSWORD,
                },
            },
        )
        assert r.status_code == 201, r.get_json()

    # bench_a / bench_b point at the SAME seeded Postgres (two catalogs so the
    # identity join is cross-catalog → no pushdown). bench_fed points at a
    # SEPARATE Postgres backend for the genuinely-federated variant.
    register_pg_catalog("bench_a", PERF_PG_ALIAS)
    register_pg_catalog("bench_b", PERF_PG_ALIAS)
    register_pg_catalog("bench_fed", PERF_PG_B_ALIAS)

    def ds(catalog: str, table: str) -> str:
        rows = c.get(f"/data-sources?catalog={catalog}").get_json()
        for row in rows:
            if row["schema_name"] == "public" and row["table_name"] == table:
                return row["id"]
        raise AssertionError(f"{catalog}.public.{table} not discovered: {rows}")

    def make_type(name: str) -> str:
        return c.post("/object-types", json={"name": name}).get_json()["id"]

    def make_factory(source_id: str, type_id: str, **kwargs) -> None:
        body = {"data_source_id": source_id, "object_type_id": type_id, **kwargs}
        r = c.post("/object-factories", json=body)
        assert r.status_code == 201, r.get_json()

    # Fast-path floor: single factory over the tiny table.
    small = make_type("InstrumentSmall")
    make_factory(ds("bench_a", "instruments_small"), small)

    # UNION path at scale: single factory over a large table (LIMIT pushdown
    # should keep this fast).
    large = make_type("InstrumentLarge")
    make_factory(ds("bench_a", "instruments_a"), large)

    # Identity JOIN path: two factories across two catalogs, keyed on optiver_id.
    joined = make_type("Instrument")
    c.put(f"/object-types/{joined}/traits/identity")
    make_factory(
        ds("bench_a", "instruments_a"), joined,
        trait_config={"identity": {"column": "optiver_id"}},
    )
    make_factory(
        ds("bench_b", "instruments_b"), joined,
        trait_config={"identity": {"column": "optiver_id"}},
    )

    # Federated identity JOIN: same shape but across TWO separate Postgres
    # backends (bench_a → pg_a, bench_fed → pg_b) — genuine cross-database
    # federation. Confirms the pre-join key bound holds across real backends.
    federated = make_type("InstrumentFederated")
    c.put(f"/object-types/{federated}/traits/identity")
    make_factory(
        ds("bench_a", "instruments_a"), federated,
        trait_config={"identity": {"column": "optiver_id"}},
    )
    make_factory(
        ds("bench_fed", "instruments_b"), federated,
        trait_config={"identity": {"column": "optiver_id"}},
    )

    # Warm Trino's JIT + the JDBC connection pools before any measurement, so
    # the one-time cold-start cost doesn't land on whichever fast scenario runs
    # first and make its baseline noisy. (The identity path is intentionally not
    # warmed — its cost dwarfs warmup and we want its true first-hit latency.)
    for _ in range(2):
        harness.semantic_query(c, "InstrumentSmall", limit=5)
        harness.semantic_query(c, "InstrumentLarge", limit=5)
        harness.raw_query(
            c, "SELECT count(*) FROM bench_a.public.instruments_a", max_rows=1
        )

    try:
        yield Bench(
            small_type="InstrumentSmall",
            large_type="InstrumentLarge",
            joined_type="Instrument",
            federated_type="InstrumentFederated",
            table_a="bench_a.public.instruments_a",
        )
    finally:
        for name in ("bench_a", "bench_b", "bench_fed"):
            try:
                c.delete(f"/catalogs/{name}")
            except Exception:
                pass


# --------------------------------------------------------------------------
# Reporting hooks
# --------------------------------------------------------------------------


def pytest_terminal_summary(terminalreporter):
    harness.finalize(terminalreporter.write_line)
