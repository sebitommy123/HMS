"""read_observation — pull the full data of one on-screen observation.

The drill-in half of the "see what the user sees" pair: get_current_view lists
what's on screen (keys + descriptions); this returns the actual data for one
key — e.g. the rows a data-source preview returned, a query result, or a flex
module being edited. Data is a snapshot of what the user saw and may be
truncated for size."""

import json
from typing import Any

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError


class ReadObservationTool(Tool):
    name = "read_observation"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Read the full data of one on-screen observation the user is "
                "looking at — the rows a data-source 'preview' returned, the "
                "results on a query page, a flex module being edited, etc. Get "
                "the available keys from get_current_view first. Returns the "
                "observation's data (a snapshot of what the user saw; may be "
                "truncated for size)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": (
                            "The observation key, as listed by get_current_view."
                        ),
                    },
                },
                "required": ["key"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        key = input.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ToolError("input.key is required and must be a non-empty string")
        if ctx.view is None:
            raise ToolError(
                "no live view available — the user's chat panel may be closed"
            )
        obs = ctx.view.read(key)
        if obs is None:
            available = ctx.view.observation_keys()
            raise ToolError(
                f"no observation {key!r} in the current view. Available keys: "
                + (", ".join(available) if available else "(none)")
                + ". Call get_current_view to see what's on screen."
            )
        return json.dumps(obs, indent=2, default=str)
