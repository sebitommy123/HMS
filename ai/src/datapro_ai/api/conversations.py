from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from datapro_ai.config import Config
from datapro_ai.models import Conversation, Message

bp = Blueprint("conversations", __name__)


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    system_prompt: str | None = Field(default=None, max_length=100_000)


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    system_prompt: str | None = Field(default=None, max_length=100_000)


def _session():
    return current_app.extensions["db_session"]()


def _cfg() -> Config:
    return current_app.config["DATAPRO_AI"]


@bp.get("/conversations")
def list_conversations():
    with _session() as session:
        rows = (
            session.execute(
                select(Conversation).order_by(Conversation.updated_at.desc())
            )
            .scalars()
            .all()
        )
        # Annotate each row with the count of messages and the preview of the
        # latest user message so the UI can render a list view without
        # fanning out N+1 queries.
        bodies = []
        for c in rows:
            preview, count = _summary(session, c.id)
            body = c.to_dict()
            body["message_count"] = count
            body["preview"] = preview
            bodies.append(body)
        return jsonify(bodies)


@bp.get("/conversations/<uuid:conversation_id>")
def get_conversation(conversation_id):
    with _session() as session:
        c = session.get(Conversation, conversation_id)
        if c is None:
            return jsonify({"error": "not_found", "id": str(conversation_id)}), 404
        body = c.to_dict()
        body["messages"] = [m.to_dict() for m in c.messages]
        return jsonify(body)


@bp.post("/conversations")
def create_conversation():
    try:
        payload = ConversationCreateRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
        return _validation_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    cfg = _cfg()
    with _session() as session:
        c = Conversation(
            title=payload.title or "New conversation",
            model=payload.model or cfg.model,
            system_prompt=payload.system_prompt,
        )
        session.add(c)
        session.commit()
        return jsonify(c.to_dict()), 201


@bp.patch("/conversations/<uuid:conversation_id>")
def update_conversation(conversation_id):
    try:
        payload = ConversationUpdateRequest.model_validate(request.get_json(force=True))
    except ValidationError as exc:
        return _validation_response(exc)
    except Exception as exc:
        return jsonify({"error": "invalid_json", "details": str(exc)}), 400

    with _session() as session:
        c = session.get(Conversation, conversation_id)
        if c is None:
            return jsonify({"error": "not_found", "id": str(conversation_id)}), 404
        if payload.title is not None:
            c.title = payload.title
        if payload.system_prompt is not None:
            c.system_prompt = payload.system_prompt
        session.commit()
        return jsonify(c.to_dict())


@bp.delete("/conversations/<uuid:conversation_id>")
def delete_conversation(conversation_id):
    with _session() as session:
        c = session.get(Conversation, conversation_id)
        if c is None:
            return jsonify({"error": "not_found", "id": str(conversation_id)}), 404
        session.delete(c)
        session.commit()
        return jsonify({"deleted": str(conversation_id)})


def _summary(session, conversation_id):
    """Return (preview, count) for the conversation list view."""
    msgs = (
        session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.position.asc())
        )
        .scalars()
        .all()
    )
    if not msgs:
        return None, 0
    # Find the first user-text block as a friendly preview.
    preview = None
    for m in msgs:
        if m.role == "user":
            for block in m.content or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    preview = block.get("text", "")[:200]
                    break
            if preview is not None:
                break
    return preview, len(msgs)


def _validation_response(exc: ValidationError):
    return (
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
