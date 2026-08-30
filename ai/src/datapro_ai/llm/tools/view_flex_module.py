"""view_flex_module — show a flex catalog's Python source with line numbers.

Every returned line is prefixed with its 1-based line number + a tab,
so the model can confidently pick line ranges or quote substrings for
the edit tools (``replace_flex_module_lines``,
``replace_in_flex_module``).

Optional ``start_line`` / ``end_line`` view just a slice of the file
— useful when the module is long.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


# Width for the line-number prefix. Padded so the source column lines
# up no matter how long the file is. 4 covers up to 9999 lines.
_NUM_WIDTH = 4


class ViewFlexModuleTool(Tool):
    name = "view_flex_module"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Read the Python source backing a flex catalog. Output is "
                "the source text with each line prefixed by its 1-based "
                "line number — use those line numbers when calling "
                "replace_flex_module_lines, and use the exact text (without "
                "the prefix) when calling replace_in_flex_module. Default "
                "view is the whole file; pass start_line + end_line for a "
                "slice (inclusive, 1-based)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "catalog_name": {
                        "type": "string",
                        "description": "Name of the flex catalog whose module to view.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional 1-based first line to include (default 1).",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional 1-based last line to include (default: end of file).",
                    },
                },
                "required": ["catalog_name"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        catalog_name = input.get("catalog_name")
        if not isinstance(catalog_name, str) or not catalog_name.strip():
            raise ToolError("input.catalog_name is required and must be a non-empty string")
        start_line = input.get("start_line")
        end_line = input.get("end_line")
        if start_line is not None and not isinstance(start_line, int):
            raise ToolError("input.start_line, if provided, must be an integer")
        if end_line is not None and not isinstance(end_line, int):
            raise ToolError("input.end_line, if provided, must be an integer")

        try:
            r = requests.get(
                f"{ctx.core_url}/flex-modules/{catalog_name}", timeout=10
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        if r.status_code == 404:
            raise ToolError(f"no flex module for catalog {catalog_name!r}")
        try:
            body = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")
        if r.status_code != 200:
            raise ToolError(
                f"{body.get('error', 'request failed')} (HTTP {r.status_code})"
            )

        source: str = body["source_text"]
        lines = source.splitlines()
        total = len(lines)
        # Default range = whole file. Clamp to [1, total] so users
        # can't accidentally see an empty payload from off-by-ones.
        lo = 1 if start_line is None else max(1, start_line)
        hi = total if end_line is None else min(total, end_line)
        if hi < lo:
            raise ToolError(
                f"empty range: start_line={lo} > end_line={hi} (file has {total} lines)"
            )

        numbered = "\n".join(
            f"{str(i).rjust(_NUM_WIDTH)}\t{lines[i - 1]}"
            for i in range(lo, hi + 1)
        )
        payload = {
            "catalog_name": catalog_name,
            "line_count": total,
            "showing": {"start_line": lo, "end_line": hi},
            "source": numbered,
        }
        return json.dumps(payload, indent=2)
