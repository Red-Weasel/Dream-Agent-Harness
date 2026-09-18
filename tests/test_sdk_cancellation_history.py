"""Exact SDK cancellation provenance without changing completion contracts."""
import asyncio
from contextlib import aclosing
import json
import os
import socket
import subprocess

import anyio
import claude_agent_sdk as sdk
import pytest

from dream.core import moe
from dream.core.backends.anthropic import AnthropicBackend
from dream.core.engine import Engine
from dream.core.providers import get_provider
from dream.memory.embeddings import Embedder, Reranker


@pytest.fixture(autouse=True)
def forbid_external_operations(monkeypatch):
    attempts = []
    def denied(*args, **kwargs):
        attempts.append('external operation, model loading or Engine.start')
        raise AssertionError(attempts[-1])
    for obj, attr in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                      (subprocess.Popen, '__init__'), (os, 'system'),
                      (asyncio, 'create_subprocess_exec'), (asyncio, 'create_subprocess_shell'),
                      (Embedder, '_ensure'), (Reranker, '_ensure'), (Engine, 'start')):
        monkeypatch.setattr(obj, attr, denied)
    yield
    assert attempts == []


def assistant():
    return sdk.AssistantMessage(content=[sdk.TextBlock(text='COMPLETE fixture advice')], model='fixture-sdk')


def terminal():
    return sdk.ResultMessage(subtype='success', is_error=False, duration_ms=1, duration_api_ms=1,
                             num_turns=1, session_id='fixture-native', terminal_reason='completed',
                             usage={'input_tokens': 7, 'output_tokens': 3}, total_cost_usd=0.25)


class Boundary:
    def __init__(self, *, read=None, close=None, before=None):
        self.read, self.close, self.before = read, close, before
        self.queries = self.closes = self.settled = 0

    async def packets(self):
        self.owner = asyncio.current_task()
        with anyio.CancelScope():
            try:
                if self.before:
                    await self.before()
                yield assistant()
                if self.read:
                    await self.read()
                yield terminal()
            finally:
                assert asyncio.current_task() is self.owner
                self.settled += 1

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await anext(self.inner)

    async def aclose(self):
        self.closes += 1
        assert asyncio.current_task() is self.owner
        try:
            if self.close:
                await self.close()
        finally:
            await self.inner.aclose()

    def sdk_query(self, *, prompt, options):
        self.queries += 1
        self.options = options
        self.inner = self.packets()
        return self

    async def query(self, prompt):
        self.queries += 1

    def receive_messages(self):
        return self.source

    def receive_response(self):
        self.source = self.packets()
        self.inner = self.native_response()
        return self

    async def native_response(self):
        async with aclosing(self.source):
            async with aclosing(sdk.ClaudeSDKClient.receive_response(self)) as response:
                async for message in response:
                    yield message


def main_backend(boundary, root):
    backend = AnthropicBackend(system_prompt='fixture system', mcp_server={},
        preapproved_tool_ids=[], agents=None, permission_cb=None, model='fixture-sdk', cwd=str(root))
    backend.client = boundary
    return backend


async def invoke(target, boundary, root, monkeypatch):
    monkeypatch.setattr(sdk, 'query', boundary.sdk_query)
    if target == 'council':
        return await moe._consult_anthropic(get_provider('anthropic'), 'q', cwd=str(root),
                                           model='fixture-sdk', effort='high')
    return [event async for event in main_backend(boundary, root).ask('q')]


@pytest.mark.parametrize('target', ['council', 'main'])
@pytest.mark.parametrize('where', ['read', 'close'])
@pytest.mark.parametrize('latest', [False, True])
async def test_same_owner_repeated_injections_use_only_retained_latest_identity(
        tmp_path, monkeypatch, target, where, latest):
    entered = [asyncio.Event(), asyncio.Event()]
    cancellations, faults = [], []
    async def repeated():
        for at in entered:
            at.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                cancellations.append(exc)
                try:
                    raise RuntimeError('identical retained error')
                except RuntimeError as fault:
                    faults.append(fault)
        raise faults[1 if latest else 0]
    boundary = Boundary(**{where: repeated})
    async def run():
        try:
            return await invoke(target, boundary, tmp_path, monkeypatch)
        except BaseException as exc:
            return exc
    task = asyncio.create_task(run())
    try:
        for at in entered:
            await asyncio.wait_for(at.wait(), 1)
            task.cancel('identical current cancellation')
        result = await task
        if latest:
            assert result is cancellations[1]
        elif target == 'council':
            assert result is faults[0]
        else:
            assert any(e.kind == 'error' and 'identical retained error' in e.data for e in result)
        assert result is not cancellations[0] and task.cancelling() == 2
        assert boundary.queries == boundary.closes == boundary.settled == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('target', ['council', 'main'])
@pytest.mark.parametrize('latest', [False, True])
async def test_reused_query_generator_does_not_lend_previous_read_identity(tmp_path, monkeypatch, target, latest):
    entered = [asyncio.Event(), asyncio.Event()]
    cancellations, faults = [], []
    class Reused(Boundary):
        async def packets(self):
            self.owner = asyncio.current_task()
            with anyio.CancelScope():
                try:
                    for index, at in enumerate(entered):
                        at.set()
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError as exc:
                            cancellations.append(exc)
                            try:
                                raise RuntimeError('same generator retained failure')
                            except RuntimeError as fault:
                                faults.append(fault)
                        if index == 0:
                            yield assistant()
                    raise faults[1 if latest else 0]
                finally:
                    assert asyncio.current_task() is self.owner
                    self.settled += 1
    boundary = Reused()
    async def run():
        try:
            return await invoke(target, boundary, tmp_path, monkeypatch)
        except BaseException as exc:
            return exc
    task = asyncio.create_task(run())
    try:
        for at in entered:
            await asyncio.wait_for(at.wait(), 1)
            task.cancel('same cancellation text')
        result = await task
        if latest:
            assert result is cancellations[1]
        elif target == 'council':
            assert result is faults[0]
        else:
            assert any(e.kind == 'error' and 'same generator retained failure' in e.data for e in result)
        assert result is not cancellations[0]
        assert boundary.queries == boundary.closes == boundary.settled == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('action', ['continue', 'close', 'throw'])
async def test_main_successful_read_cannot_lend_witness_to_consumer_error(tmp_path, action):
    at = asyncio.Event()
    saved = []
    async def before():
        at.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            try:
                raise RuntimeError('retained successful-read error')
            except RuntimeError as fault:
                saved.append((exc, fault))
    boundary = Boundary(before=before)
    async def consume():
        stream = main_backend(boundary, tmp_path).ask('q')
        try:
            assert (await anext(stream)).kind == 'assistant_done'
            if action == 'continue':
                events = [e async for e in stream]
                assert [e.kind for e in events] == ['result'] and events[0].data['is_error'] is False
            elif action == 'close':
                await stream.aclose()
            else:
                # Main's established contract reports operational failures as events.
                event = await stream.athrow(saved[0][1])
                assert event.kind == 'error' and 'retained successful-read error' in event.data
            assert asyncio.current_task().cancelling() == 1
        finally:
            await stream.aclose()
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(at.wait(), 1)
        task.cancel('consumed read cancellation')
        await task
        assert boundary.queries == boundary.closes == boundary.settled == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('target', ['council', 'main'])
@pytest.mark.parametrize('wrapped', [False, True])
async def test_new_close_cancellation_after_read_fault_preserves_current(tmp_path, monkeypatch, target, wrapped):
    at = asyncio.Event()
    saved = []
    async def read():
        raise ValueError('earlier read failure')
    async def close():
        at.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            saved.append(exc)
            if wrapped:
                raise RuntimeError('current close finalizer')
            raise
    boundary = Boundary(read=read, close=close)
    async def run():
        try:
            return await invoke(target, boundary, tmp_path, monkeypatch)
        except BaseException as exc:
            return exc
    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(at.wait(), 1)
        task.cancel('current close cancellation')
        assert await task is saved[0]
        assert task.cancelling() == 1 and boundary.closes == boundary.settled == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('wrapped', [False, True])
async def test_council_actual_five_second_deadline_is_operational_and_settles_own_count(tmp_path, monkeypatch, wrapped):
    saved = []
    async def close():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            saved.append(exc)
            if wrapped:
                raise RuntimeError('local deadline finalizer')
            raise
    boundary = Boundary(close=close)
    async def run():
        try:
            await invoke('council', boundary, tmp_path, monkeypatch)
        except (TimeoutError, RuntimeError) as exc:
            assert asyncio.current_task().cancelling() == 0
            return exc
        raise AssertionError('Deadline returned successful advice')
    outcome = await asyncio.wait_for(asyncio.create_task(run()), 8)
    assert not isinstance(outcome, asyncio.CancelledError) and len(saved) == 1
    assert boundary.closes == boundary.settled == 1


from test_council_handoff import engine


@pytest.mark.parametrize('consumer', ['engine', 'loop'])
@pytest.mark.parametrize('failed', [False, True])
async def test_actual_main_consumers_keep_operational_failure_and_first_usage(engine, tmp_path, consumer, failed):
    from dream.core.loop import AutonomousLoop
    from test_durable_autonomy import Reviewer
    from test_sdk_main_completion import configure
    at = asyncio.Event()
    saved = []
    async def close():
        if not failed:
            return
        try:
            raise asyncio.CancelledError('historical close cancellation')
        except asyncio.CancelledError as old:
            try:
                raise RuntimeError('retained operational close failure')
            except RuntimeError as fault:
                saved.append((old, fault))
        at.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as current:
            saved.append(current)
        raise saved[0][1]

    class Completed(Boundary):
        async def packets(self):
            self.owner = asyncio.current_task()
            with anyio.CancelScope():
                try:
                    yield sdk.AssistantMessage(content=[sdk.TextBlock(text='Fixture complete.\nSTATUS: DONE')],
                                               model='fixture-sdk')
                    yield terminal()
                finally:
                    assert asyncio.current_task() is self.owner
                    self.settled += 1
        async def query(self, prompt):
            await super().query(prompt)
            if consumer == 'loop':
                (loop.workspace / 'progress.md').write_text('# Progress\n\nFixture evidence.\n')

    boundary = Completed(close=close)
    await configure(engine, boundary)
    reviews = []
    loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs', evaluator_timeout=1,
        evaluator_backend_factory=lambda *args: reviews.append(args) or Reviewer())
    async def run():
        if consumer == 'engine':
            return [event async for event in engine.ask('q')]
        return await loop.run('fixture goal', acceptance_criteria='- Preserve fixture evidence.')
    task = asyncio.create_task(run())
    try:
        if failed:
            await asyncio.wait_for(at.wait(), 1)
            boundary.owner.cancel('consumed current cancellation')
        result = await task
        if consumer == 'engine':
            receipts = [e.data for e in result if e.kind == 'result']
            assert len(receipts) == 1 and receipts[0]['is_error'] is failed
            assert receipts[0]['usage'] == {'input_tokens': 7, 'output_tokens': 3}
            assert any(e.kind == 'error' for e in result) is failed
        else:
            assert result.status == ('error' if failed else 'done')
            rows = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
            assert sum(row['event'] == 'worker_finished' for row in rows) == len(reviews) == int(not failed)
            assert rows[-1]['state']['uncertain'] is failed
        assert engine.session_tokens == 10 and engine.total_cost_usd == 0.25
        assert engine.runtime_meter.prompt_tokens == 7 and engine.runtime_meter.output_tokens == 3
        assert bool(engine._pending_council) is failed
        assert (engine._pending_handoff is None) is (not failed)
        assert boundary.closes == boundary.settled == boundary.queries == 1
        assert engine.backend.client is boundary
        if failed:
            assert saved[0][0] is not saved[1]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
