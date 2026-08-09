"""Integration tests for /raw-trino-query — raw SQL passthrough to live Trino.

Limits enforcement (row cap, timeout) is exercised against a real Trino so we
know the trino-python-client's fetch loop and our wall-clock check actually
cooperate, not just in a unit-test fantasy.

Note on cold-Trino: the first query against a freshly-booted Trino container
can take 10–30s while the JVM warms up. We pass a generous timeout for "normal"
tests and use the explicit `_warm_trino` fixture so the warmup hit only happens
once per session, not per test.
"""

import pytest

WARM_TIMEOUT = 90.0  # generous: cold-start JVM warmup hits the first query.


@pytest.fixture(scope="session")
def _warm_trino(config):
    """Run one cheap query before any test so JVM warmup doesn't show up as a
    timeout in later tests. Builds its own TrinoClient so we don't depend on the
    function-scoped core_app fixture."""
    from datapro_core.trino_client import TrinoClient

    trino = TrinoClient(
        host=config.trino_host,
        port=config.trino_port,
        user=config.trino_user,
    )
    trino.execute_query("SELECT 1", timeout_seconds=120, max_rows=1)


@pytest.fixture
def with_tpch(client, _warm_trino):
    """Ensure a tpch catalog exists so queries can target real data."""
    name = "q_tpch"
    r = client.post("/catalogs", json={"name": name, "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    yield name


def test_show_catalogs_returns_rows(client, _warm_trino):
    r = client.post(
        "/raw-trino-query", json={"sql": "SHOW CATALOGS", "timeout_seconds": WARM_TIMEOUT}
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["columns"] == ["Catalog"]
    names = {row[0] for row in body["rows"]}
    assert "system" in names
    assert body["truncated"] is False
    assert body["query_id"]
    assert body["elapsed_seconds"] >= 0
    assert body["applied_limits"]["max_rows"] == 10_000
    # We passed WARM_TIMEOUT; the server caps it at MAX_TIMEOUT_SECONDS (60),
    # so the applied value is the capped one.
    assert body["applied_limits"]["timeout_seconds"] == min(WARM_TIMEOUT, 60.0)


def test_select_against_registered_catalog(client, with_tpch):
    r = client.post(
        "/raw-trino-query",
        json={"sql": f"SELECT name FROM {with_tpch}.tiny.nation ORDER BY name LIMIT 3"},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["columns"] == ["name"]
    assert len(body["rows"]) == 3
    assert body["rows"][0][0] == "ALGERIA"


def test_invalid_sql_returns_400_with_trino_message(client, _warm_trino):
    r = client.post(
        "/raw-trino-query",
        json={
            "sql": "SELECT nonexistent_column FROM system.runtime.nodes",
            "timeout_seconds": WARM_TIMEOUT,
        },
    )
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "trino_error"
    assert "nonexistent_column" in body["details"].lower() or "column" in body["details"].lower()


def test_catalog_not_found_returns_400(client, _warm_trino):
    r = client.post(
        "/raw-trino-query",
        json={
            "sql": "SELECT * FROM imaginary_catalog.s.t LIMIT 1",
            "timeout_seconds": WARM_TIMEOUT,
        },
    )
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "trino_error"


def test_truncation_when_row_cap_is_low(client, with_tpch):
    # tpch.tiny.nation has 25 rows. Cap at 5.
    r = client.post(
        "/raw-trino-query",
        json={
            "sql": f"SELECT * FROM {with_tpch}.tiny.nation",
            "max_rows": 5,
        },
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["truncated"] is True
    assert body["row_count"] == 5
    assert len(body["rows"]) == 5


def test_no_rows_path_for_ddl_like_statement(client, with_tpch):
    # SHOW SCHEMAS returns rows; we want to also exercise something that
    # returns nothing. EXPLAIN returns a single text row, which is fine for
    # exercising the "few rows, not truncated" case.
    r = client.post(
        "/raw-trino-query",
        json={"sql": f"EXPLAIN SELECT count(*) FROM {with_tpch}.tiny.nation"},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["truncated"] is False
    assert len(body["columns"]) >= 1


def test_empty_sql_is_rejected(client):
    r = client.post("/raw-trino-query", json={"sql": ""})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_request"


def test_invalid_json_is_rejected(client):
    r = client.post(
        "/raw-trino-query",
        data="not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400


def test_max_rows_caps_at_server_limit(client, _warm_trino):
    # Caller asks for absurd number; server should silently cap and surface
    # the applied limit in the response.
    r = client.post(
        "/raw-trino-query",
        json={"sql": "SHOW CATALOGS", "max_rows": 10_000_000, "timeout_seconds": WARM_TIMEOUT},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["applied_limits"]["max_rows"] == 100_000  # MAX_ROWS_CAP


def test_timeout_seconds_caps_at_server_limit(client, _warm_trino):
    # Caller asks for absurd timeout; server should silently cap to MAX_TIMEOUT_SECONDS.
    r = client.post(
        "/raw-trino-query",
        json={"sql": "SHOW CATALOGS", "timeout_seconds": 9999},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["applied_limits"]["timeout_seconds"] == 60.0  # MAX_TIMEOUT_SECONDS


def test_query_times_out_on_slow_workload(client, with_tpch):
    """A 3-way cross join of tiny.lineitem (60k rows) materializes ~10^14 tuples.
    Even Trino's pipelining can't return the first batch in <1s, so the timeout
    fires reliably. This exercises the wall-clock path in execute_query, end to end."""
    r = client.post(
        "/raw-trino-query",
        json={
            "sql": (
                f"SELECT count(*) FROM {with_tpch}.tiny.lineitem a, "
                f"{with_tpch}.tiny.lineitem b, {with_tpch}.tiny.lineitem c"
            ),
            "timeout_seconds": 1,
        },
    )
    assert r.status_code == 504, r.get_json()
    body = r.get_json()
    assert body["error"] == "timeout"
    assert body["timeout_seconds"] == 1.0
