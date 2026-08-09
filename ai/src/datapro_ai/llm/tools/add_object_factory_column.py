"""add_object_factory_column — append (or insert at a given position) one column."""

import json
from typing import Any

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError
from datapro_ai.llm.tools._factory_helpers import (
    fetch_factory,
    patch_column_spec,
    require_non_empty_string,
)


class AddObjectFactoryColumnTool(Tool):
    name = "add_object_factory_column"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Add a single column expression to an object factory's "
                "column_spec. Appended at the end by default; pass `position` "
                "(0-based) to insert at a specific index. Duplicates are "
                "allowed — Core doesn't dedupe column entries. To use the "
                "list at all, the factory must have use_all_columns=false "
                "(see set_object_factory_use_all_columns)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Factory UUID."},
                    "column": {
                        "type": "string",
                        "description": (
                            "Column expression — a column name, alias, or any "
                            "SQL expression like 'count(*) as filings'."
                        ),
                    },
                    "position": {
                        "type": "integer",
                        "description": (
                            "Optional 0-based insert position. Default is end "
                            "of list. Must be in [0, current_len]."
                        ),
                    },
                },
                "required": ["id", "column"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        factory_id = require_non_empty_string(input, "id")
        column = require_non_empty_string(input, "column")
        current = fetch_factory(ctx, factory_id)
        spec = list(current.get("column_spec") or [])

        if "position" in input:
            pos = input["position"]
            if not isinstance(pos, int) or isinstance(pos, bool):
                raise ToolError("input.position, if provided, must be an integer")
            if pos < 0 or pos > len(spec):
                raise ToolError(
                    f"input.position {pos} is out of range; valid range is [0, {len(spec)}]"
                )
            spec.insert(pos, column)
        else:
            spec.append(column)

        updated = patch_column_spec(ctx, factory_id, spec)
        return json.dumps({"response": updated}, indent=2)
