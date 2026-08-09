"""list / get / create / update / delete object type tools, against live Core."""

import json
import os
import uuid
from collections.abc import Iterator

import pytest
import requests

from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.create_object_type import CreateObjectTypeTool
from datapro_ai.llm.tools.delete_object_type import DeleteObjectTypeTool
from datapro_ai.llm.tools.get_object_type import GetObjectTypeTool
from datapro_ai.llm.tools.list_object_types import ListObjectTypesTool
from datapro_ai.llm.tools.update_object_type import UpdateObjectTypeTool


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
def ephemeral_type(ctx: ToolContext) -> Iterator[dict]:
    """Create a unique object type via the live tool, yield its dict, then
    delete it (best-effort) at teardown."""
    name = f"ToolTest_{uuid.uuid4().hex[:8]}"
    out = CreateObjectTypeTool().execute(ctx, {"name": name})
    row = json.loads(out)["response"]
    yield row
    requests.delete(f"{CORE_URL}/object-types/{row['id']}", timeout=5)


# ---- list ----------------------------------------------------------------


def test_list_returns_array(ctx, ephemeral_type):
    out = ListObjectTypesTool().execute(ctx, {})
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert any(r["id"] == ephemeral_type["id"] for r in parsed)


def test_list_search_filters(ctx, ephemeral_type):
    out = ListObjectTypesTool().execute(ctx, {"search": ephemeral_type["name"]})
    parsed = json.loads(out)
    assert [r["id"] for r in parsed] == [ephemeral_type["id"]]


# ---- get -----------------------------------------------------------------


def test_get_returns_the_row(ctx, ephemeral_type):
    out = GetObjectTypeTool().execute(ctx, {"id": ephemeral_type["id"]})
    parsed = json.loads(out)
    assert parsed["name"] == ephemeral_type["name"]


def test_get_rejects_invalid_uuid(ctx):
    with pytest.raises(ToolError, match="UUID"):
        GetObjectTypeTool().execute(ctx, {"id": "not-a-uuid"})


def test_get_raises_on_missing(ctx):
    with pytest.raises(ToolError, match="no object type"):
        GetObjectTypeTool().execute(ctx, {"id": str(uuid.uuid4())})


# ---- create --------------------------------------------------------------


def test_create_returns_201_with_uuid(ctx):
    name = f"Created_{uuid.uuid4().hex[:8]}"
    try:
        out = CreateObjectTypeTool().execute(
            ctx, {"name": name, "description": "hello"}
        )
        parsed = json.loads(out)
        assert parsed["http_status"] == 201
        assert parsed["response"]["name"] == name
        assert parsed["response"]["description"] == "hello"
        uuid.UUID(parsed["response"]["id"])  # parses
    finally:
        requests.delete(f"{CORE_URL}/object-types/{parsed['response']['id']}", timeout=5)


def test_create_rejects_empty_name(ctx):
    with pytest.raises(ToolError, match="name"):
        CreateObjectTypeTool().execute(ctx, {"name": ""})


def test_create_409_on_duplicate(ctx, ephemeral_type):
    out = CreateObjectTypeTool().execute(ctx, {"name": ephemeral_type["name"]})
    parsed = json.loads(out)
    assert parsed["http_status"] == 409
    assert parsed["response"]["error"] == "already_exists"


# ---- update --------------------------------------------------------------


def test_update_renames(ctx, ephemeral_type):
    new_name = f"Renamed_{uuid.uuid4().hex[:8]}"
    out = UpdateObjectTypeTool().execute(
        ctx, {"id": ephemeral_type["id"], "name": new_name}
    )
    parsed = json.loads(out)
    assert parsed["http_status"] == 200
    assert parsed["response"]["name"] == new_name
    assert parsed["response"]["id"] == ephemeral_type["id"]


def test_update_changes_description(ctx, ephemeral_type):
    out = UpdateObjectTypeTool().execute(
        ctx, {"id": ephemeral_type["id"], "description": "new desc"}
    )
    assert json.loads(out)["response"]["description"] == "new desc"


def test_update_rejects_empty_patch(ctx, ephemeral_type):
    with pytest.raises(ToolError, match="at least one"):
        UpdateObjectTypeTool().execute(ctx, {"id": ephemeral_type["id"]})


def test_update_raises_on_missing(ctx):
    with pytest.raises(ToolError, match="no object type"):
        UpdateObjectTypeTool().execute(
            ctx, {"id": str(uuid.uuid4()), "description": "x"}
        )


# ---- delete --------------------------------------------------------------


def test_delete_removes_the_row(ctx):
    name = f"ToDelete_{uuid.uuid4().hex[:8]}"
    created = json.loads(CreateObjectTypeTool().execute(ctx, {"name": name}))["response"]
    out = DeleteObjectTypeTool().execute(ctx, {"id": created["id"]})
    assert json.loads(out)["deleted"] == created["id"]
    # And now it's gone.
    with pytest.raises(ToolError, match="no object type"):
        GetObjectTypeTool().execute(ctx, {"id": created["id"]})


def test_delete_raises_on_missing(ctx):
    with pytest.raises(ToolError, match="no object type"):
        DeleteObjectTypeTool().execute(ctx, {"id": str(uuid.uuid4())})


# ---- registration --------------------------------------------------------


def test_all_tools_registered():
    from datapro_ai.llm.agent import default_tools

    names = set(default_tools().names())
    for n in (
        "list_object_types",
        "get_object_type",
        "create_object_type",
        "update_object_type",
        "delete_object_type",
    ):
        assert n in names, f"{n} not in default tool set"
