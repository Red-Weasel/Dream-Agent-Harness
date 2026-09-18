"""Adapter Stop reaches network waits without cancelling the owning worker."""
import asyncio

import httpx
import pytest

from test_backend_resilience import _backend
from test_local_request_coordination import local_backend


async def test_stop_wins_even_when_tool_response_read_is_already_ready():
    from test_backend_resilience import _tool, _multi_call_round, _text_round, _Ctx, _Resp
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []
    async def write(args):
        effects.append('unwanted write')
        return {'content': [{'type': 'text', 'text': 'written'}]}
    backend = _backend([_tool('write_file', write)])
    class Response(_Resp):
        async def aiter_lines(self):
            entered.set()
            await release.wait()
            for line in self._lines:
                yield line
    class Client:
        calls = 0
        def stream(self, *args, **kwargs):
            self.calls += 1
            return _Ctx(resp=Response(_multi_call_round(1, 'write_file'))
                        if self.calls == 1 else _Resp(_text_round()))
    backend._client = Client()
    async def consume():
        return [event async for event in backend.ask('fixture')]
    task = asyncio.create_task(consume())
    await entered.wait()
    release.set()  # queue the worker before timeout expiry can be scheduled
    await backend.interrupt()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert effects == [] and backend._client.calls == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_stop_before_tool_dispatch_and_explicit_next_turn():
    from test_backend_resilience import _tool, _multi_call_round, _text_round
    from test_schema_deferral import _FakeClient
    effects = []
    async def write(args):
        effects.append('write')
        return {'content': [{'type': 'text', 'text': 'written'}]}
    backend = _backend([_tool('write_file', write)])
    backend._client = _FakeClient([_multi_call_round(1, 'write_file'), _text_round()])
    events = backend.ask('fixture')
    try:
        async for event in events:
            if event.kind == 'tool_use':
                break
        await backend.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await anext(events)
        assert effects == []
        fresh = [event async for event in backend.ask('explicit new turn')]
        assert any(event.kind == 'assistant_done' for event in fresh)
    finally:
        await events.aclose()


async def test_stop_waiting_for_local_lease_preserves_other_owner(tmp_path):
    backend = local_backend(tmp_path)
    entered = asyncio.Event()
    class Client:
        calls = 0
        async def post(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError('waiting request must not be sent')
    backend._client = Client()
    async with backend._coordinator().request():
        async def request():
            entered.set()
            return await backend._post_with_retry({})
        task = asyncio.create_task(request())
        try:
            await entered.wait()
            await asyncio.sleep(0)
            await backend.interrupt()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), 1)
            assert backend._client.calls == 0
            assert backend.coordination_status()['state'] == 'running'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert backend.coordination_status()['state'] == 'idle'


async def test_queued_httpx_headers_after_stop_still_close_owned_response():
    entered, release = asyncio.Event(), asyncio.Event()
    class Body(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            raise AssertionError('stopped response must not be consumed')
            yield b''
        async def aclose(self):
            self.closed = True
    body = Body()
    async def serve(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, stream=body)
    backend = _backend()
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve), base_url='https://fixture.invalid') as client:
        backend._client = client
        async def consume():
            return [event async for event in backend.ask('fixture')]
        task = asyncio.create_task(consume())
        await entered.wait()
        release.set()
        await backend.interrupt()
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert body.closed
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('status', [200, 400])
async def test_httpx_post_eof_cleanup_is_not_interrupted(status):
    closing, finish = asyncio.Event(), asyncio.Event()
    class Body(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            self.owner = asyncio.current_task()
            yield b'{}'
        async def aclose(self):
            assert asyncio.current_task() is self.owner
            closing.set()
            await finish.wait()
            self.closed = True
    body = Body()
    backend = _backend()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, stream=body)), base_url='https://fixture.invalid') as client:
        backend._client = client
        task = asyncio.create_task(backend._post_with_retry({}))
        try:
            await closing.wait()
            for _ in range(3):
                await backend.interrupt()
                await asyncio.sleep(0)
            assert not task.done() and not body.closed
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert body.closed
        finally:
            finish.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('phase', ['open', 'read', 'post', 'error_body'])
@pytest.mark.parametrize('local', [False, True])
async def test_interrupt_cancels_pending_io_once_without_replay(tmp_path, phase, local):
    entered = asyncio.Event()
    stopped = asyncio.Event()
    backend = local_backend(tmp_path) if local else _backend()
    class Response:
        status_code = 400 if phase == 'error_body' else 200
        headers = {}
        closed = False
        calls = 0
        async def block(self):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        async def __aenter__(self):
            self.owner = asyncio.current_task()
            if phase == 'open':
                await self.block()
            return self
        async def __aexit__(self, *args):
            assert asyncio.current_task() is self.owner
            self.closed = True
        async def aiter_lines(self):
            await self.block()
            yield 'unreachable'
        async def aread(self):
            await self.block()
        def stream(self, *args, **kwargs):
            self.calls += 1
            return self
        async def post(self, *args, **kwargs):
            self.calls += 1
            await self.block()
    response = Response()
    backend._client = response
    async def request():
        if phase == 'post':
            return await backend._post_with_retry({})
        return [event async for event in backend.ask('fixture')]
    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await backend.interrupt()
        await backend.interrupt()
        await asyncio.wait_for(stopped.wait(), .3)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), 1)
        assert response.calls == 1
        if phase in ('read', 'error_body'):
            assert response.closed
        if local:
            assert backend.coordination_status()['state'] == 'uncertain'
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_repeated_interrupt_does_not_abort_response_cleanup_or_other_backend():
    closing, finish, pending = asyncio.Event(), asyncio.Event(), asyncio.Event()
    a, b = _backend(), _backend()
    class Response:
        status_code = 200
        headers = {}
        closed = False
        async def __aenter__(self):
            self.owner = asyncio.current_task()
            return self
        async def __aexit__(self, *args):
            assert asyncio.current_task() is self.owner
            closing.set()
            await finish.wait()
            self.closed = True
        async def aiter_lines(self):
            pending.set()
            await asyncio.Event().wait()
            yield 'unreachable'
        def stream(self, *args, **kwargs):
            return self
    response = Response()
    a._client = response
    other_entered = asyncio.Event()
    class Client:
        async def post(self, *args, **kwargs):
            other_entered.set()
            await asyncio.Event().wait()
    b._client = Client()
    async def consume():
        return [e async for e in a.ask('fixture')]
    task = asyncio.create_task(consume())
    other = asyncio.create_task(b._post_with_retry({}))
    try:
        await asyncio.wait_for(pending.wait(), 1)
        await asyncio.wait_for(other_entered.wait(), 1)
        await a.interrupt()
        await asyncio.wait_for(closing.wait(), .3)
        for _ in range(3):
            await a.interrupt()
            await asyncio.sleep(0)
        assert not task.done() and not response.closed
        assert not other.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert response.closed
    finally:
        finish.set()
        task.cancel()
        other.cancel()
        await asyncio.gather(task, other, return_exceptions=True)


@pytest.mark.parametrize('streaming', [True, False])
async def test_interrupt_stops_retry_delay_without_another_request(streaming):
    backend = _backend()
    attempted = asyncio.Event()
    class Client:
        calls = 0
        async def post(self, *args, **kwargs):
            self.calls += 1
            attempted.set()
            return httpx.Response(429, text='busy', headers={'retry-after': '30'})
    # The streaming fixture needs the same context interface as httpx.stream.
    class Context:
        async def __aenter__(self):
            attempted.set()
            return httpx.Response(429, text='busy', headers={'retry-after': '30'})
        async def __aexit__(self, *args):
            pass
    client = Client()
    def stream(*args, **kwargs):
        client.calls += 1
        return Context()
    client.stream = stream
    backend._client = client
    async def request():
        if streaming:
            return [e async for e in backend.ask('fixture')]
        return await backend._post_with_retry({})
    task = asyncio.create_task(request())
    try:
        await attempted.wait()
        await asyncio.sleep(0)
        await backend.interrupt()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), 1)
        assert client.calls == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
