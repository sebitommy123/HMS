"""get_current_view — see what the user is currently looking at.

The chat panel is docked beside the user's view in the same browser, so the
browser publishes a live description of their screen (route + on-screen
observations). This returns the compact index; read_observation pulls a
specific item's data. Keeping payloads behind read_observation is deliberate —
it keeps the context window small."""

import json
from typing import Any

from datapro_ai.llm.tools.base import Tool, ToolContext


class GetCurrentViewTool(Tool):
    name = "get_current_view"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "See what the user is currently looking at in the app — you can "
                "effectively 'see' their screen. Returns the current page/route, "
                "the entity on screen (e.g. which data source or catalog), and a "
                "list of available on-screen observations, each with a key and a "
                "one-line description but NOT its data. Call this whenever the "
                "user refers to what they're viewing ('this table', 'that "
                "result', 'here', 'the preview', 'what I'm looking at'), then use "
                "read_observation(key) to inspect a specific item. No arguments."
            ),
            "input_schema": {"type": "object", "properties": {}},
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        manifest = ctx.view.manifest() if ctx.view is not None else None
        if manifest is None:
            return json.dumps(
                {
                    "current_view": None,
                    "note": (
                        "No live view available — the user's chat panel may be "
                        "closed, or they haven't navigated yet. Ask what they're "
                        "looking at, or use the catalog / data-source / query "
                        "tools directly."
                    ),
                },
                indent=2,
            )
        return json.dumps(manifest, indent=2, default=str)
