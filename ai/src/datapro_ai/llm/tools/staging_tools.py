"""The agent's staging surface — its ONLY write path.

The agent never mutates Core/prod. It appends **actions** to the chat's
changeset, builds an ephemeral staging env from them to test (each query
re-materializes off current prod — the agent has no scratch because it can only
speak actions), and finally applies (promotes) atomically.

Every create-action declares a symbolic ``handle``; later actions reference it
(``target_handle``) or a prod entity (``target_prod_id``). Data sources are named
structurally by catalog + schema + table.
"""

from __future__ import annotations

import json
from typing import Any

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError
from datapro_ai.staging.changeset_store import ChangesetError
from datapro_ai.staging.core_client import CoreError, HttpCoreClient
from datapro_ai.staging.resolver import ResolverError


def _store(ctx: ToolContext):
    if ctx.changeset_store is None:
        raise ToolError("no changeset for this context (staging tools need a chat)")
    return ctx.changeset_store


def _append(ctx: ToolContext, action: dict, input: dict) -> str:
    # Carry the agent's human-readable note onto the action (shown to the user
    # when they expand the action in the UI).
    action["note"] = str(input.get("note", "") or "")
    try:
        stored = _store(ctx).append(action)
    except ChangesetError as exc:
        raise ToolError(str(exc)) from exc
    return json.dumps({"appended": stored, "changeset_length": len(_store(ctx).raw())})


def _target(input: dict, kind: str) -> dict:
    """Build a Ref from tool input: an action handle or a prod id."""
    h = input.get("target_handle")
    pid = input.get("target_prod_id")
    if h:
        return {"source": "action", "handle": h}
    if pid:
        return {"source": "prod", "kind": kind, "id": pid}
    raise ToolError("provide target_handle (env-created) or target_prod_id (production)")


_TARGET_SCHEMA = {
    "target_handle": {"type": "string", "description": "Handle of an entity created earlier in this changeset."},
    "target_prod_id": {"type": "string", "description": "Id of an existing production entity to change."},
}

# Every action-authoring tool includes this so the agent writes a one-sentence,
# user-facing description of what the action does and why. Shown on expand.
_NOTE_SCHEMA = {
    "note": {
        "type": "string",
        "description": (
            "A short, human-readable sentence describing what this action does and why, "
            "written for the user to read when they expand the action. Always provide it."
        ),
    },
}


class _WithNote:
    """Wraps an action-authoring tool to advertise the shared ``note`` param in
    its input schema (the value is carried onto the action by ``_append``). Keeps
    the per-tool definitions focused on their real inputs while guaranteeing every
    staging action can carry a human-readable note."""

    def __init__(self, tool):
        self._tool = tool
        self.name = tool.name

    def definition(self):
        d = self._tool.definition()
        props = d.setdefault("input_schema", {}).setdefault("properties", {})
        props.setdefault("note", _NOTE_SCHEMA["note"])
        return d

    def execute(self, ctx, input):
        return self._tool.execute(ctx, input)


# --------------------------------------------------------------------------- #
# Action-authoring tools
# --------------------------------------------------------------------------- #


class StageAddCatalogTool:
    name = "stage_add_catalog"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Append an action that creates a catalog in the staging changeset. "
                "For a flex catalog set connector='flex' and pass `source` (Python). "
                "Declare a `handle` so later actions (factories) can reference it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "handle": {"type": "string", "description": "Symbolic name for this catalog within the changeset."},
                    "name": {"type": "string", "description": "Logical catalog name the user will see."},
                    "connector": {"type": "string"},
                    "properties": {"type": "object", "description": "Trino catalog properties (e.g. connection-url/user/password)."},
                    "source": {"type": "string", "description": "Flex module Python source (connector='flex' only)."},
                },
                "required": ["handle", "name", "connector"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        action = {
            "op": "create_catalog",
            "handle": input["handle"],
            "name": input["name"],
            "connector": input["connector"],
            "properties": input.get("properties") or {},
        }
        if input.get("source") is not None:
            action["source"] = input["source"]
        return _append(ctx, action, input)


class StageAddObjectTypeTool:
    name = "stage_add_object_type"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Append an action creating an object type. Declare a `handle` for later reference.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "handle": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["handle", "name"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        return _append(ctx, {
            "op": "create_object_type",
            "handle": input["handle"],
            "name": input["name"],
            "description": input.get("description", ""),
        }, input)


class StageAddTraitTool:
    name = "stage_add_trait"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Append an action attaching a trait (e.g. 'identity', 'temporal') to an object type.",
            "input_schema": {
                "type": "object",
                "properties": {**_TARGET_SCHEMA, "trait": {"type": "string"}},
                "required": ["trait"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        return _append(ctx, {
            "op": "add_trait",
            "target": _target(input, "object_type"),
            "trait": input["trait"],
        }, input)


class StageRemoveTraitTool:
    name = "stage_remove_trait"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Append an action removing a trait from an object type.",
            "input_schema": {
                "type": "object",
                "properties": {**_TARGET_SCHEMA, "trait": {"type": "string"}},
                "required": ["trait"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        return _append(ctx, {
            "op": "remove_trait",
            "target": _target(input, "object_type"),
            "trait": input["trait"],
        }, input)


class StageAddObjectFactoryTool:
    name = "stage_add_object_factory"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Append an action creating an object factory — 'this data source produces objects of "
                "this type'. The data source is named by catalog + schema + table; the catalog is either "
                "a handle from an earlier stage_add_catalog (`catalog_handle`) or a production catalog "
                "name (`catalog_name`). trait_config carries per-trait setup, e.g. {'identity': {'column': 'id'}}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "handle": {"type": "string"},
                    "catalog_handle": {"type": "string", "description": "Handle of a stage_add_catalog action."},
                    "catalog_name": {"type": "string", "description": "Logical name of a production catalog."},
                    "schema_name": {"type": "string"},
                    "table": {"type": "string"},
                    "object_type_handle": {"type": "string"},
                    "object_type_prod_id": {"type": "string"},
                    "use_all_columns": {"type": "boolean"},
                    "column_spec": {"type": "array", "items": {"type": "string"}},
                    "trait_config": {"type": "object"},
                    "description": {"type": "string"},
                },
                "required": ["handle", "schema_name", "table"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        if input.get("catalog_handle"):
            catalog_ref = {"source": "action", "handle": input["catalog_handle"]}
        elif input.get("catalog_name"):
            catalog_ref = {"source": "prod", "kind": "catalog", "id": input["catalog_name"]}
        else:
            raise ToolError("provide catalog_handle or catalog_name")
        if input.get("object_type_handle"):
            type_ref = {"source": "action", "handle": input["object_type_handle"]}
        elif input.get("object_type_prod_id"):
            type_ref = {"source": "prod", "kind": "object_type", "id": input["object_type_prod_id"]}
        else:
            raise ToolError("provide object_type_handle or object_type_prod_id")
        return _append(ctx, {
            "op": "create_object_factory",
            "handle": input["handle"],
            "data_source": {"catalog": catalog_ref, "schema_name": input["schema_name"], "table": input["table"]},
            "object_type": type_ref,
            "use_all_columns": input.get("use_all_columns", True),
            "column_spec": input.get("column_spec", []),
            "trait_config": input.get("trait_config", {}),
            "description": input.get("description", ""),
        }, input)


class StageDeleteTool:
    """Generic delete-action author for catalog/object_type/object_factory."""

    def __init__(self, name: str, op: str, kind: str, desc: str):
        self.name = name
        self._op = op
        self._kind = kind
        self._desc = desc

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self._desc,
            "input_schema": {"type": "object", "properties": dict(_TARGET_SCHEMA)},
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        return _append(ctx, {"op": self._op, "target": _target(input, self._kind)}, input)


# --------------------------------------------------------------------------- #
# Changeset management + staging ops
# --------------------------------------------------------------------------- #


class ShowChangesetTool:
    name = "show_changeset"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Return the current ordered list of staged actions (the reviewable changeset).",
            "input_schema": {"type": "object", "properties": {}},
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        return json.dumps({"actions": _store(ctx).raw()}, indent=2)


class RemoveActionTool:
    name = "remove_action"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Remove one action from the changeset by its id (from show_changeset).",
            "input_schema": {
                "type": "object",
                "properties": {"action_id": {"type": "string"}},
                "required": ["action_id"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        try:
            _store(ctx).remove(input["action_id"])
        except ChangesetError as exc:
            raise ToolError(str(exc)) from exc
        return json.dumps({"removed": input["action_id"], "changeset_length": len(_store(ctx).raw())})


class QueryStageTool:
    name = "query_stage"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Build a fresh staging env from the current changeset (off current production) and run a "
                "semantic query against it, then tear it down. Use this to TEST that your staged actions "
                "produce the objects you expect before applying. Returns the interpreted objects."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "Object type name to query."},
                    "limit": {"type": "integer"},
                },
                "required": ["from"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        core = HttpCoreClient(ctx.core_url)
        store = _store(ctx)
        try:
            env = store.materialize(core)
        except (ResolverError, CoreError) as exc:
            raise ToolError(f"could not build staging env: {exc}") from exc
        try:
            result = core.query(env, {"from": input["from"], "limit": input.get("limit", 20)})
        except CoreError as exc:
            raise ToolError(f"stage query failed: {exc}") from exc
        finally:
            try:
                core.drop_env(env)
            except CoreError:
                pass
        return json.dumps({"objects": result.get("objects", []),
                           "result_status": result.get("result_status", {})})


class BuildAndTestStageTool:
    name = "build_and_test_stage"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Build a fresh staging env from the changeset and run every saved acceptance test against "
                "it (see save_stage_test), then tear it down. Returns per-test pass/fail. Run this to prove "
                "the changeset is good before applying, and again right before applying if prod has moved."
            ),
            "input_schema": {"type": "object", "properties": {}},
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        core = HttpCoreClient(ctx.core_url)
        store = _store(ctx)
        try:
            env = store.materialize(core)
        except (ResolverError, CoreError) as exc:
            raise ToolError(f"could not build staging env: {exc}") from exc
        results = []
        try:
            for t in store.tests():
                results.append(_run_test(core, env, t))
        finally:
            try:
                core.drop_env(env)
            except CoreError:
                pass
        return json.dumps({"tests": results, "all_passed": all(r["passed"] for r in results) if results else None})


class SaveStageTestTool:
    name = "save_stage_test"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Save an acceptance test that build_and_test_stage will re-run. A test queries an object "
                "type and asserts a minimum object count (proof the wiring works). Persisted with the "
                "changeset so it can be re-run before promoting after prod drifts."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "from": {"type": "string", "description": "Object type to query."},
                    "min_objects": {"type": "integer", "description": "Assert at least this many objects come back."},
                },
                "required": ["name", "from"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        _store(ctx).add_test({
            "name": input["name"],
            "from": input["from"],
            "min_objects": input.get("min_objects", 1),
        })
        return json.dumps({"saved": input["name"], "total_tests": len(_store(ctx).tests())})


# NOTE: there is deliberately NO apply/promote tool. Promotion to production is
# a HUMAN action performed through the UI (which calls Core's promote endpoint
# via the changeset HTTP blueprint). The agent can build and test a staging env
# but has no capability to change production — that gate is a person clicking
# Apply, not a prompt instruction the model could talk itself past.


class StageUpdateCatalogTool:
    name = "stage_update_catalog"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Append an action updating a catalog's connector and/or properties. "
                "`properties`, if given, fully REPLACES the property dict."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    **_TARGET_SCHEMA,
                    "connector": {"type": "string"},
                    "properties": {"type": "object"},
                },
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        action: dict[str, Any] = {"op": "update_catalog", "target": _target(input, "catalog")}
        if input.get("connector") is not None:
            action["connector"] = input["connector"]
        if input.get("properties") is not None:
            action["properties"] = input["properties"]
        return _append(ctx, action, input)


class StageUpdateObjectTypeTool:
    name = "stage_update_object_type"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Append an action updating an object type's name and/or description.",
            "input_schema": {
                "type": "object",
                "properties": {
                    **_TARGET_SCHEMA,
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        action: dict[str, Any] = {"op": "update_object_type", "target": _target(input, "object_type")}
        if input.get("name") is not None:
            action["name"] = input["name"]
        if input.get("description") is not None:
            action["description"] = input["description"]
        return _append(ctx, action, input)


class StageUpdateObjectFactoryTool:
    name = "stage_update_object_factory"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Append an action updating an object factory: description, column-selection "
                "mode (use_all_columns), the explicit column_spec, and/or per-trait config. "
                "`column_spec` and `trait_config`, if given, fully REPLACE the existing value."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    **_TARGET_SCHEMA,
                    "description": {"type": "string"},
                    "use_all_columns": {"type": "boolean"},
                    "column_spec": {"type": "array", "items": {"type": "string"}},
                    "trait_config": {"type": "object"},
                },
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        action: dict[str, Any] = {
            "op": "update_object_factory",
            "target": _target(input, "object_factory"),
        }
        for k in ("description", "use_all_columns", "column_spec", "trait_config"):
            if input.get(k) is not None:
                action[k] = input[k]
        return _append(ctx, action, input)


class StageSetFlexModuleTool:
    name = "stage_set_flex_module"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Append an action setting the full Python source of a flex catalog's module "
                "(whole-file replace). Target the catalog by its handle (a stage_add_catalog with "
                "connector='flex') or a prod catalog name. Use get_flex_contract for the module contract."
            ),
            "input_schema": {
                "type": "object",
                "properties": {**_TARGET_SCHEMA, "source": {"type": "string"}},
                "required": ["source"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        return _append(ctx, {
            "op": "set_flex_module",
            "target": _target(input, "catalog"),
            "source": input["source"],
        }, input)


class ReorderActionsTool:
    name = "reorder_actions"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Reorder the changeset. Provide every action id (from show_changeset) in the "
                "new order — it must be a permutation of the current ids."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"action_ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["action_ids"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        try:
            _store(ctx).reorder(list(input["action_ids"]))
        except ChangesetError as exc:
            raise ToolError(str(exc)) from exc
        return json.dumps({"reordered": True, "actions": _store(ctx).raw()})


class ClearChangesetTool:
    name = "clear_changeset"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": "Remove ALL staged actions, resetting the changeset to empty. Use to start over.",
            "input_schema": {"type": "object", "properties": {}},
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        _store(ctx).clear()
        return json.dumps({"cleared": True})


def _run_test(core: HttpCoreClient, env: str, test: dict) -> dict:
    try:
        result = core.query(env, {"from": test["from"], "limit": 100})
        n = len(result.get("objects", []))
        passed = n >= int(test.get("min_objects", 1))
        return {"name": test.get("name"), "passed": passed, "objects": n}
    except CoreError as exc:
        return {"name": test.get("name"), "passed": False, "error": str(exc)}


def staging_tools() -> list[Tool]:
    """Every staging tool, in a sensible order for the agent."""
    # Every action-authoring tool is wrapped so it advertises the `note` param.
    authoring = [
        # create
        StageAddCatalogTool(),
        StageAddObjectTypeTool(),
        StageAddTraitTool(),
        StageAddObjectFactoryTool(),
        # update
        StageUpdateCatalogTool(),
        StageUpdateObjectTypeTool(),
        StageUpdateObjectFactoryTool(),
        StageSetFlexModuleTool(),
        StageRemoveTraitTool(),
        # delete
        StageDeleteTool("stage_delete_catalog", "delete_catalog", "catalog",
                        "Append an action deleting a catalog (env-created via handle, or a prod one via id)."),
        StageDeleteTool("stage_delete_object_type", "delete_object_type", "object_type",
                        "Append an action deleting an object type."),
        StageDeleteTool("stage_delete_object_factory", "delete_object_factory", "object_factory",
                        "Append an action deleting an object factory."),
    ]
    return [
        *[_WithNote(t) for t in authoring],
        # changeset management
        ShowChangesetTool(),
        RemoveActionTool(),
        ReorderActionsTool(),
        ClearChangesetTool(),
        # test only — the agent proves the changeset works but CANNOT apply it.
        # Promotion to production is a human action (the UI's Apply button), so
        # the agent has no apply tool. This is the hard governance boundary:
        # prod only ever changes when a person clicks Apply.
        SaveStageTestTool(),
        QueryStageTool(),
        BuildAndTestStageTool(),
    ]
