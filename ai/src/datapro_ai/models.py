"""Persistence model for chat sessions.

Design notes:

- `Message.content` stores Anthropic's content-blocks array **verbatim** as JSON.
  This means tool_use, tool_result, text, thinking, etc. blocks round-trip
  unchanged when we replay history to the model — no translation layer to keep
  in sync with the SDK.
- Tool invocations are NOT a separate table. They live inside `messages.content`
  as `tool_use` blocks (assistant role) and `tool_result` blocks (user role).
  The UI walks the same JSON to render them. One source of truth per turn.
"""

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from datapro_ai.db import Base


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"  # reserved for mid-conversation system messages (Opus 4.8+)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String, nullable=False, default="New conversation")
    model: Mapped[str] = mapped_column(String, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.position",
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "title": self.title,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Monotonic order within a conversation. We assign explicitly rather than
    # relying on created_at to avoid ambiguity when many messages land in the
    # same agent-loop iteration.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    # Anthropic content-blocks array, verbatim. Always a list of dicts, even
    # for plain-text user turns (we normalise to [{"type": "text", "text": ...}]).
    content: Mapped[list] = mapped_column(JSON, nullable=False)
    # For assistant turns only; null for user/system.
    stop_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # Usage echoed back from Anthropic for cost tracking.
    usage_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "conversation_id": str(self.conversation_id),
            "position": self.position,
            "role": self.role,
            "content": self.content,
            "stop_reason": self.stop_reason,
            "usage": {
                "input_tokens": self.usage_input_tokens,
                "output_tokens": self.usage_output_tokens,
                "cache_read_tokens": self.usage_cache_read_tokens,
                "cache_creation_tokens": self.usage_cache_creation_tokens,
            } if self.usage_input_tokens is not None or self.usage_output_tokens is not None else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
