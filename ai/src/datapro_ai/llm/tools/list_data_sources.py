"""list_data_sources tool — GET /data-sources with optional ?catalog= filter."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class ListDataSourcesTool(Tool):
    name = "list_data_sources"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "List data sources. A data source is a specific "
                "(catalog, schema, table) handle that object factories read "
                "from. They're auto-discovered from each catalog's tables by "
                "Core's reconciler (not created by hand). Each has a `status`: "
                "`active`, or `deleted` (the table vanished from Trino but "
                "factories still reference it). Filter to one catalog with "
                "`catalog`."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "catalog": {
                        "type": "string",
                        "description": "Optional catalog name to filter by.",
                    },
                },
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        params: dict[str, str] = {}
        catalog = input.get("catalog")
        if isinstance(catalog, str) and catalog.strip():
            params["catalog"] = catalog.strip()
        try:
            r = requests.get(f"{ctx.core_url}/data-sources", params=params, timeout=10)
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        if not r.ok:
            raise ToolError(f"Core returned HTTP {r.status_code}: {r.text[:500]}")
        return json.dumps(r.json(), indent=2)
