"""list_traits / add_trait_to_object_type / remove_trait_from_object_type /
set_factory_trait_config tools, against live Core."""

import json
import os
import uuid
from collections.abc import Iterator

import pytest
import requests

from datapro_ai.llm.tools.add_trait_to_object_type import AddTraitToObjectTypeTool
from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.create_object_type import CreateObjectTypeTool
from datapro_ai.llm.tools.list_traits import ListTraitsTool
from datapro_ai.llm.tools.remove_trait_from_object_type import (
    RemoveTraitFromObjectTypeTool,
)
from datapro_ai.llm.tools.set_factory_trait_config import SetFactoryTraitConfigTool


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
    name = f"TraitToolTest_{uuid.uuid4().hex[:8]}"
    out = CreateObjectTypeTool().execute(ctx, {"name": name})
    row = json.loads(out)["response"]
    yield row
    requests.delete(f"{CORE_URL}/object-types/{row['id']}", timeout=5)


# ---- list_traits ----


def test_list_traits_includes_identity_and_temporal(ctx):
    out = ListTraitsTool().execute(ctx, {})
    parsed = json.loads(out)["response"]
    names = [t["name"] for t in parsed]
    assert "identity" in names
    assert "temporal" in names


# ---- add_trait_to_object_type ----


def test_add_trait_attaches(ctx, ephemeral_type):
    out = AddTraitToObjectTypeTool().execute(
        ctx, {"id": ephemeral_type["id"], "trait_name": "identity"}
    )
    parsed = json.loads(out)
    assert parsed["http_status"] == 200
    assert parsed["response"]["traits"] == ["identity"]


def test_add_trait_idempotent(ctx, ephemeral_type):
    AddTraitToObjectTypeTool().execute(
        ctx, {"id": ephemeral_type["id"], "trait_name": "identity"}
    )
    # Same call again — no error, still just the one.
    out = AddTraitToObjectTypeTool().execute(
        ctx, {"id": ephemeral_type["id"], "trait_name": "identity"}
    )
    assert json.loads(out)["response"]["traits"] == ["identity"]


def test_add_unknown_trait_raises(ctx, ephemeral_type):
    with pytest.raises(ToolError, match="unknown trait"):
        AddTraitToObjectTypeTool().execute(
            ctx, {"id": ephemeral_type["id"], "trait_name": "bogus"}
        )


def test_add_invalid_uuid_raises(ctx):
    with pytest.raises(ToolError, match="UUID"):
        AddTraitToObjectTypeTool().execute(
            ctx, {"id": "not-a-uuid", "trait_name": "identity"}
        )


def test_add_missing_type_raises(ctx):
    with pytest.raises(ToolError, match="no object type"):
        AddTraitToObjectTypeTool().execute(
            ctx, {"id": str(uuid.uuid4()), "trait_name": "identity"}
        )


# ---- remove_trait_from_object_type ----


def test_remove_trait_detaches(ctx, ephemeral_type):
    AddTraitToObjectTypeTool().execute(
        ctx, {"id": ephemeral_type["id"], "trait_name": "identity"}
    )
    out = RemoveTraitFromObjectTypeTool().execute(
        ctx, {"id": ephemeral_type["id"], "trait_name": "identity"}
    )
    assert json.loads(out)["response"]["traits"] == []


def test_remove_absent_trait_is_idempotent(ctx, ephemeral_type):
    out = RemoveTraitFromObjectTypeTool().execute(
        ctx, {"id": ephemeral_type["id"], "trait_name": "identity"}
    )
    assert json.loads(out)["response"]["traits"] == []


# ---- set_factory_trait_config ----


def test_set_factory_trait_config_invalid_uuid(ctx):
    with pytest.raises(ToolError, match="UUID"):
        SetFactoryTraitConfigTool().execute(
            ctx, {"id": "not-a-uuid", "trait_config": {}}
        )


def test_set_factory_trait_config_requires_object(ctx):
    with pytest.raises(ToolError, match="object"):
        SetFactoryTraitConfigTool().execute(
            ctx, {"id": str(uuid.uuid4()), "trait_config": "not an object"}
        )


def test_set_factory_trait_config_missing_factory(ctx):
    with pytest.raises(ToolError, match="no object factory"):
        SetFactoryTraitConfigTool().execute(
            ctx, {"id": str(uuid.uuid4()), "trait_config": {}}
        )


# ---- registration ----


def test_all_trait_tools_registered():
    from datapro_ai.llm.agent import default_tools

    names = set(default_tools().names())
    for n in (
        "list_traits",
        "add_trait_to_object_type",
        "remove_trait_from_object_type",
        "set_factory_trait_config",
    ):
        assert n in names, f"{n} not in default tool set"
