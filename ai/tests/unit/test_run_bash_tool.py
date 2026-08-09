"""run_bash unit tests. Runs real subprocesses — no mocks."""

import pytest

from datapro_ai.llm.tools.base import ToolContext, ToolError
from datapro_ai.llm.tools.run_bash import MAX_OUTPUT_BYTES, RunBashTool


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(core_url="http://unused")


def test_run_bash_captures_stdout(ctx):
    out = RunBashTool().execute(ctx, {"command": "echo hello world"})
    assert "exit code: 0" in out
    assert "hello world" in out
    assert "--- stdout ---" in out
    assert "--- stderr ---" in out


def test_run_bash_captures_stderr_and_exit_code(ctx):
    out = RunBashTool().execute(
        ctx, {"command": "echo oops 1>&2; exit 7"}
    )
    assert "exit code: 7" in out
    assert "oops" in out
    # stdout section is present but empty for this case.
    assert "(empty)" in out


def test_run_bash_honors_cwd(ctx, tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    out = RunBashTool().execute(
        ctx, {"command": "ls", "cwd": str(tmp_path)}
    )
    assert "exit code: 0" in out
    assert "marker.txt" in out


def test_run_bash_times_out(ctx):
    out = RunBashTool().execute(
        ctx, {"command": "sleep 5", "timeout_seconds": 1}
    )
    assert "TIMED OUT after 1s" in out


def test_run_bash_truncates_huge_output(ctx):
    # 64KB of 'a' chars — well over the 16KB cap.
    out = RunBashTool().execute(
        ctx, {"command": "yes a | head -c 65536"}
    )
    assert "truncated" in out
    # The full 64KB shouldn't fit into the output.
    assert len(out) < MAX_OUTPUT_BYTES * 2 + 1000


def test_run_bash_rejects_empty_command(ctx):
    with pytest.raises(ToolError, match="command"):
        RunBashTool().execute(ctx, {"command": ""})


def test_run_bash_rejects_invalid_timeout(ctx):
    with pytest.raises(ToolError, match="timeout"):
        RunBashTool().execute(
            ctx, {"command": "true", "timeout_seconds": 9999}
        )


def test_run_bash_definition_is_registered_in_default_tools():
    from datapro_ai.llm.agent import default_tools

    names = default_tools().names()
    assert "run_bash" in names
