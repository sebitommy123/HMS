"""update_object_type tool — PATCH /object-types/{id}."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class UpdateObjectTypeTool(Tool):
    name = "update_object_type"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Modify an existing object type. Either `name` or "
                "`description` (or both) — both optional, but at least one "
                "must be provided. Renaming is safe: the UUID is stable, so "
                "anything pointing at this type by id keeps working."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "The object type's UUID.",
                    },
                    "name": {
                        "type": "string",
                        "description": "New display name. Omit to keep current name.",
                    },
                    "description": {
                        "type": "string",
                        "description": "New description. Omit to keep current description.",
                    },
                },
                "required": ["id"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        type_id = input.get("id")
        if not isinstance(type_id, str) or not type_id.strip():
            raise ToolError("input.id is required and must be a non-empty string")

        body: dict[str, Any] = {}
        if "name" in input:
            name = input["name"]
            if not isinstance(name, str) or not name.strip():
                raise ToolError("input.name, if provided, must be a non-empty string")
            body["name"] = name
        if "description" in input:
            description = input["description"]
            if not isinstance(description, str):
                raise ToolError("input.description, if provided, must be a string")
            body["description"] = description

        if not body:
            raise ToolError(
                "update_object_type needs at least one of `name` or `description` to change"
            )

        try:
            r = requests.patch(
                f"{ctx.core_url}/object-types/{type_id}", json=body, timeout=10
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        if r.status_code == 400 and r.json().get("error") == "invalid_id":
            raise ToolError(f"{type_id!r} is not a valid UUID")
        if r.status_code == 404:
            raise ToolError(f"no object type with id {type_id!r}")

        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)
