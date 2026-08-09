"""create_object_type tool — POST /object-types."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class CreateObjectTypeTool(Tool):
    name = "create_object_type"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Create a new object type in DataPro. Right now an object type "
                "is just a name + description; fields and traits are added in "
                "later slices. Names must be alphanumerics, underscores, or "
                "hyphens. Use PascalCase by convention (e.g. 'Company', "
                "'Filing'). Returns the new row including its server-minted "
                "UUID."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Display name. Unique. Mutable later via update_object_type.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional human-readable description. Defaults to empty.",
                    },
                },
                "required": ["name"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        name = input.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolError("input.name is required and must be a non-empty string")
        body: dict[str, Any] = {"name": name}
        description = input.get("description")
        if description is not None:
            if not isinstance(description, str):
                raise ToolError("input.description, if provided, must be a string")
            body["description"] = description

        try:
            r = requests.post(f"{ctx.core_url}/object-types", json=body, timeout=10)
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)
