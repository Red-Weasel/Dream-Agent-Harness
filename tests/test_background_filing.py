"""Background filing integration with fake transports; no model/GPU activity."""
import asyncio
import copy
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.engine import Engine
from dream.core.profiles import PROFILES
from dream.hooks import HookOutcome, HookReport
from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory
from dream.telemetry.runtime import RunMeter
from dream.tools.context import ToolContext, bind_context, ctx
from test_schema_deferral import _FakeClient, _backend, _text_round, _tool


async def collect(source):
    return [event async for event in source]


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    from dream.core.backends import openai_compat
    monkeypatch.setattr(openai_compat, '_AUTO_FILE', True)
    emitted = []
    engine = Engine(provider='machx', model='fixture', workspace=tmp_path, emit=emitted.append)
    engine.store = MemoryStore(tmp_path / 'fixture.db')
    engine.store.start_session(engine.session_id)
    engine.working = WorkingMemory(engine.store, engine.session_id)
    engine._tool_context = ToolContext(engine.store, engine.working, None,
                                      engine.session_id, workspace=tmp_path)
    specs = {name: SimpleNamespace(description=name, prompt=name, tool_names=[])
             for name in ('filer', 'verifier')}
    engine.backend = _backend(subagents=specs)
    engine.backend._client = _FakeClient([_text_round('Fixture answer.')])
    engine.backend.enable_background_filing(emitted.append)
    engine.backend._idle_work.idle_grace = 0
    engine._started = True
    try:
        yield engine, emitted
    finally:
        await engine.backend.close_background()
        engine.store.close()


@pytest.mark.asyncio
async def test_engine_result_is_available_while_optional_filing_remains_queued(engine, monkeypatch):
    engine, _ = engine
    engine.backend._idle_work.idle_grace = 60
    calls = []

    async def subagent(self, kind, prompt):
        calls.append(kind)
        return 'NOTHING', False

    monkeypatch.setattr(OpenAICompatBackend, '_run_subagent', subagent)
    events = await asyncio.wait_for(collect(engine.ask('hello fixture')), 2)
    assert any(event.kind == 'result' and not event.data['is_error'] for event in events)
    assert engine.backend.background_status()['queued'] == 1
    assert calls == []


@pytest.mark.asyncio
async def test_next_engine_turn_joins_filer_cleanup_before_meter_change_or_request(engine, monkeypatch):
    engine, _ = engine
    started, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active = False
    old_meter = None

    async def subagent(self, kind, prompt):
        nonlocal active
        assert kind == 'filer'
        active = True
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()
            assert engine.runtime_meter is old_meter
            assert ctx().runtime_meter is old_meter
            active = False

    monkeypatch.setattr(OpenAICompatBackend, '_run_subagent', subagent)
    client = engine.backend._client
    real_stream = client.stream

    def stream(*args, **kwargs):
        assert not active, 'foreground transport overlapped filing cleanup'
        return real_stream(*args, **kwargs)

    monkeypatch.setattr(client, 'stream', stream)
    await collect(engine.ask('first fixture'))
    old_meter = engine.runtime_meter
    await asyncio.wait_for(started.wait(), 1)
    next_turn = asyncio.create_task(collect(engine.ask('second fixture')))
    await asyncio.wait_for(cleanup.wait(), 1)
    assert not next_turn.done() and len(client.payloads) == 1
    assert engine.runtime_meter is old_meter
    # Prevent the second optional pass from starting while inspecting its lead result.
    engine.backend._idle_work.idle_grace = 60
    release.set()
    events = await asyncio.wait_for(next_turn, 2)
    assert len(client.payloads) == 2
    assert engine.runtime_meter is not old_meter
    assert engine.backend.background_status()['interrupted'] == 1
    assert any(event.kind == 'result' for event in events)


@pytest.mark.asyncio
async def test_required_verifier_stays_on_result_path_before_optional_queue(engine, monkeypatch):
    engine, _ = engine
    backend = engine.backend
    backend._idle_work.idle_grace = 60
    started, release = asyncio.Event(), asyncio.Event()
    kinds = []

    async def subagent(self, kind, prompt):
        kinds.append(kind)
        if kind == 'verifier':
            started.set()
            await release.wait()
            return 'PASS', False
        return 'NOTHING', False

    monkeypatch.setattr(OpenAICompatBackend, '_run_subagent', subagent)
    backend._verify_at_turn_end = 'fixture.html'
    pending = asyncio.create_task(collect(engine.ask('hello fixture')))
    await asyncio.wait_for(started.wait(), 1)
    assert not pending.done() and backend.background_status()['queued'] == 0
    release.set()
    events = await asyncio.wait_for(pending, 2)
    assert kinds == ['verifier']
    verifier = next(i for i, event in enumerate(events) if event.kind == 'system' and 'verifier: PASS' in event.data)
    result = next(i for i, event in enumerate(events) if event.kind == 'result')
    assert verifier < result and backend.background_status()['queued'] == 1


@pytest.mark.asyncio
async def test_deferred_turn_retains_history_usage_and_tool_context_after_later_turn(engine, monkeypatch):
    engine, emitted = engine
    backend = engine.backend
    backend._idle_work.idle_grace = 60
    # Run two real lead turns before allowing either queued filer to start.
    await collect(engine.ask('first source fixture'))
    old_meter = engine.runtime_meter
    await collect(engine.ask('second source fixture'))
    new_meter = engine.runtime_meter
    lead_history = copy.deepcopy(backend.messages)
    lead_usage = dict(backend._delegated_usage)
    observations = []
    finished = asyncio.Event()
    original_run = OpenAICompatBackend._run_subagent

    async def inspect_subagent(self, kind, prompt):
        observations.append((self, prompt, ctx().runtime_meter, list(self.messages)))
        result = await original_run(self, kind, prompt)
        if len(observations) == 2:
            finished.set()
        return result

    monkeypatch.setattr(OpenAICompatBackend, '_run_subagent', inspect_subagent)
    posts = []

    async def post(url, json):
        posts.append(copy.deepcopy(json))
        return SimpleNamespace(status_code=200, json=lambda: {
            'usage': {'prompt_tokens': 17, 'completion_tokens': 3},
            'choices': [{'message': {'content': 'NOTHING'}, 'finish_reason': 'stop'}],
        })

    monkeypatch.setattr(backend._client, 'post', post, raising=False)
    # Stop only the timer, preserving both jobs and the captured contexts.
    await backend.prepare_user_turn()
    backend._idle_work.idle_grace = 0
    backend.foreground_finished()
    await asyncio.wait_for(finished.wait(), 2)
    await asyncio.sleep(0)
    await backend.close_background()
    assert len(posts) == 2 and len(observations) == 2
    assert 'first source fixture' in observations[0][1]
    assert 'second source fixture' not in observations[0][1]
    assert observations[0][2] is old_meter and observations[1][2] is new_meter
    assert observations[0][3] == observations[1][3] == []
    assert observations[0][0] is not backend
    assert backend.messages == lead_history and backend._delegated_usage == lead_usage
    assert old_meter.phases['filer'] == {'requests': 1, 'prompt_tokens': 17, 'output_tokens': 3}
    assert new_meter.phases['filer'] == {'requests': 1, 'prompt_tokens': 17, 'output_tokens': 3}
    assert any(event.kind == 'background_work' and event.data['kind'] == 'completed' for event in emitted)


@pytest.mark.asyncio
async def test_wrapped_filing_tool_and_hooks_use_captured_old_meter(engine, monkeypatch, tmp_path):
    engine, _ = engine
    old = RunMeter(engine.session_id, 1, PROFILES['lean'], tmp_path / 'old.jsonl')
    new = RunMeter(engine.session_id, 2, PROFILES['lean'], tmp_path / 'new.jsonl')
    captured = replace(engine._tool_context, runtime_meter=old)
    engine.runtime_meter = new
    engine._tool_context.runtime_meter = new
    seen = []
    tool = _tool('fixture_memory_write')

    async def handler(args):
        seen.append(ctx().runtime_meter)
        ctx().runtime_meter.usage({'prompt_tokens': 5}, phase='fixture_tool')
        return {'content': [{'type': 'text', 'text': 'saved fixture'}]}

    tool.handler = handler

    async def hooks(event, payload):
        return HookReport(outcomes=[HookOutcome('fixture-hook', event, 'completed', True)])

    monkeypatch.setattr('dream.hooks.run_hooks', hooks)
    with bind_context(captured):
        result = await engine._wrap_tool(tool).handler({})
    assert not result.get('is_error') and seen == [old]
    assert old.prompt_tokens == 5 and new.prompt_tokens == 0
    events = [json.loads(line) for line in old.path.read_text().splitlines()]
    assert len([event for event in events if event['event'] == 'hook']) == 2
    assert not new.path.exists()


@pytest.mark.asyncio
async def test_filer_failure_is_visible_and_not_counted_as_completion(engine, monkeypatch):
    engine, emitted = engine
    failed = asyncio.Event()

    async def subagent(self, kind, prompt):
        return 'fixture service unavailable', True

    def observe(event):
        emitted.append(event)
        if event.kind == 'background_work' and event.data['kind'] == 'failed':
            failed.set()

    monkeypatch.setattr(OpenAICompatBackend, '_run_subagent', subagent)
    engine.backend._background_emit = observe
    engine.backend._idle_work.on_event = lambda data: observe(SimpleNamespace(kind='background_work', data=data))
    await collect(engine.ask('hello fixture'))
    await asyncio.wait_for(failed.wait(), 1)
    assert engine.backend.background_status()['failed'] == 1
    assert engine.backend.background_status()['completed'] == 0
    assert any(event.kind == 'system' and 'filer: could not run' in event.data for event in emitted)


@pytest.mark.asyncio
async def test_disconnect_joins_filing_before_closing_client(engine, monkeypatch):
    engine, _ = engine
    started, cleanup = asyncio.Event(), asyncio.Event()
    order = []

    async def subagent(self, kind, prompt):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleanup.set()
            order.append('filer cleaned')

    async def close():
        assert cleanup.is_set()
        order.append('client closed')

    monkeypatch.setattr(OpenAICompatBackend, '_run_subagent', subagent)
    monkeypatch.setattr(engine.backend._client, 'aclose', close, raising=False)
    await collect(engine.ask('hello fixture'))
    await asyncio.wait_for(started.wait(), 1)
    await engine.backend.disconnect()
    assert order == ['filer cleaned', 'client closed']
    assert engine.backend.background_status()['closed']
    assert not engine.backend.background_status()['running']


@pytest.mark.asyncio
async def test_engine_stop_joins_optional_work_before_consolidation(engine, monkeypatch):
    engine, _ = engine
    started = asyncio.Event()
    order = []

    async def subagent(self, kind, prompt):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            order.append('filer cleaned')

    async def consolidate(self):
        assert order == ['filer cleaned']
        order.append('consolidated')
        return 'fixture summary'

    async def cleanup(self):
        order.append('engine cleaned')

    monkeypatch.setattr(OpenAICompatBackend, '_run_subagent', subagent)
    monkeypatch.setattr(Engine, 'consolidate', consolidate)
    monkeypatch.setattr(Engine, '_cleanup', cleanup)
    await collect(engine.ask('hello fixture'))
    await asyncio.wait_for(started.wait(), 1)
    summary = await engine.stop()
    assert summary == 'fixture summary'
    assert order == ['filer cleaned', 'consolidated', 'engine cleaned']
