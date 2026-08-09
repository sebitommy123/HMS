"""update_catalog tool — modify an existing catalog via Core's PATCH /catalogs/{name}."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class UpdateCatalogTool(Tool):
    name = "update_catalog"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Modify an existing catalog. Both `connector` and `properties` "
                "are optional — provide whichever you want to change. "
                "Important: `properties`, if provided, REPLACES the entire "
                "properties dict on the catalog. To add or remove a single "
                "key, first call get_catalog to read the current properties, "
                "merge your change in locally, then pass the full updated map "
                "here. Core forces a Trino DROP+CREATE so the new "
                "configuration takes effect immediately. If the catalog was "
                "previously broken, this is also how you recover it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The catalog to update.",
                    },
                    "connector": {
                        "type": "string",
                        "description": (
                            "New connector. Omit to keep the current connector."
                        ),
                    },
                    "properties": {
                        "type": "object",
                        "description": (
                            "REPLACEMENT properties map (string-to-string). "
                            "Omit to leave properties unchanged. To remove a "
                            "single property, pass the current properties "
                            "without that key. To add one, pass the current "
                            "properties plus the new key."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["name"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        name = input.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolError("input.name is required and must be a non-empty string")

        body: dict[str, Any] = {}
        if "connector" in input:
            connector = input.get("connector")
            if not isinstance(connector, str) or not connector.strip():
                raise ToolError("input.connector, if provided, must be a non-empty string")
            body["connector"] = connector
        if "properties" in input:
            properties = input.get("properties")
            if not isinstance(properties, dict):
                raise ToolError("input.properties, if provided, must be an object")
            for k, v in properties.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise ToolError(
                        "input.properties must be a flat string-to-string map; got non-string entry"
                    )
            body["properties"] = properties

        if not body:
            raise ToolError(
                "update_catalog needs at least one of `connector` or `properties` to change"
            )

        try:
            r = requests.patch(
                f"{ctx.core_url}/catalogs/{name}",
                json=body,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        if r.status_code == 404:
            raise ToolError(f"no catalog named {name!r} is registered")

        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        # Pass through ok or not. 502 means Trino rejected the recreate; the
        # model can read `catalog.last_error` and try again.
        return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)
