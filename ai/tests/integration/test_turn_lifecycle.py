"""Cancel + reload-resume + heartbeat — exercises the turn registry over a real
WSGI server with the real Anthropic API and real Core."""

import json
import os
import re
import threading
import time
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


def _parse_sse(stream) -> Iterator[dict]:
    buffer = ""
    for chunk in stream.iter_content(chunk_size=None, decode_unicode=True):
        if not chunk:
            continue
        buffer += chunk
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            event_type = None
            data_lines: list[str] = []
            for line in raw.splitlines():
                if line.startswith("event:"):
                    event_type = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:") :].lstrip())
            if event_type and data_lines:
                yield {"type": event_type, "data": json.loads("\n".join(data_lines))}


def test_cancel_stops_the_agent_mid_turn(server, client):
    """POST /cancel mid-stream causes the agent to exit at its next checkpoint
    and the stream to emit a stream_done with final_stop_reason=cancelled."""
    conv_id = client.post("/conversations", json={"title": "Cancel test"}).get_json()[
        "id"
    ]

    # Ask the model to do something expensive enough that we can cancel it
    # mid-stream. A long-form prompt with no tool use gives us a steady
    # text_delta stream we can cut off.
    resp = requests.post(
        f"{server}/conversations/{conv_id}/messages/stream",
        json={
            "text": (
                "Write a 500-word essay about the history of the printing press. "
                "Be thorough."
            )
        },
        stream=True,
        timeout=120,
    )
    assert resp.status_code == 200

    events: list[dict] = []
    cancelled = False
    for ev in _parse_sse(resp):
        events.append(ev)
        # Cancel as soon as we see real text streaming.
        if not cancelled and ev["type"] == "text_delta":
            r = requests.post(
                f"{server}/conversations/{conv_id}/messages/cancel", timeout=10
            )
            assert r.status_code == 200, r.text
            assert r.json()["cancelled"] is True
            cancelled = True
        if ev["type"] in ("stream_done", "stream_error"):
            break

    types = [e["type"] for e in events]
    assert "stream_error" not in types, [e for e in events if e["type"] == "stream_error"]
    done = next(e for e in events if e["type"] == "stream_done")
    assert done["data"]["final_stop_reason"] == "cancelled"

    # The partial assistant message we persisted should be visible on refetch.
    refreshed = client.get(f"/conversations/{conv_id}").get_json()
    asst = [m for m in refreshed["messages"] if m["role"] == "assistant"]
    assert len(asst) >= 1
    # Last assistant message should be the cancelled partial.
    assert asst[-1]["stop_reason"] == "cancelled"


def test_cancel_with_no_active_turn_returns_404(server, client):
    conv_id = client.post("/conversations", json={}).get_json()["id"]
    r = requests.post(f"{server}/conversations/{conv_id}/messages/cancel", timeout=10)
    assert r.status_code == 404
    assert r.json()["cancelled"] is False


def test_subscribe_to_active_stream_after_send(server, client):
    """A second subscriber that joins an already-running turn should see the
    same events as the first (replay + live tail) and reach the same end state."""
    conv_id = client.post("/conversations", json={"title": "Resume test"}).get_json()[
        "id"
    ]

    # Start the turn in the background and capture all events.
    primary_events: list[dict] = []

    def primary() -> None:
        resp = requests.post(
            f"{server}/conversations/{conv_id}/messages/stream",
            json={"text": "List my catalogs in detail."},
            stream=True,
            timeout=120,
        )
        for ev in _parse_sse(resp):
            primary_events.append(ev)
            if ev["type"] in ("stream_done", "stream_error"):
                break

    t = threading.Thread(target=primary, daemon=True)
    t.start()

    # Give the primary subscriber a moment to start the turn — then attach as
    # a second subscriber via GET (the reload-resume code path).
    time.sleep(0.5)
    resume_events: list[dict] = []
    resp2 = requests.get(
        f"{server}/conversations/{conv_id}/messages/stream",
        stream=True,
        timeout=120,
    )
    assert resp2.status_code == 200, resp2.text
    for ev in _parse_sse(resp2):
        resume_events.append(ev)
        if ev["type"] in ("stream_done", "stream_error"):
            break

    t.join(timeout=60)
    assert not t.is_alive()

    # Resume saw the full event sequence (replay + live tail).
    primary_types = [e["type"] for e in primary_events]
    resume_types = [e["type"] for e in resume_events]
    assert resume_types[0] == "stream_start"
    assert resume_types[-1] == "stream_done"
    # Both reached the same terminal state.
    assert primary_types[-1] == "stream_done"
    assert (
        primary_events[-1]["data"]["final_stop_reason"]
        == resume_events[-1]["data"]["final_stop_reason"]
    )


def test_subscribe_when_no_active_turn_returns_204(server, client):
    conv_id = client.post("/conversations", json={}).get_json()["id"]
    r = requests.get(
        f"{server}/conversations/{conv_id}/messages/stream", timeout=10
    )
    assert r.status_code == 204


def test_second_concurrent_send_returns_409(server, client):
    """While a turn is in flight, a new POST on the same conversation should
    be rejected — a single conversation has at most one active turn."""
    conv_id = client.post("/conversations", json={}).get_json()["id"]

    def first() -> None:
        resp = requests.post(
            f"{server}/conversations/{conv_id}/messages/stream",
            json={"text": "Write a 300-word essay about Rust's borrow checker."},
            stream=True,
            timeout=120,
        )
        # Drain it so it actually starts (and stays open).
        for _ in _parse_sse(resp):
            pass

    t = threading.Thread(target=first, daemon=True)
    t.start()
    time.sleep(0.5)  # let the first turn register.

    # Second concurrent POST should 409.
    r2 = requests.post(
        f"{server}/conversations/{conv_id}/messages/stream",
        json={"text": "should be rejected"},
        timeout=10,
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"] == "turn_in_progress"

    # Cancel the first turn so the test doesn't waste tokens.
    requests.post(f"{server}/conversations/{conv_id}/messages/cancel", timeout=5)
    t.join(timeout=30)


def test_heartbeat_keeps_long_stream_alive(server, client):
    """If the agent goes quiet for a while (e.g. slow tool call), the SSE
    consumer should emit heartbeat comment lines so the connection stays
    alive. Verify by reading raw bytes and looking for ``:`` comment lines."""
    # Patch heartbeat down to 1s so the test doesn't take forever. We import
    # the module and rebind the constant for this test only.
    from datapro_ai.api import messages as messages_mod

    original = messages_mod.HEARTBEAT_SECONDS
    messages_mod.HEARTBEAT_SECONDS = 0.5
    try:
        conv_id = client.post("/conversations", json={}).get_json()["id"]
        # Long bash sleep — keeps the agent busy on the tool runner side with
        # no SSE events flowing for a few seconds.
        resp = requests.post(
            f"{server}/conversations/{conv_id}/messages/stream",
            json={"text": "Run the bash command: sleep 3 && echo done"},
            stream=True,
            timeout=60,
        )
        raw = b""
        deadline = time.monotonic() + 30
        for chunk in resp.iter_content(chunk_size=None):
            raw += chunk
            if b"stream_done" in raw or time.monotonic() > deadline:
                break
        # SSE comment lines start with ":" (followed by free text).
        assert re.search(rb"\n: heartbeat\n", raw) or raw.startswith(b": heartbeat"), (
            "expected at least one heartbeat comment line; got first 500 bytes: "
            + repr(raw[:500])
        )
    finally:
        messages_mod.HEARTBEAT_SECONDS = original
