"""run_raw_trino_query tool — calls Core's POST /raw-trino-query.

This is the raw-SQL debugging escape hatch. For semantic, object-oriented
queries (eventually), use the dedicated query tool that goes through
DataPro's semantic layer. Use this one when you specifically want to talk
to Trino directly.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class RunRawTrinoQueryTool(Tool):
    name = "run_raw_trino_query"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Execute a raw Trino SQL query, bypassing DataPro's semantic "
                "layer. Intended for debugging / ad-hoc exploration when the "
                "semantic layer isn't enough. Results are returned as JSON "
                "with columns, rows, and elapsed time. Core enforces a "
                "wall-clock timeout and a max-rows cap; large result sets "
                "are truncated rather than streamed in full. Use LIMIT in "
                "your query to keep responses small when exploring."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "The SQL statement to execute. Reference catalogs by their registered name (e.g. SELECT * FROM sec_edgar.public.companies LIMIT 10).",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Cap on rows returned. Defaults to 100; max 10000. Keep this small while exploring.",
                        "default": 100,
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Wall-clock budget for the query, in seconds. Defaults to 30; max 60.",
                        "default": 30,
                    },
                },
                "required": ["sql"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        sql = input.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise ToolError("input.sql is required and must be a non-empty string")
        max_rows = int(input.get("max_rows", 100))
        timeout_seconds = int(input.get("timeout_seconds", 30))

        try:
            r = requests.post(
                f"{ctx.core_url}/raw-trino-query",
                json={"sql": sql, "max_rows": max_rows, "timeout_seconds": timeout_seconds},
                # Generous HTTP timeout — Core enforces the actual query budget.
                # We add a few seconds of slack so a query that uses its full
                # budget still gets a response back rather than timing out the
                # transport layer.
                timeout=timeout_seconds + 10,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        # Core returns structured JSON for both success (200) and failure (400/504).
        # We pass it through verbatim — the model already knows how to interpret
        # the {error, details} shape vs the {columns, rows, ...} shape.
        try:
            body = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        if not r.ok:
            # Re-marshal so the model sees the error structure clearly.
            return json.dumps({"http_status": r.status_code, "response": body}, indent=2)
        return json.dumps(body, indent=2)
