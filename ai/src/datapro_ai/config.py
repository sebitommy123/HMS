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
        default_origins = [
            f"http://{host}:{port}"
            for host in ("localhost", "127.0.0.1")
            for port in (5173, 5174, 5175, 5176, 4173, 4174, 4175, 4176)
        ]
        cors_raw = os.environ.get("CORS_ORIGINS", ",".join(default_origins))

        # CORE_URL is mandatory: the AI service is useless without Core, and a
        # silent localhost default turns a misconfigured port into a debugging
        # scavenger hunt (the agent hits whatever else is on :5001, gets 405s,
        # and retries). Fail loud instead. scripts/dev-up.sh always sets it.
        core_url = os.environ.get("CORE_URL")
        if not core_url:
            raise RuntimeError(
                "CORE_URL is required — the AI service can't run without Core. "
                "Start via scripts/dev-up.sh, or set it explicitly "
                "(e.g. CORE_URL=http://127.0.0.1:5001)."
            )

        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql+psycopg://datapro:datapro@localhost:5434/datapro_ai",
            ),
            core_url=core_url,
            # Empty string is allowed for local-tooling smoke tests that don't
            # actually call Anthropic. Real usage requires a real key.
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            model=os.environ.get("AI_MODEL", DEFAULT_MODEL),
            max_tokens=int(os.environ.get("AI_MAX_TOKENS", "8192")),
            # Hard upper bound on how many tool round-trips the agent loop will
            # take before forcing termination. Prevents pathological loops while
            # leaving room for legitimate multi-step tasks.
            max_tool_iterations=int(os.environ.get("AI_MAX_TOOL_ITERATIONS", "10")),
            cors_origins=tuple(o.strip() for o in cors_raw.split(",") if o.strip()),
        )
