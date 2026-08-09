"""CRUD for object types. Live Postgres via the test container; no Trino needed."""

import uuid


def test_list_empty(client):
    r = client.get("/object-types")
    assert r.status_code == 200
    assert r.get_json() == []


def test_create_then_list_then_get(client):
    r = client.post(
        "/object-types",
        json={"name": "Company", "description": "A publicly traded company."},
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["name"] == "Company"
    assert body["description"] == "A publicly traded company."
    assert uuid.UUID(body["id"])  # parses as UUID
    type_id = body["id"]

    # Visible in list.
    r = client.get("/object-types")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["id"] == type_id

    # Fetch by id.
    r = client.get(f"/object-types/{type_id}")
    assert r.status_code == 200
    assert r.get_json()["name"] == "Company"


def test_create_defaults_description_to_empty_string(client):
    r = client.post("/object-types", json={"name": "Filing"})
    assert r.status_code == 201
    assert r.get_json()["description"] == ""


def test_create_409_on_duplicate_name(client):
    client.post("/object-types", json={"name": "Company"})
    r = client.post("/object-types", json={"name": "Company"})
    assert r.status_code == 409
    assert r.get_json()["error"] == "already_exists"


def test_create_validates_name_format(client):
    r = client.post("/object-types", json={"name": "bad name!"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_request"


def test_create_rejects_unknown_fields(client):
    r = client.post(
        "/object-types",
        json={"name": "X", "fields": []},  # `fields` doesn't exist yet
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_request"


def test_get_400_on_invalid_uuid(client):
    r = client.get("/object-types/not-a-uuid")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_id"


def test_get_404_when_missing(client):
    r = client.get(f"/object-types/{uuid.uuid4()}")
    assert r.status_code == 404


def test_patch_updates_description(client):
    type_id = client.post(
        "/object-types", json={"name": "Company", "description": "old"}
    ).get_json()["id"]
    r = client.patch(f"/object-types/{type_id}", json={"description": "new"})
    assert r.status_code == 200
    assert r.get_json()["description"] == "new"
    assert r.get_json()["name"] == "Company"  # unchanged


def test_patch_renames_keeping_id(client):
    type_id = client.post("/object-types", json={"name": "Company"}).get_json()["id"]
    r = client.patch(f"/object-types/{type_id}", json={"name": "Org"})
    assert r.status_code == 200
    assert r.get_json()["name"] == "Org"
    assert r.get_json()["id"] == type_id

    # Fetching by the same id still works.
    assert client.get(f"/object-types/{type_id}").get_json()["name"] == "Org"


def test_patch_rejects_empty_body(client):
    type_id = client.post("/object-types", json={"name": "Company"}).get_json()["id"]
    r = client.patch(f"/object-types/{type_id}", json={})
    assert r.status_code == 400
    assert r.get_json()["error"] == "empty_patch"


def test_patch_rejects_unknown_fields(client):
    type_id = client.post("/object-types", json={"name": "Company"}).get_json()["id"]
    r = client.patch(f"/object-types/{type_id}", json={"description": "x", "extra": 1})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_request"


def test_patch_409_on_rename_collision(client):
    a = client.post("/object-types", json={"name": "A"}).get_json()["id"]
    client.post("/object-types", json={"name": "B"})
    r = client.patch(f"/object-types/{a}", json={"name": "B"})
    assert r.status_code == 409


def test_patch_404_when_missing(client):
    r = client.patch(
        f"/object-types/{uuid.uuid4()}", json={"description": "anything"}
    )
    assert r.status_code == 404


def test_delete_removes_the_row(client):
    type_id = client.post("/object-types", json={"name": "Tmp"}).get_json()["id"]
    r = client.delete(f"/object-types/{type_id}")
    assert r.status_code == 200
    assert r.get_json()["deleted"] == type_id
    assert client.get(f"/object-types/{type_id}").status_code == 404


def test_delete_404_when_missing(client):
    r = client.delete(f"/object-types/{uuid.uuid4()}")
    assert r.status_code == 404


def test_search_filters_case_insensitively_on_name_and_description(client):
    client.post("/object-types", json={"name": "Company", "description": "issuer"})
    client.post("/object-types", json={"name": "Filing", "description": "10-K, 10-Q"})
    client.post("/object-types", json={"name": "Officer", "description": "person"})

    # Match by name substring.
    rows = client.get("/object-types?search=comp").get_json()
    assert [r["name"] for r in rows] == ["Company"]

    # Match by description substring.
    rows = client.get("/object-types?search=10-k").get_json()
    assert [r["name"] for r in rows] == ["Filing"]

    # Empty filter returns everything.
    rows = client.get("/object-types?search=").get_json()
    assert {r["name"] for r in rows} == {"Company", "Filing", "Officer"}
