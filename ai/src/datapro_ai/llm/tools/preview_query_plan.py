"""preview_query_plan tool — POST /preview-query-plan on Core.

Same input as query_objects but doesn't execute. Returns the SQL Core
would send to Trino, plus which factories it'd touch and which it'd
skip. Useful before running an expensive query, or for showing the
user what's about to happen.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class PreviewQueryPlanTool(Tool):
    name = "preview_query_plan"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Plan a Core semantic query without executing it. Returns "
                "the exact Trino SQL Core would send (after introspecting "
                "each factory's columns), plus the factories that would "
                "be used and those that would be skipped (with reasons). "
                "Use this before query_objects to sanity-check a query or "
                "to show the user what's about to run."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "from": {
                        "type": "string",
                        "description": "Object type name (e.g. 'Company').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Per-factory row cap (1-100). Default 25.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Wall-clock budget (1-30). Default 10.",
                    },
                },
                "required": ["from"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        body = _build_body(input)
        try:
            r = requests.post(
                f"{ctx.core_url}/preview-query-plan",
                json=body,
                timeout=30,
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
