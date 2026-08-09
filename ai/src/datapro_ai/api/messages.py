"""Agent-loop endpoints, all driven by a shared in-memory turn registry.

  POST /conversations/{id}/messages         — JSON; creates a turn, drains
                                                synchronously, returns summary.
  POST /conversations/{id}/messages/stream  — SSE; creates a turn (or rejects
                                                with 409 if one is in flight),
                                                subscribes, streams events.
  GET  /conversations/{id}/messages/stream  — SSE; subscribes to the in-flight
                                                turn if one exists. 204 if not.
  POST /conversations/{id}/messages/cancel  — Cancels the in-flight turn.

The turn runs in a background thread (TurnRegistry creates it). Subscribers
are HTTP handlers — they can disconnect freely (page reload, browser close)
without affecting the running agent, and a fresh subscriber gets the full
event replay so a reloaded page picks up where it left off.
"""

import json
import queue

import anthropic
from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    stream_with_context,
)
from pydantic import BaseModel, Field, ValidationError

from datapro_ai.api.health import core_reachable
from datapro_ai.config import Config
from datapro_ai.llm.agent import (
    StreamDone,
    StreamError,
    add_user_message,
    default_tools,
    event_to_dict,
    run_agent_stream,
)
from datapro_ai.models import Conversation, Message
from datapro_ai.turn_registry import ActiveTurn, TurnRegistry, iter_subscription
from datapro_ai.view_context import ViewAccessor, ViewContextStore, normalize_and_cap

bp = Blueprint("messages", __name__)


# SSE comment line — clients ignore these, but proxies / browsers see bytes
# moving and don't kill the connection during long quiet stretches.
HEARTBEAT_SECONDS = 15.0


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)


def _cfg() -> Config:
    return current_app.config["DATAPRO_AI"]


def _session_factory():
    return current_app.extensions["db_session"]


def _anthropic_client() -> anthropic.Anthropic:
    return current_app.extensions["anthropic_client"]


def _registry() -> TurnRegistry:
    return current_app.extensions["turn_registry"]


def _view_store() -> ViewContextStore:
    return current_app.extensions["view_context_store"]


def _validate_request():
    try:
        payload = SendMessageRequest.model_validate(request.get_json(force=True))
        return payload, None
    except ValidationError as exc:
        return None, (
            jsonify(
                {
                    "error": "invalid_request",
                    "details": [
                        {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                        for e in exc.errors()
                    ],
                }
            ),
            400,
        )
    except Exception as exc:
        return None, (
            jsonify({"error": "invalid_json", "details": str(exc)}),
            400,
        )


def _key_check_response():
    cfg = _cfg()
    if not cfg.anthropic_api_key:
        return (
            jsonify(
                {
                    "error": "anthropic_not_configured",
                    "details": "ANTHROPIC_API_KEY is not set on the AI service.",
                }
            ),
            503,
        )
    return None


def _core_check_response():
    """Refuse to start a turn if Core is down. Every agent tool goes through
    Core (catalogs, queries, data sources, …), so a turn with a dead Core would
    just fail on the first tool call — better to reject up front."""
    cfg = _cfg()
    if not core_reachable(cfg.core_url):
        return (
            jsonify(
                {
                    "error": "core_unreachable",
                    "details": (
                        f"Core is not reachable at {cfg.core_url}. The agent's "
                        "tools all go through Core, so it can't run without it — "
                        "start Core and try again."
                    ),
                }
            ),
            503,
        )
    return None


# ---- Turn lifecycle --------------------------------------------------------


def _start_turn(
    conversation_id, text: str
) -> tuple[ActiveTurn | None, tuple | None]:
    """Try to start a new turn for ``conversation_id``. Returns ``(turn, None)``
    on success, or ``(None, (json_response, status))`` on conflict / 404."""
    registry = _registry()
    existing = registry.get(str(conversation_id))
    if existing is not None and not existing.done:
        return None, (
            jsonify(
                {
                    "error": "turn_in_progress",
                    "details": (
                        "An agent turn is already running for this conversation. "
                        "Subscribe to GET /conversations/{id}/messages/stream to "
                        "watch it, or POST .../cancel to stop it."
                    ),
                }
            ),
            409,
        )

    # Drop any finished prior turn so this conversation starts clean.
    registry.drop(str(conversation_id))

    SessionLocal = _session_factory()
    # Validate the conversation exists + persist the user message on the
    # request thread (using a request-scoped session that we close
    # immediately). The agent thread opens its own session.
    with SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return None, (
                jsonify({"error": "not_found", "id": str(conversation_id)}),
                404,
            )
        add_user_message(conversation=conversation, session=session, text=text)

    cfg = _cfg()
    client = _anthropic_client()
    tools = default_tools()
    conv_id_str = str(conversation_id)
    # Bind a live view over what the user is looking at (captured here, on the
    # request thread, so the agent thread can read it without an app context).
    view_accessor = ViewAccessor(_view_store(), conv_id_str)

    def runner(turn: ActiveTurn) -> None:
        # New session for the agent thread — SQLAlchemy sessions are not
        # safe to share across threads.
        session = SessionLocal()
        try:
            conv = session.get(Conversation, conversation_id)
            if conv is None:
                turn.publish(StreamError(error="not_found", details=conv_id_str))
                return
            # The user message was just persisted; it's the last one in history.
            user_msg = conv.messages[-1]
            try:
                for ev in run_agent_stream(
                    conversation=conv,
                    user_message=user_msg,
                    tools=tools,
                    client=client,
                    session=session,
                    cfg=cfg,
                    cancel_event=turn.cancel_event,
                    view_context=view_accessor,
                ):
                    turn.publish(ev)
            except Exception as exc:
                turn.publish(
                    StreamError(error=type(exc).__name__, details=str(exc))
                )
                turn.publish(
                    StreamDone(
                        final_stop_reason="error",
                        iterations=0,
                        truncated_by_iteration_cap=False,
                    )
                )
        finally:
            session.close()

    turn, _started = registry.try_start(conv_id_str, runner)
    return turn, None


# ---- JSON endpoint (drains a turn synchronously) ---------------------------


@bp.post("/conversations/<uuid:conversation_id>/messages")
def send_message(conversation_id):
    key_error = _key_check_response()
    if key_error:
        return key_error

    core_error = _core_check_response()
    if core_error:
        return core_error

    payload, validation_error = _validate_request()
    if validation_error:
        return validation_error

    SessionLocal = _session_factory()
    with SessionLocal() as session:
        first_new_position = _count_messages(session, conversation_id)

    turn, err = _start_turn(conversation_id, payload.text)
    if err is not None:
        return err
    assert turn is not None

    # Drain synchronously.
    events: list = []
    q = turn.subscribe()
    try:
        # Use a large timeout — no heartbeats needed for the JSON path. Just
        # block until each event arrives.
        for ev in iter_subscription(q, heartbeat_seconds=3600):
            if ev is None:
                # Heartbeat tick — ignore for JSON path.
                continue
            events.append(ev)
    finally:
        turn.unsubscribe(q)

    for ev in events:
        if ev.type == "stream_error":
            d = event_to_dict(ev)
            return jsonify({"error": d["error"], "details": d["details"]}), 502

    with SessionLocal() as session:
        new_messages = [
            m.to_dict()
            for m in session.query(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.position >= first_new_position)
            .order_by(Message.position.asc())
            .all()
        ]

    done = next((e for e in events if e.type == "stream_done"), None)
    title_update = next((e for e in events if e.type == "title_updated"), None)

    return jsonify(
        {
            "conversation_id": str(conversation_id),
            "messages": new_messages,
            "final_stop_reason": event_to_dict(done)["final_stop_reason"]
            if done
            else "unknown",
            "iterations": event_to_dict(done)["iterations"] if done else 0,
            "truncated_by_iteration_cap": event_to_dict(done)[
                "truncated_by_iteration_cap"
            ]
            if done
            else False,
            "title_updated_to": event_to_dict(title_update)["title"]
            if title_update
            else None,
        }
    )


def _count_messages(session, conversation_id) -> int:
    return (
        session.query(Message)
        .where(Message.conversation_id == conversation_id)
        .count()
    )


# ---- SSE endpoint (starts + subscribes) ------------------------------------


@bp.post("/conversations/<uuid:conversation_id>/messages/stream")
def send_message_stream(conversation_id):
    key_error = _key_check_response()
    if key_error:
        return key_error

    core_error = _core_check_response()
    if core_error:
        return core_error

    payload, validation_error = _validate_request()
    if validation_error:
        return validation_error

    turn, err = _start_turn(conversation_id, payload.text)
    if err is not None:
        return err
    assert turn is not None
    return _sse_response_for_turn(turn)


# ---- SSE endpoint (subscribe to in-flight turn, for reload-resume) ---------


@bp.get("/conversations/<uuid:conversation_id>/messages/stream")
def subscribe_active_stream(conversation_id):
    registry = _registry()
    turn = registry.get(str(conversation_id))
    if turn is None:
        # 204 — there's no active turn for this conversation. The client
        # should fall back to GET /conversations/{id} for the persisted state.
        return Response(status=204)
    return _sse_response_for_turn(turn)


# ---- Cancel endpoint -------------------------------------------------------


@bp.post("/conversations/<uuid:conversation_id>/messages/cancel")
def cancel_active_stream(conversation_id):
    registry = _registry()
    turn = registry.get(str(conversation_id))
    if turn is None or turn.done:
        return jsonify({"cancelled": False, "reason": "no_active_turn"}), 404
    turn.cancel()
    return jsonify({"cancelled": True})


# ---- View context (what the user is looking at) ----------------------------


@bp.put("/conversations/<uuid:conversation_id>/view-context")
def put_view_context(conversation_id):
    """The browser publishes what the user is currently looking at — route +
    on-screen observations — for this conversation. The agent reads it live via
    get_current_view / read_observation. Fire-and-forget and defensive: a
    malformed body yields a minimal view rather than an error, so a client bug
    can't break publishing."""
    raw = request.get_json(force=True, silent=True)
    _view_store().put(str(conversation_id), normalize_and_cap(raw))
    return ("", 204)


# ---- SSE rendering ---------------------------------------------------------


def _sse_response_for_turn(turn: ActiveTurn) -> Response:
    def event_stream():
        q = turn.subscribe()
        try:
            for ev in iter_subscription(q, heartbeat_seconds=HEARTBEAT_SECONDS):
                if ev is None:
                    yield ": heartbeat\n\n"
                    continue
                yield _sse(ev.type, event_to_dict(ev))
        finally:
            turn.unsubscribe(q)

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse(event_type: str, payload: dict) -> str:
    data = json.dumps(payload, default=str, separators=(",", ":"))
    return f"event: {event_type}\ndata: {data}\n\n"


# Re-export for backwards-compat with tests / older callers.
__all__ = ["bp", "queue"]
