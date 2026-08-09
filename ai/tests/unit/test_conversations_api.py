"""CRUD on conversations needs no LLM or Core — Postgres only."""


def test_create_and_list(client, config):
    r = client.post("/conversations", json={"title": "Hello"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["title"] == "Hello"
    # Use whatever model the test config defaults to (Sonnet 4.6 in tests,
    # Opus 4.8 in production — see conftest.py).
    assert body["model"] == config.model

    r = client.get("/conversations")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["title"] == "Hello"
    assert rows[0]["message_count"] == 0


def test_get_one_includes_messages(client):
    r = client.post("/conversations", json={"title": "Empty"})
    cid = r.get_json()["id"]
    r = client.get(f"/conversations/{cid}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["title"] == "Empty"
    assert body["messages"] == []


def test_get_nonexistent_returns_404(client):
    r = client.get("/conversations/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_patch_updates_title(client):
    r = client.post("/conversations", json={"title": "Original"})
    cid = r.get_json()["id"]
    r = client.patch(f"/conversations/{cid}", json={"title": "Renamed"})
    assert r.status_code == 200
    assert r.get_json()["title"] == "Renamed"


def test_delete_removes_conversation(client):
    r = client.post("/conversations", json={"title": "To delete"})
    cid = r.get_json()["id"]
    r = client.delete(f"/conversations/{cid}")
    assert r.status_code == 200
    r = client.get(f"/conversations/{cid}")
    assert r.status_code == 404


def test_send_message_requires_anthropic_key_when_unset(client):
    """When ANTHROPIC_API_KEY is unset, the send endpoint returns 503 cleanly
    rather than crashing inside the SDK."""
    import os

    if os.environ.get("ANTHROPIC_API_KEY"):
        # Live key is present — skip; the live test exercises the happy path.
        import pytest

        pytest.skip("ANTHROPIC_API_KEY is set; covered by the live test")

    r = client.post("/conversations", json={"title": "Will fail"})
    cid = r.get_json()["id"]
    r = client.post(f"/conversations/{cid}/messages", json={"text": "hi"})
    assert r.status_code == 503
    assert r.get_json()["error"] == "anthropic_not_configured"
