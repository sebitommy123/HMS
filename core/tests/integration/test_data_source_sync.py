"""Reconciler data-source sync: discover, delete, mark-deleted, revive, and
the DOWN catalog state. Uses the real Trino `memory` connector (which supports
CREATE/DROP TABLE) for the live path, plus stubbed-Trino unit tests for the
introspection-failure (DOWN) path."""

import uuid


# ---- helpers -----------------------------------------------------------


def _sql(client, sql: str):
    r = client.post("/raw-trino-query", json={"sql": sql})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _catalog(client, name: str) -> str:
    r = client.post("/catalogs", json={"name": name, "connector": "memory"})
    assert r.status_code == 201, r.get_json()
    return name


def _find(client, catalog: str, schema: str, table: str):
    rows = client.get(f"/data-sources?catalog={catalog}").get_json()
    for r in rows:
        if r["schema_name"] == schema and r["table_name"] == table:
            return r
    return None


def _make_table(client, catalog: str, schema: str, table: str) -> None:
    _sql(client, f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
    _sql(client, f"CREATE TABLE {catalog}.{schema}.{table} (id bigint)")


def _factory_on(client, data_source_id: str) -> str:
    """Create an object type + a factory pointing at the data source, so the
    source counts as referenced."""
    tname = f"T_{uuid.uuid4().hex[:6]}"
    tid = client.post("/object-types", json={"name": tname}).get_json()["id"]
    r = client.post(
        "/object-factories",
        json={"data_source_id": data_source_id, "object_type_id": tid},
    )
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


# ---- discovery ---------------------------------------------------------


def test_reconcile_discovers_new_tables(client):
    cat = _catalog(client, "mem_disc")
    _make_table(client, cat, "s1", "t1")

    client.post("/reconcile")

    ds = _find(client, cat, "s1", "t1")
    assert ds is not None, "new table should be discovered as a data source"
    assert ds["status"] == "active"


def test_reconcile_ignores_information_schema(client):
    cat = _catalog(client, "mem_infosch")
    _make_table(client, cat, "s1", "t1")
    client.post("/reconcile")
    rows = client.get(f"/data-sources?catalog={cat}").get_json()
    assert all(r["schema_name"] != "information_schema" for r in rows)


# ---- disappearance -----------------------------------------------------


def test_reconcile_hard_deletes_unreferenced_vanished_table(client):
    cat = _catalog(client, "mem_del")
    _make_table(client, cat, "s1", "t1")
    _make_table(client, cat, "s1", "keep")  # filler so discovery stays non-empty
    client.post("/reconcile")
    assert _find(client, cat, "s1", "t1") is not None

    _sql(client, f"DROP TABLE {cat}.s1.t1")
    client.post("/reconcile")

    assert _find(client, cat, "s1", "t1") is None, "unreferenced table should be removed"
    assert _find(client, cat, "s1", "keep") is not None  # the survivor stays


def test_reconcile_marks_referenced_vanished_table_deleted(client):
    cat = _catalog(client, "mem_ref")
    _make_table(client, cat, "s1", "t1")
    _make_table(client, cat, "s1", "keep")  # filler so discovery stays non-empty
    client.post("/reconcile")
    ds = _find(client, cat, "s1", "t1")
    assert ds is not None
    _factory_on(client, ds["id"])  # now referenced

    _sql(client, f"DROP TABLE {cat}.s1.t1")
    client.post("/reconcile")

    ds = _find(client, cat, "s1", "t1")
    assert ds is not None, "referenced source must be kept, not deleted"
    assert ds["status"] == "deleted"


def test_reconcile_revives_reappeared_table(client):
    cat = _catalog(client, "mem_revive")
    _make_table(client, cat, "s1", "t1")
    _make_table(client, cat, "s1", "keep")  # filler so discovery stays non-empty
    client.post("/reconcile")
    ds = _find(client, cat, "s1", "t1")
    _factory_on(client, ds["id"])
    _sql(client, f"DROP TABLE {cat}.s1.t1")
    client.post("/reconcile")
    assert _find(client, cat, "s1", "t1")["status"] == "deleted"

    # Table comes back → next reconcile flips it active again.
    _sql(client, f"CREATE TABLE {cat}.s1.t1 (id bigint)")
    client.post("/reconcile")
    assert _find(client, cat, "s1", "t1")["status"] == "active"


def test_reconcile_empty_enumeration_never_deletes_existing_sources(client):
    """SAFETY: if a flaky store reports zero tables (here simulated by
    dropping every table), the reconciler must NOT delete existing data
    sources — a "went from N tables to zero in one pass" read is treated as
    untrustworthy, not a real drop-all. Regression guard for a whole
    catalog's sources vanishing on a transient blip."""
    cat = _catalog(client, "mem_empty")
    _make_table(client, cat, "s1", "a")
    _make_table(client, cat, "s1", "b")
    client.post("/reconcile")
    assert _find(client, cat, "s1", "a") is not None
    assert _find(client, cat, "s1", "b") is not None

    # Every table gone → discovery is empty.
    _sql(client, f"DROP TABLE {cat}.s1.a")
    _sql(client, f"DROP TABLE {cat}.s1.b")
    client.post("/reconcile")

    # Both sources survive, untouched.
    assert _find(client, cat, "s1", "a")["status"] == "active"
    assert _find(client, cat, "s1", "b")["status"] == "active"


# ---- DOWN: introspection failure (stubbed Trino) -----------------------


class _StubTrino:
    """Minimal stand-in for TrinoClient's list_tables. Everything else is
    unused by sync_data_sources on these paths."""

    def __init__(self, result=None, error=None):
        self._result = result or []
        self._error = error

    def list_tables(self, catalog):
        if self._error is not None:
            raise self._error
        return list(self._result)


def _insert_catalog_and_source(core_app, catalog_name, status, schema, table, ds_status):
    from datapro_core.models import Catalog, DataSource

    Session = core_app.extensions["db_session"]
    with Session() as s:
        s.add(
            Catalog(
                name=catalog_name,
                connector="postgresql",
                properties={},
                status=status,
            )
        )
        s.add(
            DataSource(
                catalog_name=catalog_name,
                schema_name=schema,
                table_name=table,
                status=ds_status,
            )
        )
        s.commit()


def test_sync_marks_catalog_down_when_introspection_fails(core_app):
    from datapro_core.models import Catalog, CatalogStatus, DataSource, DataSourceStatus
    from datapro_core.reconciler import sync_data_sources
    from datapro_core.trino_client import TrinoError

    _insert_catalog_and_source(
        core_app, "downcat", CatalogStatus.ENABLED, "s", "t", DataSourceStatus.ACTIVE
    )
    Session = core_app.extensions["db_session"]
    with Session() as s:
        sync_data_sources(s, _StubTrino(error=TrinoError("store unreachable")), "downcat")
        s.commit()

        cat = s.get(Catalog, "downcat")
        assert cat.status == CatalogStatus.DOWN
        assert cat.last_error and "store unreachable" in cat.last_error
        # Data sources are left untouched — we don't know what's really there.
        kept = s.query(DataSource).filter_by(catalog_name="downcat").all()
        assert len(kept) == 1 and kept[0].status == DataSourceStatus.ACTIVE


def test_sync_recovers_down_catalog_when_introspection_succeeds(core_app):
    from datapro_core.models import Catalog, CatalogStatus, DataSourceStatus
    from datapro_core.reconciler import sync_data_sources

    _insert_catalog_and_source(
        core_app, "recov", CatalogStatus.DOWN, "s", "t", DataSourceStatus.ACTIVE
    )
    Session = core_app.extensions["db_session"]
    with Session() as s:
        sync_data_sources(s, _StubTrino(result=[("s", "t")]), "recov")
        s.commit()

        cat = s.get(Catalog, "recov")
        assert cat.status == CatalogStatus.ENABLED
        assert cat.last_error is None
