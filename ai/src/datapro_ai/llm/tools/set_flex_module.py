"""set_flex_module — replace a flex module's source entirely.

The escape hatch when neither substring nor line-range edits are a
good fit (massive rewrite, file unreadable, etc.). Prefer the focused
edit tools for everyday changes — full overwrites lose review fidelity.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class SetFlexModuleTool(Tool):
    name = "set_flex_module"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Replace a flex catalog's entire Python source. Use this "
                "for wholesale rewrites; for surgical edits prefer "
                "replace_in_flex_module (substring) or "
                "replace_flex_module_lines (line range). The change "
                "hot-swaps live — the next query against the catalog "
                "runs the new module."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "catalog_name": {"type": "string"},
                    "source": {
                        "type": "string",
                        "description": (
                            "Full Python source. Must define get_tables and "
                            "read_table. Invalid Python is rejected at write "
                            "time, before the file is materialized."
                        ),
                    },
                },
                "required": ["catalog_name", "source"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        catalog_name = input.get("catalog_name")
        source = input.get("source")
        if not isinstance(catalog_name, str) or not catalog_name.strip():
            raise ToolError("input.catalog_name is required and must be a non-empty string")
        if not isinstance(source, str) or not source.strip():
            raise ToolError("input.source is required and must be a non-empty string")

        try:
            r = requests.put(
                f"{ctx.core_url}/flex-modules/{catalog_name}",
                json={"source": source},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        try:
            body = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        if r.status_code == 404:
            raise ToolError(f"no flex module for catalog {catalog_name!r}")
        if r.status_code == 400 and body.get("error") == "invalid_python":
            raise ToolError(f"invalid Python syntax: {body.get('details')}")
        if r.status_code != 200:
            raise ToolError(
                f"{body.get('error', 'request failed')} (HTTP {r.status_code})"
            )
        return json.dumps({"http_status": r.status_code, "response": body}, indent=2)
