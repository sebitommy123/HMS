"""End-to-end CRUD against live Postgres + live Trino. The six Phase-0 invariants.

Each test names which invariant it covers from the Phase-0 plan.
"""

import requests


def _trino_catalog_names(client) -> set[str]:
    """Read live Trino state via the Core API, not via a separate client. This
    proves both the API and the underlying Trino are agreeing."""
    r = client.get("/trino/state")
    assert r.status_code == 200, r.get_json()
    return {row["name"] for row in r.get_json()}


def test_invariant_1_happy_path(client):
    """POST /catalogs → 201, row persisted, Trino has it."""
    r = client.post("/catalogs", json={"name": "tpch_demo", "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["catalog"]["name"] == "tpch_demo"
    assert body["catalog"]["status"] == "enabled"
    assert body["reconcile"]["all_ok"] is True

    # Persisted in Postgres (via API).
    r = client.get("/catalogs/tpch_demo")
    assert r.status_code == 200
    assert r.get_json()["connector"] == "tpch"

    # Actually visible in Trino.
    assert "tpch_demo" in _trino_catalog_names(client)


def test_invariant_2_idempotent_reconcile(client):
    """Reconcile twice — second call should produce zero actions."""
    client.post("/catalogs", json={"name": "tpch_a", "connector": "tpch"})

    r = client.post("/reconcile")
    assert r.status_code == 200
    assert r.get_json()["actions"] == []


def test_invariant_3_convergence_after_drift(client, core_app):
    """The headline invariant: drop the catalog out-of-band in Trino, reconcile,
    catalog returns. Simulates the catalog-store-in-memory restart cheaply."""
    client.post("/catalogs", json={"name": "tpch_drift", "connector": "tpch"})
    assert "tpch_drift" in _trino_catalog_names(client)

    # Out-of-band drop.
    core_app.extensions["trino"].drop_catalog("tpch_drift")
    assert "tpch_drift" not in _trino_catalog_names(client)

    # Reconcile should bring it back.
    r = client.post("/reconcile")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    actions = body["actions"]
    assert any(a["kind"] == "create" and a["name"] == "tpch_drift" for a in actions)
    assert "tpch_drift" in _trino_catalog_names(client)


def test_invariant_4_broken_catalog_persists_error(client):
    """Bad connection string → Trino rejects → row marked broken with error message."""
    r = client.post(
        "/catalogs",
        json={
            "name": "pg_broken",
            "connector": "postgresql",
            "properties": {"connection-url": "jdbc:postgresql://nowhere.invalid:5432/db",
                           "connection-user": "x", "connection-password": "x"},
        },
    )
    # Trino's CREATE CATALOG for postgresql actually validates the connection eagerly,
    # so we expect 502 with a recorded error. (If a future Trino allows lazy creation
    # this test would need adjustment.)
    body = r.get_json()
    assert r.status_code in (201, 502), body

    # Inspect the persisted row regardless of HTTP code.
    detail = client.get("/catalogs/pg_broken").get_json()
    if r.status_code == 502:
        assert detail["status"] == "broken"
        assert detail["last_error"], "broken catalogs should record Trino's error"
        # Catalog should NOT be in Trino.
        assert "pg_broken" not in _trino_catalog_names(client)
    else:
        # Trino accepted the CREATE lazily (no eager connection check). The
        # data-source sync then can't reach the backing store, so the catalog
        # lands as DOWN (registered in Trino, backing store unreachable) with
        # the error recorded — still "persists error", just via the down path.
        assert detail["status"] == "down"
        assert detail["last_error"], "an unreachable catalog should record its error"


def test_invariant_5_delete_drops_from_trino(client):
    """DELETE /catalogs/<name> → reconcile drops it from Trino → GET 404."""
    client.post("/catalogs", json={"name": "tpch_byebye", "connector": "tpch"})
    assert "tpch_byebye" in _trino_catalog_names(client)

    r = client.delete("/catalogs/tpch_byebye")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["reconcile"]["all_ok"] is True

    r = client.get("/catalogs/tpch_byebye")
    assert r.status_code == 404
    assert "tpch_byebye" not in _trino_catalog_names(client)


def test_invariant_6_persistence_across_core_restart(client, config, core_app):
    """Recreate the Flask app pointing at the same Postgres + same Trino, run startup
    reconcile, and Trino state must match Postgres state."""
    # Set up: register two catalogs through the original app.
    client.post("/catalogs", json={"name": "tpch_keep_a", "connector": "tpch"})
    client.post("/catalogs", json={"name": "tpch_keep_b", "connector": "tpch"})

    # Simulate Trino losing its memory: drop both out-of-band.
    core_app.extensions["trino"].drop_catalog("tpch_keep_a")
    core_app.extensions["trino"].drop_catalog("tpch_keep_b")

    # "Restart" Core: build a new app on the same Postgres/Trino, with startup
    # reconcile enabled. After construction, Trino state should match Postgres.
    from datapro_core.app import create_app

    new_app = create_app(config, start_heartbeat=False, run_startup_reconcile=True)
    new_client = new_app.test_client()
    names = {row["name"] for row in new_client.get("/trino/state").get_json()}
    assert "tpch_keep_a" in names
    assert "tpch_keep_b" in names


# -------- duplicate prevention / validation -----------------------------------


def test_duplicate_name_returns_409(client):
    client.post("/catalogs", json={"name": "tpch_dup", "connector": "tpch"})
    r = client.post("/catalogs", json={"name": "tpch_dup", "connector": "tpch"})
    assert r.status_code == 409


def test_invalid_name_returns_400(client):
    r = client.post("/catalogs", json={"name": "bad name!", "connector": "tpch"})
    assert r.status_code == 400


def test_get_nonexistent_returns_404(client):
    r = client.get("/catalogs/does_not_exist")
    assert r.status_code == 404
