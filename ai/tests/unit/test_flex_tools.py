"""Live tests for the flex AI tools against a running Core.

Covers:
  - create_flex_catalog: creates + queries
  - view_flex_module: line-number prefixed output, range view
  - replace_in_flex_module: single-occurrence happy + ambiguous reject
  - replace_flex_module_lines: line-range happy + invalid range reject
  - set_flex_module: full overwrite
  - preview_flex_module: returns sample rows from a transient catalog

Cleans up created catalogs in teardown.
"""

import json
import os
import uuid
from collections.abc import Iterator

import pytest
import requests

from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.create_flex_catalog import CreateFlexCatalogTool
from datapro_ai.llm.tools.preview_flex_module import PreviewFlexModuleTool
from datapro_ai.llm.tools.replace_flex_module_lines import (
    ReplaceFlexModuleLinesTool,
)
from datapro_ai.llm.tools.replace_in_flex_module import ReplaceInFlexModuleTool
from datapro_ai.llm.tools.set_flex_module import SetFlexModuleTool
from datapro_ai.llm.tools.view_flex_module import ViewFlexModuleTool


CORE_URL = os.environ.get("CORE_URL", "http://127.0.0.1:5001")


def _core_alive() -> bool:
    try:
        return requests.get(f"{CORE_URL}/health", timeout=2).ok
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(
    not _core_alive(), reason="Core not reachable at CORE_URL"
)


SIMPLE = """\
from datapro_flex import batch_from_rows

TABLE = {
    "schema": "default",
    "name": "items",
    "columns": [
        {"name": "id", "type": "BIGINT"},
        {"name": "label", "type": "VARCHAR"},
    ],
}


def get_tables():
    return [TABLE]


def read_table(table):
    rows = [{"id": 1, "label": "alpha"}, {"id": 2, "label": "beta"}]
    yield batch_from_rows(rows, table=TABLE)
"""


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(core_url=CORE_URL)


@pytest.fixture
def ephemeral_flex_catalog(ctx: ToolContext) -> Iterator[str]:
    name = f"ftt_{uuid.uuid4().hex[:8]}"
    out = CreateFlexCatalogTool().execute(ctx, {"name": name, "source": SIMPLE})
    parsed = json.loads(out)
    assert parsed["http_status"] in (201, 502), parsed
    yield name
    requests.delete(f"{CORE_URL}/catalogs/{name}", timeout=10)


# ---- create_flex_catalog ---------------------------------------------------


def test_create_flex_catalog_creates_a_queryable_catalog(ctx, ephemeral_flex_catalog):
    name = ephemeral_flex_catalog
    # SHOW TABLES proves Trino sees the flex catalog's declared table.
    r = requests.post(
        f"{CORE_URL}/raw-trino-query",
        json={"sql": f'SHOW TABLES FROM "{name}"."default"'},
        timeout=15,
    )
    assert r.status_code == 200, r.json()
    rows = {row[0] for row in r.json()["rows"]}
    assert "items" in rows


def test_create_flex_catalog_rejects_invalid_python(ctx):
    with pytest.raises(ToolError, match="invalid Python syntax"):
        CreateFlexCatalogTool().execute(
            ctx, {"name": f"ftt_{uuid.uuid4().hex[:8]}", "source": "def get_tables(:\n"}
        )


# ---- view_flex_module -----------------------------------------------------


def test_view_flex_module_line_number_prefix(ctx, ephemeral_flex_catalog):
    out = ViewFlexModuleTool().execute(
        ctx, {"catalog_name": ephemeral_flex_catalog}
    )
    parsed = json.loads(out)
    assert parsed["catalog_name"] == ephemeral_flex_catalog
    assert parsed["line_count"] >= 1
    # Every non-empty line in the rendered source starts with a
    # padded line number followed by a tab.
    for line in parsed["source"].splitlines():
        assert "\t" in line
        prefix, rest = line.split("\t", 1)
        # prefix is right-justified digits
        assert prefix.strip().isdigit() or prefix.strip() == ""


def test_view_flex_module_range_view(ctx, ephemeral_flex_catalog):
    out = ViewFlexModuleTool().execute(
        ctx,
        {"catalog_name": ephemeral_flex_catalog, "start_line": 1, "end_line": 3},
    )
    parsed = json.loads(out)
    assert parsed["showing"] == {"start_line": 1, "end_line": 3}
    assert len(parsed["source"].splitlines()) == 3


def test_view_flex_module_missing_catalog_raises(ctx):
    with pytest.raises(ToolError, match="no flex module"):
        ViewFlexModuleTool().execute(
            ctx, {"catalog_name": f"nonexistent_{uuid.uuid4().hex[:6]}"}
        )


# ---- replace_in_flex_module -----------------------------------------------


def test_replace_in_flex_module_happy_path(ctx, ephemeral_flex_catalog):
    out = ReplaceInFlexModuleTool().execute(
        ctx,
        {
            "catalog_name": ephemeral_flex_catalog,
            "old_text": '"alpha"',
            "new_text": '"ALPHA"',
        },
    )
    parsed = json.loads(out)
    assert parsed["http_status"] == 200
    # Confirm the source actually changed by reading it back.
    view = json.loads(
        ViewFlexModuleTool().execute(ctx, {"catalog_name": ephemeral_flex_catalog})
    )
    assert "ALPHA" in view["source"]


def test_replace_in_flex_module_rejects_ambiguous(ctx, ephemeral_flex_catalog):
    with pytest.raises(ToolError, match="appears"):
        ReplaceInFlexModuleTool().execute(
            ctx,
            {
                "catalog_name": ephemeral_flex_catalog,
                "old_text": "id",  # appears many times
                "new_text": "ident",
            },
        )


def test_replace_in_flex_module_rejects_missing(ctx, ephemeral_flex_catalog):
    with pytest.raises(ToolError, match="does not appear"):
        ReplaceInFlexModuleTool().execute(
            ctx,
            {
                "catalog_name": ephemeral_flex_catalog,
                "old_text": "this string is not in the source",
                "new_text": "x",
            },
        )


# ---- replace_flex_module_lines --------------------------------------------


def test_replace_flex_module_lines_happy_path(ctx, ephemeral_flex_catalog):
    # Find the rows-literal line so the test doesn't break if the
    # SIMPLE template shifts vertically.
    view = json.loads(
        ViewFlexModuleTool().execute(ctx, {"catalog_name": ephemeral_flex_catalog})
    )
    rows_line = next(
        int(line.split("\t", 1)[0])
        for line in view["source"].splitlines()
        if "rows = [" in line.split("\t", 1)[1]
    )
    out = ReplaceFlexModuleLinesTool().execute(
        ctx,
        {
            "catalog_name": ephemeral_flex_catalog,
            "start_line": rows_line,
            "end_line": rows_line,
            "new_text": '    rows = [{"id": 9, "label": "lone"}]',
        },
    )
    parsed = json.loads(out)
    assert parsed["http_status"] == 200


def test_replace_flex_module_lines_rejects_out_of_range(ctx, ephemeral_flex_catalog):
    with pytest.raises(ToolError, match="exceeds"):
        ReplaceFlexModuleLinesTool().execute(
            ctx,
            {
                "catalog_name": ephemeral_flex_catalog,
                "start_line": 1,
                "end_line": 999_999,
                "new_text": "",
            },
        )


def test_replace_flex_module_lines_rejects_inverted_range(ctx, ephemeral_flex_catalog):
    with pytest.raises(ToolError, match="end_line must be >="):
        ReplaceFlexModuleLinesTool().execute(
            ctx,
            {
                "catalog_name": ephemeral_flex_catalog,
                "start_line": 5,
                "end_line": 2,
                "new_text": "x",
            },
        )


# ---- set_flex_module ------------------------------------------------------


def test_set_flex_module_full_overwrite(ctx, ephemeral_flex_catalog):
    new = SIMPLE.replace("alpha", "wholesale").replace("beta", "rewrite")
    out = SetFlexModuleTool().execute(
        ctx, {"catalog_name": ephemeral_flex_catalog, "source": new}
    )
    parsed = json.loads(out)
    assert parsed["http_status"] == 200


def test_set_flex_module_rejects_invalid_python(ctx, ephemeral_flex_catalog):
    with pytest.raises(ToolError, match="invalid Python syntax"):
        SetFlexModuleTool().execute(
            ctx, {"catalog_name": ephemeral_flex_catalog, "source": "@@@ not python"}
        )


# ---- preview_flex_module --------------------------------------------------


def test_preview_flex_module_returns_sample_rows(ctx):
    out = PreviewFlexModuleTool().execute(ctx, {"source": SIMPLE, "sample_limit": 5})
    parsed = json.loads(out)
    assert parsed["http_status"] == 200
    tables = parsed["response"]["tables"]
    assert len(tables) == 1
    assert tables[0]["name"] == "items"
    assert tables[0]["sample_rows"] == [[1, "alpha"], [2, "beta"]]


def test_preview_flex_module_rejects_invalid_python(ctx):
    with pytest.raises(ToolError, match="invalid Python syntax"):
        PreviewFlexModuleTool().execute(ctx, {"source": "@@@"})


# ---- registration ---------------------------------------------------------


def test_all_flex_tools_registered():
    from datapro_ai.llm.agent import default_tools

    names = set(default_tools().names())
    for n in (
        "create_flex_catalog",
        "view_flex_module",
        "replace_in_flex_module",
        "replace_flex_module_lines",
        "set_flex_module",
        "preview_flex_module",
    ):
        assert n in names, f"{n} not in default tool set"
