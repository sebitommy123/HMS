"""In-memory registry of in-flight agent turns.

Single-instance design (the user has explicitly locked this in). We hold one
``ActiveTurn`` per conversation that's currently running. New subscribers get
the full event replay from the start of the turn, plus any further events as
they happen. This is what powers:

  * Cancellation — set the turn's cancel_event; the agent loop checks it.
  * Reload-resume — a fresh page load can subscribe to an already-running turn
    and see the same events it would have seen had it been connected all along.
  * Multi-tab fan-out — two tabs watching the same conversation get the same
    events; the agent runs once.

The agent runs in a background daemon thread. The HTTP request handler is just
a subscriber, so it can disconnect (page reload, browser tab closed) without
affecting the agent.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from datapro_ai.llm.agent import StreamEvent

# Sentinel sent on a subscriber queue when the turn is done.
_DONE = object()


@dataclass
class ActiveTurn:
    """One running agent turn. Thread-safe — multiple HTTP handlers subscribe."""

    conversation_id: str
    events: list[StreamEvent] = field(default_factory=list)
    subscribers: list[queue.Queue] = field(default_factory=list)
    done: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, event: StreamEvent) -> None:
        with self._lock:
            self.events.append(event)
            for q in self.subscribers:
                q.put(event)

    def subscribe(self) -> queue.Queue:
        """Get a queue that yields all events so far, then live ones. The
        caller must call ``unsubscribe`` when done."""
        q: queue.Queue = queue.Queue()
        with self._lock:
            for ev in self.events:
                q.put(ev)
            if self.done:
                q.put(_DONE)
            else:
                self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self.subscribers.remove(q)
            except ValueError:
                pass

    def finish(self) -> None:
        with self._lock:
            self.done = True
            for q in self.subscribers:
                q.put(_DONE)
            self.subscribers.clear()

    def cancel(self) -> None:
        self.cancel_event.set()


class TurnRegistry:
    """Holds one ActiveTurn per conversation. App-singleton."""

    def __init__(self) -> None:
        self._turns: dict[str, ActiveTurn] = {}
        self._lock = threading.Lock()

    def get(self, conversation_id: str) -> ActiveTurn | None:
        with self._lock:
            return self._turns.get(conversation_id)

    def try_start(
        self, conversation_id: str, runner: Callable[[ActiveTurn], None]
    ) -> tuple[ActiveTurn | None, bool]:
        """Create + register a new turn for ``conversation_id`` and start its
        runner in a background thread.

        Returns ``(turn, True)`` on success. If a turn is already in flight for
        this conversation, returns ``(existing_turn, False)`` so the caller can
        decide whether to subscribe or reject.
        """
        with self._lock:
            existing = self._turns.get(conversation_id)
            if existing is not None and not existing.done:
                return existing, False
            turn = ActiveTurn(conversation_id=conversation_id)
            self._turns[conversation_id] = turn

        def wrapper() -> None:
            try:
                runner(turn)
            finally:
                turn.finish()
                # Leave the turn briefly so late subscribers can replay; a
                # background sweep would be overkill. The next try_start for
                # the same conversation replaces it.
                with self._lock:
                    if self._turns.get(conversation_id) is turn:
                        # Keep the finished turn in the registry — late
                        # subscribers (e.g. a slow page reload) still get the
                        # replay. It gets evicted when the next turn starts.
                        pass

        threading.Thread(target=wrapper, daemon=True).start()
        return turn, True

    def drop(self, conversation_id: str) -> None:
        """Force-remove a turn from the registry (mainly for tests)."""
        with self._lock:
            self._turns.pop(conversation_id, None)


def iter_subscription(
    q: queue.Queue, heartbeat_seconds: float
) -> Iterator[StreamEvent | None]:
    """Drain a subscriber queue, yielding events as they arrive. Yields
    ``None`` whenever ``heartbeat_seconds`` pass with no events — the SSE
    layer turns ``None`` into a comment line to keep the connection alive.
    Returns when the turn is done."""
    while True:
        try:
            item: Any = q.get(timeout=heartbeat_seconds)
        except queue.Empty:
            yield None
            continue
        if item is _DONE:
            return
        yield item  # type: ignore[misc]
