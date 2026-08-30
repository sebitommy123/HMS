import os
from dataclasses import dataclass


# Per the claude-api skill: claude-opus-4-8 is the default unless the operator
# explicitly chooses another model.
DEFAULT_MODEL = "claude-opus-4-8"

# Per the skill: high is the recommended minimum for intelligence-sensitive work.
# Chat-with-tools is intelligence-sensitive; xhigh is the best setting for
# agentic / coding use cases and pairs naturally with what the operator chat does.
DEFAULT_EFFORT = "high"


@dataclass(frozen=True)
class Config:
    database_url: str
    core_url: str
    anthropic_api_key: str
    model: str
    max_tokens: int
    max_tool_iterations: int
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Config":
        # The default covers this checkout's own UI dev server (UI_PORT comes
        # from its stack slot — see scripts/hms.py) plus vite's preview port.
        ui_port = os.environ.get("UI_PORT", "5003")
        default_origins = [
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1")
            for port in (ui_port, "4173")
        ]
        cors_raw = os.environ.get("CORS_ORIGINS", ",".join(default_origins))

        # CORE_URL is mandatory: the AI service is useless without Core, and a
        # silent localhost default turns a misconfigured port into a debugging
        # scavenger hunt (the agent hits whatever else is on that port, gets
        # 405s, and retries). Worse now that every checkout runs its own Core on
        # its own port — a default would mean talking to another worktree's.
        # Fail loud instead. scripts/dev-up.sh always sets it.
        core_url = os.environ.get("CORE_URL")
        if not core_url:
            raise RuntimeError(
                "CORE_URL is required — the AI service can't run without Core. "
                'Start via scripts/dev-up.sh, or `eval "$(scripts/hms.py env)"` '
                "to get this checkout's."
            )

        # Same reasoning as CORE_URL: per-checkout, so no fallback.
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL is required. Start via scripts/dev-up.sh, or set it "
                'explicitly — `eval "$(scripts/hms.py env)"` exports the right one '
                "for this checkout as AI_DATABASE_URL."
            )

        return cls(
            database_url=database_url,
            core_url=core_url,
            # Empty string is allowed for local-tooling smoke tests that don't
            # actually call Anthropic. Real usage requires a real key.
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            model=os.environ.get("AI_MODEL", DEFAULT_MODEL),
            max_tokens=int(os.environ.get("AI_MAX_TOKENS", "8192")),
            # Hard upper bound on how many tool round-trips the agent loop will
            # take before forcing termination. A backstop against pathological
            # loops, set high enough that real multi-step tasks don't hit it —
            # and when a turn DOES stop here, the UI surfaces it with a Continue
            # affordance rather than failing silently.
            max_tool_iterations=int(os.environ.get("AI_MAX_TOOL_ITERATIONS", "100")),
            cors_origins=tuple(o.strip() for o in cors_raw.split(",") if o.strip()),
        )
