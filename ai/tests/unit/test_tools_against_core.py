"""These tests hit a real running Core service. They're 'unit' tests for the
tool implementations specifically — verifying they handle Core's actual response
shapes correctly. They auto-skip when Core isn't reachable."""

import json
import os

import pytest
import requests

from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.list_catalogs import ListCatalogsTool
from datapro_ai.llm.tools.run_raw_trino_query import RunRawTrinoQueryTool


CORE_URL = os.environ.get("CORE_URL", "http://127.0.0.1:5001")


def _core_alive() -> bool:
    try:
        return requests.get(f"{CORE_URL}/health", timeout=2).ok
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(
    not _core_alive(),
    reason="Core not reachable at CORE_URL",
)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(core_url=CORE_URL)


def test_list_catalogs_returns_json_list(ctx):
    tool = ListCatalogsTool()
    result = tool.execute(ctx, {})
    # When Core has catalogs, this is valid JSON; when none, a friendly string.
    if result.startswith("("):
        assert result == "(no catalogs registered)"
    else:
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        for c in parsed:
            assert "name" in c
            assert "connector" in c
            assert "status" in c


def test_run_raw_trino_query_show_catalogs_works(ctx):
    tool = RunRawTrinoQueryTool()
    result = tool.execute(ctx, {"sql": "SHOW CATALOGS", "timeout_seconds": 60})
    parsed = json.loads(result)
    assert "columns" in parsed
    assert "rows" in parsed
    names = {row[0] for row in parsed["rows"]}
    assert "system" in names


def test_run_raw_trino_query_rejects_empty_input(ctx):
    tool = RunRawTrinoQueryTool()
    with pytest.raises(ToolError, match="required"):
        tool.execute(ctx, {"sql": ""})


def test_run_raw_trino_query_returns_error_shape_for_bad_sql(ctx):
    tool = RunRawTrinoQueryTool()
    result = tool.execute(
        ctx,
        {"sql": "SELECT nonexistent_column_xyz FROM system.runtime.nodes", "timeout_seconds": 60},
    )
    # Bad SQL returns a JSON envelope with http_status + response, not a raise.
    parsed = json.loads(result)
    assert parsed["http_status"] == 400
    assert parsed["response"]["error"] == "trino_error"
