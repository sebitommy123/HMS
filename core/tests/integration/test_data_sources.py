"""Read-only data source API. Data sources are sync-owned (discovered from
Trino by the reconciler) — there is no create/update/delete. Live Postgres +
Trino via testcontainers. Sync behavior itself is covered in
test_data_source_sync.py."""

import uuid


def _create_catalog(client, name: str = "tpch_demo", connector: str = "tpch") -> str:
    r = client.post("/catalogs", json={"name": name, "connector": connector})
    assert r.status_code == 201, r.get_json()
    return name


def _find(client, catalog: str, schema: str, table: str):
    rows = client.get(f"/data-sources?catalog={catalog}").get_json()
    for r in rows:
        if r["schema_name"] == schema and r["table_name"] == table:
            return r
    return None


def test_list_empty(client):
    assert client.get("/data-sources").get_json() == []


def test_catalog_creation_discovers_data_sources(client):
    """Creating a catalog runs a reconcile that discovers its tables as data
    sources — no manual creation step."""
    _create_catalog(client)
    ds = _find(client, "tpch_demo", "tiny", "nation")
    assert ds is not None, "tiny.nation should have been discovered"
    assert ds["path"] == "tpch_demo.tiny.nation"
    assert ds["status"] == "active"
    uuid.UUID(ds["id"])


def test_get_by_id(client, make_data_source):
    _create_catalog(client, "memcat", "memory")  # no tables → no sync noise
    sid = make_data_source("memcat", "s", "t")
    body = client.get(f"/data-sources/{sid}").get_json()
    assert body["id"] == sid
    assert body["path"] == "memcat.s.t"
    assert body["status"] == "active"


def test_list_filter_by_catalog(client, make_data_source):
    _create_catalog(client, "cat_a", "memory")
    _create_catalog(client, "cat_b", "memory")
    make_data_source("cat_a", "s", "t1")
    make_data_source("cat_b", "s", "t2")
    rows = client.get("/data-sources?catalog=cat_a").get_json()
    assert {r["table_name"] for r in rows} == {"t1"}


def test_get_400_on_invalid_uuid(client):
    assert client.get("/data-sources/not-a-uuid").status_code == 400


def test_get_404_when_missing(client):
    assert client.get(f"/data-sources/{uuid.uuid4()}").status_code == 404


# ---- mutation endpoints are gone (sync-owned) --------------------------


def test_create_endpoint_removed(client):
    r = client.post(
        "/data-sources",
        json={"catalog_name": "x", "schema_name": "s", "table_name": "t"},
    )
    assert r.status_code == 405


def test_update_endpoint_removed(client, make_data_source):
    _create_catalog(client, "memcat", "memory")
    sid = make_data_source("memcat", "s", "t")
    assert client.patch(f"/data-sources/{sid}", json={"description": "x"}).status_code == 405


def test_delete_endpoint_removed(client, make_data_source):
    _create_catalog(client, "memcat", "memory")
    sid = make_data_source("memcat", "s", "t")
    assert client.delete(f"/data-sources/{sid}").status_code == 405


# ---- columns (live Trino introspection) --------------------------------


def test_columns_for_real_tpch_table(client):
    """Live introspection against TPC-H tiny.nation."""
    _create_catalog(client)
    ds = _find(client, "tpch_demo", "tiny", "nation")
    assert ds is not None
    sid = ds["id"]

    r = client.get(f"/data-sources/{sid}/columns")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["data_source_id"] == sid
    assert body["path"] == "tpch_demo.tiny.nation"

    by_name = {c["name"]: c["type"] for c in body["columns"]}
    assert by_name["nationkey"] == "bigint"
    assert by_name["name"].startswith("varchar")
    assert "regionkey" in by_name
    assert "comment" in by_name


def test_columns_400_on_invalid_uuid(client):
    assert client.get("/data-sources/nope/columns").status_code == 400


def test_columns_404_when_datasource_missing(client):
    assert client.get(f"/data-sources/{uuid.uuid4()}/columns").status_code == 404


def test_columns_502_when_trino_cant_resolve_table(client, make_data_source):
    """Data source pointing at a non-existent table → Trino errors on SHOW
    COLUMNS → 502 with details for the UI."""
    _create_catalog(client)  # real tpch catalog
    sid = make_data_source("tpch_demo", "tiny", "no_such_table_xyz")
    r = client.get(f"/data-sources/{sid}/columns")
    assert r.status_code == 502
    body = r.get_json()
    assert body["error"] == "trino_error"
    assert body["details"]


def test_cascade_when_catalog_deleted(client, make_data_source):
    _create_catalog(client, "memcat", "memory")
    sid = make_data_source("memcat", "s", "t")
    client.delete("/catalogs/memcat")
    assert client.get(f"/data-sources/{sid}").status_code == 404
