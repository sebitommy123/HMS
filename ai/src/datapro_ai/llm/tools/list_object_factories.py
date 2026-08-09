"""list_object_factories tool — GET /object-factories with optional filters."""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class ListObjectFactoriesTool(Tool):
    name = "list_object_factories"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "List object factories. A factory is a (data_source, "
                "object_type) pair that says \"this data source produces "
                "objects of this type.\" Filter by any combination of: "
                "catalog name (joins through data_sources), data_source_id, "
                "object_type_id."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "catalog": {
                        "type": "string",
                        "description": "Filter to factories under this catalog (joined through data sources).",
                    },
                    "data_source_id": {
                        "type": "string",
                        "description": "Filter to factories on this specific data source (UUID).",
                    },
                    "object_type_id": {
                        "type": "string",
                        "description": "Filter to factories producing this object type (UUID).",
                    },
                },
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        params: dict[str, str] = {}
        catalog = input.get("catalog")
        if isinstance(catalog, str) and catalog.strip():
            params["catalog"] = catalog.strip()
        data_source_id = input.get("data_source_id")
        if isinstance(data_source_id, str) and data_source_id.strip():
            params["data_source_id"] = data_source_id.strip()
        type_id = input.get("object_type_id")
        if isinstance(type_id, str) and type_id.strip():
            params["object_type_id"] = type_id.strip()

        try:
            r = requests.get(
                f"{ctx.core_url}/object-factories", params=params, timeout=10
            )
        except requests.RequestException as exc:
            raise ToolError(f"could not reach Core: {exc}") from exc
        if not r.ok:
            raise ToolError(f"Core returned HTTP {r.status_code}: {r.text[:500]}")
        return json.dumps(r.json(), indent=2)
