"""Fan-out of the engine's Event stream to GUI subscribers.

The terminal renders events synchronously as they arrive; the GUI is a second
consumer reached over a websocket, which means it can be slow, asleep, or gone
entirely. So the direction of the coupling matters: the TUI must never wait on
the GUI. ``publish`` is therefore synchronous, non-blocking, and total — it
takes no locks the agent loop can contend on, never awaits, and swallows a
broken subscriber rather than letting it escape into the turn.

The bound is per-subscriber and drops the OLDEST event when full. A GUI that
stops reading is showing a stale screen either way; when it comes back, the
useful thing to show is where the turn is NOW, not a frozen prefix from
several minutes ago.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

# Deep enough to ride out a normal render hitch, shallow enough that a dead GUI
# cannot pin meaningful memory. A long tool result is one event, not a thousand.
DEFAULT_MAXSIZE = 512


class Subscription:
    """One GUI consumer's view of the stream.

    Not an asyncio.Queue: put_nowait on a full asyncio.Queue raises, and the
    behaviour wanted here is "drop the oldest and keep going". The waiter is an
    Event rather than a condition variable so ``publish`` never needs the loop.
    """

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        from collections import deque

        self._items: deque = deque(maxlen=maxsize)
        self._maxsize = maxsize
        self.dropped = 0
        self._waiter: Any = None  # asyncio.Event, created lazily on first get()
        self._loop: Any = None

    def qsize(self) -> int:
        return len(self._items)

    def put_nowait(self, ev: Any) -> None:
        """Never blocks, never raises. Called from the agent loop AND from
        worker threads, so it must not touch the event loop directly."""
        if len(self._items) == self._maxsize:
            self.dropped += 1  # deque(maxlen) evicts the oldest for us
        self._items.append(ev)
        waiter = self._waiter
        if waiter is not None and not waiter.is_set():
            try:
                self._loop.call_soon_threadsafe(waiter.set)
            except RuntimeError:
                pass  # subscriber's event loop has closed

    async def get(self) -> Any:
        import asyncio

        if self._waiter is None:
            self._waiter = asyncio.Event()
            self._loop = asyncio.get_running_loop()
        while True:
            if self._items:
                return self._items.popleft()
            self._waiter.clear()
            await self._waiter.wait()


class EventBus:
    """Publish-side of the GUI stream. One per session."""

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        self._subscribers: set[Any] = set()
        self._maxsize = maxsize
        from .conversation import Conversation
        self.conversation = Conversation()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @contextmanager
    def subscribe(self) -> Iterator[Subscription]:
        sub = Subscription(self._maxsize)
        self._subscribers.add(sub)
        try:
            yield sub
        finally:
            self._subscribers.discard(sub)

    def publish(self, ev: Any) -> None:
        """Hand an event to every subscriber. Total: a subscriber that raises is
        dropped from consideration for this event, never propagated to the
        caller — the caller is the agent loop."""
        self.conversation.append(ev)
        if not self._subscribers:
            return
        for sub in tuple(self._subscribers):
            try:
                sub.put_nowait(ev)
            except Exception:
                pass
