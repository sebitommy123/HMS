"""End-to-end tests for the Traits system.

Covers:
  - /traits discovery
  - PUT/DELETE object-type trait attachments (idempotent)
  - factory_validator enforces trait_config presence + validity
  - Identity trait causes FULL OUTER JOIN SQL generation
  - Identity-joined query executes and merges rows on the identity column
"""


# ---- helpers (mirror style of test_query_engine / test_factory_validator) ----


def _create_catalog(client, name: str = "tpch_tr") -> str:
    r = client.post("/catalogs", json={"name": name, "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    return name


def _create_type(client, name: str) -> str:
    r = client.post("/object-types", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _create_source(client, catalog: str, schema: str, table: str) -> str:
    """Data sources are sync-discovered on catalog creation — look up the row
    for this (schema, table) rather than creating it."""
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


# ---- /traits discovery ----


def test_traits_endpoint_lists_known_traits(client):
    r = client.get("/traits")
    assert r.status_code == 200
    body = r.get_json()
    names = [t["name"] for t in body]
    # The two we've shipped.
    assert "identity" in names
    assert "temporal" in names
    # Each carries the required-config-keys advertisement.
    by_name = {t["name"]: t for t in body}
    assert by_name["identity"]["required_config_keys"] == ["column"]
    assert by_name["temporal"]["required_config_keys"] == ["column"]


# ---- attaching / detaching traits on an object type ----


def test_put_trait_adds_idempotently(client):
    type_id = _create_type(client, "ObjA")
    r = client.put(f"/object-types/{type_id}/traits/identity")
    assert r.status_code == 200
    assert r.get_json()["traits"] == ["identity"]
    # Same call again — still 200, still just one trait.
    r = client.put(f"/object-types/{type_id}/traits/identity")
    assert r.status_code == 200
    assert r.get_json()["traits"] == ["identity"]


def test_put_two_traits_both_appear_sorted(client):
    type_id = _create_type(client, "ObjB")
    client.put(f"/object-types/{type_id}/traits/temporal")
    r = client.put(f"/object-types/{type_id}/traits/identity")
    assert r.get_json()["traits"] == ["identity", "temporal"]


def test_put_unknown_trait_is_400(client):
    type_id = _create_type(client, "ObjC")
    r = client.put(f"/object-types/{type_id}/traits/no_such_trait")
    assert r.status_code == 400
    body = r.get_json()
    assert body["error"] == "unknown_trait"
    assert "identity" in body["known"]


def test_put_on_missing_type_is_404(client):
    import uuid as _uuid
    r = client.put(f"/object-types/{_uuid.uuid4()}/traits/identity")
    assert r.status_code == 404


def test_delete_trait_removes(client):
    type_id = _create_type(client, "ObjD")
    client.put(f"/object-types/{type_id}/traits/identity")
    r = client.delete(f"/object-types/{type_id}/traits/identity")
    assert r.status_code == 200
    assert r.get_json()["traits"] == []


def test_delete_trait_is_idempotent(client):
    type_id = _create_type(client, "ObjE")
    # Never attached — delete still succeeds.
    r = client.delete(f"/object-types/{type_id}/traits/identity")
    assert r.status_code == 200
    assert r.get_json()["traits"] == []


# ---- factory_validator enforces trait config ----


def test_adding_trait_marks_factories_broken_until_configured(client):
    """Add Identity to a type that already has a factory with no
    trait_config — re-validation should flip the factory to broken with
    a precise reason that names the trait."""
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    type_id = _create_type(client, "NationT")
    body = _create_factory(client, src, type_id)
    fid = body["id"]
    assert body["status"] == "ok"

    # Attach identity → factories under this type get re-validated.
    client.put(f"/object-types/{type_id}/traits/identity")

    f = _factory(client, fid)
    assert f["status"] == "broken"
    assert "identity" in f["last_error"].lower()


def test_invalid_identity_column_marks_factory_broken(client):
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    type_id = _create_type(client, "NationU")
    client.put(f"/object-types/{type_id}/traits/identity")

    # Create factory pointing identity at a column that doesn't exist.
    f = _create_factory(
        client,
        src,
        type_id,
        trait_config={"identity": {"column": "not_a_column"}},
    )
    assert f["status"] == "broken"
    assert "not_a_column" in f["last_error"]


def test_valid_identity_config_keeps_factory_ok(client):
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    type_id = _create_type(client, "NationV")
    client.put(f"/object-types/{type_id}/traits/identity")

    f = _create_factory(
        client,
        src,
        type_id,
        trait_config={"identity": {"column": "nationkey"}},
    )
    assert f["status"] == "ok"
    assert f["trait_config"] == {"identity": {"column": "nationkey"}}


def test_removing_trait_self_heals_factory(client):
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    type_id = _create_type(client, "NationW")
    client.put(f"/object-types/{type_id}/traits/identity")
    fid = _create_factory(client, src, type_id)["id"]
    assert _factory(client, fid)["status"] == "broken"

    client.delete(f"/object-types/{type_id}/traits/identity")
    assert _factory(client, fid)["status"] == "ok"


def test_patch_factory_trait_config_revalidates(client):
    cat = _create_catalog(client)
    src = _create_source(client, cat, "tiny", "nation")
    type_id = _create_type(client, "NationX")
    client.put(f"/object-types/{type_id}/traits/identity")
    fid = _create_factory(
        client,
        src,
        type_id,
        trait_config={"identity": {"column": "ghost"}},
    )["id"]
    assert _factory(client, fid)["status"] == "broken"

    # Patch with a valid column → next query should mark it ok.
    r = client.patch(
        f"/object-factories/{fid}",
        json={"trait_config": {"identity": {"column": "nationkey"}}},
    )
    assert r.status_code == 200
    # Plan a query so the validator runs and persists ok.
    client.post("/query", json={"from": "NationX", "limit": 1})
    assert _factory(client, fid)["status"] == "ok"


# ---- Identity-aware SQL generation ----


def test_identity_preview_emits_key_spine_join(client):
    cat = _create_catalog(client)
    type_id = _create_type(client, "JoinedNation")
    client.put(f"/object-types/{type_id}/traits/identity")
    src_a = _create_source(client, cat, "tiny", "nation")
    src_b = _create_source(client, cat, "sf1", "nation")
    _create_factory(
        client, src_a, type_id,
        trait_config={"identity": {"column": "nationkey"}},
    )
    _create_factory(
        client, src_b, type_id,
        trait_config={"identity": {"column": "nationkey"}},
    )

    body = client.post(
        "/preview-query-plan", json={"from": "JoinedNation", "limit": 3}
    ).get_json()
    sql = body["sql"]
    # Bounded key spine, referenced once, with each branch LEFT JOINed onto it.
    assert "WITH _identity_keys AS" in sql
    assert sql.count("_identity_keys AS _keys") == 1
    assert sql.count("LEFT JOIN") == 2
    assert "._identity = _keys._identity" in sql
    # No UNION-strategy artefacts when the type has Identity.
    assert "UNION ALL CORRESPONDING" not in sql
    # Prefix-with-source: per-factory column names carry the data source path.
    assert f"__{cat}.tiny.nation" in sql
    assert f"__{cat}.sf1.nation" in sql


def test_identity_query_merges_overlapping_rows(client):
    """Two factories on tpch.tiny.nation (same data, different
    aliases via separate data sources... actually we'll use tiny.nation
    twice via different schemas to ensure shared nationkeys). With
    Identity, rows with the same nationkey from both branches should
    collapse onto a single output row via FULL OUTER JOIN."""
    cat = _create_catalog(client)
    type_id = _create_type(client, "JoinedNation2")
    client.put(f"/object-types/{type_id}/traits/identity")
    # tpch.tiny.nation and tpch.sf1.nation share the same 25 nationkeys
    # (0..24), so a FULL OUTER JOIN on nationkey should yield exactly
    # 25 distinct identity values.
    src_a = _create_source(client, cat, "tiny", "nation")
    src_b = _create_source(client, cat, "sf1", "nation")
    _create_factory(
        client, src_a, type_id,
        trait_config={"identity": {"column": "nationkey"}},
    )
    _create_factory(
        client, src_b, type_id,
        trait_config={"identity": {"column": "nationkey"}},
    )

    body = client.post(
        "/query", json={"from": "JoinedNation2", "limit": 100}
    ).get_json()
    assert body["result_status"]["all_ok"] is True, body["result_status"]
    # _identity column appears once (collapsed by USING).
    assert "_identity" in body["result_status"]["columns"]
    # Per-source _datasource sentinels appear once each.
    assert f"_datasource__{cat}.tiny.nation" in body["result_status"]["columns"]
    assert f"_datasource__{cat}.sf1.nation" in body["result_status"]["columns"]
    # 25 distinct identity values, since both branches share the same set.
    identity_idx = body["result_status"]["columns"].index("_identity")
    identities = {row[identity_idx] for row in body["result_status"]["rows"]}
    assert len(identities) == 25

    # The interpreted objects layer: one object per row, each merged from BOTH
    # sources (identity trait) with an id and per-source field provenance.
    objects = body["objects"]
    assert len(objects) == len(body["result_status"]["rows"])
    obj = objects[0]
    assert set(obj["data_sources"]) == {f"{cat}.tiny.nation", f"{cat}.sf1.nation"}
    assert obj["id"] in identities
    # `name` comes from both sources → keyed by data source, never flattened.
    assert set(obj["fields"]["name"].keys()) == {f"{cat}.tiny.nation", f"{cat}.sf1.nation"}


def test_identity_merge_across_many_sources_is_complete_and_deterministic(client):
    """Regression: an object present in multiple sources must merge ALL of them,
    the same way on every run. The identity-key CTE is referenced by every
    branch's filter and Trino may re-evaluate it per reference; if that CTE isn't
    deterministic, branches pick different keys and the FULL OUTER JOIN fragments
    (the object comes back split, missing sources, differently each run). With a
    small limit the fragmentation is stark — one key, but only some of its
    sources survive."""
    cat = _create_catalog(client)
    type_id = _create_type(client, "JoinedNation3")
    client.put(f"/object-types/{type_id}/traits/identity")
    # tpch `nation` carries the same 25 nationkeys (0..24) in every schema, so
    # every key lives in all three sources — a clean multi-source merge.
    schemas = ["tiny", "sf1", "sf100"]
    for sch in schemas:
        src = _create_source(client, cat, sch, "nation")
        _create_factory(
            client, src, type_id, trait_config={"identity": {"column": "nationkey"}}
        )

    expected_sources = {f"{cat}.{s}.nation" for s in schemas}
    ids = []
    for _ in range(3):
        body = client.post("/query", json={"from": "JoinedNation3", "limit": 1}).get_json()
        assert body["result_status"]["all_ok"] is True, body["result_status"]
        assert len(body["objects"]) == 1
        obj = body["objects"][0]
        # The single object must include ALL three sources — not a fragment.
        assert set(obj["data_sources"]) == expected_sources, obj
        ids.append(obj["id"])
    # Deterministic: the same identity is selected every run.
    assert len(set(ids)) == 1, f"key selection was non-deterministic: {ids}"
