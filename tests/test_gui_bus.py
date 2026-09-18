"""The event bus that feeds the GUI.

Dream's TUI is the harness. The GUI is a SECOND consumer of the same event
stream, and the whole design rests on one rule: a GUI that is slow, absent,
broken, or wedged must never slow down, block, or crash the terminal. These
tests pin that rule, because it is the one that decides whether adding a GUI
was safe.
"""

from __future__ import annotations

import asyncio

from dream.core.backends.base import Event
from dream.gui.bus import EventBus


def test_publish_with_no_subscribers_is_a_noop():
    # The overwhelmingly common case: `dream` running with no GUI attached.
    bus = EventBus()
    bus.publish(Event("text_delta", "hello"))  # must not raise, must not block
    assert bus.subscriber_count == 0


async def test_a_subscriber_receives_published_events_in_order():
    bus = EventBus()
    with bus.subscribe() as sub:
        bus.publish(Event("text_delta", "a"))
        bus.publish(Event("text_delta", "b"))
        assert (await sub.get()).data == "a"
        assert (await sub.get()).data == "b"


async def test_two_subscribers_each_get_their_own_copy():
    bus = EventBus()
    with bus.subscribe() as one, bus.subscribe() as two:
        assert bus.subscriber_count == 2
        bus.publish(Event("system", "x"))
        assert (await one.get()).data == "x"
        assert (await two.get()).data == "x"


async def test_unsubscribe_on_context_exit():
    bus = EventBus()
    with bus.subscribe():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0
    bus.publish(Event("system", "after"))  # no listeners left; still fine


async def test_a_slow_subscriber_drops_events_instead_of_blocking_the_publisher():
    """The load-bearing test. A GUI that stops reading — a wedged browser tab, a
    laptop asleep, a paused debugger — must NOT apply backpressure to the agent
    loop. Events are dropped for that subscriber; publish always returns."""
    bus = EventBus(maxsize=4)
    with bus.subscribe() as sub:
        for i in range(1000):
            bus.publish(Event("text_delta", str(i)))  # never awaits, never blocks
        assert sub.dropped == 996
        # The queue holds at most its bound — memory cannot grow without limit.
        assert sub.qsize() == 4
        # What is RETAINED is the newest window, still in order: a GUI that
        # comes back shows where the turn is now, not a frozen prefix from the
        # start of it. Oldest-of-the-newest first, newest last.
        assert [(await sub.get()).data for _ in range(4)] == ["996", "997", "998", "999"]


async def test_a_subscriber_that_raises_cannot_break_publish():
    bus = EventBus()

    class Exploding:
        def put_nowait(self, ev):
            raise RuntimeError("subscriber is broken")

    bus._subscribers.add(Exploding())  # a wedged//corrupt consumer
    with bus.subscribe() as healthy:
        bus.publish(Event("system", "still delivered"))
        # The broken one must not stop the healthy one from being served.
        assert (await healthy.get()).data == "still delivered"


async def test_publish_is_safe_from_a_thread_without_a_running_loop():
    """Engine work runs through in_thread(); a publish from a worker thread must
    not need an event loop to exist there."""
    bus = EventBus()
    with bus.subscribe() as sub:
        await asyncio.to_thread(bus.publish, Event("system", "from a thread"))
        assert (await sub.get()).data == "from a thread"
