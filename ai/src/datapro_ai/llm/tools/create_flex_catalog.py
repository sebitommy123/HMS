"""create_flex_catalog — register a new flex-backed Trino catalog with
its module source in one call.

Wraps POST /catalogs with connector=flex + source. Core materializes
the module file, persists the FlexModule row, and applies CREATE
CATALOG to Trino synchronously, all in one HTTP round trip.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class CreateFlexCatalogTool(Tool):
    name = "create_flex_catalog"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Create a new flex catalog from a Python module source. "
                "The module must define get_tables and read_table — see "
                "view_flex_module on any existing flex catalog for the "
                "contract. Strongly consider calling "
                "preview_flex_module first to validate the draft. The "
                "catalog name follows the same rules as create_catalog "
                "(alphanumerics, underscores, hyphens)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Catalog name. Becomes the SQL identifier you query as.",
                    },
                    "source": {
                        "type": "string",
                        "description": "Full Python source for the flex module.",
                    },
                },
                "required": ["name", "source"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        name = input.get("name")
        source = input.get("source")
        if not isinstance(name, str) or not name.strip():
            raise ToolError("input.name is required and must be a non-empty string")
        if not isinstance(source, str) or not source.strip():
            raise ToolError("input.source is required and must be a non-empty string")

        try:
            r = requests.post(
                f"{ctx.core_url}/catalogs",
                json={"name": name, "connector": "flex", "source": source},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        try:
            body = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        if r.status_code == 409:
            raise ToolError(f"a catalog named {name!r} already exists")
        if r.status_code == 400 and body.get("error") == "invalid_python":
            raise ToolError(f"invalid Python syntax: {body.get('details')}")
        if r.status_code not in (201, 502):
            # 502 = Core saved the row but Trino rejected CREATE. Treat
            # that as success-with-warning, so the model can surface
            # the error to the user and decide whether to keep iterating.
            raise ToolError(
                f"{body.get('error', 'request failed')}: {body.get('details', '')} "
                f"(HTTP {r.status_code})"
            )
        return json.dumps({"http_status": r.status_code, "response": body}, indent=2)
