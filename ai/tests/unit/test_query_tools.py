"""query_objects + preview_query_plan tools against live Core."""

import json
import os
import uuid
from collections.abc import Iterator

import pytest
import requests

from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.preview_query_plan import PreviewQueryPlanTool
from datapro_ai.llm.tools.query_objects import QueryObjectsTool


CORE_URL = os.environ.get("CORE_URL", "http://127.0.0.1:5001")


def _core_alive() -> bool:
    try:
        return requests.get(f"{CORE_URL}/health", timeout=2).ok
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(
    not _core_alive(), reason="Core not reachable at CORE_URL"
)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(core_url=CORE_URL)


@pytest.fixture
def ephemeral_setup() -> Iterator[dict]:
    """Wire up a catalog + data source + object type + factory so the
    semantic query has something to resolve. Tears it all down via the
    catalog DELETE (cascades to source + factory)."""
    catalog = f"qtool_{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{CORE_URL}/catalogs",
        json={"name": catalog, "connector": "tpch"},
        timeout=10,
    )
    assert r.status_code == 201, r.text

    # Data sources are sync-discovered on catalog creation — look up tiny.nation.
    src_rows = requests.get(
        f"{CORE_URL}/data-sources?catalog={catalog}", timeout=10
    ).json()
    src = next(
        r for r in src_rows if r["schema_name"] == "tiny" and r["table_name"] == "nation"
    )
    type_row = requests.post(
        f"{CORE_URL}/object-types",
        json={"name": f"Nation_{uuid.uuid4().hex[:6]}"},
        timeout=10,
    ).json()
    requests.post(
        f"{CORE_URL}/object-factories",
        json={
            "data_source_id": src["id"],
            "object_type_id": type_row["id"],
            "use_all_columns": False,
            "column_spec": ["nationkey", "name"],
        },
        timeout=10,
    )
    try:
        yield {
            "catalog": catalog,
            "data_source_id": src["id"],
            "object_type_id": type_row["id"],
            "object_type_name": type_row["name"],
        }
    finally:
        requests.delete(f"{CORE_URL}/catalogs/{catalog}", timeout=10)
        requests.delete(f"{CORE_URL}/object-types/{type_row['id']}", timeout=10)


# ---- query_objects ------------------------------------------------------


def test_query_objects_returns_columns_rows_and_status(ctx, ephemeral_setup):
    out = QueryObjectsTool().execute(
        ctx, {"from": ephemeral_setup["object_type_name"], "limit": 3}
    )
    parsed = json.loads(out)
    assert "_datasource" in parsed["columns"]
    assert "nationkey" in parsed["columns"]
    assert "name" in parsed["columns"]
    assert len(parsed["rows"]) == 3
    assert parsed["result_status"]["all_ok"] is True
    assert parsed["result_status"]["sql"]


def test_query_objects_unknown_type_raises_with_helpful_message(ctx):
    with pytest.raises(ToolError, match="no object type"):
        QueryObjectsTool().execute(ctx, {"from": "DefinitelyNotAType"})


def test_query_objects_rejects_missing_from(ctx):
    with pytest.raises(ToolError, match="from"):
        QueryObjectsTool().execute(ctx, {})


def test_query_objects_rejects_non_int_limit(ctx, ephemeral_setup):
    with pytest.raises(ToolError, match="limit"):
        QueryObjectsTool().execute(
            ctx,
            {"from": ephemeral_setup["object_type_name"], "limit": "5"},  # type: ignore[dict-item]
        )


# ---- preview_query_plan -------------------------------------------------


def test_preview_returns_sql_without_executing(ctx, ephemeral_setup):
    out = PreviewQueryPlanTool().execute(
        ctx, {"from": ephemeral_setup["object_type_name"], "limit": 5}
    )
    parsed = json.loads(out)
    assert parsed["from"] == ephemeral_setup["object_type_name"]
    assert parsed["limit"] == 5
    assert "SELECT" in parsed["sql"]
    assert "nationkey" in parsed["sql"]
    assert len(parsed["factories_used"]) == 1
    assert parsed["factories_skipped"] == []


def test_preview_unknown_type_raises(ctx):
    with pytest.raises(ToolError, match="no object type"):
        PreviewQueryPlanTool().execute(ctx, {"from": "Nope"})


# ---- registration -------------------------------------------------------


def test_tools_registered_in_default_set():
    from datapro_ai.llm.agent import default_tools

    names = set(default_tools().names())
    assert "query_objects" in names
    assert "preview_query_plan" in names
