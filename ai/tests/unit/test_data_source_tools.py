"""Data-source AI tools against live Core. Data sources are sync-owned
(discovered by Core's reconciler), so the agent only reads them — there are no
create/update/delete tools."""

import json
import os
import uuid
from collections.abc import Iterator

import pytest
import requests

from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.get_data_source import GetDataSourceTool
from datapro_ai.llm.tools.get_data_source_columns import GetDataSourceColumnsTool
from datapro_ai.llm.tools.list_data_sources import ListDataSourcesTool


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
def ephemeral_catalog() -> Iterator[str]:
    name = f"dstool_{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{CORE_URL}/catalogs", json={"name": name, "connector": "tpch"}, timeout=10
    )
    assert r.status_code == 201
    yield name
    requests.delete(f"{CORE_URL}/catalogs/{name}", timeout=10)


@pytest.fixture
def ephemeral_source(ctx, ephemeral_catalog) -> dict:
    """A data source is discovered by sync when the catalog is created — look
    up tiny.nation rather than creating it. No cleanup: it cascades away with
    the catalog."""
    rows = json.loads(ListDataSourcesTool().execute(ctx, {"catalog": ephemeral_catalog}))
    return next(
        r for r in rows if r["schema_name"] == "tiny" and r["table_name"] == "nation"
    )


def test_list_filters_by_catalog(ctx, ephemeral_source):
    rows = json.loads(
        ListDataSourcesTool().execute(ctx, {"catalog": ephemeral_source["catalog_name"]})
    )
    # tpch exposes many tables; the discovered source must be among them, and
    # every row belongs to the requested catalog.
    assert ephemeral_source["id"] in {r["id"] for r in rows}
    assert all(r["catalog_name"] == ephemeral_source["catalog_name"] for r in rows)


def test_list_reports_status(ctx, ephemeral_source):
    rows = json.loads(
        ListDataSourcesTool().execute(ctx, {"catalog": ephemeral_source["catalog_name"]})
    )
    assert all(r["status"] in ("active", "deleted") for r in rows)


def test_get_returns_the_row(ctx, ephemeral_source):
    out = GetDataSourceTool().execute(ctx, {"id": ephemeral_source["id"]})
    assert json.loads(out)["id"] == ephemeral_source["id"]


def test_get_raises_on_missing(ctx):
    with pytest.raises(ToolError, match="no data source"):
        GetDataSourceTool().execute(ctx, {"id": str(uuid.uuid4())})


def test_get_columns_returns_name_type_list(ctx, ephemeral_source):
    out = GetDataSourceColumnsTool().execute(ctx, {"id": ephemeral_source["id"]})
    parsed = json.loads(out)
    assert parsed["http_status"] == 200
    body = parsed["response"]
    assert body["path"].endswith(".tiny.nation")
    by_name = {c["name"]: c["type"] for c in body["columns"]}
    assert "nationkey" in by_name
    assert "name" in by_name


def test_get_columns_raises_on_missing_source(ctx):
    with pytest.raises(ToolError, match="no data source"):
        GetDataSourceColumnsTool().execute(ctx, {"id": str(uuid.uuid4())})


def test_only_read_tools_registered():
    from datapro_ai.llm.agent import default_tools

    names = set(default_tools().names())
    for n in ("list_data_sources", "get_data_source", "get_data_source_columns"):
        assert n in names, f"{n} not in default tool set"
    # Data sources are sync-owned — the mutation tools must NOT exist.
    for n in ("create_data_source", "update_data_source", "delete_data_source"):
        assert n not in names, f"{n} should have been removed"
