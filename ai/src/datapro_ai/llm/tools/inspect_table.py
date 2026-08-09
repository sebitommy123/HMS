"""inspect_table tool — `DESCRIBE catalog.schema.table` and a row sample.

Convenience wrapper around two SQL calls so the agent can explore a table's
shape in one round-trip instead of two.
"""

import json
from typing import Any

import requests

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class InspectTableTool(Tool):
    name = "inspect_table"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Inspect a table's schema and a small row sample. Returns the "
                "columns (with Trino types) and the first few rows. Useful "
                "before constructing a more specific query — saves you from "
                "running two separate DESCRIBE + SELECT round-trips. "
                "Identifiers must already be unquoted (e.g. "
                "sec_edgar.public.companies)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "catalog": {
                        "type": "string",
                        "description": "Catalog name (e.g. 'sec_edgar').",
                    },
                    "schema": {
                        "type": "string",
                        "description": "Schema name within the catalog (e.g. 'public', 'sec_financial').",
                    },
                    "table": {
                        "type": "string",
                        "description": "Table name (e.g. 'companies', 'num').",
                    },
                    "sample_rows": {
                        "type": "integer",
                        "description": "How many sample rows to return (default 5; max 50). Set to 0 to skip sampling.",
                        "default": 5,
                    },
                },
                "required": ["catalog", "schema", "table"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        catalog = _require_ident(input, "catalog")
        schema = _require_ident(input, "schema")
        table = _require_ident(input, "table")
        sample_rows = int(input.get("sample_rows", 5))
        if sample_rows < 0:
            sample_rows = 0
        if sample_rows > 50:
            sample_rows = 50

        fqtn = f'"{catalog}"."{schema}"."{table}"'

        describe = _query(ctx, f"DESCRIBE {fqtn}", timeout_seconds=30)
        result: dict[str, Any] = {
            "fully_qualified_name": fqtn,
            "columns": [
                {"name": row[0], "type": row[1]}
                for row in describe.get("rows", [])
            ],
        }
        if sample_rows > 0:
            sample = _query(
                ctx,
                f"SELECT * FROM {fqtn} LIMIT {sample_rows}",
                timeout_seconds=30,
            )
            result["sample_columns"] = sample.get("columns", [])
            result["sample_rows"] = sample.get("rows", [])
            if sample.get("truncated"):
                result["sample_truncated"] = True
        return json.dumps(result, indent=2, default=str)


def _query(ctx: ToolContext, sql: str, *, timeout_seconds: int) -> dict[str, Any]:
    try:
        r = requests.post(
            f"{ctx.core_url}/raw-trino-query",
            json={"sql": sql, "timeout_seconds": timeout_seconds, "max_rows": 100},
            timeout=timeout_seconds + 10,
        )
    except requests.RequestException as exc:
        raise ToolError(f"could not reach Core: {exc}") from exc
    try:
        body = r.json()
    except ValueError:
        raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")
    if not r.ok:
        raise ToolError(
            f"Trino rejected `{sql}`: {body.get('details', body)}"
        )
    return body


def _require_ident(input: dict[str, Any], field: str) -> str:
    value = input.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"input.{field} is required and must be a non-empty string")
    # Disallow embedded quotes so the FQTN we construct is safe — operators
    # don't put quotes in identifiers anyway, and the model shouldn't either.
    if any(ch in value for ch in '"`\''):
        raise ToolError(f"input.{field} must not contain quote characters; got {value!r}")
    return value
