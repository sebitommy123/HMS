"""get_catalog + inspect_table against the live Core service."""

import json
import os
import uuid

import pytest
import requests

from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.get_catalog import GetCatalogTool
from datapro_ai.llm.tools.inspect_table import InspectTableTool


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


@pytest.fixture
def ephemeral_tpch_catalog():
    """Register a unique tpch catalog for the test, yield its name, then drop it."""
    name = f"tool_test_{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{CORE_URL}/catalogs",
        json={"name": name, "connector": "tpch"},
        timeout=10,
    )
    assert r.status_code == 201, r.json()
    yield name
    requests.delete(f"{CORE_URL}/catalogs/{name}", timeout=10)


def test_get_catalog_returns_details(ctx, ephemeral_tpch_catalog):
    tool = GetCatalogTool()
    result = tool.execute(ctx, {"name": ephemeral_tpch_catalog})
    parsed = json.loads(result)
    assert parsed["name"] == ephemeral_tpch_catalog
    assert parsed["connector"] == "tpch"
    assert parsed["status"] == "enabled"


def test_get_catalog_404_becomes_tool_error(ctx):
    tool = GetCatalogTool()
    with pytest.raises(ToolError, match="no catalog"):
        tool.execute(ctx, {"name": "definitely_not_registered_xyz123"})


def test_get_catalog_masks_secret_properties(ctx):
    """If we register a catalog with a credential-shaped property, the tool
    output should mask it rather than echo it back to the model."""
    name = f"masked_test_{uuid.uuid4().hex[:8]}"
    # postgresql will fail validation eagerly (broken catalog) but the row is
    # persisted with the properties — perfect for testing the masking path.
    r = requests.post(
        f"{CORE_URL}/catalogs",
        json={
            "name": name,
            "connector": "postgresql",
            "properties": {
                "connection-url": "jdbc:postgresql://nowhere.invalid:5432/x",
                "connection-user": "alice",
                "connection-password": "supersecret",
            },
        },
        timeout=10,
    )
    # 502 because broken — but the row is persisted, so get_catalog still works.
    assert r.status_code in (201, 502), r.text
    try:
        tool = GetCatalogTool()
        result = tool.execute(ctx, {"name": name})
        parsed = json.loads(result)
        assert "supersecret" not in result
        assert parsed["properties"]["connection-password"] == "••••••••"
        assert parsed["properties"]["connection-user"] == "alice"
    finally:
        requests.delete(f"{CORE_URL}/catalogs/{name}", timeout=10)


def test_inspect_table_returns_columns_and_rows(ctx, ephemeral_tpch_catalog):
    tool = InspectTableTool()
    result = tool.execute(
        ctx,
        {
            "catalog": ephemeral_tpch_catalog,
            "schema": "tiny",
            "table": "nation",
            "sample_rows": 3,
        },
    )
    parsed = json.loads(result)
    assert parsed["fully_qualified_name"] == f'"{ephemeral_tpch_catalog}"."tiny"."nation"'
    col_names = [c["name"] for c in parsed["columns"]]
    assert "nationkey" in col_names
    assert "name" in col_names
    assert len(parsed["sample_rows"]) == 3


def test_inspect_table_rejects_quoted_identifiers(ctx):
    tool = InspectTableTool()
    with pytest.raises(ToolError, match="quote"):
        tool.execute(
            ctx,
            {"catalog": 'evil"', "schema": "tiny", "table": "nation"},
        )


def test_inspect_table_rejects_missing_fields(ctx):
    tool = InspectTableTool()
    with pytest.raises(ToolError, match="catalog"):
        tool.execute(ctx, {"schema": "tiny", "table": "nation"})


def test_inspect_table_raises_when_table_does_not_exist(ctx, ephemeral_tpch_catalog):
    tool = InspectTableTool()
    with pytest.raises(ToolError):
        tool.execute(
            ctx,
            {
                "catalog": ephemeral_tpch_catalog,
                "schema": "tiny",
                "table": "no_such_table_xyz",
            },
        )
