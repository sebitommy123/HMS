"""get_data_source_columns tool — GET /data-sources/{id}/columns.

Asks Core to introspect the data source's underlying Trino table and
return its column names + types. Useful when the agent is deciding what
to put in a factory's column_spec, or when answering "what fields does
this table have".
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class GetDataSourceColumnsTool(Tool):
    name = "get_data_source_columns"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "List the columns (name + Trino type) of the table a data "
                "source points to. Goes through Core, which dispatches a "
                "live SHOW COLUMNS to Trino — schemas can change upstream, "
                "so this is read every time. Returns 502 with a Trino "
                "error if the table doesn't exist or the catalog is down."
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
            r = requests.get(
                f"{ctx.core_url}/data-sources/{source_id}/columns", timeout=30
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        if r.status_code == 400:
            raise ToolError(f"{source_id!r} is not a valid UUID")
        if r.status_code == 404:
            raise ToolError(f"no data source with id {source_id!r}")

        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        # 502 (Trino error) is passed through verbatim — the agent can read
        # the details and decide whether to surface to the user or retry.
        return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)
