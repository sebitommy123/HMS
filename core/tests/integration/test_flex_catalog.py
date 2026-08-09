"""End-to-end test for the flex connector through Core.

Verifies the full path:

  POST /catalogs (connector=flex, flex.module_path=<mounted example>)
    → Core asks Trino to CREATE CATALOG
    → Trino loads the flex plugin
    → first /query triggers FlexConnector → FlexPythonWorker spawn
    → Python worker loads users_json/module.py, serves Arrow Flight
    → Trino streams the batches back

The Trino container has the example mount baked in via the session
fixture (see conftest.FLEX_MODULES_MOUNT). We only ever talk through
Core's HTTP API — no direct Trino calls — so this is also a sanity
check that Core treats `flex` as a regular connector name.
"""


CATALOG_NAME = "users_flex"
MODULE_PATH = "/var/datapro-flex/users_json/module.py"


def _register_catalog(client) -> None:
    r = client.post(
        "/catalogs",
        json={
            "name": CATALOG_NAME,
            "connector": "flex",
            "properties": {"flex.module_path": MODULE_PATH},
        },
    )
    assert r.status_code == 201, r.get_json()


def _create_source(client, schema: str, table: str) -> str:
    """Data sources are sync-discovered on catalog creation — look up the row
    for this (schema, table) rather than creating it."""
    rows = client.get(f"/data-sources?catalog={CATALOG_NAME}").get_json()
    for row in rows:
        if row["schema_name"] == schema and row["table_name"] == table:
            return row["id"]
    raise AssertionError(
        f"data source {CATALOG_NAME}.{schema}.{table} was not discovered; got {rows}"
    )


def _create_type(client, name: str) -> str:
    r = client.post("/object-types", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _create_factory(client, source_id: str, type_id: str, **kwargs) -> dict:
    body = {"data_source_id": source_id, "object_type_id": type_id, **kwargs}
    r = client.post("/object-factories", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


# ---- the test ---------------------------------------------------------


def test_flex_catalog_serves_rows_through_core(client):
    _register_catalog(client)

    # Trino sees the schema + table the Python module declared.
    r = client.post(
        "/raw-trino-query",
        json={"sql": f'SHOW SCHEMAS FROM "{CATALOG_NAME}"'},
    )
    assert r.status_code == 200, r.get_json()
    schemas = {row[0] for row in r.get_json()["rows"]}
    assert "default" in schemas

    r = client.post(
        "/raw-trino-query",
        json={"sql": f'SHOW TABLES FROM "{CATALOG_NAME}"."default"'},
    )
    assert r.status_code == 200, r.get_json()
    tables = {row[0] for row in r.get_json()["rows"]}
    assert tables == {"users"}

    # Direct SQL: rows match what users.json declares.
    r = client.post(
        "/raw-trino-query",
        json={
            "sql": f'SELECT id, name, age FROM "{CATALOG_NAME}"."default".users ORDER BY id'
        },
    )
    assert r.status_code == 200, r.get_json()
    rows = r.get_json()["rows"]
    assert rows == [
        [1, "Alice", 32],
        [2, "Bob", 47],
        [3, "Carol", 29],
        [4, "Dave", 51],
        [5, "Eve", 38],
    ]


def test_flex_catalog_via_semantic_query(client):
    """Full /query stack: ObjectType + ObjectFactory pointing at the
    flex-backed data source, queried through the semantic engine."""
    _register_catalog(client)
    src = _create_source(client, "default", "users")
    type_id = _create_type(client, "User")
    _create_factory(client, src, type_id)

    r = client.post("/query", json={"from": "User", "limit": 3})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["result_status"]["all_ok"] is True, body["result_status"]
    # _datasource column lands first (synthetic), then the declared cols.
    assert set(body["columns"]) >= {"_datasource", "id", "name", "age", "email"}
    # LIMIT 3 → 3 rows.
    assert len(body["rows"]) == 3
    ds_col = body["columns"].index("_datasource")
    assert all(row[ds_col] == f"{CATALOG_NAME}.default.users" for row in body["rows"])


def test_flex_predicate_pushdown_returns_correct_rows(client):
    """Predicate pushdown is a hint — Trino still post-filters for
    correctness. Either way, WHERE results must match the example data."""
    _register_catalog(client)
    r = client.post(
        "/raw-trino-query",
        json={
            "sql": (
                f'SELECT id, name FROM "{CATALOG_NAME}"."default".users '
                'WHERE id BETWEEN 2 AND 4 ORDER BY id'
            )
        },
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["rows"] == [[2, "Bob"], [3, "Carol"], [4, "Dave"]]
