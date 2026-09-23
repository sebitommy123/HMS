"""The agent loop, as a generator that yields events.

Both endpoints (SSE streaming + JSON collect-then-return) consume the same
generator. Events are emitted at meaningful boundaries so the UI can render
partial state:

    StreamStart            — the agent is about to call the model for the first
                             turn this run. Carries the conversation_id and the
                             user message id (the one we just persisted).

    AssistantStart         — a new assistant turn just started streaming. The
                             text_deltas that follow belong to this message.
                             Carries no id yet — id appears on AssistantPersisted
                             when the streaming finishes and we save to Postgres.

    TextDelta              — partial assistant text. Concatenate into the
                             in-progress assistant message bubble.

    AssistantPersisted     — assistant turn finished; persisted to Postgres.
                             Carries the saved message dict (with id, content
                             blocks verbatim, stop_reason, usage).

    ToolExecuting          — about to run one tool. Carries name + input.

    ToolResult             — one tool finished. Carries the matching
                             tool_use_id, the result content, and is_error.

    ToolResultsPersisted   — the user-role tool_result message has been saved.
                             Carries the saved message dict.

    TitleUpdated           — auto-naming kicked in and renamed the conversation.

    StreamDone             — terminal event. Carries final_stop_reason +
                             iteration count + truncation flag.

    StreamError            — fatal: the loop is bailing out. Carries the error.

Why a generator: the streaming endpoint serializes each event to SSE; the
JSON endpoint collects them all and returns a summary. One implementation,
two transports — no drift.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

import anthropic
from sqlalchemy.orm import Session

from datapro_ai.config import Config, DEFAULT_EFFORT
from datapro_ai.llm.tools.base import ToolContext, ToolError, ToolRegistry
from datapro_ai.models import Conversation, Message, Role
from datapro_ai.view_context import ViewAccessor


# ---- Event types -----------------------------------------------------------


@dataclass(frozen=True)
class StreamEvent:
    """Marker base class so type checkers can narrow."""

    type: str


@dataclass(frozen=True)
class StreamStart(StreamEvent):
    conversation_id: str
    user_message: dict

    def __init__(self, *, conversation_id: str, user_message: dict) -> None:
        object.__setattr__(self, "type", "stream_start")
        object.__setattr__(self, "conversation_id", conversation_id)
        object.__setattr__(self, "user_message", user_message)


@dataclass(frozen=True)
class AssistantStart(StreamEvent):
    iteration: int

    def __init__(self, *, iteration: int) -> None:
        object.__setattr__(self, "type", "assistant_start")
        object.__setattr__(self, "iteration", iteration)


@dataclass(frozen=True)
class TextDelta(StreamEvent):
    text: str

    def __init__(self, *, text: str) -> None:
        object.__setattr__(self, "type", "text_delta")
        object.__setattr__(self, "text", text)


@dataclass(frozen=True)
class AssistantPersisted(StreamEvent):
    message: dict

    def __init__(self, *, message: dict) -> None:
        object.__setattr__(self, "type", "assistant_persisted")
        object.__setattr__(self, "message", message)


@dataclass(frozen=True)
class ToolExecuting(StreamEvent):
    tool_use_id: str
    name: str
    input: dict

    def __init__(self, *, tool_use_id: str, name: str, input: dict) -> None:
        object.__setattr__(self, "type", "tool_executing")
        object.__setattr__(self, "tool_use_id", tool_use_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "input", input)


@dataclass(frozen=True)
class ToolResult(StreamEvent):
    tool_use_id: str
    output: str
    is_error: bool

    def __init__(self, *, tool_use_id: str, output: str, is_error: bool) -> None:
        object.__setattr__(self, "type", "tool_result")
        object.__setattr__(self, "tool_use_id", tool_use_id)
        object.__setattr__(self, "output", output)
        object.__setattr__(self, "is_error", is_error)


@dataclass(frozen=True)
class Heartbeat(StreamEvent):
    """Emitted periodically while a tool runs. Tools execute off the stream
    thread, so without this the SSE stream would go silent during a long tool
    and the client's stall detector would wrongly declare it dead ("stream went
    quiet"). Carries enough context for the UI to optionally show "still running
    <tool> (Ns)"."""

    tool_use_id: str
    name: str
    elapsed_seconds: float

    def __init__(self, *, tool_use_id: str, name: str, elapsed_seconds: float) -> None:
        object.__setattr__(self, "type", "heartbeat")
        object.__setattr__(self, "tool_use_id", tool_use_id)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "elapsed_seconds", elapsed_seconds)


@dataclass(frozen=True)
class ToolResultsPersisted(StreamEvent):
    message: dict

    def __init__(self, *, message: dict) -> None:
        object.__setattr__(self, "type", "tool_results_persisted")
        object.__setattr__(self, "message", message)


@dataclass(frozen=True)
class TitleUpdated(StreamEvent):
    title: str

    def __init__(self, *, title: str) -> None:
        object.__setattr__(self, "type", "title_updated")
        object.__setattr__(self, "title", title)


@dataclass(frozen=True)
class StreamDone(StreamEvent):
    final_stop_reason: str
    iterations: int
    truncated_by_iteration_cap: bool

    def __init__(
        self, *, final_stop_reason: str, iterations: int, truncated_by_iteration_cap: bool
    ) -> None:
        object.__setattr__(self, "type", "stream_done")
        object.__setattr__(self, "final_stop_reason", final_stop_reason)
        object.__setattr__(self, "iterations", iterations)
        object.__setattr__(self, "truncated_by_iteration_cap", truncated_by_iteration_cap)


@dataclass(frozen=True)
class StreamError(StreamEvent):
    error: str
    details: str | None

    def __init__(self, *, error: str, details: str | None = None) -> None:
        object.__setattr__(self, "type", "stream_error")
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "details", details)


def event_to_dict(event: StreamEvent) -> dict[str, Any]:
    """Serialize an event for the wire (SSE data field or JSON list entry)."""
    return {k: v for k, v in event.__dict__.items()}


# ---- The loop --------------------------------------------------------------


DEFAULT_TITLE = "New conversation"
TITLE_MODEL = "claude-haiku-4-5"


# Always-present system prompt describing the staging workflow. The agent has NO
# way to change production directly — its only write path is the chat's
# changeset of actions, which it builds, tests in a throwaway staging
# environment, and finally promotes. This prompt is prepended to every turn so
# the model reliably works in that model rather than reaching for (now absent)
# direct-mutation tools.
STAGING_SYSTEM_PROMPT = """\
You are DataPro's data-modeling agent. You help the user shape a deterministic \
semantic layer over their data: catalogs (Trino connections), object types, \
traits (identity/temporal), and object factories (which data source produces \
which object type).

HOW YOU WORK — read this carefully, it governs everything:

- You NEVER change production directly. Your only write capability is to append \
ACTIONS to this chat's staging changeset. There are no tools that mutate \
production; do not look for any.
- The read tools (list_catalogs, list_object_types, list_data_sources, \
inspect_table, query_objects, run_raw_trino_query, get_flex_contract, ...) all \
read PRODUCTION. Use them to understand what exists before you stage anything.
- The write tools all start with `stage_` (create/update/delete catalogs, object \
types, traits, factories, flex modules). Each appends one action to the \
changeset. `show_changeset` shows the ordered list; `remove_action`, \
`reorder_actions`, and `clear_changeset` edit it.

REFERENCES between actions:
- Every `stage_add_*` (create) action takes a `handle` — a short symbolic name \
you choose (e.g. "sales_cat", "customer_type"). Later actions reference things \
you created by that handle (e.g. `target_handle`, `catalog_handle`, \
`object_type_handle`).
- To act on something that ALREADY EXISTS IN PRODUCTION, reference it by its \
production id instead (`target_prod_id`, `catalog_name`, `object_type_prod_id`).
- A factory's data source is named structurally: the catalog (by handle or prod \
name) plus schema + table. Discover valid (schema, table) pairs with \
list_data_sources against the relevant catalog — but note a catalog you only \
just STAGED won't appear in production reads; its tables are discovered when the \
stage is built. If unsure of a staged catalog's tables, use query_stage / \
build_and_test_stage to surface errors and iterate.

TEST — you cannot apply:
- `query_stage` builds a fresh staging environment off current production, \
replays your changeset into it, runs one semantic query against real Trino, then \
tears it down. Use it to prove your staged actions produce the objects you \
expect. If a catalog has a bad password or a factory is misconfigured, this is \
where you see it — fix the offending action and re-run. Nothing you do here \
touches production.
- `save_stage_test` records an acceptance test (an object type + a minimum \
object count). `build_and_test_stage` re-runs all saved tests. Save tests as you \
go so the user can re-verify before promoting.
- You have NO tool to apply/promote to production, and that is deliberate. \
Promotion is the user's decision: they review the staged actions and click \
"Apply" in the UI. Your job ends at a well-tested changeset — when you believe \
it is ready, TELL the user it's ready to apply and let them promote it. Never \
imply that you can or will push to production yourself.

KEEP THE CHANGESET CLEAN — it's a plan, not a history:
- The changeset describes the END STATE you want to build, not the sequence of \
edits you went through to get there. It should always read as the minimal, \
coherent set of actions that produces exactly that state — nothing an outside \
reviewer would find contradictory.
- When you change your mind about something that is STILL in the unapplied \
changeset, FIX THE SOURCE ACTION: remove it (`remove_action`) and re-add a \
corrected one, or edit the field on that action. Do NOT be lazy and append a \
second action whose only job is to undo or patch an earlier one in the same \
changeset. For example, never leave "create a factory WITH an identity trait" \
followed by "update that factory to REMOVE identity" — that's a self-contradiction; \
instead edit the create so it never had identity in the first place.
- A compensating/corrective action is only legitimate when you are changing \
something that ALREADY EXISTS IN PRODUCTION — there you genuinely can't edit the \
past, so you add an action. Inside your own not-yet-applied changeset there is no \
past to preserve, so there is never a reason to keep an action that only reverses \
another. After any such rework, glance at `show_changeset` and make sure every \
action still earns its place.
- Also: don't invent requirements. Don't add traits, columns, or config the user \
didn't ask for on a hunch — if you think something like an identity trait is \
warranted, propose it rather than silently baking it in (that initiative is what \
usually creates the churn above).

ALWAYS annotate: every `stage_*` action takes a `note` argument. Write one short, \
plain-English sentence for the user describing what that action does and why \
(e.g. "Registers the sales Postgres as a catalog so we can read its tables"). \
The user reads these to review the changeset, so make them clear and specific — \
never leave the note blank.

WORKING STYLE:
- Prefer many small, single-purpose actions over bundling.
- Keep the user informed: describe what you're staging, use `show_changeset` when \
it helps them review, and once tests pass tell them it's ready for them to apply.
- Think in terms of the end state the user wants, then express it as an ordered \
list of actions that builds it."""


def run_agent_stream(
    *,
    conversation: Conversation,
    user_message: Message,
    tools: ToolRegistry,
    client: anthropic.Anthropic,
    session: Session,
    cfg: Config,
    cancel_event: threading.Event | None = None,
    view_context: ViewAccessor | None = None,
) -> Iterator[StreamEvent]:
    """Drive the agent until terminal, yielding events along the way.

    If ``cancel_event`` is provided and set, the loop exits at the next
    boundary it checks: between iterations, between tool calls, and during
    text-delta streaming. On cancel mid-stream, whatever assistant text we've
    already buffered is persisted so the conversation isn't left mid-thought.
    """

    yield StreamStart(
        conversation_id=str(conversation.id),
        user_message=user_message.to_dict(),
    )

    from datapro_ai.staging.changeset_store import ChangesetStore

    tool_ctx = ToolContext(
        core_url=cfg.core_url,
        cancel_event=cancel_event,
        view=view_context,
        # The agent's only write path: it appends actions here and builds/tests/
        # applies a staging env from them — it never mutates Core/prod directly.
        changeset_store=ChangesetStore(session, conversation),
    )
    iterations = 0
    final_stop_reason = "unknown"
    truncated = False

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    try:
        for iteration in range(cfg.max_tool_iterations):
            if cancelled():
                final_stop_reason = "cancelled"
                break

            iterations = iteration + 1
            yield AssistantStart(iteration=iteration)

            api_messages = [
                {"role": m.role, "content": m.content} for m in conversation.messages
            ]

            kwargs: dict[str, Any] = {
                "model": conversation.model,
                "max_tokens": cfg.max_tokens,
                "tools": tools.definitions(),
                "messages": api_messages,
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": DEFAULT_EFFORT},
            }
            # System = the conversation's base prompt plus a fresh one-line hint
            # about what the user is currently looking at, so the agent always
            # knows WHERE they are without spending a tool call. Details stay
            # behind get_current_view / read_observation to protect the context
            # window. Rebuilt each turn since the user may have navigated.
            system_parts: list[str] = [STAGING_SYSTEM_PROMPT]
            if conversation.system_prompt:
                system_parts.append(conversation.system_prompt)
            view_hint = view_context.system_hint() if view_context is not None else None
            if view_hint:
                system_parts.append(view_hint)
            if system_parts:
                kwargs["system"] = "\n\n".join(system_parts)

            # Stream the response. text_stream gives us only the visible text
            # deltas (no thinking_delta noise); we still get the structured
            # message at the end via get_final_message().
            accumulated_text = ""
            cancelled_mid_stream = False
            final_message: Any = None
            with client.messages.stream(**kwargs) as stream:
                for chunk in stream.text_stream:
                    if cancelled():
                        cancelled_mid_stream = True
                        break
                    if chunk:
                        accumulated_text += chunk
                        yield TextDelta(text=chunk)
                if not cancelled_mid_stream:
                    final_message = stream.get_final_message()

            if cancelled_mid_stream:
                # Persist whatever the model emitted so far as a partial
                # assistant message — better than dropping it on the floor.
                if accumulated_text:
                    partial_msg = _persist_message(
                        session=session,
                        conversation=conversation,
                        role=Role.ASSISTANT,
                        content=[{"type": "text", "text": accumulated_text}],
                        stop_reason="cancelled",
                        usage=None,
                    )
                    yield AssistantPersisted(message=partial_msg.to_dict())
                final_stop_reason = "cancelled"
                break

            # Some content-block fields the SDK exposes are output-only (e.g.
            # text.parsed_output for structured-outputs) and the API rejects
            # them on the next request's `messages`. exclude_none=True drops
            # those before we persist + replay.
            assistant_blocks = [
                block.model_dump(exclude_none=True) for block in final_message.content
            ]
            assistant_msg = _persist_message(
                session=session,
                conversation=conversation,
                role=Role.ASSISTANT,
                content=assistant_blocks,
                stop_reason=final_message.stop_reason,
                usage=final_message.usage,
            )
            yield AssistantPersisted(message=assistant_msg.to_dict())

            stop_reason = final_message.stop_reason or "unknown"
            final_stop_reason = stop_reason

            if stop_reason == "tool_use":
                tool_use_blocks = [b for b in final_message.content if b.type == "tool_use"]
                if not tool_use_blocks:
                    break

                tool_result_blocks: list[dict[str, Any]] = []
                cancelled_between_tools = False
                for tu in tool_use_blocks:
                    if cancelled():
                        cancelled_between_tools = True
                        # Synthesise an is_error result for the unexecuted tool
                        # use — the API requires every tool_use to have a
                        # matching tool_result before the next user turn.
                        tool_result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu.id,
                                "content": "tool execution cancelled by user",
                                "is_error": True,
                            }
                        )
                        continue
                    yield ToolExecuting(
                        tool_use_id=tu.id, name=tu.name, input=tu.input
                    )
                    # Run the tool off this thread, emitting heartbeats while it
                    # works and bailing out at once if the user hits Stop.
                    tool_out: dict[str, Any] = {}
                    yield from _run_tool_streaming(
                        tu, tools, tool_ctx, cancel_event, tool_out
                    )
                    if tool_out.get("cancelled"):
                        cancelled_between_tools = True
                        output, is_error = "tool execution cancelled by user", True
                    else:
                        output, is_error = tool_out["result"]
                    yield ToolResult(
                        tool_use_id=tu.id, output=output, is_error=is_error
                    )
                    tool_result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": output,
                            "is_error": is_error,
                        }
                    )

                tool_results_msg = _persist_message(
                    session=session,
                    conversation=conversation,
                    role=Role.USER,
                    content=tool_result_blocks,
                    stop_reason=None,
                    usage=None,
                )
                yield ToolResultsPersisted(message=tool_results_msg.to_dict())
                if cancelled_between_tools:
                    final_stop_reason = "cancelled"
                    break
                continue

            if stop_reason == "pause_turn":
                # Server-side tool paused — re-send history as-is.
                continue

            # Terminal: end_turn / refusal / max_tokens / anything else.
            break
        else:
            # The for-loop completed without break — we hit the iteration cap.
            truncated = True

        # Auto-name on first agent turn if the title is still default. Skip
        # when cancelled — a cancelled turn isn't a great basis for a title.
        if conversation.title == DEFAULT_TITLE and final_stop_reason != "cancelled":
            try:
                new_title = _generate_title(client, conversation)
                if new_title:
                    conversation.title = new_title
                    session.commit()
                    yield TitleUpdated(title=new_title)
            except Exception:
                # Title generation is a nicety; never let it fail the turn.
                pass

    except anthropic.APIStatusError as exc:
        yield StreamError(
            error=f"anthropic_status_{exc.status_code}",
            details=str(getattr(exc, "message", exc)),
        )
        return
    except anthropic.APIConnectionError as exc:
        yield StreamError(error="anthropic_unreachable", details=str(exc))
        return

    yield StreamDone(
        final_stop_reason=final_stop_reason,
        iterations=iterations,
        truncated_by_iteration_cap=truncated,
    )


# ---- Helpers ---------------------------------------------------------------


def _execute_tool(
    tool_use: Any, tools: ToolRegistry, ctx: ToolContext
) -> tuple[str, bool]:
    tool = tools.get(tool_use.name)
    if tool is None:
        return f"unknown tool: {tool_use.name}", True
    try:
        return tool.execute(ctx, tool_use.input), False
    except ToolError as exc:
        return str(exc), True


# How often to emit a heartbeat while a tool runs (keeps the SSE stream from
# looking "quiet"), and how fast we poll for cancellation while waiting.
_TOOL_HEARTBEAT_SECONDS = 10.0
_TOOL_POLL_SECONDS = 0.2


def _run_tool_streaming(
    tool_use: Any,
    tools: ToolRegistry,
    ctx: ToolContext,
    cancel_event: threading.Event | None,
    out: dict[str, Any],
) -> Iterator[StreamEvent]:
    """Run a tool OFF the stream thread, yielding Heartbeat events while it
    works. Two problems this solves at once:

      * "Stop" is responsive: if the user cancels, we stop waiting immediately
        rather than blocking until the tool's own timeout. The abandoned worker
        thread carries its own timeout (HTTP/Trino clients), and run_bash
        additionally kills its process group via ctx.cancel_event, so nothing
        runs forever — its (discarded) result just lands later.
      * The stream never goes silent: a long tool would otherwise emit no
        events and trip the client's stall detector.

    Writes into ``out``: ``out["result"] = (output, is_error)`` on completion,
    or ``out["cancelled"] = True`` if the user cancelled. Re-raises an
    unexpected tool exception (ToolError is already turned into an is_error
    result by _execute_tool)."""
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["result"] = _execute_tool(tool_use, tools, ctx)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
            box["exc"] = exc

    thread = threading.Thread(
        target=worker, name=f"tool-{tool_use.name}", daemon=True
    )
    started = time.monotonic()
    last_beat = started
    thread.start()

    while True:
        thread.join(timeout=_TOOL_POLL_SECONDS)
        if not thread.is_alive():
            break
        if cancel_event is not None and cancel_event.is_set():
            out["cancelled"] = True
            return
        now = time.monotonic()
        if now - last_beat >= _TOOL_HEARTBEAT_SECONDS:
            last_beat = now
            yield Heartbeat(
                tool_use_id=tool_use.id,
                name=tool_use.name,
                elapsed_seconds=now - started,
            )

    if "exc" in box:
        raise box["exc"]
    out["result"] = box["result"]


def _persist_message(
    *,
    session: Session,
    conversation: Conversation,
    role: Role,
    content: list[dict[str, Any]],
    stop_reason: str | None,
    usage: Any,
) -> Message:
    next_position = len(conversation.messages)
    message = Message(
        conversation_id=conversation.id,
        position=next_position,
        role=role.value,
        content=content,
        stop_reason=stop_reason,
        usage_input_tokens=usage.input_tokens if usage else None,
        usage_output_tokens=usage.output_tokens if usage else None,
        usage_cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) if usage else None,
        usage_cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", None) if usage else None,
    )
    session.add(message)
    session.flush()
    conversation.messages.append(message)
    session.commit()
    return message


def add_user_message(
    *, conversation: Conversation, session: Session, text: str
) -> Message:
    return _persist_message(
        session=session,
        conversation=conversation,
        role=Role.USER,
        content=[{"type": "text", "text": text}],
        stop_reason=None,
        usage=None,
    )


def _generate_title(
    client: anthropic.Anthropic, conversation: Conversation
) -> str | None:
    """Use a fast cheap model to produce a 3-6 word title for the conversation.

    We pass just the first user message + first assistant text — that's plenty
    for a title, and keeps the call to <500 input tokens.
    """
    first_user_text = ""
    first_assistant_text = ""
    for m in conversation.messages:
        if m.role == "user" and not first_user_text:
            for block in m.content or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    first_user_text = str(block.get("text", ""))[:1000]
                    break
        elif m.role == "assistant" and not first_assistant_text:
            for block in m.content or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    first_assistant_text = str(block.get("text", ""))[:1000]
                    break
        if first_user_text and first_assistant_text:
            break

    if not first_user_text:
        return None

    prompt = (
        "Generate a 3 to 6 word title for the conversation below. "
        "Return ONLY the title text — no quotes, no punctuation, no preamble.\n\n"
        f"USER: {first_user_text}\n\n"
        f"ASSISTANT: {first_assistant_text}"
    )

    response = client.messages.create(
        model=TITLE_MODEL,
        max_tokens=32,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in response.content:
        if block.type == "text":
            title = block.text.strip().strip('"').strip("'").strip(".").strip()
            # Cap at 80 chars defensively.
            if title:
                return title[:80]
    return None


def default_tools() -> ToolRegistry:
    # READ tools (all against production — the agent inspects, then stages).
    from datapro_ai.llm.tools.get_current_view import GetCurrentViewTool
    from datapro_ai.llm.tools.read_observation import ReadObservationTool
    from datapro_ai.llm.tools.get_catalog import GetCatalogTool
    from datapro_ai.llm.tools.get_data_source import GetDataSourceTool
    from datapro_ai.llm.tools.get_flex_contract import GetFlexContractTool
    from datapro_ai.llm.tools.get_data_source_columns import GetDataSourceColumnsTool
    from datapro_ai.llm.tools.get_object_factory import GetObjectFactoryTool
    from datapro_ai.llm.tools.get_object_type import GetObjectTypeTool
    from datapro_ai.llm.tools.inspect_table import InspectTableTool
    from datapro_ai.llm.tools.list_catalogs import ListCatalogsTool
    from datapro_ai.llm.tools.list_data_sources import ListDataSourcesTool
    from datapro_ai.llm.tools.list_object_factories import ListObjectFactoriesTool
    from datapro_ai.llm.tools.list_object_types import ListObjectTypesTool
    from datapro_ai.llm.tools.list_traits import ListTraitsTool
    from datapro_ai.llm.tools.preview_flex_module import PreviewFlexModuleTool
    from datapro_ai.llm.tools.preview_query_plan import PreviewQueryPlanTool
    from datapro_ai.llm.tools.query_objects import QueryObjectsTool
    from datapro_ai.llm.tools.run_bash import RunBashTool
    from datapro_ai.llm.tools.run_raw_trino_query import RunRawTrinoQueryTool
    from datapro_ai.llm.tools.view_flex_module import ViewFlexModuleTool
    # WRITE tools: staging changeset only (the agent's sole write path).
    from datapro_ai.llm.tools.staging_tools import staging_tools

    return ToolRegistry(
        [
            # ---- READ surface (all against PRODUCTION) ---------------------
            # The agent inspects prod to decide what actions to stage. It never
            # writes here — its only write path is the staging changeset below.
            GetCurrentViewTool(),
            ReadObservationTool(),
            ListCatalogsTool(),
            GetCatalogTool(),
            InspectTableTool(),
            ListObjectTypesTool(),
            GetObjectTypeTool(),
            ListTraitsTool(),
            ListDataSourcesTool(),
            GetDataSourceTool(),
            GetDataSourceColumnsTool(),
            ListObjectFactoriesTool(),
            GetObjectFactoryTool(),
            # Semantic query + raw SQL against PROD (read-only inspection). To
            # test staged changes, use query_stage / build_and_test_stage.
            PreviewQueryPlanTool(),
            QueryObjectsTool(),
            RunRawTrinoQueryTool(),
            # Flex read/preview helpers (preview runs in a transient catalog).
            GetFlexContractTool(),
            ViewFlexModuleTool(),
            PreviewFlexModuleTool(),
            RunBashTool(),
            # ---- WRITE surface: staging changeset only ---------------------
            # Every mutation is an ACTION appended to the chat's changeset. The
            # agent builds/tests an ephemeral staging env from it and promotes
            # atomically — it can never touch production directly.
            *staging_tools(),
        ]
    )
