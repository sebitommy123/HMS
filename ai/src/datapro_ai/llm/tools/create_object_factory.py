"""create_object_factory tool — POST /object-factories."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class CreateObjectFactoryTool(Tool):
    name = "create_object_factory"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Attach an object type to a data source by creating an "
                "object factory. A data source can have at most one factory "
                "per object type — duplicates return 409. Both parents must "
                "exist or you get 404. The data source carries the catalog "
                "+ schema + table; the factory adds the column-selection "
                "config and per-trait config on top. If the object type has "
                "traits enabled, supply trait_config too (or set it later via "
                "set_factory_trait_config) or the factory will start broken. "
                "Returns the new factory including its UUID."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "data_source_id": {
                        "type": "string",
                        "description": (
                            "UUID of an existing data source. Data sources "
                            "are auto-discovered from the catalog's tables — "
                            "use list_data_sources to find the one you want. "
                            "If it's missing, the catalog may still be syncing "
                            "(or its backing store is down); it can't be "
                            "created by hand."
                        ),
                    },
                    "object_type_id": {
                        "type": "string",
                        "description": "UUID of an existing object type.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional human-readable description of how the factory works.",
                    },
                    "use_all_columns": {
                        "type": "boolean",
                        "description": (
                            "If true (default), the factory inherits every "
                            "column from the source table verbatim. If false, "
                            "supply `column_spec` with the explicit SELECT "
                            "list."
                        ),
                    },
                    "column_spec": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "When `use_all_columns=false`, the list of "
                            "column expressions to select — e.g. "
                            "[\"name\", \"sic\", \"state\"]. Each entry is "
                            "freeform (a column name, an alias, or any "
                            "SQL expression). Order is preserved. Ignored "
                            "when `use_all_columns=true`."
                        ),
                    },
                    "trait_config": {
                        "type": "object",
                        "description": (
                            "Per-trait config dict, shape "
                            "{trait_name: {trait-specific keys}}. Required "
                            "when the parent object type has traits enabled "
                            "— e.g. for an object type with the Identity "
                            "trait, supply {\"identity\": {\"column\": "
                            "\"cik\"}}. Omit if the object type has no "
                            "traits."
                        ),
                    },
                },
                "required": ["data_source_id", "object_type_id"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        data_source_id = input.get("data_source_id")
        object_type_id = input.get("object_type_id")
        if not isinstance(data_source_id, str) or not data_source_id.strip():
            raise ToolError("input.data_source_id is required and must be a non-empty string")
        if not isinstance(object_type_id, str) or not object_type_id.strip():
            raise ToolError("input.object_type_id is required and must be a non-empty string")

        body: dict[str, Any] = {
            "data_source_id": data_source_id,
            "object_type_id": object_type_id,
        }
        description = input.get("description")
        if description is not None:
            if not isinstance(description, str):
                raise ToolError("input.description, if provided, must be a string")
            body["description"] = description
        use_all = input.get("use_all_columns")
        if use_all is not None:
            if not isinstance(use_all, bool):
                raise ToolError("input.use_all_columns, if provided, must be a boolean")
            body["use_all_columns"] = use_all
        column_spec = input.get("column_spec")
        if column_spec is not None:
            if not isinstance(column_spec, list) or not all(
                isinstance(c, str) for c in column_spec
            ):
                raise ToolError(
                    "input.column_spec, if provided, must be a list of strings"
                )
            body["column_spec"] = column_spec
        trait_config = input.get("trait_config")
        if trait_config is not None:
            if not isinstance(trait_config, dict):
                raise ToolError(
                    "input.trait_config, if provided, must be an object"
                )
            body["trait_config"] = trait_config

        try:
            r = requests.post(
                f"{ctx.core_url}/object-factories", json=body, timeout=10
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)
