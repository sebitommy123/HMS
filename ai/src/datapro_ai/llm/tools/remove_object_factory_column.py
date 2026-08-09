"""remove_object_factory_column — drop one column from the list by 0-based index."""

import json
from typing import Any

from datapro_ai.llm.tools.base import Tool, ToolContext
from datapro_ai.llm.tools._factory_helpers import (
    fetch_factory,
    patch_column_spec,
    require_int_index,
    require_non_empty_string,
)


class RemoveObjectFactoryColumnTool(Tool):
    name = "remove_object_factory_column"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Remove a single column from an object factory's column_spec "
                "by its 0-based index. Read the current list with "
                "get_object_factory first if you're not sure which index to "
                "use — the response's column_spec is in display order."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Factory UUID."},
                    "index": {
                        "type": "integer",
                        "description": "0-based index of the column to remove.",
                    },
                },
                "required": ["id", "index"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        factory_id = require_non_empty_string(input, "id")
        current = fetch_factory(ctx, factory_id)
        spec = list(current.get("column_spec") or [])
        idx = require_int_index(input, len(spec))
        removed = spec.pop(idx)
        updated = patch_column_spec(ctx, factory_id, spec)
        return json.dumps({"removed": removed, "response": updated}, indent=2)
