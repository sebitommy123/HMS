"""preview_flex_module — materialize-and-sample a draft module without
committing.

POSTs the source to /flex-modules/preview, which spins up a transient
catalog in Trino, walks its declared tables, samples rows, and tears
the catalog back down. Returns a structured view of what each table
would look like.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class PreviewFlexModuleTool(Tool):
    name = "preview_flex_module"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Try a flex module source in a throwaway Trino catalog "
                "and return the schemas it declares plus a sample of "
                "rows from each table. Use this before set_flex_module / "
                "create_flex_catalog to confirm a draft actually produces "
                "the rows you expect. Does NOT persist anything."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Full Python source of the draft module to preview.",
                    },
                    "sample_limit": {
                        "type": "integer",
                        "description": "Max rows to sample per table (default 10, max 500).",
                    },
                },
                "required": ["source"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        source = input.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ToolError("input.source is required and must be a non-empty string")
        sample_limit = input.get("sample_limit", 10)
        if not isinstance(sample_limit, int) or sample_limit < 1:
            raise ToolError("input.sample_limit, if provided, must be a positive integer")

        try:
            r = requests.post(
                f"{ctx.core_url}/flex-modules/preview",
                json={"source": source, "sample_limit": sample_limit},
                # Preview spins up a transient catalog + worker, so give
                # it longer than the simple-write paths above.
                timeout=60,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc

        try:
            body = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")

        if r.status_code == 400 and body.get("error") == "invalid_python":
            raise ToolError(f"invalid Python syntax: {body.get('details')}")
        if r.status_code != 200:
            raise ToolError(
                f"{body.get('error', 'request failed')}: {body.get('details', '')} "
                f"(HTTP {r.status_code})"
            )
        return json.dumps({"http_status": r.status_code, "response": body}, indent=2)
