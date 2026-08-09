"""set_object_factory_use_all_columns — toggle the all-columns mode."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class SetObjectFactoryUseAllColumnsTool(Tool):
    name = "set_object_factory_use_all_columns"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Toggle whether the factory inherits every column from the "
                "source automatically (true) or uses only the columns listed "
                "in its column_spec (false). The column list is preserved "
                "across toggles — turning all-columns back off restores the "
                "previous explicit list."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Factory UUID."},
                    "use_all_columns": {
                        "type": "boolean",
                        "description": "True = inherit all source columns. False = use explicit column_spec.",
                    },
                },
                "required": ["id", "use_all_columns"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        factory_id = input.get("id")
        if not isinstance(factory_id, str) or not factory_id.strip():
            raise ToolError("input.id is required and must be a non-empty string")
        use_all = input.get("use_all_columns")
        if not isinstance(use_all, bool):
            raise ToolError("input.use_all_columns is required and must be a boolean")

        try:
            r = requests.patch(
                f"{ctx.core_url}/object-factories/{factory_id}",
                json={"use_all_columns": use_all},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        if r.status_code == 400 and r.json().get("error") == "invalid_id":
            raise ToolError(f"{factory_id!r} is not a valid UUID")
        if r.status_code == 404:
            raise ToolError(f"no object factory with id {factory_id!r}")
        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")
        return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)
