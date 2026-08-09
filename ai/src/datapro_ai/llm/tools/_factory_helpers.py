"""Shared helpers for object-factory mutation tools."""

from typing import Any

import requests

from datapro_ai.llm.tools.base import ToolContext, ToolError


def fetch_factory(ctx: ToolContext, factory_id: str) -> dict[str, Any]:
    """GET /object-factories/{id}; raise ToolError on any non-200."""
    try:
        r = requests.get(
            f"{ctx.core_url}/object-factories/{factory_id}", timeout=10
        )
    except requests.RequestException as exc:
        raise ToolError(f"could not reach Core: {exc}") from exc
    if r.status_code == 400:
        raise ToolError(f"{factory_id!r} is not a valid UUID")
    if r.status_code == 404:
        raise ToolError(f"no object factory with id {factory_id!r}")
    if not r.ok:
        raise ToolError(f"Core returned HTTP {r.status_code}: {r.text[:500]}")
    return r.json()


def patch_column_spec(
    ctx: ToolContext, factory_id: str, new_spec: list[str]
) -> dict[str, Any]:
    """PATCH /object-factories/{id} with a new column_spec. Returns parsed body."""
    try:
        r = requests.patch(
            f"{ctx.core_url}/object-factories/{factory_id}",
            json={"column_spec": new_spec},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise ToolError(f"could not reach Core: {exc}") from exc
    try:
        parsed = r.json()
    except ValueError:
        raise ToolError(f"Core returned non-JSON response: {r.text[:500]}")
    if not r.ok:
        raise ToolError(f"Core returned HTTP {r.status_code}: {parsed}")
    return parsed


def require_int_index(input: dict[str, Any], current_len: int) -> int:
    """Validate input.index is an integer in [0, current_len)."""
    idx = input.get("index")
    if not isinstance(idx, int) or isinstance(idx, bool):
        raise ToolError("input.index is required and must be a non-negative integer")
    if idx < 0 or idx >= current_len:
        raise ToolError(
            f"input.index {idx} is out of range; current list has {current_len} columns"
        )
    return idx


def require_non_empty_string(input: dict[str, Any], key: str) -> str:
    val = input.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ToolError(f"input.{key} is required and must be a non-empty string")
    return val
