"""query_objects tool — POST /query on Core, the semantic query engine.

Returns a tabular result (columns + rows) plus a result_status block. Each
row carries a `_datasource` column identifying which (catalog, schema,
table) it came from — useful when multiple factories produce the same
object type.

For the SQL string Core would send (without executing), use
preview_query_plan first.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class QueryObjectsTool(Tool):
    name = "query_objects"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Run a semantic Core query: ask DataPro for objects of a "
                "given type. Core resolves the type to its object "
                "factories, generates one UNION ALL CORRESPONDING Trino "
                "statement, and runs it. Returns the raw table (columns + "
                "rows) plus a result_status block listing which factories "
                "were used, which were skipped (and why), any errors, "
                "the SQL that ran, and timing. Each row has a "
                "_datasource column identifying its source."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "from": {
                        "type": "string",
                        "description": (
                            "Display name of the object type to query "
                            "(e.g. 'Company', 'Filing')."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Per-factory row cap (1-100). Each factory gets "
                            "this limit; total rows = limit * number of "
                            "factories. Default 25."
                        ),
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": (
                            "Wall-clock budget for the whole Trino "
                            "statement (1-30). Default 10."
                        ),
                    },
                },
                "required": ["from"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        body = _build_body(input)
        try:
            r = requests.post(
                f"{ctx.core_url}/query",
                json=body,
                timeout=(body.get("timeout_seconds", 10) + 10),
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        try:
            parsed = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        if r.status_code == 404 and parsed.get("error") == "object_type_not_found":
            raise ToolError(
                f"no object type named {body.get('from')!r}; "
                "list_object_types to see what's registered"
            )
        if not r.ok:
            return json.dumps({"http_status": r.status_code, "response": parsed}, indent=2)

        return json.dumps(parsed, indent=2)


def _build_body(input: dict[str, Any]) -> dict[str, Any]:
    from_type = input.get("from")
    if not isinstance(from_type, str) or not from_type.strip():
        raise ToolError("input.from is required and must be a non-empty string")
    body: dict[str, Any] = {"from": from_type}
    if "limit" in input:
        limit = input["limit"]
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ToolError("input.limit, if provided, must be an integer")
        body["limit"] = limit
    if "timeout_seconds" in input:
        timeout = input["timeout_seconds"]
        if not isinstance(timeout, int) or isinstance(timeout, bool):
            raise ToolError("input.timeout_seconds, if provided, must be an integer")
        body["timeout_seconds"] = timeout
    return body
