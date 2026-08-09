"""replace_flex_module_lines — replace a 1-based inclusive line range.

Calls Core's POST /flex-modules/{name}/replace-lines endpoint. Use
this when an edit is best described as "lines 12–18 should become
this" — e.g. rewriting a function body wholesale.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class ReplaceFlexModuleLinesTool(Tool):
    name = "replace_flex_module_lines"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Edit a flex module by replacing an inclusive line range. "
                "Use view_flex_module first to confirm the line numbers. "
                "`new_text` replaces lines [start_line..end_line] verbatim "
                "— it can span any number of lines (including 0 to delete "
                "the range). Don't include the line-number prefixes from "
                "view_flex_module's output; just the source text. The "
                "change hot-swaps live."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "catalog_name": {"type": "string"},
                    "start_line": {
                        "type": "integer",
                        "description": "1-based inclusive first line to replace.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "1-based inclusive last line to replace. Must be >= start_line.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": (
                            "Replacement source for those lines. Trailing "
                            "newline is added automatically if the original "
                            "range ended with one. Pass \"\" to delete the "
                            "range entirely."
                        ),
                    },
                },
                "required": ["catalog_name", "start_line", "end_line", "new_text"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        catalog_name = input.get("catalog_name")
        start_line = input.get("start_line")
        end_line = input.get("end_line")
        new_text = input.get("new_text")
        if not isinstance(catalog_name, str) or not catalog_name.strip():
            raise ToolError("input.catalog_name is required and must be a non-empty string")
        if not isinstance(start_line, int) or start_line < 1:
            raise ToolError("input.start_line is required and must be a positive integer")
        if not isinstance(end_line, int) or end_line < 1:
            raise ToolError("input.end_line is required and must be a positive integer")
        if end_line < start_line:
            raise ToolError("input.end_line must be >= input.start_line")
        if not isinstance(new_text, str):
            raise ToolError("input.new_text is required and must be a string (may be empty)")

        try:
            r = requests.post(
                f"{ctx.core_url}/flex-modules/{catalog_name}/replace-lines",
                json={
                    "start_line": start_line,
                    "end_line": end_line,
                    "new_text": new_text,
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        try:
            body = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        if r.status_code == 404:
            raise ToolError(f"no flex module for catalog {catalog_name!r}")
        if r.status_code == 400 and body.get("error") == "end_line_out_of_range":
            raise ToolError(body.get("details") or "end_line is beyond the file's end")
        if r.status_code == 400 and body.get("error") == "invalid_range":
            raise ToolError(body.get("details") or "invalid line range")
        if r.status_code == 400 and body.get("error") == "invalid_python":
            raise ToolError(
                f"the resulting source has invalid Python syntax: {body.get('details')}"
            )
        if r.status_code != 200:
            raise ToolError(
                f"{body.get('error', 'request failed')} (HTTP {r.status_code})"
            )

        return json.dumps({"http_status": r.status_code, "response": body}, indent=2)
