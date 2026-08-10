"""Query-engine performance scenarios.

Each scenario is a thin call into the harness against the session-scoped `bench`
environment (see conftest). Nothing here asserts on time — this is a *tracked
baseline* suite: results are measured, compared to a committed baseline, and
regressions surface as warnings + a terminal report (harness.finalize).

Scenario order matters: the heavier identity-join scenarios (and the reconcile
scenario) run AFTER the fast-path ones. Historically the unbounded identity join
could exhaust Trino's memory and fail outright, contaminating any fast scenario
after it; the engine now bounds it, but keeping the order is cheap insurance.

Run with:  uv run pytest -m perf -s     (add PERF_SCALE_ROWS=200000 for a fast pass)
Update the baseline:  PERF_UPDATE_BASELINE=1 uv run pytest -m perf
"""

import os

import pytest

from . import harness

pytestmark = pytest.mark.perf

# Number of tables the reconciler-sync scenario declares (via a flex catalog).
MANY_TABLES = int(os.environ.get("PERF_SYNC_TABLES", "300"))


def test_union_single_factory_small(bench, perf_client):
    """Fast-path floor: single factory over a tiny table. Establishes the
    fixed per-query overhead (app + Trino round-trip)."""
    harness.measure(
        "union_single_factory_small",
        budget_ms=1500,
        fn=lambda: harness.semantic_query(perf_client, bench.small_type, limit=25),
    )


def test_union_single_factory_large(bench, perf_client):
    """UNION path at scale. The single-branch SELECT carries the LIMIT, which
    Trino's postgresql connector pushes into Postgres — so this should stay fast
    even over a multi-million-row table. This is the contrast that makes the
    identity-join regression legible."""
    harness.measure(
        "union_single_factory_large",
        budget_ms=3000,
        fn=lambda: harness.semantic_query(perf_client, bench.large_type, limit=25),
    )


def test_raw_trino_scan_large(bench, perf_client):
    """Raw Trino path: bounded scan of a large table. LIMIT pushes to Postgres."""
    sql = f"SELECT * FROM {bench.table_a} LIMIT 10000"
    harness.measure(
        "raw_trino_scan_large",
        budget_ms=3000,
        fn=lambda: harness.raw_query(perf_client, sql, max_rows=10_000),
    )


def test_raw_trino_count_large(bench, perf_client):
    """Raw Trino path: count(*) over a large table (the 'count blew past 30s'
    symptom). Trino pushes the aggregate to Postgres when it can — this tracks
    whether that pushdown keeps holding."""
    sql = f"SELECT count(*) FROM {bench.table_a}"
    harness.measure(
        "raw_trino_count_large",
        budget_ms=15_000,
        fn=lambda: harness.raw_query(perf_client, sql, max_rows=10),
    )


def test_identity_join_two_factories_large(bench, perf_client):
    """FLAGSHIP guard. Identity trait + two factories across two catalogs → a
    FULL OUTER JOIN. Unbounded, this scanned both full tables (LIMIT only on the
    outer SELECT) and timed out; the engine now bounds the identity key set
    before the join (see query/sql_builder.py) so it stays well under budget.
    This scenario guards that bound from regressing back to a full scan."""
    harness.measure(
        "identity_join_two_factories_large",
        budget_ms=5000,
        # 15s request budget: a backstop that would catch a regression back to a
        # full scan (which times out) rather than waiting the default 30s.
        fn=lambda: harness.semantic_query(
            perf_client, bench.joined_type, limit=25, timeout_seconds=15
        ),
        warmup=1,
        iterations=4,
    )


def test_identity_join_federated_two_postgres(bench, perf_client):
    """Federated variant of the flagship: the two identity factories live on two
    GENUINELY SEPARATE Postgres backends (bench_a → pg_a, bench_fed → pg_b), so
    Trino federates a real cross-database join. Confirms the pre-join key bound
    holds across distinct backends (dynamic filtering pushed to each)."""
    harness.measure(
        "identity_join_federated_two_postgres",
        budget_ms=5000,
        fn=lambda: harness.semantic_query(
            perf_client, bench.federated_type, limit=25, timeout_seconds=15
        ),
        warmup=1,
        iterations=4,
    )


def test_catalog_sync_many_tables(perf_client):
    """Reconciler efficiency: registering a catalog runs reconcile +
    sync_data_sources synchronously. Use a flex catalog that declares
    MANY_TABLES tables (no extra database needed) and measure how long syncing
    that many data sources takes. Single-shot — a catalog name can't be
    re-registered within a session."""
    body = {
        "name": "bench_many",
        "connector": "flex",
        "source": _many_tables_flex_module(MANY_TABLES),
    }
    harness.measure(
        "catalog_sync_many_tables",
        budget_ms=10_000,
        fn=lambda: harness.register_catalog(perf_client, body),
        warmup=0,
        iterations=1,
    )


def _many_tables_flex_module(n: int) -> str:
    """A flex module declaring `n` tables. read_table is never called during
    sync (it only lists table metadata), so it can be trivial."""
    return (
        '_COLUMNS = [{"name": "id", "type": "BIGINT"}, '
        '{"name": "label", "type": "VARCHAR"}]\n'
        "\n"
        "def get_tables():\n"
        '    return [{"schema": "default", "name": f"t{i}", "columns": _COLUMNS} '
        "for i in range(%d)]\n"
        "\n"
        "def read_table(table):\n"
        "    return []\n"
    ) % n
