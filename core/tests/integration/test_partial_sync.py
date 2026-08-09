"""Integration tests for partial-sync scenarios — what reconcile does when Postgres
and Trino aren't aligned in various subtle ways.

These complement the basic invariants:
- invariant 3 covers "Trino missing a catalog Postgres has"
- invariant 5 covers the explicit DELETE path

These tests cover everything else:
- Trino has an EXTRA catalog Postgres doesn't know about (reconcile drops it)
- Name match but wrong connector (reconcile drops and re-creates)
- One action fails, others still run
- Trino completely unreachable during reconcile
"""

from sqlalchemy import text

from datapro_core.trino_client import TrinoClient


def _trino_names(client) -> set[str]:
    return {row["name"] for row in client.get("/trino/state").get_json()}


# -- 1. Trino has a catalog Postgres doesn't (via reconcile, not DELETE) -------


def test_reconcile_drops_extra_catalogs_in_trino(client, core_app):
    """Operator created a catalog in Trino out-of-band. Reconcile should remove it
    because Core asserts itself as source of truth."""
    # Manually create a catalog in Trino, bypassing Core.
    core_app.extensions["trino"].create_catalog("rogue", "tpch", {})
    assert "rogue" in _trino_names(client)

    r = client.post("/reconcile")
    assert r.status_code == 200, r.get_json()
    actions = r.get_json()["actions"]
    assert any(a["kind"] == "drop" and a["name"] == "rogue" and a["ok"] for a in actions)

    assert "rogue" not in _trino_names(client)


# -- 2. Name match but wrong connector -----------------------------------------


def test_connector_mismatch_triggers_replace(client, core_app):
    """Postgres says foo: tpch. Trino actually has foo: memory. Reconcile should
    drop and re-create with the correct connector."""
    # Set up the drift: Postgres has tpch, Trino has memory under the same name.
    r = client.post("/catalogs", json={"name": "foo", "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    # Out-of-band: drop, then re-create with wrong connector.
    core_app.extensions["trino"].drop_catalog("foo")
    core_app.extensions["trino"].create_catalog("foo", "memory", {})
    # Confirm the drift.
    state = {row["name"]: row["connector"] for row in client.get("/trino/state").get_json()}
    assert state["foo"] == "memory"

    # Reconcile.
    r = client.post("/reconcile")
    assert r.status_code == 200, r.get_json()
    actions = r.get_json()["actions"]
    # Should be exactly: DROP foo, CREATE foo. In that order.
    kinds = [(a["kind"], a["name"], a["ok"]) for a in actions]
    assert kinds[0] == ("drop", "foo", True)
    assert kinds[1] == ("create", "foo", True)

    # Final state: Trino has foo with the correct connector.
    state = {row["name"]: row["connector"] for row in client.get("/trino/state").get_json()}
    assert state["foo"] == "tpch"


# -- 3. Partial failure: one action fails, others still run --------------------


def test_partial_failure_does_not_block_other_actions(client, core_app):
    """Mix one catalog that will fail with one that will succeed. The good one
    must still get created; the bad one must be marked broken.

    We bypass the API for the inserts so the per-POST synchronous reconcile
    doesn't run — we want the test's explicit reconcile to be the one that
    surfaces the partial failure.

    "bad_one" uses a connector name that Trino doesn't have a plugin for; this
    gives a deterministic eager failure (the postgresql connector's connection
    validation is lazy in some Trino versions)."""
    engine = core_app.extensions["db_engine"]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO catalogs "
                "(name, connector, properties, status, version, created_at, updated_at) "
                "VALUES (:n1, 'tpch', :p, 'enabled', 1, NOW(), NOW()), "
                "       (:n2, :bad_conn, :p, 'enabled', 1, NOW(), NOW())"
            ),
            {
                "n1": "good_one",
                "n2": "bad_one",
                "p": "{}",
                "bad_conn": "nonexistent_plugin",
            },
        )

    r = client.post("/reconcile")
    body = r.get_json()
    # Reconcile reports per-action status; overall all_ok is False but the good
    # action still ran.
    assert body["all_ok"] is False
    by_name = {a["name"]: a for a in body["actions"]}
    assert by_name["good_one"]["ok"] is True, body
    assert by_name["bad_one"]["ok"] is False
    assert by_name["bad_one"]["error"], "expected Trino's error message"

    # Postgres state reflects per-row outcome.
    assert client.get("/catalogs/good_one").get_json()["status"] == "enabled"
    bad = client.get("/catalogs/bad_one").get_json()
    assert bad["status"] == "broken"
    assert bad["last_error"]

    # Trino has the good one, not the bad one.
    names = _trino_names(client)
    assert "good_one" in names
    assert "bad_one" not in names


# -- 4. Trino completely unreachable during reconcile --------------------------


def test_reconcile_fails_cleanly_when_trino_down(client, core_app):
    """If Trino is unreachable, reconcile must surface that without partially
    mutating Postgres state."""
    # First register a catalog while Trino is reachable.
    r = client.post("/catalogs", json={"name": "before_outage", "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    snapshot_before = client.get("/catalogs/before_outage").get_json()

    # Point Core's TrinoClient at a port that doesn't exist (simulates Trino down,
    # cheaper than actually killing the container).
    core_app.extensions["trino"] = TrinoClient(
        host="127.0.0.1", port=1, user="datapro-core-test"
    )

    r = client.post("/reconcile")
    # Reconcile bubbles up the Trino failure as a 500 (the list_catalogs() call
    # at the top of reconcile() raises before any actions are planned).
    assert r.status_code >= 500

    # Postgres row is unchanged — no partial state mutation.
    snapshot_after = client.get("/catalogs/before_outage").get_json()
    assert snapshot_after["status"] == snapshot_before["status"]
    assert snapshot_after["last_error"] == snapshot_before["last_error"]


# -- 5. Reconcile against half-stale Trino: mix of OK, drift, extras, missing --


def test_reconcile_converges_multiple_drift_classes_at_once(client, core_app):
    """The boss test: simultaneously have (a) catalog in Postgres but missing in
    Trino, (b) catalog in Trino but missing in Postgres, (c) catalog in both
    with wrong connector. Reconcile should fix all three in one pass.

    We deliberately bypass the POST /catalogs endpoint to set up the desired
    state, because the per-POST synchronous reconcile would otherwise "fix" the
    drift before the test's explicit reconcile sees it."""
    engine = core_app.extensions["db_engine"]
    trino = core_app.extensions["trino"]

    # Set up Postgres desired state via direct INSERT (no reconcile triggered).
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO catalogs "
                "(name, connector, properties, status, version, created_at, updated_at) "
                "VALUES (:n, :c, :p, 'enabled', 1, NOW(), NOW())"
            ),
            [
                {"n": "missing", "c": "tpch", "p": "{}"},
                {"n": "wrong_conn", "c": "tpch", "p": "{}"},
            ],
        )

    # Set up Trino's actual state directly:
    # (a) "missing" absent from Trino
    # (b) "extra" exists in Trino but not in Postgres
    # (c) "wrong_conn" exists in Trino with the wrong connector
    trino.create_catalog("extra", "tpch", {})
    trino.create_catalog("wrong_conn", "memory", {})

    # Now reconcile.
    r = client.post("/reconcile")
    body = r.get_json()
    assert body["all_ok"] is True, body

    kinds = [(a["kind"], a["name"]) for a in body["actions"]]
    assert ("create", "missing") in kinds
    assert ("drop", "extra") in kinds
    assert ("drop", "wrong_conn") in kinds
    assert ("create", "wrong_conn") in kinds

    state = {row["name"]: row["connector"] for row in client.get("/trino/state").get_json()}
    assert state.get("missing") == "tpch"
    assert state.get("wrong_conn") == "tpch"
    assert "extra" not in state
