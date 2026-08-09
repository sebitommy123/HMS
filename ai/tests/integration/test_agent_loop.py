"""Live end-to-end: real Anthropic API + real Core.

Marked `live` — requires `ANTHROPIC_API_KEY` AND Core running on `CORE_URL`.
Without either, these tests skip automatically. They cost real Anthropic API
tokens (Opus-tier), so they're opt-in via `make test-live`.
"""

import os

import pytest
import requests

CORE_URL = os.environ.get("CORE_URL", "http://127.0.0.1:5001")


def _core_alive() -> bool:
    try:
        return requests.get(f"{CORE_URL}/health", timeout=2).ok
    except requests.RequestException:
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    ),
    pytest.mark.skipif(not _core_alive(), reason="Core not reachable at CORE_URL"),
]


def test_agent_lists_catalogs_when_asked(client):
    """The model should pick the list_catalogs tool and report what it found."""
    r = client.post("/conversations", json={"title": "Catalog inventory"})
    cid = r.get_json()["id"]
    r = client.post(
        f"/conversations/{cid}/messages",
        json={"text": "What catalogs are registered? Don't query them — just list."},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["final_stop_reason"] == "end_turn"

    # The transcript should contain at least one tool_use for list_catalogs.
    tool_uses = []
    for m in body["messages"]:
        if m["role"] == "assistant":
            for block in m["content"]:
                if block.get("type") == "tool_use":
                    tool_uses.append(block["name"])
    assert "list_catalogs" in tool_uses


def test_agent_runs_sql_when_asked_to_query(client):
    """Tell the model to run a specific SHOW CATALOGS query — it should pick
    run_raw_trino_query and surface the results to the user."""
    r = client.post("/conversations", json={"title": "Run SHOW CATALOGS"})
    cid = r.get_json()["id"]
    r = client.post(
        f"/conversations/{cid}/messages",
        json={
            "text": "Run `SHOW CATALOGS` via run_raw_trino_query and tell me how many catalogs exist.",
        },
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()

    tool_uses = [
        b["name"]
        for m in body["messages"]
        if m["role"] == "assistant"
        for b in m["content"]
        if b.get("type") == "tool_use"
    ]
    assert "run_raw_trino_query" in tool_uses


def test_full_persistence_round_trip(client):
    """After sending a message, the GET endpoint returns the full transcript
    including tool_use / tool_result blocks."""
    r = client.post("/conversations", json={"title": "Round-trip"})
    cid = r.get_json()["id"]
    r = client.post(
        f"/conversations/{cid}/messages",
        json={"text": "List the catalogs."},
    )
    assert r.status_code == 200

    r = client.get(f"/conversations/{cid}")
    assert r.status_code == 200
    body = r.get_json()
    # At minimum: user msg, assistant w/ tool_use, user w/ tool_result, assistant final
    assert len(body["messages"]) >= 4
    assert body["messages"][0]["role"] == "user"
