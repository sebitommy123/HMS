"""get_catalog tool — fetch details for one registered catalog."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class GetCatalogTool(Tool):
    name = "get_catalog"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Fetch details for a single registered catalog by name: its "
                "connector, status, last error, and configuration properties. "
                "Use this to diagnose a broken catalog or to inspect its "
                "configuration. Use list_catalogs first if you don't know "
                "the name."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The catalog name as registered in Core (e.g. 'tpch_demo', 'sec_edgar').",
                    },
                },
                "required": ["name"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        name = input.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolError("input.name is required and must be a non-empty string")
        try:
            r = requests.get(
                f"{ctx.core_url}/catalogs/{name}",
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        if r.status_code == 404:
            raise ToolError(f"no catalog named {name!r} is registered")
        if not r.ok:
            raise ToolError(f"Core returned HTTP {r.status_code}: {r.text[:500]}")
        body = r.json()
        # Mask credential-shaped properties so the model doesn't echo them back.
        if isinstance(body.get("properties"), dict):
            body["properties"] = _mask_secrets(body["properties"])
        return json.dumps(body, indent=2)


def _mask_secrets(props: dict[str, Any]) -> dict[str, Any]:
    sensitive = ("password", "secret", "token", "key")
    return {
        k: ("••••••••" if any(s in k.lower() for s in sensitive) and v else v)
        for k, v in props.items()
    }
