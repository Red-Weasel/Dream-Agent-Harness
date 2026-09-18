"""Run ownership must outlast its worker, including abandoned event streams."""
import asyncio

from anyio import CancelScope
import httpx

import pytest

from dream.core.backends.base import Event
from dream.core.loop import AutonomousLoop
from dream.core.run_state import RunState
from dream.tools.context import ctx
from test_task_guidance_integration import engine
from test_backend_resilience import _backend


@pytest.mark.parametrize('cleanup', ['response', 'tool', 'response_ready', 'permission'])
async def test_subagent_does_not_resume_work_after_deadline_during_cleanup(cleanup):
    import json
    from dataclasses import replace
    from types import SimpleNamespace
    from dream.core.profiles import PROFILES
    from test_backend_resilience import _tool
    closing, finish = asyncio.Event(), asyncio.Event()
    effects = []
    async def write(args):
        effects.append('write')
        if cleanup == 'tool':
            with CancelScope(shield=True):
                closing.set()
                await finish.wait()
        return {'content': [{'type': 'text', 'text': 'written'}]}
    backend = _backend([_tool('write_file', write)], subs={'worker': SimpleNamespace(
        description='fixture', prompt='fixture', tool_names=['write_file'])})
    if cleanup == 'permission':
        async def permission(name, args):
            with CancelScope(shield=True):
                closing.set()
                await finish.wait()
            return True
        backend.permission_cb = permission
    backend.profile = replace(PROFILES['lean'], subagent_timeout_s=.03)
    payload = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
        {'id': name, 'type': 'function', 'function': {'name': 'write_file', 'arguments': '{}'}}
        for name in ('first', 'second')]}}]}
    class Body(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield json.dumps(payload).encode()
        async def aclose(self):
            if cleanup == 'response':
                closing.set()
                await finish.wait()
            elif cleanup == 'response_ready':
                # Complete without yielding: elapsed time must be checked even
                # before the event loop delivers the scheduled deadline callback.
                import time
                time.sleep(.07)
            self.closed = True
    body = Body()
    requests = []
    async def serve(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, stream=body)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'done'}}]})
    async with httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(serve)) as client:
        backend._client = client
        task = asyncio.create_task(backend._run_subagent('worker', 'fixture'))
        try:
            if cleanup != 'response_ready':
                await asyncio.wait_for(closing.wait(), 1)
                await asyncio.sleep(.07)
                assert not task.done()
                finish.set()
            result, failed = await asyncio.wait_for(task, 1)
            assert failed and 'timed out' in result
            assert effects == (['write'] if cleanup == 'tool' else [])
            assert len(requests) == 1 and body.closed
        finally:
            finish.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('stop', [False, True])
async def test_permission_response_cannot_start_tool_after_stop(tmp_path, stop):
    from test_backend_resilience import _tool, _ScriptedClient, _multi_call_round, _text_round
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []
    async def write(args):
        effects.append('write')
        return {'content': [{'type': 'text', 'text': 'written'}]}
    async def permission(name, args):
        entered.set()
        await release.wait()
        return True
    backend = _backend([_tool('write_file', write)])
    backend.workspace = tmp_path
    backend.permission_cb = permission
    backend._client = _ScriptedClient([_multi_call_round(1, 'write_file'), _text_round('STATUS: DONE')])
    loop = AutonomousLoop(backend, state_dir=tmp_path / 'runs', evaluate=False)
    task = asyncio.create_task(loop.run('fixture', acceptance_criteria='- fixture'))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if stop:
            task.cancel()
            await asyncio.sleep(.02)
            assert backend._interrupts.generation == 1
            assert not task.done()
            with pytest.raises(BlockingIOError):
                RunState(tmp_path / 'runs', loop._journal.run_id)
        release.set()
        if stop:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        else:
            await asyncio.wait_for(task, 1)
        assert effects == ([] if stop else ['write'])
        assert backend._client.attempts == (1 if stop else 2)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_subagent_deadline_still_stops_a_blocked_request():
    from dataclasses import replace
    from dream.core.profiles import PROFILES
    from test_parallel_subagents import _backend_for
    backend = _backend_for('openai')
    backend.profile = replace(PROFILES['lean'], subagent_timeout_s=.03)
    class Body(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b''
        async def aclose(self):
            self.closed = True
    body = Body()
    requests = []
    async def serve(request):
        requests.append(request)
        return httpx.Response(200, stream=body)
    async with httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(serve)) as client:
        backend._client = client
        result, failed = await asyncio.wait_for(backend._run_subagent('worker', 'fixture'), 1)
    assert failed and 'timed out' in result
    assert body.closed and len(requests) == 1


@pytest.mark.parametrize('waiting_for_slot', [False, True])
async def test_closed_delegate_batch_does_not_start_queued_tools(waiting_for_slot):
    from test_parallel_subagents import _backend_for, _round, _task
    from test_schema_deferral import _FakeClient
    backend = _backend_for('openai')
    backend._client = _FakeClient([_round([_task('one', 'one'), _task('two', 'two')])])
    effects = []
    async def child(sub, prompt):
        effects.append(prompt)
        return 'unexpected', False
    backend._run_subagent = child
    if waiting_for_slot:
        backend._task_slots = asyncio.Semaphore(0)
    iterator = backend.ask('fixture')
    async for event in iterator:
        if event.kind == 'tool_use':
            if waiting_for_slot:
                await asyncio.sleep(0)
            await iterator.aclose()
            break
    await asyncio.sleep(0)
    assert effects == []
    assert [m['tool_call_id'] for m in backend.messages if m['role'] == 'tool'] == ['one', 'two']


@pytest.mark.parametrize('mode', ['direct', 'parallel', 'deadline', 'deadline_eof'])
async def test_stop_preserves_httpcore_error_cleanup_and_pending_effects(tmp_path, mode):
    import json
    from copy import copy
    import httpcore
    from httpcore._async.http11 import HTTP11ConnectionByteStream
    from httpx._transports.default import AsyncResponseStream

    parallel = mode == 'parallel'
    deadline = mode.startswith('deadline')
    closing, finish, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    effects = []
    async def pending_effect():
        await release.wait()
        effects.append('late effect')
    effect = asyncio.create_task(pending_effect())
    class Connection:
        closed = False
        close_cancelled = False
        async def _receive_response_body(self, request):
            self.owner = asyncio.current_task()
            if mode == 'deadline_eof':
                yield b'{"choices":[{"message":{"content":"done"},"finish_reason":"stop"}]}'
                return
            raise httpcore.ReadError('fixture read failed')
            yield b''
        async def _response_closed(self):
            assert asyncio.current_task() is self.owner
            closing.set()
            try:
                await finish.wait()
                effect.cancel()
                await asyncio.gather(effect, return_exceptions=True)
                self.closed = True
            except asyncio.CancelledError:
                self.close_cancelled = True
                raise
    connection = Connection()
    stream = AsyncResponseStream(HTTP11ConnectionByteStream(
        connection, httpcore.Request('POST', 'https://fixture.invalid')))
    entered = asyncio.Event()
    class WaitingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b''
        async def aclose(self):
            pass
    async def serve(request):
        if parallel or deadline:
            from test_parallel_subagents import _round, _task
            payload = json.loads(request.content)
            if payload.get('stream'):
                calls = [_task('one', 'one'), _task('two', 'two')] if parallel else [_task('one', 'one')]
                return httpx.Response(200, content=('\n\n'.join(
                    _round(calls)) + '\n\n').encode())
            if payload.get('child') == 'one':
                return httpx.Response(200, stream=WaitingBody())
        return httpx.Response(200, stream=stream)
    if parallel or deadline:
        from test_parallel_subagents import _backend_for
        backend = _backend_for('openai')
    else:
        backend = _backend()
    if parallel:
        async def child(sub, prompt):
            worker = copy(backend)
            result = await worker._post_with_retry({'child': prompt})
            return result.text, False
        backend._run_subagent = child
    if deadline:
        from dataclasses import replace
        from dream.core.profiles import PROFILES
        backend.profile = replace(PROFILES['lean'], subagent_timeout_s=.08)
    backend.workspace = tmp_path
    backend._client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=
        httpx.MockTransport(serve))
    loop = AutonomousLoop(backend, state_dir=tmp_path / 'runs', evaluate=False)
    task = asyncio.create_task(loop.run('fixture', acceptance_criteria='- fixture'))
    try:
        await asyncio.wait_for(closing.wait(), 1)
        if parallel:
            await asyncio.wait_for(entered.wait(), 1)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        await asyncio.sleep(.12 if deadline else .02)
        assert not task.done()
        assert not connection.close_cancelled
        with pytest.raises(BlockingIOError):
            RunState(tmp_path / 'runs', loop._journal.run_id)
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), 1)
        assert connection.closed
        recovered = RunState(tmp_path / 'runs', loop._journal.run_id)
        try:
            assert recovered.state['uncertain']
        finally:
            recovered.close()
        release.set()
        await asyncio.sleep(0)
        assert effects == []
    finally:
        finish.set()
        task.cancel()
        effect.cancel()
        await asyncio.gather(task, effect, return_exceptions=True)
        await backend._client.aclose()


@pytest.mark.parametrize('natural_eof', [False, True])
async def test_real_httpx_stream_stops_through_engine_and_loop_before_lock_release(engine, tmp_path, natural_eof):
    entered, closing, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class Bytes(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            self.owner = asyncio.current_task()
            entered.set()
            if natural_eof:
                yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            else:
                await asyncio.Event().wait()
                yield b'unreachable'
        async def aclose(self):
            assert asyncio.current_task() is self.owner
            closing.set()
            await finish.wait()
            self.closed = True
    body = Bytes()
    requests = []
    async def serve(request):
        requests.append(request)
        return httpx.Response(200, stream=body)
    backend = _backend()
    backend._client = httpx.AsyncClient(transport=httpx.MockTransport(serve), base_url='https://fixture.invalid')
    engine.backend = backend
    loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs', evaluate=False)
    task = asyncio.create_task(loop.run('Hello there', acceptance_criteria='- inspect fixture'))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if natural_eof:
            await asyncio.wait_for(closing.wait(), 1)
        task.cancel()
        await asyncio.wait_for(closing.wait(), 1)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        await asyncio.sleep(.02)  # allow the adapter stop request to reach nested EOF cleanup
        assert not task.done()
        with pytest.raises(BlockingIOError):
            RunState(tmp_path / 'runs', loop._journal.run_id)
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), 1)
        assert body.closed and len(requests) == 1
        recovered = RunState(tmp_path / 'runs', loop._journal.run_id)
        try:
            assert recovered.state['uncertain'] is True
        finally:
            recovered.close()
    finally:
        finish.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await backend._client.aclose()
        engine.store.close()


class PendingWorker:
    def __init__(self, workspace, event, ending=None):
        self.workspace = workspace
        self.event = event
        self.ending = ending
        self.entered = asyncio.Event()
        self.cleanup = asyncio.Event()
        self.finish_cleanup = asyncio.Event()
        self.release_effect = asyncio.Event()
        self.effects = []
        self.closed = False

    def ask(self, prompt):
        self.iterator = self.events()
        return self.iterator

    async def events(self):
        async def effect():
            await self.release_effect.wait()
            self.effects.append('late effect')
        pending = asyncio.create_task(effect())
        owner = asyncio.current_task()
        with CancelScope():
            try:
                self.entered.set()
                yield self.event
                if self.ending == 'error':
                    raise ValueError('backend failed internally')
                if self.ending == 'eof':
                    return
                await asyncio.Event().wait()
            finally:
                assert asyncio.current_task() is owner
                self.cleanup.set()
                await self.finish_cleanup.wait()
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                self.closed = True

    async def interrupt(self):
        pass


@pytest.mark.parametrize('failure', ['error', 'failed_result', 'sink', 'cancel'])
async def test_run_retains_lock_until_worker_cleanup(tmp_path, failure):
    event = {'error': Event('error', 'transport failed'),
             'failed_result': Event('result', {'is_error': True}),
             'sink': Event('text_delta', 'partial'),
             'cancel': Event('text_delta', 'partial')}[failure]
    worker = PendingWorker(tmp_path, event)
    sink_entered = asyncio.Event()
    async def sink(ev):
        if ev.kind == 'text_delta':
            sink_entered.set()
            if failure == 'sink':
                raise ValueError('consumer failed')
            await asyncio.Event().wait()
    loop = AutonomousLoop(worker, on_event=sink, state_dir=tmp_path / 'runs', evaluate=False)
    task = asyncio.create_task(loop.run('fixture', acceptance_criteria='- inspect fixture'))
    try:
        await asyncio.wait_for(worker.entered.wait(), 1)
        if failure == 'cancel':
            await asyncio.wait_for(sink_entered.wait(), 1)
            task.cancel()
        await asyncio.wait_for(worker.cleanup.wait(), .5)
        assert not task.done(), 'run returned before cleanup completed'
        with pytest.raises(BlockingIOError):
            RunState(tmp_path / 'runs', loop._journal.run_id)
        worker.finish_cleanup.set()
        if failure == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).status == 'error'
        assert worker.closed
        recovered = RunState(tmp_path / 'runs', loop._journal.run_id)
        try:
            assert recovered.state['uncertain'] is True
        finally:
            recovered.close()
        worker.release_effect.set()
        await asyncio.sleep(0)
        assert worker.effects == []
    finally:
        worker.finish_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.iterator.aclose()


@pytest.mark.parametrize('ending', ['error', 'eof'])
async def test_cancellation_cannot_abort_cleanup_inside_iterator_advancement(tmp_path, ending):
    worker = PendingWorker(tmp_path, Event('text_delta', 'partial'), ending=ending)
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs', evaluate=False)
    task = asyncio.create_task(loop.run('fixture', acceptance_criteria='- inspect fixture'))
    try:
        await asyncio.wait_for(worker.cleanup.wait(), 1)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done(), 'cancellation aborted backend-internal cleanup'
        with pytest.raises(BlockingIOError):
            RunState(tmp_path / 'runs', loop._journal.run_id)
        worker.finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker.closed
        recovered = RunState(tmp_path / 'runs', loop._journal.run_id)
        try:
            assert recovered.state['uncertain'] is True
        finally:
            recovered.close()
        worker.release_effect.set()
        await asyncio.sleep(0)
        assert worker.effects == []
    finally:
        worker.finish_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.iterator.aclose()


@pytest.mark.parametrize('initial_error', [False, True])
async def test_cancellation_during_cleanup_cannot_release_run_ownership(tmp_path, initial_error):
    worker = PendingWorker(tmp_path, Event('error' if initial_error else 'text_delta', 'fixture'))
    sink_entered = asyncio.Event()
    async def sink(event):
        if event.kind == 'text_delta':
            sink_entered.set()
            await asyncio.Event().wait()
    loop = AutonomousLoop(worker, on_event=sink, state_dir=tmp_path / 'runs', evaluate=False)
    task = asyncio.create_task(loop.run('fixture', acceptance_criteria='- inspect fixture'))
    try:
        if not initial_error:
            await asyncio.wait_for(sink_entered.wait(), 1)
            task.cancel()
        await asyncio.wait_for(worker.cleanup.wait(), 1)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done(), 'cancellation abandoned cleanup'
        with pytest.raises(BlockingIOError):
            RunState(tmp_path / 'runs', loop._journal.run_id)
        worker.finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker.closed
        recovered = RunState(tmp_path / 'runs', loop._journal.run_id)
        try:
            assert recovered.state['uncertain'] is True
        finally:
            recovered.close()
        worker.release_effect.set()
        await asyncio.sleep(0)
        assert worker.effects == []
    finally:
        worker.finish_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.iterator.aclose()


class ClosingBackend:
    def __init__(self, budget=False):
        self.budget = budget
        self.closed = False
        self.late = False

    def ask(self, prompt):
        self.iterator = self.events()
        return self.iterator

    async def events(self):
        self.owner = asyncio.current_task()
        self.context = ctx()
        try:
            if self.budget:
                yield Event('tool_use', {'name': 'fixture', 'input': {}})
                yield Event('tool_result', {'name': 'fixture', 'content': 'done'})
            else:
                yield Event('text_delta', 'partial')
            self.late = True
        finally:
            await asyncio.sleep(0)
            self.cleanup_owner = asyncio.current_task()
            self.cleanup_context = ctx()
            self.closed = True

    async def interrupt(self):
        pass


@pytest.mark.parametrize('interrupt_fails', [False, True])
async def test_unsettled_request_retains_ownership_after_stop_request(tmp_path, interrupt_fails):
    entered, settle, interrupted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class Worker:
        workspace = tmp_path
        closed = False
        calls = 0
        async def ask(self, prompt):
            self.calls += 1
            with CancelScope():
                try:
                    entered.set()
                    await settle.wait()
                    yield Event('assistant_done', 'response after stop')
                finally:
                    self.closed = True
        async def interrupt(self):
            interrupted.set()
            if interrupt_fails:
                raise RuntimeError('provider cannot confirm interruption')
    worker = Worker()
    delivered = []
    loop = AutonomousLoop(worker, on_event=delivered.append, state_dir=tmp_path / 'runs', evaluate=False)
    task = asyncio.create_task(loop.run('fixture', acceptance_criteria='- inspect fixture'))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        await asyncio.wait_for(interrupted.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(BlockingIOError):
            RunState(tmp_path / 'runs', loop._journal.run_id)
        settle.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker.closed and worker.calls == 1
        assert all(ev.kind != 'assistant_done' for ev in delivered)
    finally:
        settle.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('budget', [False, True])
async def test_engine_closes_nested_backend_in_same_task_and_context(engine, budget):
    backend = ClosingBackend(budget)
    engine.backend = backend
    engine.set_tool_budget(1)
    stream = engine.ask('Hello there')
    try:
        if budget:
            events = [event async for event in stream]
            assert any('budget reached' in str(ev.data) for ev in events)
        else:
            assert (await anext(stream)).kind == 'text_delta'
            await stream.aclose()
        assert backend.closed, 'public Engine iterator abandoned backend cleanup'
        assert not backend.late
        assert backend.cleanup_owner is backend.owner is asyncio.current_task()
        assert backend.cleanup_context is backend.context is engine._tool_context
    finally:
        await stream.aclose()
        # Close a retained red-test iterator under its original context.
        from dream.tools.context import bind_context
        with bind_context(engine._tool_context):
            await backend.iterator.aclose()
        engine.store.close()
