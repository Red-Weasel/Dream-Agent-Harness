"""Optional work yields to foreground using asyncio fixtures, without providers."""
import asyncio
from contextvars import ContextVar

import pytest


@pytest.mark.asyncio
async def test_foreground_cancels_and_joins_running_work_without_replay():
    from dream.core.idle_work import IdleWorkQueue
    started, cleanup_started, allow_cleanup, second_done = [asyncio.Event() for _ in range(4)]
    events, calls = [], []
    queue = IdleWorkQueue(idle_grace=0, on_event=events.append)

    async def first():
        calls.append('first')
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await allow_cleanup.wait()

    async def second():
        calls.append('second')
        second_done.set()

    queue.enqueue(first, label='first')
    queue.enqueue(second, label='second')
    await asyncio.wait_for(started.wait(), 1)
    foreground = asyncio.create_task(queue.foreground_started())
    await asyncio.wait_for(cleanup_started.wait(), 1)
    assert not foreground.done() and queue.snapshot()['queued'] == 1
    allow_cleanup.set()
    await asyncio.wait_for(foreground, 1)
    assert calls == ['first']
    assert queue.snapshot()['interrupted'] == 1
    queue.foreground_finished()
    await asyncio.wait_for(second_done.wait(), 1)
    await queue.close()
    assert calls == ['first', 'second']
    assert any(item['kind'] == 'interrupted' and item['label'] == 'first' for item in events)


@pytest.mark.asyncio
async def test_foreground_cancels_idle_timer_preserving_unstarted_job():
    from dream.core.idle_work import IdleWorkQueue
    ran = asyncio.Event()
    queue = IdleWorkQueue(idle_grace=0.02)

    async def job():
        ran.set()

    queue.enqueue(job, label='waiting')
    await queue.foreground_started()
    await asyncio.sleep(0.04)
    assert not ran.is_set()
    assert queue.snapshot()['queued'] == 1
    assert queue.snapshot()['interrupted'] == 0
    queue.foreground_finished()
    await asyncio.wait_for(ran.wait(), 1)
    await queue.close()


@pytest.mark.asyncio
async def test_queue_bound_drops_newest_and_close_discards_pending():
    from dream.core.idle_work import IdleWorkQueue
    queue = IdleWorkQueue(max_pending=2, idle_grace=0)
    await queue.foreground_started()
    calls = []

    async def job():
        calls.append('ran')

    assert queue.enqueue(job, label='one')
    assert queue.enqueue(job, label='two')
    assert not queue.enqueue(job, label='three')
    assert queue.snapshot()['queued'] == 2
    assert queue.snapshot()['dropped'] == 1
    await queue.close()
    assert queue.snapshot()['queued'] == 0 and queue.snapshot()['dropped'] == 3
    assert not queue.enqueue(job, label='closed')
    queue.foreground_finished()
    await asyncio.sleep(0)
    assert not calls


@pytest.mark.asyncio
async def test_each_job_uses_enqueue_context_and_failures_do_not_stop_queue():
    from dream.core.idle_work import IdleWorkQueue
    context = ContextVar('idle_test_context', default='default')
    queue = IdleWorkQueue(idle_grace=0)
    await queue.foreground_started()
    observed, events = [], []
    queue.on_event = events.append
    finished = asyncio.Event()

    def failing_factory():
        observed.append(context.get())
        raise ValueError('fixture failure')

    async def success():
        observed.append(context.get())
        finished.set()

    token = context.set('first session')
    queue.enqueue(failing_factory, label='failed')
    context.set('second session')
    queue.enqueue(success, label='success')
    context.reset(token)
    queue.foreground_finished()
    await asyncio.wait_for(finished.wait(), 1)
    # Let completion accounting happen before shutdown.
    await asyncio.sleep(0)
    await queue.close()
    assert observed == ['first session', 'second session']
    assert queue.snapshot()['failed'] == 1
    assert queue.snapshot()['completed'] == 1
    assert any(item['kind'] == 'failed' and item['error_type'] == 'ValueError' for item in events)


@pytest.mark.asyncio
async def test_close_joins_running_cleanup_and_leaves_no_owned_tasks():
    from dream.core.idle_work import IdleWorkQueue
    baseline = set(asyncio.all_tasks())
    started, cleaned = asyncio.Event(), asyncio.Event()
    queue = IdleWorkQueue(idle_grace=0)

    async def job():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    queue.enqueue(job, label='owned')
    await asyncio.wait_for(started.wait(), 1)
    await queue.close()
    assert cleaned.is_set()
    assert not queue.snapshot()['running'] and queue.snapshot()['closed']
    assert set(asyncio.all_tasks()) <= baseline
    await queue.close()


@pytest.mark.asyncio
async def test_job_suppressing_cancellation_finishes_before_foreground_returns():
    from dream.core.idle_work import IdleWorkQueue
    started, cancelled, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    queue = IdleWorkQueue(idle_grace=0)

    async def job():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await finish.wait()

    queue.enqueue(job, label='cleanup')
    await asyncio.wait_for(started.wait(), 1)
    foreground = asyncio.create_task(queue.foreground_started())
    await asyncio.wait_for(cancelled.wait(), 1)
    assert not foreground.done()
    finish.set()
    await asyncio.wait_for(foreground, 1)
    assert queue.snapshot()['interrupted'] == 1
    assert queue.snapshot()['completed'] == 0
    await queue.close()


@pytest.mark.asyncio
async def test_observer_failure_is_logged_without_losing_job(caplog):
    from dream.core.idle_work import IdleWorkQueue
    done = asyncio.Event()

    def broken_observer(event):
        raise RuntimeError('fixture observer failed')

    async def job():
        done.set()

    queue = IdleWorkQueue(idle_grace=0, on_event=broken_observer)
    queue.enqueue(job, label='retained')
    await asyncio.wait_for(done.wait(), 1)
    await asyncio.sleep(0)
    await queue.close()
    assert queue.snapshot()['completed'] == 1
    assert 'Idle work event observer failed' in caplog.text
