"""The 'AI can see what you see' channel: the PUT endpoint, the store, and the
get_current_view / read_observation tools. No LLM needed."""

import json

from datapro_ai.llm.tools.base import ToolContext
from datapro_ai.llm.tools.get_current_view import GetCurrentViewTool
from datapro_ai.llm.tools.read_observation import ReadObservationTool
from datapro_ai.view_context import ViewAccessor, ViewContextStore, normalize_and_cap


CONV = "11111111-1111-1111-1111-111111111111"


def _put(client, conv, body):
    return client.put(f"/conversations/{conv}/view-context", json=body)


def test_put_stores_normalized_view(client):
    r = _put(
        client,
        CONV,
        {
            "route": "/data-sources/abc",
            "title": "cat.sch.tbl",
            "entity": {"type": "data_source", "id": "abc"},
            "observations": {
                "data_source_preview": {
                    "description": "Preview: 1 row",
                    "kind": "table",
                    "data": {"columns": ["a"], "rows": [[1]]},
                }
            },
        },
    )
    assert r.status_code == 204
    view = client.application.extensions["view_context_store"].get(CONV)
    assert view["route"] == "/data-sources/abc"
    assert view["title"] == "cat.sch.tbl"
    assert view["observations"]["data_source_preview"]["data"] == {
        "columns": ["a"],
        "rows": [[1]],
    }
    assert view["updated_at"] > 0  # server-stamped


def test_tools_read_the_published_view(client):
    _put(
        client,
        CONV,
        {
            "route": "/data-sources/abc",
            "title": "cat.sch.tbl",
            "observations": {
                "data_source_preview": {
                    "description": "Preview: 0 rows",
                    "kind": "table",
                    "data": {"columns": ["num", "text"], "rows": []},
                }
            },
        },
    )
    store = client.application.extensions["view_context_store"]
    ctx = ToolContext(core_url="x", view=ViewAccessor(store, CONV))

    manifest = json.loads(GetCurrentViewTool().execute(ctx, {}))
    assert manifest["route"] == "/data-sources/abc"
    # The manifest lists observations but NOT their payloads.
    assert manifest["observations"][0]["key"] == "data_source_preview"
    assert "data" not in manifest["observations"][0]

    obs = json.loads(ReadObservationTool().execute(ctx, {"key": "data_source_preview"}))
    assert obs["data"] == {"columns": ["num", "text"], "rows": []}


def test_put_tolerates_garbage_body(client):
    r = client.put(
        f"/conversations/{CONV}/view-context",
        data="this is not json",
        content_type="text/plain",
    )
    assert r.status_code == 204  # never errors on a bad body


def test_no_view_returns_graceful_note(client):
    store = client.application.extensions["view_context_store"]
    ctx = ToolContext(core_url="x", view=ViewAccessor(store, "no-such-conv"))
    manifest = json.loads(GetCurrentViewTool().execute(ctx, {}))
    assert manifest["current_view"] is None


# ---- capping / robustness (units) ----


def test_oversized_observation_is_capped():
    view = normalize_and_cap(
        {
            "route": "/x",
            "observations": {
                "big": {"description": "d", "kind": "text", "data": "x" * 200_000}
            },
        }
    )
    obs = view["observations"]["big"]
    assert obs["truncated"] is True
    assert len(obs["data"]) < 200_000


def test_row_list_is_prefix_truncated():
    rows = [[i, "v"] for i in range(100_000)]
    view = normalize_and_cap(
        {"route": "/x", "observations": {"t": {"kind": "table", "data": rows}}}
    )
    obs = view["observations"]["t"]
    assert obs["truncated"] is True
    assert 0 < len(obs["data"]) < len(rows)  # kept a prefix


# ---- multi-user isolation (the crucial property) ----


def test_views_are_isolated_per_conversation():
    store = ViewContextStore()
    store.put("userA", {"route": "/a", "observations": {}})
    store.put("userB", {"route": "/b", "observations": {}})
    # Each conversation's accessor sees ONLY its own view — never the other's.
    assert ViewAccessor(store, "userA").current()["route"] == "/a"
    assert ViewAccessor(store, "userB").current()["route"] == "/b"
    assert ViewAccessor(store, "userC").current() is None


def test_store_lru_evicts_oldest():
    store = ViewContextStore(max_conversations=2)
    store.put("a", {"route": "/a"})
    store.put("b", {"route": "/b"})
    store.put("c", {"route": "/c"})  # over cap → evicts least-recently-used (a)
    assert store.get("a") is None
    assert store.get("b") is not None
    assert store.get("c") is not None
