"""Tool framework — a tiny protocol that knows how to:

1. produce the Anthropic tool definition (name, description, input_schema)
2. execute against a parsed input dict
3. return a stringified result that goes into a tool_result block

Tools are stateless. Per-request state (the Core URL, an HTTP session) is passed
into `execute` via the `ToolContext` argument so individual tool classes don't
hold on to anything.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolContext:
    """Per-invocation state passed to every tool's execute()."""

    core_url: str


@dataclass(frozen=True)
class ToolError(Exception):
    """Raised when a tool can't complete. The agent surfaces the message to the
    model as a tool_result with is_error=true so the model can recover."""

    message: str

    def __str__(self) -> str:
        return self.message


class Tool(Protocol):
    """Anthropic-compatible tool. Implementations are simple classes/dataclasses
    that define `definition()` and `execute()`."""

    name: str

    def definition(self) -> dict[str, Any]:
        """Return the JSON dict Anthropic's tools= parameter expects."""
        ...

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        """Run the tool. Return a stringified result for the tool_result block.

        Raise ToolError for recoverable problems (bad input, upstream rejection)
        so the agent surfaces them to the model. Let unexpected exceptions
        propagate — they should bubble up and fail the request, not be hidden
        from the model as a normal tool_result.
        """
        ...


class ToolRegistry:
    """Holds the set of tools available to the agent. Looked up by name."""

    def __init__(self, tools: list[Tool]) -> None:
        seen: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in seen:
                raise ValueError(f"duplicate tool name: {tool.name}")
            seen[tool.name] = tool
        self._tools = seen

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())
