"""set_object_factory_description — single-purpose: change just the description."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class SetObjectFactoryDescriptionTool(Tool):
    name = "set_object_factory_description"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Set the description on an object factory. Replaces the "
                "existing description entirely. Doesn't touch column "
                "settings."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Factory UUID."},
                    "description": {
                        "type": "string",
                        "description": "New description text. Pass empty string to clear.",
                    },
                },
                "required": ["id", "description"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        factory_id = input.get("id")
        if not isinstance(factory_id, str) or not factory_id.strip():
            raise ToolError("input.id is required and must be a non-empty string")
        description = input.get("description")
        if not isinstance(description, str):
            raise ToolError("input.description is required and must be a string")

        try:
            r = requests.patch(
                f"{ctx.core_url}/object-factories/{factory_id}",
                json={"description": description},
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
