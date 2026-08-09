"""set_factory_trait_config — PATCH /object-factories/{id} trait_config only.

A focused single-purpose tool, matching the rest of the factory
mutation suite. Replaces the factory's entire trait_config dict — pass
all traits' config at once, not just the one you want to change. Use
get_object_factory first if you want to preserve existing entries.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class SetFactoryTraitConfigTool(Tool):
    name = "set_factory_trait_config"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Replace an object factory's trait_config dict. Shape: "
                "{trait_name: {trait-specific keys}}. Example for the "
                "Identity trait: {\"identity\": {\"column\": \"cik\"}}. "
                "Each trait validates its slot — picking a column that "
                "doesn't exist on the data source flips the factory to "
                "broken with a precise reason in last_error. THIS REPLACES "
                "the whole trait_config dict; if you only want to change "
                "one trait's config, get_object_factory first and merge."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Factory UUID."},
                    "trait_config": {
                        "type": "object",
                        "description": (
                            "The replacement trait_config dict. Pass {} "
                            "to clear all trait config (factory will be "
                            "broken if the parent object type still has "
                            "any traits requiring config)."
                        ),
                    },
                },
                "required": ["id", "trait_config"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        factory_id = input.get("id")
        trait_config = input.get("trait_config")
        if not isinstance(factory_id, str) or not factory_id.strip():
            raise ToolError("input.id is required and must be a non-empty string")
        if not isinstance(trait_config, dict):
            raise ToolError("input.trait_config is required and must be an object")

        try:
            r = requests.patch(
                f"{ctx.core_url}/object-factories/{factory_id}",
                json={"trait_config": trait_config},
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
