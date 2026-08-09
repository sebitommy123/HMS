"""CRUD for object factories. Live Postgres via testcontainer.

Factories now hang off a DataSource (not a Catalog directly), so every test
first creates a catalog, then a data source within it, then the factory.
"""

import uuid


def _create_catalog(client, name: str = "tpch_demo") -> str:
    r = client.post("/catalogs", json={"name": name, "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    return name


def _create_type(client, name: str = "Company") -> str:
    r = client.post("/object-types", json={"name": name})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _create_source(
    client,
    catalog: str = "tpch_demo",
    schema: str = "tiny",
    table: str = "nation",
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


def test_list_empty(client):
    assert client.get("/object-factories").get_json() == []


def test_create_then_list_then_get(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)

    r = client.post(
        "/object-factories",
        json={
            "data_source_id": source_id,
            "object_type_id": type_id,
            "description": "tiny.nation -> Company",
        },
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["data_source_id"] == source_id
    assert body["object_type_id"] == type_id
    assert body["object_type_name"] == "Company"
    assert body["catalog_name"] == "tpch_demo"
    assert body["schema_name"] == "tiny"
    assert body["table_name"] == "nation"
    assert body["data_source_path"] == "tpch_demo.tiny.nation"
    factory_id = body["id"]

    assert len(client.get("/object-factories").get_json()) == 1
    assert client.get(f"/object-factories/{factory_id}").get_json()["id"] == factory_id


def test_create_defaults(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    r = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    )
    assert r.status_code == 201
    body = r.get_json()
    assert body["description"] == ""
    assert body["use_all_columns"] is True
    assert body["column_spec"] == []


def test_create_with_specific_columns(client):
    _create_catalog(client)
    source_id = _create_source(client)  # tpch tiny.nation
    type_id = _create_type(client)
    r = client.post(
        "/object-factories",
        json={
            "data_source_id": source_id,
            "object_type_id": type_id,
            "use_all_columns": False,
            "column_spec": ["nationkey", "name", "regionkey"],
        },
    )
    assert r.status_code == 201
    body = r.get_json()
    assert body["use_all_columns"] is False
    assert body["column_spec"] == ["nationkey", "name", "regionkey"]


def test_create_404_when_data_source_missing(client):
    type_id = _create_type(client)
    r = client.post(
        "/object-factories",
        json={"data_source_id": str(uuid.uuid4()), "object_type_id": type_id},
    )
    assert r.status_code == 404
    assert r.get_json()["error"] == "data_source_not_found"


def test_create_404_when_object_type_missing(client):
    _create_catalog(client)
    source_id = _create_source(client)
    r = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404
    assert r.get_json()["error"] == "object_type_not_found"


def test_create_400_when_object_type_id_not_uuid(client):
    _create_catalog(client)
    source_id = _create_source(client)
    r = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": "not-a-uuid"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_id"


def test_create_400_when_data_source_id_not_uuid(client):
    type_id = _create_type(client)
    r = client.post(
        "/object-factories",
        json={"data_source_id": "not-a-uuid", "object_type_id": type_id},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_id"


def test_create_409_on_duplicate_source_type_pair(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    )
    r = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    )
    assert r.status_code == 409
    assert r.get_json()["error"] == "already_exists"


def test_create_allows_same_type_under_different_sources(client):
    """A type can be produced by multiple data sources — the (source, type)
    pair is unique, not type alone."""
    _create_catalog(client)
    src_a = _create_source(client, schema="tiny", table="nation")
    src_b = _create_source(client, schema="tiny", table="region")
    type_id = _create_type(client)

    r1 = client.post(
        "/object-factories",
        json={"data_source_id": src_a, "object_type_id": type_id},
    )
    r2 = client.post(
        "/object-factories",
        json={"data_source_id": src_b, "object_type_id": type_id},
    )
    assert r1.status_code == 201 and r2.status_code == 201


def test_create_rejects_unknown_fields(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    r = client.post(
        "/object-factories",
        json={
            "data_source_id": source_id,
            "object_type_id": type_id,
            "settings": {},
        },
    )
    assert r.status_code == 400


# ---- filters ------------------------------------------------------------


def test_filter_by_data_source(client):
    _create_catalog(client)
    src_a = _create_source(client, schema="tiny", table="nation")
    src_b = _create_source(client, schema="tiny", table="region")
    t1 = _create_type(client, "T1")
    client.post("/object-factories", json={"data_source_id": src_a, "object_type_id": t1})
    client.post("/object-factories", json={"data_source_id": src_b, "object_type_id": t1})

    rows = client.get(f"/object-factories?data_source_id={src_a}").get_json()
    assert [r["data_source_id"] for r in rows] == [src_a]


def test_filter_by_catalog_still_works_via_join(client):
    """Even though factories no longer FK to catalogs directly, the
    ?catalog= filter joins through data_sources so the UI's per-catalog
    panel keeps working."""
    cat_a = _create_catalog(client, "cat_a")
    cat_b = _create_catalog(client, "cat_b")
    src_a = _create_source(client, catalog="cat_a")
    src_b = _create_source(client, catalog="cat_b")
    t = _create_type(client)
    client.post("/object-factories", json={"data_source_id": src_a, "object_type_id": t})
    client.post("/object-factories", json={"data_source_id": src_b, "object_type_id": t})

    a = client.get("/object-factories?catalog=cat_a").get_json()
    b = client.get("/object-factories?catalog=cat_b").get_json()
    assert {r["catalog_name"] for r in a} == {"cat_a"}
    assert {r["catalog_name"] for r in b} == {"cat_b"}


def test_filter_by_object_type(client):
    _create_catalog(client)
    src = _create_source(client)
    t1 = _create_type(client, "T1")
    t2 = _create_type(client, "T2")
    src2 = _create_source(client, schema="tiny", table="region")
    client.post("/object-factories", json={"data_source_id": src, "object_type_id": t1})
    client.post("/object-factories", json={"data_source_id": src2, "object_type_id": t1})
    client.post("/object-factories", json={"data_source_id": src, "object_type_id": t2})

    rows = client.get(f"/object-factories?object_type_id={t1}").get_json()
    assert {r["data_source_id"] for r in rows} == {src, src2}


def test_get_400_on_invalid_uuid(client):
    assert client.get("/object-factories/nope").status_code == 400


def test_get_404_when_missing(client):
    assert client.get(f"/object-factories/{uuid.uuid4()}").status_code == 404


# ---- patch --------------------------------------------------------------


def test_patch_updates_description(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    factory_id = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    ).get_json()["id"]
    r = client.patch(
        f"/object-factories/{factory_id}", json={"description": "edited"}
    )
    assert r.status_code == 200
    assert r.get_json()["description"] == "edited"


def test_patch_toggles_use_all_columns_and_spec(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    factory_id = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    ).get_json()["id"]

    r = client.patch(
        f"/object-factories/{factory_id}",
        json={"use_all_columns": False, "column_spec": ["nationkey", "name"]},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["use_all_columns"] is False
    assert body["column_spec"] == ["nationkey", "name"]


def test_patch_rejects_empty_body(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    factory_id = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    ).get_json()["id"]
    r = client.patch(f"/object-factories/{factory_id}", json={})
    assert r.status_code == 400
    assert r.get_json()["error"] == "empty_patch"


def test_patch_404_when_missing(client):
    r = client.patch(f"/object-factories/{uuid.uuid4()}", json={"description": "x"})
    assert r.status_code == 404


# ---- delete + cascade ---------------------------------------------------


def test_delete_removes_the_row(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    factory_id = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    ).get_json()["id"]
    r = client.delete(f"/object-factories/{factory_id}")
    assert r.status_code == 200
    assert client.get(f"/object-factories/{factory_id}").status_code == 404


def test_cascade_catalog_delete_removes_factories(client):
    """Data sources are sync-owned (no manual delete), but the FK cascade still
    matters: deleting a catalog cascades through its data sources to the object
    factories hanging off them (ON DELETE CASCADE the whole way down)."""
    cat = _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    factory_id = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    ).get_json()["id"]
    assert client.get(f"/object-factories/{factory_id}").status_code == 200

    client.delete(f"/catalogs/{cat}")
    assert client.get(f"/object-factories/{factory_id}").status_code == 404


def test_cascade_when_catalog_deleted(client):
    """Deleting a catalog cascades through data_sources to factories."""
    cat = _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    factory_id = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    ).get_json()["id"]
    client.delete(f"/catalogs/{cat}")
    assert client.get(f"/object-factories/{factory_id}").status_code == 404
    assert client.get(f"/data-sources/{source_id}").status_code == 404


def test_create_rejects_invalid_columns(client):
    """When use_all_columns=false, every column_spec entry must be a real
    column on the data source's underlying Trino table."""
    _create_catalog(client)
    source_id = _create_source(client)  # tpch tiny.nation
    type_id = _create_type(client)
    r = client.post(
        "/object-factories",
        json={
            "data_source_id": source_id,
            "object_type_id": type_id,
            "use_all_columns": False,
            "column_spec": ["nationkey", "bogus_col", "another_typo"],
        },
    )
    assert r.status_code == 400, r.get_json()
    body = r.get_json()
    assert body["error"] == "invalid_columns"
    assert "bogus_col" in body["invalid"]
    assert "another_typo" in body["invalid"]
    assert "nationkey" not in body["invalid"]
    # Operator hint: full list of valid columns.
    assert "nationkey" in body["valid_columns"]
    assert "name" in body["valid_columns"]


def test_create_with_valid_columns_succeeds(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    r = client.post(
        "/object-factories",
        json={
            "data_source_id": source_id,
            "object_type_id": type_id,
            "use_all_columns": False,
            "column_spec": ["nationkey", "name"],
        },
    )
    assert r.status_code == 201, r.get_json()


def test_create_with_use_all_columns_skips_validation(client):
    """use_all_columns=true means the column_spec is unused, so any
    stored values are tolerated."""
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    r = client.post(
        "/object-factories",
        json={
            "data_source_id": source_id,
            "object_type_id": type_id,
            "use_all_columns": True,
            "column_spec": ["this_doesnt_exist"],
        },
    )
    assert r.status_code == 201, r.get_json()


def test_patch_column_spec_validates(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    factory_id = client.post(
        "/object-factories",
        json={
            "data_source_id": source_id,
            "object_type_id": type_id,
            "use_all_columns": False,
            "column_spec": ["nationkey"],
        },
    ).get_json()["id"]

    r = client.patch(
        f"/object-factories/{factory_id}", json={"column_spec": ["nope"]}
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_columns"


def test_patch_flipping_to_specific_columns_revalidates(client):
    """If the existing column_spec has stale/invalid entries (e.g. it was
    set while use_all_columns=true), flipping use_all_columns=false alone
    must trigger validation against the live table."""
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    # Created with use_all_columns=true and a bogus column_spec — allowed.
    factory_id = client.post(
        "/object-factories",
        json={
            "data_source_id": source_id,
            "object_type_id": type_id,
            "use_all_columns": True,
            "column_spec": ["totally_invented_col"],
        },
    ).get_json()["id"]

    # Flipping to false alone (no column_spec change) must validate the
    # stored spec and reject.
    r = client.patch(
        f"/object-factories/{factory_id}", json={"use_all_columns": False}
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_columns"


def test_patch_clearing_column_spec_to_empty_skips_validation(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    factory_id = client.post(
        "/object-factories",
        json={
            "data_source_id": source_id,
            "object_type_id": type_id,
            "use_all_columns": False,
            "column_spec": ["nationkey"],
        },
    ).get_json()["id"]
    r = client.patch(
        f"/object-factories/{factory_id}", json={"column_spec": []}
    )
    # Empty spec is the "no specific columns" case — nothing to validate.
    assert r.status_code == 200, r.get_json()


def test_cascade_when_object_type_deleted(client):
    _create_catalog(client)
    source_id = _create_source(client)
    type_id = _create_type(client)
    factory_id = client.post(
        "/object-factories",
        json={"data_source_id": source_id, "object_type_id": type_id},
    ).get_json()["id"]
    client.delete(f"/object-types/{type_id}")
    assert client.get(f"/object-factories/{factory_id}").status_code == 404
