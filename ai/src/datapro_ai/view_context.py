"""The user's current UI view, shared with the agent.

The left-hand chat panel and the main app view live in the same browser, so the
browser can describe "what the user is looking at" — the current route, the
entity on screen, and bounded "observations" (a data-source preview it ran, the
rows on a query page, an in-progress flex module, …). It PUTs that description
here, keyed by conversation. The agent reads it through two tools
(get_current_view / read_observation), so the details are PULLED on demand
rather than dumped into the context window.

This store is in-memory, per-conversation, latest-wins, and ephemeral — it's a
live snapshot of the browser, not durable state. Single-instance by design
(same posture as the turn registry).
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Any

# Safety backstops. The client is expected to send already-bounded observations
# (a screenful of data), but we never trust it to — cap per-observation and in
# aggregate so a runaway page can't bloat the store or the agent's tool reads.
MAX_OBSERVATION_BYTES = 32_000
MAX_TOTAL_BYTES = 128_000
MAX_OBSERVATIONS = 24
# Cap distinct conversations held at once (LRU-evicted). This is a shared,
# multi-user, single-instance service — every user's chats live here keyed by
# conversation id, so the store must stay bounded no matter how many people use
# it. Eviction only drops a stale ephemeral view; the browser re-publishes on
# the next interaction.
MAX_CONVERSATIONS = 2_000


class ViewContextStore:
    """Thread-safe, per-conversation store of the latest UI view.

    Keyed strictly by conversation id (a unique UUID), so it is safe for many
    concurrent users on one instance: a conversation's view is only ever read
    by the agent turn running for that same conversation. There is no global
    "current view" — nothing that could cross users. LRU-bounded."""

    def __init__(self, max_conversations: int = MAX_CONVERSATIONS) -> None:
        self._lock = threading.Lock()
        self._by_conversation: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._max = max_conversations

    def put(self, conversation_id: str, view: dict[str, Any]) -> None:
        with self._lock:
            self._by_conversation[conversation_id] = view
            self._by_conversation.move_to_end(conversation_id)
            while len(self._by_conversation) > self._max:
                self._by_conversation.popitem(last=False)  # evict least-recent

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        with self._lock:
            view = self._by_conversation.get(conversation_id)
            if view is not None:
                self._by_conversation.move_to_end(conversation_id)
            return view

    def clear(self, conversation_id: str) -> None:
        with self._lock:
            self._by_conversation.pop(conversation_id, None)


class ViewAccessor:
    """Conversation-scoped read view over the store, handed to tools via
    ToolContext. Reads live, so a tool call mid-turn sees whatever the browser
    last published (e.g. the user navigated after sending their message)."""

    def __init__(self, store: ViewContextStore, conversation_id: str) -> None:
        self._store = store
        self._conversation_id = conversation_id

    def current(self) -> dict[str, Any] | None:
        return self._store.get(self._conversation_id)

    def manifest(self) -> dict[str, Any] | None:
        """The compact index the agent orients with: route/entity + a list of
        available observation keys and descriptions — but NOT their payloads."""
        view = self.current()
        if view is None:
            return None
        observations = view.get("observations") or {}
        return {
            "route": view.get("route"),
            "title": view.get("title"),
            "entity": view.get("entity"),
            "updated_at": view.get("updated_at"),
            "observations": [
                {
                    "key": key,
                    "description": obs.get("description"),
                    "kind": obs.get("kind"),
                    "truncated": obs.get("truncated", False),
                    "updated_at": obs.get("updated_at"),
                }
                for key, obs in observations.items()
            ],
        }

    def read(self, key: str) -> dict[str, Any] | None:
        view = self.current()
        if view is None:
            return None
        return (view.get("observations") or {}).get(key)

    def observation_keys(self) -> list[str]:
        view = self.current()
        if view is None:
            return []
        return list((view.get("observations") or {}).keys())

    def system_hint(self) -> str | None:
        """The single line injected into the request so the agent always knows
        WHERE the user is without a tool call. Deliberately tiny — details come
        from the tools."""
        view = self.current()
        if view is None:
            return None
        title = view.get("title") or view.get("route") or "a page in the app"
        route = view.get("route")
        n = len(view.get("observations") or {})
        hint = f"CURRENT VIEW — the user is looking at {title}"
        if route and route != title:
            hint += f" ({route})"
        hint += (
            ". This is the live left-panel context: you can 'see' what they see. "
            "Call get_current_view for the page's on-screen data"
        )
        if n:
            hint += f" ({n} observation{'s' if n != 1 else ''} available)"
        hint += ", then read_observation(key) to inspect a specific item."
        return hint


def normalize_and_cap(raw: Any) -> dict[str, Any]:
    """Validate + size-cap a client-published view into the stored shape. Never
    raises on bad input — a malformed view just yields a minimal record — so a
    client bug can't 500 the publish endpoint."""
    if not isinstance(raw, dict):
        raw = {}

    route = _as_str(raw.get("route")) or ""
    title = _as_str(raw.get("title"))
    entity = raw.get("entity") if isinstance(raw.get("entity"), dict) else None

    observations: dict[str, Any] = {}
    total = 0
    raw_obs = raw.get("observations")
    if isinstance(raw_obs, dict):
        for key, obs in raw_obs.items():
            if len(observations) >= MAX_OBSERVATIONS:
                break
            if not isinstance(key, str) or not isinstance(obs, dict):
                continue
            data, truncated = _cap_data(obs.get("data"), MAX_OBSERVATION_BYTES)
            size = _json_len(data)
            if total + size > MAX_TOTAL_BYTES:
                # Out of aggregate budget — record the key as present but empty
                # so the agent knows it exists without us storing the payload.
                observations[key] = {
                    "description": _as_str(obs.get("description")),
                    "kind": _as_str(obs.get("kind")),
                    "data": None,
                    "truncated": True,
                    "updated_at": _as_num(obs.get("updated_at")),
                }
                continue
            total += size
            observations[key] = {
                "description": _as_str(obs.get("description")),
                "kind": _as_str(obs.get("kind")),
                "data": data,
                "truncated": truncated,
                "updated_at": _as_num(obs.get("updated_at")),
            }

    return {
        "route": route,
        "title": title,
        "entity": entity,
        "observations": observations,
        "updated_at": time.time(),
    }


def _cap_data(data: Any, max_bytes: int) -> tuple[Any, bool]:
    """Shrink a payload under ``max_bytes`` of JSON. Lists keep a prefix of
    items; strings keep a prefix; anything else that's still too big is replaced
    with a short marker. Returns (data, truncated)."""
    if data is None:
        return None, False
    if _json_len(data) <= max_bytes:
        return data, False
    if isinstance(data, list):
        kept: list[Any] = []
        for item in data:
            if _json_len(kept + [item]) > max_bytes:
                break
            kept.append(item)
        return kept, True
    if isinstance(data, str):
        # Byte-accurate slice, then repair any split UTF-8 at the boundary.
        clipped = data.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        return clipped, True
    # Dict/other: last resort — stringify a prefix so at least something shows.
    text = json.dumps(data, default=str)[:max_bytes]
    return {"_truncated": True, "preview": text}, True


def _json_len(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _as_num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None
