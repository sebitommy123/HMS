import pytest

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError, ToolRegistry


class _DummyTool:
    name = "echo"

    def definition(self):
        return {
            "name": self.name,
            "description": "Echo the input string back.",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict) -> str:
        return input["text"]


def test_registry_deduplicates_by_name():
    tools = [_DummyTool(), _DummyTool()]
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry(tools)


def test_registry_lookup_and_definitions():
    registry = ToolRegistry([_DummyTool()])
    assert "echo" in registry.names()
    assert registry.get("echo") is not None
    assert registry.get("nonexistent") is None
    defs = registry.definitions()
    assert len(defs) == 1
    assert defs[0]["name"] == "echo"


def test_tool_error_is_stringifiable():
    err = ToolError("something broke")
    assert str(err) == "something broke"


def test_tool_protocol_accepts_dummy():
    """Tool is a Protocol; a class with the right shape should satisfy it."""
    t: Tool = _DummyTool()
    assert t.name == "echo"
