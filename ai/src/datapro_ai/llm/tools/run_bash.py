"""run_bash tool — execute an arbitrary shell command.

Single-user, local-dev posture: the agent runs commands as whoever launched
the AI service. There is no sandbox. The point is to let the model poke at
Postgres directly (psql), curl Core's API, ls the filesystem, etc.
Guardrails: wall-clock timeout, output truncation. No allowlist — if you don't
want the agent running arbitrary commands, don't register this tool.
"""

import os
import signal
import subprocess
import time
from typing import Any

from datapro_ai.llm.tools.base import Tool, ToolContext, ToolError

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
# Truncate per-stream output sent back to the model. Past this the model gets
# a marker telling it the result was truncated — it can re-run with a tighter
# command (head/grep/wc) to get less.
MAX_OUTPUT_BYTES = 16_000


class RunBashTool(Tool):
    name = "run_bash"

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Execute an arbitrary bash command on the AI service's host. "
                "Useful for poking the underlying systems directly: psql against "
                "Postgres, curl against Core or Trino, ls/cat on disk. "
                "The command runs in /bin/bash -c with the AI service's "
                "permissions and environment. There is a wall-clock timeout "
                "and output is truncated at "
                f"{MAX_OUTPUT_BYTES:,} bytes per stream. Returns stdout, stderr, "
                "exit code, and elapsed time. Use this for diagnostics and "
                "exploration; prefer run_raw_trino_query for data queries since it goes "
                "through Trino's federation layer."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run. Passed verbatim to /bin/bash -c.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": f"Wall-clock budget. Defaults to {DEFAULT_TIMEOUT_SECONDS}; max {MAX_TIMEOUT_SECONDS}.",
                        "default": DEFAULT_TIMEOUT_SECONDS,
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory. Defaults to the AI service's CWD.",
                    },
                },
                "required": ["command"],
            },
        }

    def execute(self, ctx: ToolContext, input: dict[str, Any]) -> str:
        command = input.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolError("input.command is required and must be a non-empty string")

        timeout_seconds = int(input.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        if timeout_seconds <= 0 or timeout_seconds > MAX_TIMEOUT_SECONDS:
            raise ToolError(
                f"timeout_seconds must be in [1, {MAX_TIMEOUT_SECONDS}]; got {timeout_seconds}"
            )

        cwd = input.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise ToolError("input.cwd, if provided, must be a string")

        cancel_event = ctx.cancel_event
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                ["/bin/bash", "-c", command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=cwd,
                # Run in its own session so the child is a process-group leader.
                # bash -c may fork children (pipelines, backgrounded jobs); this
                # lets us kill the WHOLE tree on cancel/timeout, not just bash.
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            # /bin/bash not present — extremely unusual, but surface clearly.
            raise ToolError(f"could not run /bin/bash: {exc}") from exc

        cancelled = False
        timed_out = False
        stdout = ""
        stderr = ""
        # Poll communicate() on a short timeout so we can react to a cancel (the
        # user hit "Stop") or the wall-clock budget WHILE the command runs.
        # communicate() drains the pipes, avoiding the fill-the-pipe deadlock a
        # plain poll() loop would hit on chatty commands; per the stdlib, output
        # is not lost across repeated timed-out calls.
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.2)
                break  # process exited on its own
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                elif time.monotonic() - started >= timeout_seconds:
                    timed_out = True
                if not (cancelled or timed_out):
                    continue
                _kill_tree(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                break

        elapsed = time.monotonic() - started
        return _format_result(
            exit_code=None if (cancelled or timed_out) else proc.returncode,
            stdout=_trunc(_as_text(stdout)),
            stderr=_trunc(_as_text(stderr)),
            elapsed_seconds=elapsed,
            timed_out=timed_out,
            cancelled=cancelled,
            timeout_seconds=timeout_seconds,
        )


def _kill_tree(proc: "subprocess.Popen") -> None:
    """Kill the subprocess and everything it spawned. Because we started it with
    start_new_session=True, the child's pid is its process-group id, so we can
    signal the whole group. SIGTERM first (let it clean up), then SIGKILL if it
    doesn't exit promptly."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return  # already reaped
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        proc.terminate()
    try:
        proc.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _trunc(s: str) -> str:
    encoded = s.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return s
    head = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    omitted = len(encoded) - MAX_OUTPUT_BYTES
    return f"{head}\n\n[... {omitted:,} more bytes truncated]"


def _format_result(
    *,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    elapsed_seconds: float,
    timed_out: bool,
    timeout_seconds: int,
    cancelled: bool = False,
) -> str:
    """Render a result the model can read. Plain text with labeled sections —
    cheaper than JSON for the model to scan and easier to skim in the UI."""
    parts: list[str] = []
    if cancelled:
        parts.append("CANCELLED by user — the running command was killed")
    elif timed_out:
        parts.append(f"TIMED OUT after {timeout_seconds}s")
    else:
        parts.append(f"exit code: {exit_code}")
    parts.append(f"elapsed: {elapsed_seconds:.2f}s")
    parts.append(f"--- stdout ---\n{stdout if stdout else '(empty)'}")
    parts.append(f"--- stderr ---\n{stderr if stderr else '(empty)'}")
    return "\n".join(parts)
