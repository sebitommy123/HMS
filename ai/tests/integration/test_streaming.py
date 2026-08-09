"""Live SSE end-to-end. Requires ANTHROPIC_API_KEY + Core."""

import json
import os
import re
import threading
from collections.abc import Iterator

import pytest
import requests
from werkzeug.serving import make_server

CORE_URL = os.environ.get("CORE_URL", "http://127.0.0.1:5001")


def _core_alive() -> bool:
    try:
        return requests.get(f"{CORE_URL}/health", timeout=2).ok
    except requests.RequestException:
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"
    ),
    pytest.mark.skipif(not _core_alive(), reason="Core not reachable at CORE_URL"),
]


# Flask's test client buffers the whole streaming response — useless for SSE
# verification. Spin up a real WSGI server in a thread instead.
@pytest.fixture
def server(ai_app) -> Iterator[str]:
    srv = make_server("127.0.0.1", 0, ai_app, threaded=True)
    port = srv.server_port
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()
        thread.join(timeout=2)


def _parse_sse_events(stream) -> Iterator[dict]:
    """Yield each `{type, data}` event the server emits, decoding JSON data."""
    buffer = ""
    for chunk in stream.iter_content(chunk_size=None, decode_unicode=True):
        if not chunk:
            continue
        buffer += chunk
        # SSE events are separated by a blank line (\n\n). Take everything
        # complete from the buffer; keep the last partial chunk for next time.
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            event = _parse_one_event(raw)
            if event is not None:
                yield event


def _parse_one_event(raw: str) -> dict | None:
    event_type = None
    data_lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
    if event_type is None or not data_lines:
        return None
    return {"type": event_type, "data": json.loads("\n".join(data_lines))}


def test_stream_emits_full_event_sequence(server, client):
    conv_id = client.post("/conversations", json={"title": "Streaming test"}).get_json()["id"]

    with requests.post(
        f"{server}/conversations/{conv_id}/messages/stream",
        json={"text": "List my catalogs. Don't query them."},
        stream=True,
        timeout=120,
    ) as resp:
        assert resp.status_code == 200, resp.text
        assert "text/event-stream" in resp.headers.get("content-type", "")

        events = list(_parse_sse_events(resp))

    types = [e["type"] for e in events]

    # Required ordering: stream starts → at least one assistant turn → done.
    assert types[0] == "stream_start"
    assert types[-1] in ("stream_done", "stream_error"), f"unexpected tail: {types}"
    assert "stream_error" not in types, [e for e in events if e["type"] == "stream_error"]
    assert "assistant_start" in types
    assert "assistant_persisted" in types
    # We asked for catalogs, so the agent should call list_catalogs at least once.
    tool_executing = [e for e in events if e["type"] == "tool_executing"]
    assert any(e["data"]["name"] == "list_catalogs" for e in tool_executing), tool_executing
    # And the tool_result for each tool_executing.
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_results) == len(tool_executing)


def test_stream_emits_text_deltas(server, client):
    conv_id = client.post("/conversations", json={"title": "Delta test"}).get_json()["id"]

    with requests.post(
        f"{server}/conversations/{conv_id}/messages/stream",
        json={"text": "Reply with exactly: 'streaming works'"},
        stream=True,
        timeout=120,
    ) as resp:
        assert resp.status_code == 200
        events = list(_parse_sse_events(resp))

    deltas = [e["data"]["text"] for e in events if e["type"] == "text_delta"]
    assert len(deltas) > 0, "expected at least one text_delta event"
    combined = "".join(deltas).lower()
    assert "streaming" in combined or "works" in combined, combined


def test_stream_auto_names_conversation(server, client):
    # Default title — the auto-namer should rename it.
    conv_id = client.post("/conversations", json={}).get_json()["id"]
    assert client.get(f"/conversations/{conv_id}").get_json()["title"] == "New conversation"

    with requests.post(
        f"{server}/conversations/{conv_id}/messages/stream",
        json={"text": "What's the capital of France? One sentence."},
        stream=True,
        timeout=120,
    ) as resp:
        assert resp.status_code == 200
        events = list(_parse_sse_events(resp))

    title_events = [e for e in events if e["type"] == "title_updated"]
    assert len(title_events) == 1, [e["type"] for e in events]
    new_title = title_events[0]["data"]["title"]
    assert new_title != "New conversation"
    assert 1 <= len(new_title.split()) <= 10, f"title too long: {new_title!r}"

    # And it should be persisted.
    refreshed = client.get(f"/conversations/{conv_id}").get_json()
    assert refreshed["title"] == new_title


def test_stream_does_not_rename_when_title_was_set_explicitly(server, client):
    conv_id = client.post(
        "/conversations", json={"title": "My custom title"}
    ).get_json()["id"]

    with requests.post(
        f"{server}/conversations/{conv_id}/messages/stream",
        json={"text": "Say hi."},
        stream=True,
        timeout=120,
    ) as resp:
        events = list(_parse_sse_events(resp))

    assert not any(e["type"] == "title_updated" for e in events)
    assert client.get(f"/conversations/{conv_id}").get_json()["title"] == "My custom title"


def test_json_endpoint_still_works_via_same_generator(client):
    """The JSON endpoint must remain functional — it drains the same generator."""
    conv_id = client.post("/conversations", json={"title": "JSON path"}).get_json()["id"]
    r = client.post(
        f"/conversations/{conv_id}/messages",
        json={"text": "List my catalogs."},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["final_stop_reason"] == "end_turn"
    assert len(body["messages"]) >= 2


def test_stream_text_delta_count_is_positive_even_for_tool_only_turns(server, client):
    """A turn that uses only tools (no preamble text) may still have 0 text_deltas
    in the first iteration — but the FINAL turn always summarises, so the total
    across all iterations should always be > 0."""
    conv_id = client.post("/conversations", json={"title": "Tool-only test"}).get_json()["id"]
    with requests.post(
        f"{server}/conversations/{conv_id}/messages/stream",
        json={"text": "List my catalogs."},
        stream=True,
        timeout=120,
    ) as resp:
        events = list(_parse_sse_events(resp))
    total_deltas = sum(1 for e in events if e["type"] == "text_delta")
    assert total_deltas > 0


# Light sanity check the SSE wire format is correct enough that a regex parser
# could also handle it. (We don't actually use a regex anywhere; this is purely
# a smoke check that the framing isn't malformed.)
def test_sse_framing(server, client):
    conv_id = client.post("/conversations", json={"title": "Framing"}).get_json()["id"]
    with requests.post(
        f"{server}/conversations/{conv_id}/messages/stream",
        json={"text": "say hi briefly"},
        stream=True,
        timeout=120,
    ) as resp:
        raw = resp.text
    # Every event should have "event: " then "data: " on a separate line.
    assert re.search(r"event: \w+\ndata: \{", raw), raw[:500]
