"""End-to-end tests for the Q1 semantic query engine.

Goes through live Trino against TPC-H tables. Exercises:
  - Single-factory queries with both use_all_columns and explicit column_spec
  - Multi-factory queries (UNION ALL CORRESPONDING) including different
    column sets across branches
  - Pre-flight skip of factories whose catalog isn't registered in Trino
  - Object-type-not-found → 404
  - /preview-query-plan emits the SQL without executing
  - Plan validation (limit / timeout bounds, unknown fields, etc.)
"""

import uuid


# ---- helpers -------------------------------------------------------------


def _create_catalog(client, name: str = "tpch_q") -> str:
    r = client.post("/catalogs", json={"name": name, "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    return name


def _create_type(client, name: str = "Nation") -> str:
    r = client.post("/object-types", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _create_source(
    client,
    catalog: str,
    schema: str,
    table: str,
) -> str:
    """Data sources are sync-discovered on catalog creation — look up the row
    for this (schema, table) rather than creating it."""
    rows = client.get(f"/data-sources?catalog={catalog}").get_json()
    for row in rows:
        if row["schema_name"] == schema and row["table_name"] == table:
            return row["id"]
    raise AssertionError(
        f"data source {catalog}.{schema}.{table} was not discovered; got {rows}"
    )


def _create_factory(
    client,
    data_source_id: str,
    object_type_id: str,
    *,
    use_all_columns: bool = True,
    column_spec: list[str] | None = None,
) -> str:
    body = {
        "data_source_id": data_source_id,
        "object_type_id": object_type_id,
        "use_all_columns": use_all_columns,
    }
    if column_spec is not None:
        body["column_spec"] = column_spec
    r = client.post("/object-factories", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


# ---- parser / validation -------------------------------------------------


def test_missing_from_is_400(client):
    r = client.post("/query", json={})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_request"


def test_unknown_field_is_400(client):
    r = client.post("/query", json={"from": "X", "garbage": True})
    assert r.status_code == 400


def test_limit_out_of_range_is_400(client):
    r = client.post("/query", json={"from": "X", "limit": 5000})
    assert r.status_code == 400


def test_timeout_out_of_range_is_400(client):
    r = client.post("/query", json={"from": "X", "timeout_seconds": 999})
    assert r.status_code == 400


def test_object_type_not_found_is_404(client):
    r = client.post("/query", json={"from": "DoesNotExist"})
    assert r.status_code == 404
    assert r.get_json()["error"] == "object_type_not_found"


# ---- single-factory happy path ------------------------------------------


def test_single_factory_use_all_columns_returns_rows(client):
    cat = _create_catalog(client)
    type_id = _create_type(client, "Nation")
    src = _create_source(client, cat, "tiny", "nation")
    _create_factory(client, src, type_id)

    r = client.post("/query", json={"from": "Nation", "limit": 5})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["result_status"]["all_ok"] is True, body["result_status"]
    assert "_datasource" in body["columns"]
    # tpch.tiny.nation has 25 rows; LIMIT 5 caps us.
    assert len(body["rows"]) == 5
    # The synthesized _datasource column is the path string.
    ds_col = body["columns"].index("_datasource")
    assert all(row[ds_col] == f"{cat}.tiny.nation" for row in body["rows"])


def test_single_factory_with_explicit_columns(client):
    cat = _create_catalog(client)
    type_id = _create_type(client, "NationLite")
    src = _create_source(client, cat, "tiny", "nation")
    _create_factory(
        client,
        src,
        type_id,
        use_all_columns=False,
        column_spec=["nationkey", "name"],
    )

    r = client.post("/query", json={"from": "NationLite", "limit": 3})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    # We asked for two columns + _datasource; nothing else.
    assert set(body["columns"]) == {"_datasource", "nationkey", "name"}


# ---- multi-factory: UNION ALL CORRESPONDING -----------------------------


def test_multi_factory_union_aligns_overlapping_columns(client):
    """Both factories produce Nation; both select nationkey + name. The
    result should concatenate."""
    cat = _create_catalog(client)
    type_id = _create_type(client, "Nation2")
    src_a = _create_source(client, cat, "tiny", "nation")
    src_b = _create_source(client, cat, "sf1", "nation")
    _create_factory(
        client, src_a, type_id, use_all_columns=False, column_spec=["nationkey", "name"]
    )
    _create_factory(
        client, src_b, type_id, use_all_columns=False, column_spec=["nationkey", "name"]
    )

    r = client.post("/query", json={"from": "Nation2", "limit": 4})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["result_status"]["all_ok"] is True, body["result_status"]
    # Per-factory LIMIT, so 4 from each branch = up to 8 total.
    assert 4 <= len(body["rows"]) <= 8
    ds_col = body["columns"].index("_datasource")
    sources = {row[ds_col] for row in body["rows"]}
    assert sources == {f"{cat}.tiny.nation", f"{cat}.sf1.nation"}


def test_multi_factory_union_widens_non_overlapping_columns(client):
    """Factories produce the same type but expose different columns. The
    CORRESPONDING alignment should widen the result and NULL-fill the
    missing column for each branch."""
    cat = _create_catalog(client)
    type_id = _create_type(client, "MixCols")
    src_a = _create_source(client, cat, "tiny", "nation")
    src_b = _create_source(client, cat, "tiny", "region")
    # Branch A exposes nationkey + name; branch B exposes regionkey + name.
    # The shared column is `name`. The result should have all three plus
    # _datasource, with NULLs where each factory doesn't have the column.
    _create_factory(
        client, src_a, type_id, use_all_columns=False, column_spec=["nationkey", "name"]
    )
    _create_factory(
        client, src_b, type_id, use_all_columns=False, column_spec=["regionkey", "name"]
    )

    r = client.post("/query", json={"from": "MixCols", "limit": 3})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["result_status"]["all_ok"] is True, body["result_status"]
    assert set(body["columns"]) == {"_datasource", "nationkey", "name", "regionkey"}

    ds_col = body["columns"].index("_datasource")
    nk_col = body["columns"].index("nationkey")
    rk_col = body["columns"].index("regionkey")
    for row in body["rows"]:
        ds = row[ds_col]
        if ds == f"{cat}.tiny.nation":
            assert row[nk_col] is not None
            assert row[rk_col] is None
        elif ds == f"{cat}.tiny.region":
            assert row[nk_col] is None
            assert row[rk_col] is not None
        else:
            raise AssertionError(f"unexpected _datasource: {ds!r}")


# ---- skip path ----------------------------------------------------------


def test_planner_skips_when_catalog_dropped_in_trino(client, core_app):
    """Drop the catalog directly through the Trino client, leaving the
    Postgres rows alone. The planner pre-flight should skip the
    orphaned factory."""
    cat = _create_catalog(client)
    type_id = _create_type(client, "Orphan")
    src = _create_source(client, cat, "tiny", "nation")
    _create_factory(client, src, type_id)

    # Out-of-band: drop the catalog from Trino only. Postgres still has
    # the catalog row, the data source, and the factory.
    core_app.extensions["trino"].drop_catalog(cat)

    body = client.post("/query", json={"from": "Orphan"}).get_json()
    assert body["result_status"]["all_ok"] is False
    assert len(body["result_status"]["factories_used"]) == 0
    assert len(body["result_status"]["factories_skipped"]) == 1
    skipped = body["result_status"]["factories_skipped"][0]
    assert cat in skipped["reason"]
    # No errors — the skip is graceful, not an exception.
    assert body["result_status"]["errors"] == []
    assert body["rows"] == []


def test_query_with_no_factories_returns_empty_table(client):
    """An object type with zero factories: empty rows, all_ok=true, no
    error, just nothing to return."""
    _create_type(client, "Lonely")
    r = client.post("/query", json={"from": "Lonely"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["result_status"]["all_ok"] is True
    assert body["rows"] == []
    assert body["result_status"]["factories_used"] == []


# ---- preview ------------------------------------------------------------


def test_preview_returns_sql_without_executing(client):
    cat = _create_catalog(client)
    type_id = _create_type(client, "Previewable")
    src = _create_source(client, cat, "tiny", "nation")
    _create_factory(
        client,
        src,
        type_id,
        use_all_columns=False,
        column_spec=["nationkey", "name"],
    )

    r = client.post("/preview-query-plan", json={"from": "Previewable", "limit": 7})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["from"] == "Previewable"
    assert body["limit"] == 7
    sql = body["sql"]
    assert "UNION ALL CORRESPONDING" not in sql  # only one factory
    assert "_datasource" in sql
    assert "nationkey" in sql and "name" in sql
    # The quoted path appears.
    assert f'"{cat}"."tiny"."nation"' in sql


def test_preview_emits_union_for_multi_factory(client):
    cat = _create_catalog(client)
    type_id = _create_type(client, "MultiPrev")
    src_a = _create_source(client, cat, "tiny", "nation")
    src_b = _create_source(client, cat, "tiny", "region")
    _create_factory(client, src_a, type_id)
    _create_factory(client, src_b, type_id)

    body = client.post("/preview-query-plan", json={"from": "MultiPrev"}).get_json()
    assert "UNION ALL CORRESPONDING" in body["sql"]
    # Both data source paths show up as quoted identifiers.
    assert f'"{cat}"."tiny"."nation"' in body["sql"]
    assert f'"{cat}"."tiny"."region"' in body["sql"]


def test_preview_404_when_type_missing(client):
    r = client.post("/preview-query-plan", json={"from": "NoSuchType"})
    assert r.status_code == 404


# ---- response shape -----------------------------------------------------


def test_result_status_includes_sql_on_success(client):
    cat = _create_catalog(client)
    type_id = _create_type(client, "ShapeCheck")
    src = _create_source(client, cat, "tiny", "nation")
    _create_factory(client, src, type_id)

    body = client.post("/query", json={"from": "ShapeCheck", "limit": 1}).get_json()
    status = body["result_status"]
    assert "sql" in status and "SELECT" in status["sql"]
    assert "elapsed_seconds" in status
    assert status["trino_query_id"]  # populated on success
    assert status["all_ok"] is True


def test_unknown_data_source_table_skips_factory_gracefully(client, make_data_source):
    """If the data source points at a table that doesn't exist, the
    planner's DESCRIBE round-trip fails and the factory is recorded in
    factories_skipped. The query returns empty rows, not a 500, and
    doesn't even try to execute since no surviving factories remain."""
    cat = _create_catalog(client)
    type_id = _create_type(client, "Phantom")
    # Real catalog, real schema, fake table. Inserted directly since sync
    # would never discover a nonexistent table.
    src = make_data_source(cat, "tiny", f"ghost_{uuid.uuid4().hex[:6]}")
    _create_factory(client, src, type_id)

    r = client.post("/query", json={"from": "Phantom"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["result_status"]["all_ok"] is False
    # No execution errors — the failure surfaced at plan time as a skip.
    assert body["result_status"]["errors"] == []
    assert len(body["result_status"]["factories_skipped"]) == 1
    assert "can't read columns" in body["result_status"]["factories_skipped"][0]["reason"].lower()
    assert body["rows"] == []
