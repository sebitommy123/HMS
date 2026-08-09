"""create_catalog + update_catalog against the live Core service."""

import json
import os
import uuid
from collections.abc import Iterator

import pytest
import requests

from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.create_catalog import CreateCatalogTool
from datapro_ai.llm.tools.update_catalog import UpdateCatalogTool


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
def ephemeral_name() -> Iterator[str]:
    """Yield a unique catalog name; drop the catalog (best-effort) at teardown."""
    name = f"tool_mut_{uuid.uuid4().hex[:8]}"
    yield name
    requests.delete(f"{CORE_URL}/catalogs/{name}", timeout=10)


# ---- create_catalog -------------------------------------------------------


def test_create_catalog_happy_path(ctx, ephemeral_name):
    result = CreateCatalogTool().execute(
        ctx, {"name": ephemeral_name, "connector": "tpch"}
    )
    parsed = json.loads(result)
    assert parsed["http_status"] == 201
    assert parsed["response"]["catalog"]["name"] == ephemeral_name
    assert parsed["response"]["catalog"]["status"] == "enabled"
    # Verify Core actually persisted it.
    r = requests.get(f"{CORE_URL}/catalogs/{ephemeral_name}", timeout=5)
    assert r.status_code == 200


def test_create_catalog_broken_returns_502_with_error(ctx, ephemeral_name):
    """A connector Trino doesn't have should yield broken status + error in body."""
    result = CreateCatalogTool().execute(
        ctx, {"name": ephemeral_name, "connector": "no_such_connector_xyz"}
    )
    parsed = json.loads(result)
    assert parsed["http_status"] == 502
    assert parsed["response"]["catalog"]["status"] == "broken"
    assert parsed["response"]["catalog"]["last_error"]


def test_create_catalog_rejects_missing_name(ctx):
    with pytest.raises(ToolError, match="name"):
        CreateCatalogTool().execute(ctx, {"connector": "tpch"})


def test_create_catalog_rejects_missing_connector(ctx):
    with pytest.raises(ToolError, match="connector"):
        CreateCatalogTool().execute(ctx, {"name": "x"})


def test_create_catalog_rejects_non_string_property_values(ctx):
    with pytest.raises(ToolError, match="string"):
        CreateCatalogTool().execute(
            ctx,
            {
                "name": "x",
                "connector": "tpch",
                "properties": {"k": 123},  # type: ignore[dict-item]
            },
        )


def test_create_catalog_409_on_duplicate(ctx, ephemeral_name):
    # First creation succeeds.
    CreateCatalogTool().execute(
        ctx, {"name": ephemeral_name, "connector": "tpch"}
    )
    # Second is a duplicate; Core returns 409. Tool surfaces the body verbatim.
    result = CreateCatalogTool().execute(
        ctx, {"name": ephemeral_name, "connector": "tpch"}
    )
    parsed = json.loads(result)
    assert parsed["http_status"] == 409
    assert parsed["response"]["error"] == "already_exists"


# ---- update_catalog -------------------------------------------------------


def test_update_catalog_replaces_connector(ctx, ephemeral_name):
    CreateCatalogTool().execute(
        ctx, {"name": ephemeral_name, "connector": "tpch"}
    )
    result = UpdateCatalogTool().execute(
        ctx, {"name": ephemeral_name, "connector": "memory"}
    )
    parsed = json.loads(result)
    assert parsed["http_status"] == 200, parsed
    assert parsed["response"]["catalog"]["connector"] == "memory"


def test_update_catalog_replaces_properties(ctx, ephemeral_name):
    # Start with a broken postgresql catalog so a properties change is observable.
    CreateCatalogTool().execute(
        ctx,
        {
            "name": ephemeral_name,
            "connector": "postgresql",
            "properties": {
                "connection-url": "jdbc:postgresql://nowhere-a.invalid:5432/x",
                "connection-user": "a",
                "connection-password": "a",
            },
        },
    )
    result = UpdateCatalogTool().execute(
        ctx,
        {
            "name": ephemeral_name,
            "properties": {
                "connection-url": "jdbc:postgresql://nowhere-b.invalid:5432/x",
                "connection-user": "b",
                "connection-password": "b",
            },
        },
    )
    parsed = json.loads(result)
    # Still broken (still a fake URL), but the new properties are persisted.
    assert parsed["response"]["catalog"]["properties"]["connection-user"] == "b"


def test_update_catalog_404_on_missing(ctx):
    with pytest.raises(ToolError, match="no catalog"):
        UpdateCatalogTool().execute(
            ctx, {"name": "definitely_does_not_exist_xyz", "connector": "tpch"}
        )


def test_update_catalog_rejects_empty_patch(ctx, ephemeral_name):
    CreateCatalogTool().execute(
        ctx, {"name": ephemeral_name, "connector": "tpch"}
    )
    with pytest.raises(ToolError, match="at least one"):
        UpdateCatalogTool().execute(ctx, {"name": ephemeral_name})


def test_update_catalog_rejects_non_string_property_values(ctx, ephemeral_name):
    CreateCatalogTool().execute(
        ctx, {"name": ephemeral_name, "connector": "tpch"}
    )
    with pytest.raises(ToolError, match="string"):
        UpdateCatalogTool().execute(
            ctx,
            {"name": ephemeral_name, "properties": {"k": 1}},  # type: ignore[dict-item]
        )


# ---- registration --------------------------------------------------------


def test_tools_registered_in_default_set():
    from datapro_ai.llm.agent import default_tools

    names = set(default_tools().names())
    assert "create_catalog" in names
    assert "update_catalog" in names
