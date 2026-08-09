"""list_traits — GET /traits.

Returns the hardcoded trait registry so the model can discover which
traits Core actually supports rather than guessing. Use before
add_trait_to_object_type so you pick a real trait name.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class ListTraitsTool(Tool):
    name = "list_traits"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "List the traits Core knows about, with their descriptions "
                "and the per-factory config keys each one requires. Traits "
                "are a fixed registry — you can't define new ones, only "
                "attach/detach existing ones via add_trait_to_object_type. "
                "Use this when the user mentions traits or when picking "
                "trait_config keys for a factory."
            ),
            "input_schema": {"type": "object", "properties": {}},
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        try:
            r = requests.get(f"{ctx.core_url}/traits", timeout=10)
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")
        return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)
