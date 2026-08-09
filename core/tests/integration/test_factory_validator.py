"""Live tests for the factory validator. Real Postgres via the test
container; real Trino + tpch for column introspection."""

import uuid


def _create_catalog(client, name: str = "tpch_vd") -> str:
    r = client.post("/catalogs", json={"name": name, "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    return name


def _create_type(client, name: str = "Nation") -> str:
    r = client.post("/object-types", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _create_source(client, catalog: str, schema: str, table: str) -> str:
    """Data sources are sync-discovered on catalog creation — look up the row
    for this (schema, table) rather than creating it. Only works for a
    registered catalog with the table actually present in Trino; for ghost
    tables or broken catalogs use the ``make_data_source`` fixture."""
    rows = client.get(f"/data-sources?catalog={catalog}").get_json()
    for row in rows:
        if row["schema_name"] == schema and row["table_name"] == table:
            return row["id"]
    raise AssertionError(
        f"data source {catalog}.{schema}.{table} was not discovered; got {rows}"
    )


def _create_factory(client, source_id: str, type_id: str, **kwargs) -> dict:
    body = {"data_source_id": source_id, "object_type_id": type_id, **kwargs}
    r = client.post("/object-factories", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _factory(client, fid: str) -> dict:
    r = client.get(f"/object-factories/{fid}")
    assert r.status_code == 200
    return r.get_json()


# ---- happy path --------------------------------------------------------


def test_freshly_created_factory_is_ok(client):
    """The POST endpoint validates column_spec, so on creation the
    factory is born OK with no last_error."""
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    t = _create_type(client)
    body = _create_factory(client, src, t)
    assert body["status"] == "ok"
    assert body["last_error"] is None


def test_first_query_runs_validator_and_keeps_status_ok(client):
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    t = _create_type(client)
    fid = _create_factory(client, src, t)["id"]

    r = client.post("/query", json={"from": "Nation", "limit": 1})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["result_status"]["all_ok"] is True
    # Status sticks at ok.
    assert _factory(client, fid)["status"] == "ok"


# ---- planner-time validation ------------------------------------------


def test_planner_marks_broken_when_table_dropped_in_trino(client, make_data_source):
    """A factory whose data source points at a table Trino doesn't have (as
    if it was dropped upstream) should be marked broken with a helpful
    last_error when a query plans it. The ghost data source is inserted
    directly since data sources are otherwise sync-owned."""
    cat = _create_catalog(client)
    src = make_data_source(cat, "tiny", f"ghost_{uuid.uuid4().hex[:6]}")
    t = _create_type(client)
    fid = _create_factory(client, src, t)["id"]

    # Running a query plans the factory, validation fails, status flips.
    body = client.post("/query", json={"from": "Nation"}).get_json()
    assert body["result_status"]["all_ok"] is False
    assert len(body["result_status"]["factories_skipped"]) == 1
    assert "can't read columns" in body["result_status"]["factories_skipped"][0]["reason"]

    persisted = _factory(client, fid)
    assert persisted["status"] == "broken"
    assert persisted["last_error"]
    assert "can't read columns" in persisted["last_error"]


def test_planner_marks_broken_when_catalog_removed_from_trino(client, core_app):
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    t = _create_type(client)
    fid = _create_factory(client, src, t)["id"]

    # Drop the catalog directly from Trino, leaving Postgres rows alone.
    core_app.extensions["trino"].drop_catalog(cat)

    body = client.post("/query", json={"from": "Nation"}).get_json()
    assert body["result_status"]["all_ok"] is False
    skipped = body["result_status"]["factories_skipped"]
    assert len(skipped) == 1
    assert "not currently registered in Trino" in skipped[0]["reason"]

    persisted = _factory(client, fid)
    assert persisted["status"] == "broken"
    assert cat in persisted["last_error"]


def test_planner_refreshes_stale_catalog_status_before_validating(client, core_app):
    """A factory whose catalog row says 'broken' but is actually fine
    in Trino right now (e.g. an out-of-band fix) should NOT cascade-fail
    the factory. The planner's pre-validation reconcile_one flips the
    catalog back to enabled, then the validator sees the fresh state."""
    from sqlalchemy import update
    from datapro_core.models import Catalog, CatalogStatus

    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    t = _create_type(client)
    fid = _create_factory(client, src, t)["id"]
    assert _factory(client, fid)["status"] == "ok"

    # Forcibly mark the catalog 'broken' in Postgres without touching
    # Trino. Simulates a stale snapshot from an earlier failed reconcile
    # that's since been resolved out-of-band.
    SessionLocal = core_app.extensions["db_session"]
    with SessionLocal() as s:
        s.execute(
            update(Catalog)
            .where(Catalog.name == cat)
            .values(status=CatalogStatus.BROKEN, last_error="stale leftover")
        )
        s.commit()
    assert client.get(f"/catalogs/{cat}").get_json()["status"] == "broken"

    # Plan a query — planner reconciles the catalog first, so the
    # factory's validator sees enabled, not the stale broken state.
    result = client.post("/query", json={"from": "Nation", "limit": 1}).get_json()
    assert result["result_status"]["all_ok"] is True, result["result_status"]
    assert _factory(client, fid)["status"] == "ok"
    # And the catalog itself is now correctly marked enabled.
    assert client.get(f"/catalogs/{cat}").get_json()["status"] == "enabled"


def test_factory_inherits_broken_status_from_its_catalog(client, make_data_source):
    """A catalog that Trino rejected (BROKEN) should cascade: any
    factory hanging off a data source in that catalog must also be
    marked broken, with a reason that names the catalog's underlying
    error rather than the generic "not in Trino" symptom."""
    import uuid as _uuid

    # Catalog Core can't successfully create in Trino → catalog row
    # ends up BROKEN with a last_error. (no_such_connector_plugin is
    # the same trick the catalog patch tests use.)
    bad_name = f"badcat_{_uuid.uuid4().hex[:8]}"
    client.post(
        "/catalogs",
        json={"name": bad_name, "connector": "no_such_connector_plugin"},
    )
    catalog_row = client.get(f"/catalogs/{bad_name}").get_json()
    assert catalog_row["status"] == "broken"

    # ...but a data source + factory still get registered against it. The
    # catalog is broken (never registered in Trino) so sync discovered
    # nothing — insert the data source directly.
    src = make_data_source(bad_name, "tiny", "nation")
    t = _create_type(client, name=f"T_{_uuid.uuid4().hex[:6]}")
    # The factory POST validates column_spec, which requires
    # introspection — which fails because the catalog is broken. So
    # creating with use_all_columns=true (default) skips that step.
    body = _create_factory(client, src, t)
    fid = body["id"]

    # Plan a query. The validator should mark the factory broken with
    # a reason that names the parent catalog's broken state.
    result = client.post("/query", json={"from": body["object_type_name"]}).get_json()
    assert result["result_status"]["all_ok"] is False
    skipped = result["result_status"]["factories_skipped"]
    assert len(skipped) == 1
    reason = skipped[0]["reason"]
    assert bad_name in reason
    assert "broken state" in reason

    # Persisted on the factory too.
    persisted = _factory(client, fid)
    assert persisted["status"] == "broken"
    assert "broken state" in persisted["last_error"]


def test_planner_revalidates_and_clears_status_on_self_heal(client, core_app):
    """A factory marked broken should flip back to ok automatically the
    next time the underlying issue is resolved and a query is planned."""
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    t = _create_type(client)
    fid = _create_factory(client, src, t)["id"]

    # Drop catalog → planner marks broken.
    core_app.extensions["trino"].drop_catalog(cat)
    client.post("/query", json={"from": "Nation"})
    assert _factory(client, fid)["status"] == "broken"

    # Reconcile recreates the catalog in Trino; next query should heal.
    r = client.post("/reconcile")
    assert r.status_code == 200, r.get_json()
    client.post("/query", json={"from": "Nation"})
    healed = _factory(client, fid)
    assert healed["status"] == "ok"
    assert healed["last_error"] is None


# ---- query-time exclusion --------------------------------------------


def test_broken_factory_is_excluded_from_query_but_others_succeed(
    client, make_data_source
):
    """When one of several factories is broken, the query still runs
    using the healthy ones and surfaces the broken one in
    factories_skipped."""
    cat = _create_catalog(client)
    good = _create_source(client, cat, "tiny", "nation")
    # The bad one points at a table Trino doesn't have (inserted directly).
    bad = make_data_source(cat, "tiny", "no_such_table")
    t = _create_type(client)
    _create_factory(client, good, t)
    bad_fid = _create_factory(client, bad, t)["id"]

    body = client.post("/query", json={"from": "Nation", "limit": 2}).get_json()
    # Query succeeds against the healthy factory.
    assert len(body["rows"]) > 0
    # And the broken one is reported in factories_skipped.
    paths = [s["data_source_path"] for s in body["result_status"]["factories_skipped"]]
    assert any("no_such_table" in p for p in paths)
    assert _factory(client, bad_fid)["status"] == "broken"


# ---- validate_all (heartbeat surface) --------------------------------


def test_validate_all_flips_status_on_every_factory(client, core_app):
    """The heartbeat's validate_all should flip statuses for everything
    it inspects, not just one factory."""
    from datapro_core.factory_validator import validate_all

    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    t = _create_type(client)
    fid = _create_factory(client, src, t)["id"]
    assert _factory(client, fid)["status"] == "ok"

    # Break it out of band.
    core_app.extensions["trino"].drop_catalog(cat)
    SessionLocal = core_app.extensions["db_session"]
    with SessionLocal() as s:
        summary = validate_all(session=s, trino=core_app.extensions["trino"])
    assert summary["broken"] >= 1
    assert _factory(client, fid)["status"] == "broken"


# ---- API surface assertions ----------------------------------------


def test_factory_response_includes_status_fields(client):
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    t = _create_type(client)
    body = _create_factory(client, src, t)
    assert "status" in body
    assert "last_error" in body
