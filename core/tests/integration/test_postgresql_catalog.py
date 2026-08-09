"""Real-backend test: register a postgresql catalog pointing at a live Postgres
spun up by the test, run a query through Trino, verify real rows come back.

This is the test that proves the system works for the kind of catalog operators
will actually register. The other integration tests use Trino's self-contained
`tpch` connector, which is the easiest possible case (no auth, no network, no
external backend).
"""

import trino


def _query_trino_via_core(core_app, sql: str) -> list[tuple]:
    """Run a SQL query through the same Trino instance Core uses. Returns rows.

    We use the trino python client directly here (not Core's API) because Core
    doesn't expose a passthrough query endpoint — that's outside Phase 0 scope.
    What matters is that the catalog Core registered is usable via Trino at all.
    """
    trino_client = core_app.extensions["trino"]
    conn = trino.dbapi.connect(
        host=trino_client.host,
        port=trino_client.port,
        user="datapro-core-test",
        catalog="system",
        schema="runtime",
    )
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return cur.fetchall()
    finally:
        conn.close()


def test_postgresql_catalog_end_to_end(client, edgar_postgres, core_app):
    """The full real-world flow: register catalog → Trino sees it → Trino can
    actually query the backend → rows come back."""

    # 1. Register a postgresql catalog. The JDBC URL uses the docker-network
    # alias because Trino runs inside the network and resolves it that way.
    r = client.post(
        "/catalogs",
        json={
            "name": "edgar",
            "connector": "postgresql",
            "properties": {
                "connection-url": "jdbc:postgresql://edgar-pg:5432/edgar",
                "connection-user": "edgar",
                "connection-password": "edgar",
            },
        },
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["reconcile"]["all_ok"] is True
    assert body["catalog"]["status"] == "enabled"

    # 2. Core's view of Trino confirms the catalog exists.
    state = {row["name"]: row["connector"] for row in client.get("/trino/state").get_json()}
    assert state.get("edgar") == "postgresql"

    # 3. Trino can introspect the catalog: list schemas.
    schemas = {r[0] for r in _query_trino_via_core(core_app, "SHOW SCHEMAS FROM edgar")}
    assert "public" in schemas, f"expected public schema, got {schemas}"

    # 4. Trino can list tables in that schema.
    tables = {r[0] for r in _query_trino_via_core(core_app, "SHOW TABLES FROM edgar.public")}
    assert "companies" in tables

    # 5. Real query, real rows. This is the property the whole stack exists for.
    rows = _query_trino_via_core(
        core_app,
        "SELECT ticker, name FROM edgar.public.companies ORDER BY ticker",
    )
    # trino python client returns rows as lists; normalize for comparison.
    rows_t = [tuple(r) for r in rows]
    tickers = [r[0] for r in rows_t]
    assert tickers == ["AAPL", "AMZN", "MSFT", "TSLA"]
    assert ("AAPL", "Apple Inc.") in rows_t

    # 6. Aggregation query — exercises the postgresql connector's pushdown path.
    n = _query_trino_via_core(
        core_app, "SELECT COUNT(*) FROM edgar.public.companies"
    )[0][0]
    assert n == 4

    # 7. Cross-catalog query against tpch (the headline Trino feature, and the
    # whole reason Core exists — registering multiple catalogs and querying across
    # them). Verifies our newly-created postgresql catalog plays well with peers.
    cross_r = client.post("/catalogs", json={"name": "tpch_x", "connector": "tpch"})
    assert cross_r.status_code == 201, cross_r.get_json()

    joined = _query_trino_via_core(
        core_app,
        "SELECT (SELECT COUNT(*) FROM edgar.public.companies) AS c, "
        "       (SELECT COUNT(*) FROM tpch_x.tiny.nation) AS n",
    )
    assert tuple(joined[0]) == (4, 25)


def test_postgresql_catalog_drop_releases_jdbc(client, edgar_postgres):
    """DELETE should actually release the catalog in Trino, even with active
    JDBC machinery. After delete, the catalog isn't queryable and the API 404s."""

    r = client.post(
        "/catalogs",
        json={
            "name": "edgar_to_drop",
            "connector": "postgresql",
            "properties": {
                "connection-url": "jdbc:postgresql://edgar-pg:5432/edgar",
                "connection-user": "edgar",
                "connection-password": "edgar",
            },
        },
    )
    assert r.status_code == 201, r.get_json()
    assert "edgar_to_drop" in {
        row["name"] for row in client.get("/trino/state").get_json()
    }

    r = client.delete("/catalogs/edgar_to_drop")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["reconcile"]["all_ok"] is True

    assert "edgar_to_drop" not in {
        row["name"] for row in client.get("/trino/state").get_json()
    }
    assert client.get("/catalogs/edgar_to_drop").status_code == 404
