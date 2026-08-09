"""create_catalog tool — register a new catalog through Core's POST /catalogs."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class CreateCatalogTool(Tool):
    name = "create_catalog"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Register a new catalog in Core. Core persists it to Postgres "
                "and synchronously runs CREATE CATALOG against Trino so it's "
                "queryable immediately. Returns the catalog row plus the "
                "reconcile action list. If Trino rejects the catalog (bad "
                "connection URL, missing properties, unknown connector, etc.) "
                "the call returns a non-OK result and the row is marked "
                "'broken' with the Trino error message — you can fix it with "
                "the update_catalog tool. Names and connector names must be "
                "alphanumerics, underscores, or hyphens."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Unique catalog name (e.g. 'tpch_demo', 'sec_edgar').",
                    },
                    "connector": {
                        "type": "string",
                        "description": (
                            "Trino connector plugin name — e.g. 'tpch', 'memory', "
                            "'postgresql', 'mysql', 'mongodb'. Must match a plugin "
                            "Trino has loaded."
                        ),
                    },
                    "properties": {
                        "type": "object",
                        "description": (
                            "WITH-clause properties for the connector, as a flat "
                            "string→string map. For postgresql/mysql the typical "
                            "keys are 'connection-url', 'connection-user', "
                            "'connection-password'. Default: empty (most "
                            "connectors with sensible defaults like 'tpch' / "
                            "'memory' need no properties)."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["name", "connector"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        name = input.get("name")
        connector = input.get("connector")
        properties = input.get("properties") or {}

        if not isinstance(name, str) or not name.strip():
            raise ToolError("input.name is required and must be a non-empty string")
        if not isinstance(connector, str) or not connector.strip():
            raise ToolError("input.connector is required and must be a non-empty string")
        if not isinstance(properties, dict):
            raise ToolError("input.properties, if provided, must be an object")
        for k, v in properties.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ToolError(
                    "input.properties must be a flat string-to-string map; got non-string entry"
                )

        try:
            r = requests.post(
                f"{ctx.core_url}/catalogs",
                json={"name": name, "connector": connector, "properties": properties},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        try:
            body = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        # Pass the full body through whether ok or not. 502 means Trino rejected
        # the CREATE; the model can read `catalog.last_error` to see why and
        # decide whether to update_catalog with corrected properties or accept
        # the broken state.
        return json.dumps({"http_status": r.status_code, "response": body}, indent=2)
