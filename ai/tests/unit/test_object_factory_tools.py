"""Object-factory AI tools, against live Core.

The mutation surface is intentionally split into single-purpose tools rather
than one omnibus update — set_description, set_use_all_columns, add/remove/
update_column — so each test targets one action.
"""

import json
import os
import uuid
from collections.abc import Iterator

import pytest
import requests

from datapro_ai.llm.tools.add_object_factory_column import AddObjectFactoryColumnTool
from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.create_object_factory import CreateObjectFactoryTool
from datapro_ai.llm.tools.create_object_type import CreateObjectTypeTool
from datapro_ai.llm.tools.delete_object_factory import DeleteObjectFactoryTool
from datapro_ai.llm.tools.get_object_factory import GetObjectFactoryTool
from datapro_ai.llm.tools.list_object_factories import ListObjectFactoriesTool
from datapro_ai.llm.tools.remove_object_factory_column import (
    RemoveObjectFactoryColumnTool,
)
from datapro_ai.llm.tools.set_object_factory_description import (
    SetObjectFactoryDescriptionTool,
)
from datapro_ai.llm.tools.set_object_factory_use_all_columns import (
    SetObjectFactoryUseAllColumnsTool,
)
from datapro_ai.llm.tools.update_object_factory_column import (
    UpdateObjectFactoryColumnTool,
)


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
    name = f"factest_{uuid.uuid4().hex[:8]}"
    r = requests.post(
        f"{CORE_URL}/catalogs", json={"name": name, "connector": "tpch"}, timeout=10
    )
    assert r.status_code == 201, r.text
    yield name
    requests.delete(f"{CORE_URL}/catalogs/{name}", timeout=10)


@pytest.fixture
def ephemeral_data_source(ephemeral_catalog) -> Iterator[str]:
    # Data sources are sync-discovered on catalog creation — look up tiny.nation
    # rather than creating it. No cleanup: it cascades away with the catalog.
    rows = requests.get(
        f"{CORE_URL}/data-sources?catalog={ephemeral_catalog}", timeout=10
    ).json()
    match = next(
        r for r in rows if r["schema_name"] == "tiny" and r["table_name"] == "nation"
    )
    yield match["id"]


@pytest.fixture
def ephemeral_type(ctx) -> Iterator[dict]:
    name = f"FacTypeTest_{uuid.uuid4().hex[:8]}"
    out = CreateObjectTypeTool().execute(ctx, {"name": name})
    row = json.loads(out)["response"]
    yield row
    requests.delete(f"{CORE_URL}/object-types/{row['id']}", timeout=5)


@pytest.fixture
def ephemeral_factory(ctx, ephemeral_data_source, ephemeral_type) -> Iterator[dict]:
    out = CreateObjectFactoryTool().execute(
        ctx,
        {
            "data_source_id": ephemeral_data_source,
            "object_type_id": ephemeral_type["id"],
        },
    )
    row = json.loads(out)["response"]
    yield row
    requests.delete(f"{CORE_URL}/object-factories/{row['id']}", timeout=5)


# ---- list / get / create / delete still here for round-trip coverage -----


def test_list_filter_by_catalog(ctx, ephemeral_factory):
    out = ListObjectFactoriesTool().execute(
        ctx, {"catalog": ephemeral_factory["catalog_name"]}
    )
    assert [r["id"] for r in json.loads(out)] == [ephemeral_factory["id"]]


def test_get_returns_the_row(ctx, ephemeral_factory):
    out = GetObjectFactoryTool().execute(ctx, {"id": ephemeral_factory["id"]})
    assert json.loads(out)["id"] == ephemeral_factory["id"]


def test_get_raises_on_missing(ctx):
    with pytest.raises(ToolError, match="no object factory"):
        GetObjectFactoryTool().execute(ctx, {"id": str(uuid.uuid4())})


def test_create_with_specific_columns(ctx, ephemeral_data_source, ephemeral_type):
    # Data source is tpch.tiny.nation — pick columns it actually has.
    out = CreateObjectFactoryTool().execute(
        ctx,
        {
            "data_source_id": ephemeral_data_source,
            "object_type_id": ephemeral_type["id"],
            "use_all_columns": False,
            "column_spec": ["nationkey", "name", "regionkey"],
        },
    )
    resp = json.loads(out)["response"]
    assert resp["use_all_columns"] is False
    assert resp["column_spec"] == ["nationkey", "name", "regionkey"]


def test_create_rejects_invalid_columns(ctx, ephemeral_data_source, ephemeral_type):
    """Server-side validation: column_spec entries must exist on the
    data source's underlying Trino table."""
    out = CreateObjectFactoryTool().execute(
        ctx,
        {
            "data_source_id": ephemeral_data_source,
            "object_type_id": ephemeral_type["id"],
            "use_all_columns": False,
            "column_spec": ["nationkey", "not_a_real_column"],
        },
    )
    parsed = json.loads(out)
    assert parsed["http_status"] == 400
    assert parsed["response"]["error"] == "invalid_columns"
    assert "not_a_real_column" in parsed["response"]["invalid"]


def test_delete_removes_the_row(ctx, ephemeral_data_source, ephemeral_type):
    created = json.loads(
        CreateObjectFactoryTool().execute(
            ctx,
            {
                "data_source_id": ephemeral_data_source,
                "object_type_id": ephemeral_type["id"],
            },
        )
    )["response"]
    out = DeleteObjectFactoryTool().execute(ctx, {"id": created["id"]})
    assert json.loads(out)["deleted"] == created["id"]
    with pytest.raises(ToolError, match="no object factory"):
        GetObjectFactoryTool().execute(ctx, {"id": created["id"]})


# ---- set_description ----------------------------------------------------


def test_set_description_writes_just_description(ctx, ephemeral_factory):
    out = SetObjectFactoryDescriptionTool().execute(
        ctx, {"id": ephemeral_factory["id"], "description": "edited"}
    )
    resp = json.loads(out)["response"]
    assert resp["description"] == "edited"
    # Untouched.
    assert resp["use_all_columns"] is True
    assert resp["column_spec"] == []


def test_set_description_accepts_empty_string(ctx, ephemeral_factory):
    out = SetObjectFactoryDescriptionTool().execute(
        ctx, {"id": ephemeral_factory["id"], "description": ""}
    )
    assert json.loads(out)["response"]["description"] == ""


def test_set_description_rejects_missing_args(ctx, ephemeral_factory):
    with pytest.raises(ToolError, match="description"):
        SetObjectFactoryDescriptionTool().execute(
            ctx, {"id": ephemeral_factory["id"]}
        )


def test_set_description_raises_on_missing_factory(ctx):
    with pytest.raises(ToolError, match="no object factory"):
        SetObjectFactoryDescriptionTool().execute(
            ctx, {"id": str(uuid.uuid4()), "description": "x"}
        )


# ---- set_use_all_columns ------------------------------------------------


def test_set_use_all_columns_toggles_off_and_back(ctx, ephemeral_factory):
    off = SetObjectFactoryUseAllColumnsTool().execute(
        ctx, {"id": ephemeral_factory["id"], "use_all_columns": False}
    )
    assert json.loads(off)["response"]["use_all_columns"] is False

    on = SetObjectFactoryUseAllColumnsTool().execute(
        ctx, {"id": ephemeral_factory["id"], "use_all_columns": True}
    )
    assert json.loads(on)["response"]["use_all_columns"] is True


def test_set_use_all_columns_rejects_non_boolean(ctx, ephemeral_factory):
    with pytest.raises(ToolError, match="boolean"):
        SetObjectFactoryUseAllColumnsTool().execute(
            ctx,
            {"id": ephemeral_factory["id"], "use_all_columns": "false"},  # type: ignore[dict-item]
        )


# ---- add_column ----------------------------------------------------------


def test_add_column_appends_to_end_by_default(ctx, ephemeral_factory):
    # ephemeral_factory's source is tpch.tiny.nation.
    # Flip to use_all_columns=false so column_spec is meaningful.
    SetObjectFactoryUseAllColumnsTool().execute(
        ctx, {"id": ephemeral_factory["id"], "use_all_columns": False}
    )
    AddObjectFactoryColumnTool().execute(
        ctx, {"id": ephemeral_factory["id"], "column": "nationkey"}
    )
    out = AddObjectFactoryColumnTool().execute(
        ctx, {"id": ephemeral_factory["id"], "column": "name"}
    )
    assert json.loads(out)["response"]["column_spec"] == ["nationkey", "name"]


def test_add_column_inserts_at_given_position(ctx, ephemeral_factory):
    SetObjectFactoryUseAllColumnsTool().execute(
        ctx, {"id": ephemeral_factory["id"], "use_all_columns": False}
    )
    for c in ("nationkey", "regionkey"):
        AddObjectFactoryColumnTool().execute(
            ctx, {"id": ephemeral_factory["id"], "column": c}
        )
    out = AddObjectFactoryColumnTool().execute(
        ctx, {"id": ephemeral_factory["id"], "column": "name", "position": 1}
    )
    assert json.loads(out)["response"]["column_spec"] == [
        "nationkey",
        "name",
        "regionkey",
    ]


def test_add_column_rejects_out_of_range_position(ctx, ephemeral_factory):
    SetObjectFactoryUseAllColumnsTool().execute(
        ctx, {"id": ephemeral_factory["id"], "use_all_columns": False}
    )
    with pytest.raises(ToolError, match="out of range"):
        AddObjectFactoryColumnTool().execute(
            ctx,
            {"id": ephemeral_factory["id"], "column": "name", "position": 99},
        )


def test_add_column_rejects_empty_string(ctx, ephemeral_factory):
    with pytest.raises(ToolError, match="column"):
        AddObjectFactoryColumnTool().execute(
            ctx, {"id": ephemeral_factory["id"], "column": ""}
        )


def test_add_column_rejects_invalid_column(ctx, ephemeral_factory):
    """Server validates: adding a column that doesn't exist on the data
    source's table fails the resulting PATCH with invalid_columns."""
    SetObjectFactoryUseAllColumnsTool().execute(
        ctx, {"id": ephemeral_factory["id"], "use_all_columns": False}
    )
    with pytest.raises(ToolError, match="invalid_columns|HTTP"):
        AddObjectFactoryColumnTool().execute(
            ctx, {"id": ephemeral_factory["id"], "column": "not_a_real_column"}
        )


# ---- remove_column -------------------------------------------------------


def test_remove_column_drops_one_entry(ctx, ephemeral_factory):
    SetObjectFactoryUseAllColumnsTool().execute(
        ctx, {"id": ephemeral_factory["id"], "use_all_columns": False}
    )
    for c in ("nationkey", "name", "regionkey"):
        AddObjectFactoryColumnTool().execute(
            ctx, {"id": ephemeral_factory["id"], "column": c}
        )
    out = RemoveObjectFactoryColumnTool().execute(
        ctx, {"id": ephemeral_factory["id"], "index": 1}
    )
    parsed = json.loads(out)
    assert parsed["removed"] == "name"
    assert parsed["response"]["column_spec"] == ["nationkey", "regionkey"]


def test_remove_column_rejects_out_of_range_index(ctx, ephemeral_factory):
    with pytest.raises(ToolError, match="out of range"):
        RemoveObjectFactoryColumnTool().execute(
            ctx, {"id": ephemeral_factory["id"], "index": 0}
        )


# ---- update_column -------------------------------------------------------


def test_update_column_replaces_in_place(ctx, ephemeral_factory):
    SetObjectFactoryUseAllColumnsTool().execute(
        ctx, {"id": ephemeral_factory["id"], "use_all_columns": False}
    )
    for c in ("nationkey", "name", "regionkey"):
        AddObjectFactoryColumnTool().execute(
            ctx, {"id": ephemeral_factory["id"], "column": c}
        )
    out = UpdateObjectFactoryColumnTool().execute(
        ctx,
        {"id": ephemeral_factory["id"], "index": 1, "column": "comment"},
    )
    parsed = json.loads(out)
    assert parsed["replaced"] == {"old": "name", "new": "comment"}
    assert parsed["response"]["column_spec"] == ["nationkey", "comment", "regionkey"]


def test_update_column_rejects_out_of_range_index(ctx, ephemeral_factory):
    with pytest.raises(ToolError, match="out of range"):
        UpdateObjectFactoryColumnTool().execute(
            ctx, {"id": ephemeral_factory["id"], "index": 5, "column": "name"}
        )


# ---- registration --------------------------------------------------------


def test_split_mutation_tools_registered():
    from datapro_ai.llm.agent import default_tools

    names = set(default_tools().names())
    for n in (
        "set_object_factory_description",
        "set_object_factory_use_all_columns",
        "add_object_factory_column",
        "remove_object_factory_column",
        "update_object_factory_column",
    ):
        assert n in names, f"{n} not in default tool set"


def test_omnibus_update_tool_no_longer_registered():
    """update_object_factory was the bundled-fields variant; it's been
    replaced by the focused tools above."""
    from datapro_ai.llm.agent import default_tools

    assert "update_object_factory" not in set(default_tools().names())
