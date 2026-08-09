"""delete_object_type tool — DELETE /object-types/{id}."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class DeleteObjectTypeTool(Tool):
    name = "delete_object_type"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Delete an object type. Irreversible. Confirm with the user "
                "before calling this — they almost certainly do NOT want you "
                "deleting types unprompted."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "The object type's UUID.",
                    },
                },
                "required": ["id"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        type_id = input.get("id")
        if not isinstance(type_id, str) or not type_id.strip():
            raise ToolError("input.id is required and must be a non-empty string")
        try:
            r = requests.delete(
                f"{ctx.core_url}/object-types/{type_id}", timeout=10
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        if r.status_code == 400:
            raise ToolError(f"{type_id!r} is not a valid UUID")
        if r.status_code == 404:
            raise ToolError(f"no object type with id {type_id!r}")
        if not r.ok:
            raise ToolError(f"Core returned HTTP {r.status_code}: {r.text[:500]}")
        return json.dumps(r.json(), indent=2)
