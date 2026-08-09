"""get_data_source tool — GET /data-sources/{id}."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class GetDataSourceTool(Tool):
    name = "get_data_source"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Fetch one data source by its UUID. Returns catalog_name, "
                "schema_name, table_name, the fully-qualified path, "
                "description, and timestamps."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Data source UUID."},
                },
                "required": ["id"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        source_id = input.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ToolError("input.id is required and must be a non-empty string")
        try:
            r = requests.get(f"{ctx.core_url}/data-sources/{source_id}", timeout=10)
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        if r.status_code == 400:
            raise ToolError(f"{source_id!r} is not a valid UUID")
        if r.status_code == 404:
            raise ToolError(f"no data source with id {source_id!r}")
        if not r.ok:
            raise ToolError(f"Core returned HTTP {r.status_code}: {r.text[:500]}")
        return json.dumps(r.json(), indent=2)
