"""list_object_types tool — GET /object-types with optional search."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class ListObjectTypesTool(Tool):
    name = "list_object_types"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "List the object types registered in DataPro Core. Object types "
                "are the kinds of things the system can talk about (e.g. "
                "'Company', 'Filing', 'User'). Each has a stable UUID, a "
                "mutable display name, and a description. Use the `search` "
                "argument to substring-filter by name or description."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Optional case-insensitive substring filter on name + description.",
                    },
                },
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        search = input.get("search")
        params = {}
        if isinstance(search, str) and search.strip():
            params["search"] = search.strip()
        try:
            r = requests.get(
                f"{ctx.core_url}/object-types", params=params, timeout=10
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        if not r.ok:
            raise ToolError(f"Core returned HTTP {r.status_code}: {r.text[:500]}")
        return json.dumps(r.json(), indent=2)
