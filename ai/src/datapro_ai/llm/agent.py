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
from dataclasses import dataclass
from typing import Any, Iterator

import anthropic
from sqlalchemy.orm import Session

from datapro_ai.config import Config, DEFAULT_EFFORT
from datapro_ai.llm.tools.base import ToolContext, ToolError, ToolRegistry
from datapro_ai.models import Conversation, Message, Role


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


def run_agent_stream(
    *,
    conversation: Conversation,
    user_message: Message,
    tools: ToolRegistry,
    client: anthropic.Anthropic,
    session: Session,
    cfg: Config,
    cancel_event: threading.Event | None = None,
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

    tool_ctx = ToolContext(core_url=cfg.core_url)
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
            if conversation.system_prompt:
                kwargs["system"] = conversation.system_prompt

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
                    output, is_error = _execute_tool(tu, tools, tool_ctx)
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
    from datapro_ai.llm.tools.add_object_factory_column import AddObjectFactoryColumnTool
    from datapro_ai.llm.tools.add_trait_to_object_type import AddTraitToObjectTypeTool
    from datapro_ai.llm.tools.create_catalog import CreateCatalogTool
    from datapro_ai.llm.tools.create_flex_catalog import CreateFlexCatalogTool
    from datapro_ai.llm.tools.create_object_factory import CreateObjectFactoryTool
    from datapro_ai.llm.tools.create_object_type import CreateObjectTypeTool
    from datapro_ai.llm.tools.delete_object_factory import DeleteObjectFactoryTool
    from datapro_ai.llm.tools.delete_object_type import DeleteObjectTypeTool
    from datapro_ai.llm.tools.get_catalog import GetCatalogTool
    from datapro_ai.llm.tools.get_data_source import GetDataSourceTool
    from datapro_ai.llm.tools.get_flex_contract import GetFlexContractTool
    from datapro_ai.llm.tools.get_data_source_columns import (
        GetDataSourceColumnsTool,
    )
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
    from datapro_ai.llm.tools.remove_object_factory_column import (
        RemoveObjectFactoryColumnTool,
    )
    from datapro_ai.llm.tools.remove_trait_from_object_type import (
        RemoveTraitFromObjectTypeTool,
    )
    from datapro_ai.llm.tools.replace_flex_module_lines import (
        ReplaceFlexModuleLinesTool,
    )
    from datapro_ai.llm.tools.replace_in_flex_module import ReplaceInFlexModuleTool
    from datapro_ai.llm.tools.run_bash import RunBashTool
    from datapro_ai.llm.tools.run_raw_trino_query import RunRawTrinoQueryTool
    from datapro_ai.llm.tools.set_factory_trait_config import (
        SetFactoryTraitConfigTool,
    )
    from datapro_ai.llm.tools.set_flex_module import SetFlexModuleTool
    from datapro_ai.llm.tools.set_object_factory_description import (
        SetObjectFactoryDescriptionTool,
    )
    from datapro_ai.llm.tools.set_object_factory_use_all_columns import (
        SetObjectFactoryUseAllColumnsTool,
    )
    from datapro_ai.llm.tools.view_flex_module import ViewFlexModuleTool
    from datapro_ai.llm.tools.update_catalog import UpdateCatalogTool
    from datapro_ai.llm.tools.update_object_factory_column import (
        UpdateObjectFactoryColumnTool,
    )
    from datapro_ai.llm.tools.update_object_type import UpdateObjectTypeTool

    return ToolRegistry(
        [
            ListCatalogsTool(),
            GetCatalogTool(),
            InspectTableTool(),
            CreateCatalogTool(),
            UpdateCatalogTool(),
            ListObjectTypesTool(),
            GetObjectTypeTool(),
            CreateObjectTypeTool(),
            UpdateObjectTypeTool(),
            DeleteObjectTypeTool(),
            # Trait management on the object type — the fixed registry
            # lives in Core, so list_traits is the discovery surface.
            ListTraitsTool(),
            AddTraitToObjectTypeTool(),
            RemoveTraitFromObjectTypeTool(),
            # Data sources are sync-owned (discovered by Core's reconciler),
            # so the agent only reads them — no create/update/delete.
            ListDataSourcesTool(),
            GetDataSourceTool(),
            GetDataSourceColumnsTool(),
            ListObjectFactoriesTool(),
            GetObjectFactoryTool(),
            CreateObjectFactoryTool(),
            # Object-factory mutations are split into focused single-purpose
            # tools so the model can reason about each action atomically
            # instead of being tempted to bundle unrelated changes into one
            # PATCH (which leads to lazy / surprising rewrites).
            SetObjectFactoryDescriptionTool(),
            SetObjectFactoryUseAllColumnsTool(),
            AddObjectFactoryColumnTool(),
            RemoveObjectFactoryColumnTool(),
            UpdateObjectFactoryColumnTool(),
            SetFactoryTraitConfigTool(),
            DeleteObjectFactoryTool(),
            # Semantic query layer — runs through the new /query endpoint.
            # Prefer these over run_raw_trino_query for any object access;
            # raw SQL is the debugging escape hatch.
            PreviewQueryPlanTool(),
            QueryObjectsTool(),
            RunRawTrinoQueryTool(),
            # Flex catalog authoring surface — the AI's main path to
            # creating + editing Python-backed Trino catalogs. View
            # before editing; preview before committing; prefer the
            # focused substring / line-range edits over set_flex_module
            # full-overwrites.
            GetFlexContractTool(),
            CreateFlexCatalogTool(),
            ViewFlexModuleTool(),
            ReplaceInFlexModuleTool(),
            ReplaceFlexModuleLinesTool(),
            SetFlexModuleTool(),
            PreviewFlexModuleTool(),
            RunBashTool(),
        ]
    )
