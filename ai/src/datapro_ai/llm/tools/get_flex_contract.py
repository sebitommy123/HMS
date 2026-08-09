"""get_flex_contract — returns the canonical flex module contract.

Use BEFORE writing a flex module. The response carries the full
contract.py source, the type-mapping helpers, the eight supported
Trino type strings, and a worked example module — everything needed
to one-shot a syntactically and semantically correct flex module
without iterating against preview errors.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class GetFlexContractTool(Tool):
    name = "get_flex_contract"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Fetch the canonical flex module contract — exact function "
                "signatures, the eight supported Trino type strings, the "
                "contract.py source, and a worked example module. CALL "
                "THIS FIRST when you're about to author or significantly "
                "edit a flex module. It's the single source of truth; "
                "guessing from preview errors burns a lot of round trips. "
                "No arguments."
            ),
            "input_schema": {"type": "object", "properties": {}},
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        try:
            r = requests.get(f"{ctx.core_url}/flex-contract", timeout=10)
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        try:
            body = r.json()
        except ValueError:
            raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")
        if r.status_code != 200:
            raise ToolError(
                f"{body.get('error', 'request failed')} (HTTP {r.status_code})"
            )
        return json.dumps(body, indent=2)
