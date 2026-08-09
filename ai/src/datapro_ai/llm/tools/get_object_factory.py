"""get_object_factory tool — GET /object-factories/{id}."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class GetObjectFactoryTool(Tool):
    name = "get_object_factory"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Fetch one object factory by its UUID. Returns the parent "
                "catalog name, parent object type id + name, description, "
                "and timestamps."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "The factory's UUID.",
                    },
                },
                "required": ["id"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        factory_id = input.get("id")
        if not isinstance(factory_id, str) or not factory_id.strip():
            raise ToolError("input.id is required and must be a non-empty string")
        try:
            r = requests.get(
                f"{ctx.core_url}/object-factories/{factory_id}", timeout=10
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        if r.status_code == 400:
            raise ToolError(f"{factory_id!r} is not a valid UUID")
        if r.status_code == 404:
            raise ToolError(f"no object factory with id {factory_id!r}")
        if not r.ok:
            raise ToolError(f"Core returned HTTP {r.status_code}: {r.text[:500]}")
        return json.dumps(r.json(), indent=2)
