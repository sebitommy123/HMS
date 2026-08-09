"""list_catalogs tool — calls Core's GET /catalogs."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class ListCatalogsTool(Tool):
    name = "list_catalogs"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "List every catalog registered in DataPro Core. Returns each "
                "catalog's name, connector type, status (enabled/disabled/broken), "
                "and any last error. Use this to see what data sources are "
                "available before constructing a query. Takes no arguments."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        try:
            r = requests.get(f"{ctx.core_url}/catalogs", timeout=10)
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        if not r.ok:
            raise ToolError(f"Core returned HTTP {r.status_code}: {r.text[:500]}")
        catalogs = r.json()
        # Trim the response to the fields the model needs — no need to feed it
        # timestamps and version numbers on every call.
        slim = [
            {
                "name": c["name"],
                "connector": c["connector"],
                "status": c["status"],
                "last_error": c.get("last_error"),
            }
            for c in catalogs
        ]
        return json.dumps(slim, indent=2) if slim else "(no catalogs registered)"
