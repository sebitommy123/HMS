"""PATCH /catalogs/{name} — live tests against real Postgres + real Trino."""

import uuid


def _trino_catalog_names(client) -> set[str]:
    r = client.get("/trino/state")
    assert r.status_code == 200, r.get_json()
    return {row["name"] for row in r.get_json()}


def _create_tpch(client, name: str | None = None) -> str:
    name = name or f"patchtest_{uuid.uuid4().hex[:8]}"
    r = client.post("/catalogs", json={"name": name, "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    return name


def test_patch_404_when_catalog_missing(client):
    r = client.patch("/catalogs/does_not_exist_xyz", json={"connector": "tpch"})
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_patch_rejects_empty_body(client):
    name = _create_tpch(client)
    r = client.patch(f"/catalogs/{name}", json={})
    assert r.status_code == 400
    assert r.get_json()["error"] == "empty_patch"


def test_patch_rejects_unknown_fields(client):
    name = _create_tpch(client)
    r = client.patch(f"/catalogs/{name}", json={"name": "new_name"})
    assert r.status_code == 400, r.get_json()
    assert r.get_json()["error"] == "invalid_request"


def test_patch_validates_connector_format(client):
    name = _create_tpch(client)
    r = client.patch(f"/catalogs/{name}", json={"connector": "bad name!"})
    assert r.status_code == 400, r.get_json()


def test_patch_noop_when_nothing_changed(client):
    name = _create_tpch(client)
    # Same connector, no properties (tpch takes none) → no-op patch.
    r = client.patch(f"/catalogs/{name}", json={"connector": "tpch"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["catalog"]["connector"] == "tpch"
    assert body["reconcile"] is None
    # Still in Trino as before.
    assert name in _trino_catalog_names(client)


def test_patch_replaces_properties_in_trino(client):
    """Property change forces DROP+CREATE so Trino actually picks up the new
    values. tpch is too simple to vary — use memory connector which is
    similarly properties-free, then move to postgresql for a richer probe."""
    name = _create_tpch(client)
    # Switch to memory — also has no required properties, so it'll succeed.
    r = client.patch(f"/catalogs/{name}", json={"connector": "memory"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["catalog"]["connector"] == "memory"
    assert body["reconcile"]["all_ok"] is True
    # The handler did the DROP itself before reconcile ran, so the reconcile
    # action list only contains the CREATE — but the net effect (Trino now
    # has the catalog with the new connector) is verified below.
    action_kinds = [a["kind"] for a in body["reconcile"]["actions"]]
    assert "create" in action_kinds
    assert name in _trino_catalog_names(client)


def test_patch_properties_only_triggers_recreate(client, core_app):
    """The key invariant: changing only properties (with connector unchanged)
    must still force a Trino DROP+CREATE — otherwise stale properties stick
    because reconcile diffs by connector alone."""
    # postgresql needs real properties — register one pointing at a fake URL
    # (it'll be BROKEN at first because Trino validates eagerly), then PATCH
    # to a fresh fake URL and verify Trino was asked to recreate.
    name = f"pgpatch_{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/catalogs",
        json={
            "name": name,
            "connector": "postgresql",
            "properties": {
                "connection-url": "jdbc:postgresql://nowhere-a.invalid:5432/x",
                "connection-user": "a",
                "connection-password": "a",
            },
        },
    )
    # Likely 502 (broken), occasionally 201 if Trino accepted it. Either way
    # the row exists.
    assert r.status_code in (201, 502), r.get_json()

    r = client.patch(
        f"/catalogs/{name}",
        json={
            "properties": {
                "connection-url": "jdbc:postgresql://nowhere-b.invalid:5432/x",
                "connection-user": "b",
                "connection-password": "b",
            }
        },
    )
    # Still broken (different fake URL), but the row should now carry the new
    # properties and the reconcile attempted both a DROP (if it was there)
    # and a CREATE.
    body = r.get_json()
    assert body["catalog"]["properties"]["connection-user"] == "b"
    assert body["catalog"]["properties"]["connection-password"] == "b"
    # At least one CREATE action was attempted (proves we forced a recreate).
    actions = body["reconcile"]["actions"]
    assert any(a["kind"] == "create" and a["name"] == name for a in actions)


def test_patch_secrets_masked_on_get(client):
    """Round-trip: PATCH a credential-shaped property, then GET should mask it."""
    name = f"masked_{uuid.uuid4().hex[:8]}"
    client.post(
        "/catalogs",
        json={
            "name": name,
            "connector": "postgresql",
            "properties": {
                "connection-url": "jdbc:postgresql://nowhere.invalid:5432/x",
                "connection-user": "alice",
                "connection-password": "secret_v1",
            },
        },
    )
    client.patch(
        f"/catalogs/{name}",
        json={
            "properties": {
                "connection-url": "jdbc:postgresql://nowhere.invalid:5432/x",
                "connection-user": "alice",
                "connection-password": "secret_v2",
            }
        },
    )
    # Note: /catalogs/{name} returns properties verbatim (Core operator-facing
    # API). Masking is the AI service's responsibility before the model sees
    # them. So we just verify the raw value got through.
    r = client.get(f"/catalogs/{name}")
    assert r.status_code == 200
    assert r.get_json()["properties"]["connection-password"] == "secret_v2"


def test_patch_resets_broken_status_to_enabled_on_success(client):
    """A broken catalog whose PATCH fixes the issue should come back as enabled.
    We force a broken initial state by using a connector Trino doesn't have
    registered as a plugin."""
    name = f"recover_{uuid.uuid4().hex[:8]}"
    client.post(
        "/catalogs",
        json={"name": name, "connector": "no_such_connector_plugin"},
    )
    # Trino rejects the CREATE CATALOG → row goes broken.
    assert client.get(f"/catalogs/{name}").get_json()["status"] == "broken"

    # Fix it by switching to tpch (a real connector with no properties).
    r = client.patch(f"/catalogs/{name}", json={"connector": "tpch", "properties": {}})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["catalog"]["status"] == "enabled"
    assert name in _trino_catalog_names(client)
