"""update_object_factory_column — replace one column expression in place."""

import json
from typing import Any

from datapro_ai.llm.tools.base import Tool, ToolContext
from datapro_ai.llm.tools._factory_helpers import (
    fetch_factory,
    patch_column_spec,
    require_int_index,
    require_non_empty_string,
)


class UpdateObjectFactoryColumnTool(Tool):
    name = "update_object_factory_column"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Replace one column in an object factory's column_spec "
                "(at the given 0-based index) with a new expression. Order "
                "is preserved; the surrounding entries are untouched. Use "
                "this for renames/edits; use remove_object_factory_column to "
                "drop, or add_object_factory_column to append."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Factory UUID."},
                    "index": {
                        "type": "integer",
                        "description": "0-based index of the column to replace.",
                    },
                    "column": {
                        "type": "string",
                        "description": "New column expression.",
                    },
                },
                "required": ["id", "index", "column"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        factory_id = require_non_empty_string(input, "id")
        column = require_non_empty_string(input, "column")
        current = fetch_factory(ctx, factory_id)
        spec = list(current.get("column_spec") or [])
        idx = require_int_index(input, len(spec))
        old = spec[idx]
        spec[idx] = column
        updated = patch_column_spec(ctx, factory_id, spec)
        return json.dumps(
            {"replaced": {"old": old, "new": column}, "response": updated},
            indent=2,
        )
