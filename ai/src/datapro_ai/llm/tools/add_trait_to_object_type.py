"""add_trait_to_object_type — PUT /object-types/{id}/traits/{trait_name}.

Idempotent: re-adding an already-present trait is a no-op that still
returns the current object-type row. Adding a trait will mark any
factory under this type as broken until that factory has matching
trait_config — that's working as intended, prompt the user to set the
trait config next.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class AddTraitToObjectTypeTool(Tool):
    name = "add_trait_to_object_type"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Attach a trait to an object type. Use list_traits first if "
                "you're not sure what's available. Adding a trait can flip "
                "every factory under this type to broken until each one has "
                "the matching trait_config — after this, walk each factory "
                "and call set_factory_trait_config with the trait's required "
                "keys. The Identity trait specifically changes how the SQL is "
                "built (FULL OUTER JOIN across factories instead of UNION)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Object type UUID."},
                    "trait_name": {
                        "type": "string",
                        "description": (
                            "The trait identifier (e.g. 'identity', "
                            "'temporal'). Must match one of the names "
                            "returned by list_traits."
                        ),
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
            r = requests.put(
                f"{ctx.core_url}/object-types/{otype_id}/traits/{trait_name}",
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        if r.status_code == 400 and r.json().get("error") == "invalid_id":
            raise ToolError(f"{otype_id!r} is not a valid UUID")
        if r.status_code == 400 and r.json().get("error") == "unknown_trait":
            known = r.json().get("known", [])
            raise ToolError(
                f"unknown trait {trait_name!r}; known traits are: {', '.join(known)}"
            )
        if r.status_code == 404:
            raise ToolError(f"no object type with id {otype_id!r}")
        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")
        return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)
