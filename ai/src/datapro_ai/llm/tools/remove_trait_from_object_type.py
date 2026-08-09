"""remove_trait_from_object_type — DELETE /object-types/{id}/traits/{trait_name}.

Idempotent: removing a trait the type doesn't have is a no-op. Removing
a trait re-validates every factory under the type — factories that
were broken purely because of the removed trait's missing config can
flip back to ok automatically.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class RemoveTraitFromObjectTypeTool(Tool):
    name = "remove_trait_from_object_type"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Detach a trait from an object type. Idempotent — removing "
                "an absent trait succeeds with no change. Factory trait_config "
                "for the removed trait is left in place (harmless once the "
                "trait is gone; will simply be ignored). The Identity trait "
                "specifically reverts SQL generation back to UNION."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Object type UUID."},
                    "trait_name": {
                        "type": "string",
                        "description": "Trait identifier to detach.",
                    },
                },
                "required": ["id", "trait_name"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        otype_id = input.get("id")
        trait_name = input.get("trait_name")
        if not isinstance(otype_id, str) or not otype_id.strip():
            raise ToolError("input.id is required and must be a non-empty string")
        if not isinstance(trait_name, str) or not trait_name.strip():
            raise ToolError("input.trait_name is required and must be a non-empty string")

        try:
            r = requests.delete(
                f"{ctx.core_url}/object-types/{otype_id}/traits/{trait_name}",
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        if r.status_code == 400 and r.json().get("error") == "invalid_id":
            raise ToolError(f"{otype_id!r} is not a valid UUID")
        if r.status_code == 404:
            raise ToolError(f"no object type with id {otype_id!r}")
        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")
        return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)
