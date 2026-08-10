"""Query-engine performance scenarios.

Each scenario is a thin call into the harness against the session-scoped `bench`
environment (see conftest). Nothing here asserts on time — this is a *tracked
baseline* suite: results are measured, compared to a committed baseline, and
regressions surface as warnings + a terminal report (harness.finalize).

Scenario order matters: the pathological identity-join scenario runs LAST. At
scale it can exhaust Trino's memory and fail the query outright, which would
otherwise contaminate any fast scenario running after it in the same session.

Run with:  uv run pytest -m perf -s     (add PERF_SCALE_ROWS=200000 for a fast pass)
Update the baseline:  PERF_UPDATE_BASELINE=1 uv run pytest -m perf
"""

import pytest

from . import harness

pytestmark = pytest.mark.perf


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
    """FLAGSHIP / known regression — runs LAST (see module docstring).

    Identity trait + two factories across two catalogs → a FULL OUTER JOIN over
    both full tables with LIMIT only on the outer SELECT. The join blocks limit
    pushdown, so both tables scan; at scale this blows the SLO budget or fails
    the query on memory. The follow-up engine refactor should flip this green
    (bound each branch before the join). Bounded iterations: one measured hit is
    enough to record the pathology, and it limits the memory-pressure blast."""
    harness.measure(
        "identity_join_two_factories_large",
        budget_ms=5000,
        # 15s request budget: long enough that a fast machine may complete and
        # record the true (over-budget) latency; short enough not to wait 30s.
        fn=lambda: harness.semantic_query(
            perf_client, bench.joined_type, limit=25, timeout_seconds=15
        ),
        warmup=0,
        iterations=2,
    )
