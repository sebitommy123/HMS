"""replace_in_flex_module — substring replacement in a flex module.

Calls Core's POST /flex-modules/{name}/replace endpoint, which
enforces that ``old_text`` appears exactly once. If it's missing or
ambiguous, the tool surfaces a clear error so the model can adjust
(e.g. expand the substring with surrounding context).
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class ReplaceInFlexModuleTool(Tool):
    name = "replace_in_flex_module"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Edit a flex module by substring replacement. `old_text` "
                "must appear EXACTLY ONCE in the current source — if it's "
                "missing or ambiguous you'll get an error. Quote the "
                "substring verbatim (no line-number prefixes, no surrounding "
                "whitespace edits unless you mean them). For larger or "
                "multi-occurrence edits, prefer replace_flex_module_lines. "
                "The change hot-swaps live — the next query against the "
                "catalog uses the new module."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "catalog_name": {"type": "string"},
                    "old_text": {
                        "type": "string",
                        "description": (
                            "The exact substring to replace. Must appear "
                            "once and only once in the current source."
                        ),
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text. Pass \"\" to delete.",
                    },
                },
                "required": ["catalog_name", "old_text", "new_text"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        catalog_name = input.get("catalog_name")
        old_text = input.get("old_text")
        new_text = input.get("new_text")
        if not isinstance(catalog_name, str) or not catalog_name.strip():
            raise ToolError("input.catalog_name is required and must be a non-empty string")
        if not isinstance(old_text, str) or old_text == "":
            raise ToolError("input.old_text is required and must be a non-empty string")
        if not isinstance(new_text, str):
            raise ToolError("input.new_text is required and must be a string (may be empty)")

        try:
            r = requests.post(
                f"{ctx.core_url}/flex-modules/{catalog_name}/replace",
                json={"old_text": old_text, "new_text": new_text},
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
        if r.status_code == 400 and body.get("error") == "old_text_not_found":
            raise ToolError(
                "old_text does not appear in the current module — view the source "
                "first with view_flex_module and quote a real substring."
            )
        if r.status_code == 400 and body.get("error") == "old_text_ambiguous":
            occurrences = body.get("occurrences")
            raise ToolError(
                f"old_text appears {occurrences} times in the module. Either expand "
                "the substring with surrounding context until it's unique, or use "
                "replace_flex_module_lines with explicit line numbers."
            )
        if r.status_code == 400 and body.get("error") == "invalid_python":
            raise ToolError(
                f"the resulting source has invalid Python syntax: {body.get('details')}"
            )
        if r.status_code != 200:
            raise ToolError(
                f"{body.get('error', 'request failed')} (HTTP {r.status_code})"
            )

        return json.dumps({"http_status": r.status_code, "response": body}, indent=2)
