"""Human approval time is excluded from active work, across Engine adapters."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dream.core.engine import Engine
from dream.core.profiles import PROFILES
from dream.telemetry import runtime
from dream.telemetry.runtime import RunLimit, RunMeter


@pytest.fixture
def clock(monkeypatch):
    now = SimpleNamespace(value=100.0)
    # Replace this module's time object, never the event loop's real clock.
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(monotonic=lambda: now.value))
    return now


def meter(clock, **kwargs):
    return RunMeter('fixture', 1, replace(PROFILES['lean'], max_run_seconds=10),
                    started=clock.value, **kwargs)


async def test_engine_approval_excludes_wait_but_expires_active_work(clock, tmp_path):
    async def permission(name, args):
        clock.value += 3683
        return True
    engine = Engine(provider='machx', workspace=tmp_path, can_use_tool=permission)
    engine.runtime_meter = meter(clock)
    clock.value += 4
    assert await engine._authorize_tool('write_file', {})
    engine.runtime_meter.check()
    status = engine.runtime_meter.summary()
    assert status['elapsed_s'] == 3687
    assert status['approval_wait_s'] == 3683
    assert status['active_elapsed_s'] == 4
    assert status['remaining_active_s'] == 6
    assert status['max_active_s'] == 10
    assert status['approval_pending'] is False
    clock.value += 6
    with pytest.raises(RunLimit, match=r'active work time budget.*10.*retained.*new turn'):
        engine.runtime_meter.check()


def test_nested_waits_count_union_and_trace_preserves_wall(clock, tmp_path):
    run = meter(clock, path=tmp_path / 'trace.jsonl')
    clock.value += 2
    with run.approval_wait():
        clock.value += 3
        with run.approval_wait():
            clock.value += 5
            assert run.summary()['approval_wait_s'] == 8
            assert run.summary()['approval_pending'] is True
        clock.value += 7
    run.check()
    run.record('fixture')
    event = json.loads(run.path.read_text())
    assert event['elapsed_s'] == 17
    assert run.summary()['approval_wait_s'] == 15
    assert run.summary()['active_elapsed_s'] == 2


@pytest.mark.parametrize('outcome', ['denied', 'exception', 'cancelled'])
async def test_permission_failure_always_resumes_budget(clock, tmp_path, outcome):
    async def permission(name, args):
        clock.value += 100
        if outcome == 'exception':
            raise ValueError('fixture failure')
        if outcome == 'cancelled':
            raise asyncio.CancelledError
        return False
    engine = Engine(provider='machx', workspace=tmp_path, can_use_tool=permission)
    run = engine.runtime_meter = meter(clock)
    if outcome == 'denied':
        assert not await engine._authorize_tool('write_file', {})
    else:
        with pytest.raises(ValueError if outcome == 'exception' else asyncio.CancelledError):
            await engine._authorize_tool('write_file', {})
    assert not run.summary()['approval_pending']
    clock.value += 10
    with pytest.raises(RunLimit):
        run.check()


async def test_concurrent_permissions_count_union(clock, tmp_path):
    entered = [asyncio.Event(), asyncio.Event()]
    released = [asyncio.Event(), asyncio.Event()]
    async def permission(name, args):
        index = args['index']
        entered[index].set()
        await released[index].wait()
        return True
    engine = Engine(provider='machx', workspace=tmp_path, can_use_tool=permission)
    run = engine.runtime_meter = meter(clock)
    one = asyncio.create_task(engine._authorize_tool('write_file', {'index': 0}))
    await entered[0].wait()
    clock.value += 5
    two = asyncio.create_task(engine._authorize_tool('write_file', {'index': 1}))
    await entered[1].wait()
    clock.value += 5
    released[0].set()
    await one
    clock.value += 5
    released[1].set()
    await two
    assert run.summary()['approval_wait_s'] == 15
    assert run.summary()['active_elapsed_s'] == 0


def test_finish_freezes_runtime_status_even_if_permission_unwinds_late(clock):
    run = meter(clock)
    clock.value += 2
    with run.approval_wait():
        clock.value += 5
        run.finish()
        clock.value += 100
    assert run.summary()['elapsed_s'] == 7
    assert run.summary()['active_elapsed_s'] == 2
    assert run.summary()['approval_wait_s'] == 5
    assert not run.summary()['approval_pending']


@pytest.mark.parametrize('cap', ['tools', 'tokens'])
def test_approval_pause_does_not_disable_non_time_caps(clock, cap):
    run = meter(clock)
    run.profile = replace(run.profile, max_run_tools=1, max_run_tokens=1)
    with run.approval_wait():
        if cap == 'tools':
            run.before_tool('fixture')
        else:
            run.usage({'input_tokens': 1})
        with pytest.raises(RunLimit, match='tool budget' if cap == 'tools' else 'token budget'):
            run.check()


def test_optional_wall_limit_counts_approval_wait(clock):
    run = meter(clock)
    run.profile = replace(run.profile, max_wall_seconds=20)
    with run.approval_wait():
        clock.value += 20
        with pytest.raises(RunLimit, match=r'wall time budget.*20.*retained.*new turn'):
            run.check()
        assert run.summary()['remaining_wall_s'] == 0
        assert run.summary()['max_wall_s'] == 20
    assert run.summary()['active_elapsed_s'] == 0


def test_wall_limit_settings_and_environment(monkeypatch):
    from dream.core.profiles import resolve_profile
    from dream.core.providers import get_provider
    provider = get_provider('machx')
    assert resolve_profile(provider).max_wall_seconds is None
    assert resolve_profile(provider, overrides={'max_wall_seconds': 12.5}).max_wall_seconds == 12.5
    assert resolve_profile(provider, overrides={'max_wall_seconds': None}).max_wall_seconds is None
    for invalid in [0, -1, True, '10', float('nan'), float('inf')]:
        with pytest.raises(ValueError):
            resolve_profile(provider, overrides={'max_wall_seconds': invalid})
    monkeypatch.setenv('DREAM_WALL_SECONDS', '30.5')
    assert resolve_profile(provider).max_wall_seconds == 30.5


@pytest.mark.parametrize('adapter', ['http', 'sdk', 'cli'])
@pytest.mark.parametrize('wall_limit', [None, 20])
async def test_real_adapter_approval_boundary_and_wall_cutoff(clock, tmp_path, adapter, wall_limit):
    from claude_agent_sdk import tool, PermissionResultAllow
    from dream.core.backends.openai_compat import OpenAICompatBackend
    from dream.core.backends.anthropic import AnthropicBackend
    from dream.core.providers import get_provider
    from dream.tools.context import ToolContext
    calls = []
    async def permission(name, args):
        clock.value += 100
        return True
    engine = Engine(provider='machx', workspace=tmp_path, can_use_tool=permission)
    engine._tool_context = ToolContext(None, None, None, engine.session_id)
    run = engine.runtime_meter = meter(clock)
    run.profile = replace(run.profile, max_run_tools=1, max_wall_seconds=wall_limit)
    @tool('write_file', 'Fixture write', {'path': str})
    async def write(args):
        calls.append(args['path'])
        return {'content': [{'type': 'text', 'text': 'done'}]}
    clock.value += 2
    if adapter == 'http':
        backend = OpenAICompatBackend(provider=get_provider('machx'), model='fixture',
            system_prompt='fixture', tools=[write], permission_cb=engine._can_use_tool,
            profile=run.profile)
        backend.runtime_meter = run
        engine.backend = backend
        text, failed = await backend._exec_tool('write_file', {'path': 'fixture'})
    elif adapter == 'sdk':
        backend = AnthropicBackend.__new__(AnthropicBackend)
        backend.permission_cb = engine._can_use_tool
        result = await backend._permission_handler('Write', {'file_path': 'fixture'}, None)
        failed = not isinstance(result, PermissionResultAllow)
        text = getattr(result, 'message', '')
    else:
        engine._started = True
        engine._session_tools = {'write_file': engine._wrap_tool(write)}
        result = await engine._call_bridge_tool('write_file', {'path': 'fixture'})
        failed = result.get('is_error') or result.get('isError') or False
        text = result['content'][0]['text']
    assert bool(failed) == (wall_limit is not None)
    if wall_limit is not None:
        assert 'wall time budget' in text
        assert calls == []
    elif adapter != 'sdk':
        assert calls == ['fixture']  # Last permitted tool must still execute.
    assert run.summary()['approval_wait_s'] == 100
    assert run.summary()['active_elapsed_s'] == 2


def test_shared_run_freezes_only_at_end(clock, tmp_path):
    engine = Engine(provider='machx', workspace=tmp_path)
    engine.begin_run_budget()
    run = engine.runtime_meter
    run.started = clock.value
    clock.value += 4
    assert run.summary()['finished'] is False
    engine.end_run_budget()
    clock.value += 100
    assert run.summary()['finished'] is True
    assert run.summary()['elapsed_s'] == 4
    assert engine._run_meter is None


from test_council_handoff import engine as prepared_engine


@pytest.mark.parametrize('shared', [False, True])
async def test_engine_ask_freezes_standalone_but_keeps_shared_run_open(clock, prepared_engine, shared):
    from dream.core.backends.base import Event
    engine = prepared_engine
    class Backend:
        async def ask(self, prompt):
            engine.runtime_meter.started = clock.value
            clock.value += 4
            yield Event('assistant_done', 'Fixture done')
            yield Event('result', {'subtype': 'success', 'is_error': False})
    engine.backend = Backend()
    if shared:
        engine.begin_run_budget()
        engine.runtime_meter.started = clock.value
    async for _ in engine.ask('fixture'):
        pass
    run = engine.runtime_meter
    assert run.summary()['finished'] is (not shared)
    clock.value += 100
    assert run.summary()['elapsed_s'] == (104 if shared else 4)
    if shared:
        engine.end_run_budget()
        clock.value += 100
        assert run.summary()['elapsed_s'] == 104
        assert run.summary()['finished'] is True


@pytest.mark.parametrize('wall_limit', [None, 20])
async def test_queued_filing_uses_live_budget_after_foreground_status_freezes(clock, monkeypatch, wall_limit):
    from dream.core.backends.openai_compat import OpenAICompatBackend
    from dream.core.providers import get_provider
    run = meter(clock)
    run.profile = replace(run.profile, auto_filer=True, max_wall_seconds=wall_limit)
    backend = OpenAICompatBackend(provider=get_provider('machx'), model='fixture',
        system_prompt='fixture', tools=[], permission_cb=None, profile=run.profile)
    backend.runtime_meter = run
    backend._subagents = {'filer': object()}
    class Queue:
        def enqueue(self, factory, **kwargs):
            self.factory = factory
        def snapshot(self):
            return {'foreground': False}
    queue = backend._idle_work = Queue()
    async def file_turn(worker, text):
        worker.runtime_meter.check()
        return []
    monkeypatch.setattr(OpenAICompatBackend, '_run_filer', file_turn)
    await backend._finish_filing('fixture')
    clock.value += 4
    run.finish()
    displayed = run.summary()
    clock.value += 100
    with pytest.raises(RunLimit, match='wall time budget' if wall_limit else 'active work time budget'):
        await queue.factory()
    assert run.summary() == displayed


async def test_sdk_budget_denial_cannot_be_reported_as_success_and_resets_next_turn():
    from claude_agent_sdk import ResultMessage
    from dream.core.backends.anthropic import AnthropicBackend
    async def denied(name, args):
        raise RunLimit('Run wall time budget reached; completed work is retained.')
    backend = AnthropicBackend(system_prompt='fixture', mcp_server={},
        preapproved_tool_ids=[], agents=None, permission_cb=denied, model='fixture')
    class Client:
        should_deny = True
        async def query(self, prompt):
            pass
        async def receive_response(self):
            if self.should_deny:
                await backend._permission_handler('Write', {}, None)
            yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id='fixture',
                usage={'input_tokens': 7}, result='Done')
    backend.client = Client()
    events = [event async for event in backend.ask('first')]
    result = next(event.data for event in events if event.kind == 'result')
    assert result['is_error'] is True
    assert result['subtype'] == 'success'  # Preserve the raw provider report.
    assert result['usage'] == {'input_tokens': 7}
    assert 'wall time budget' in result['completion_error']
    assert any(event.kind == 'error' and 'wall time budget' in event.data for event in events)
    backend.client.should_deny = False
    events = [event async for event in backend.ask('next')]
    assert next(event.data for event in events if event.kind == 'result')['is_error'] is False
    assert not any(event.kind == 'error' for event in events)


def test_live_budget_excludes_approval_that_outlasts_frozen_foreground(clock):
    run = meter(clock)
    clock.value += 2
    with run.approval_wait():
        clock.value += 5
        run.finish()
        clock.value += 100
    clock.value += 1
    run.check_time()
    assert run.summary()['active_elapsed_s'] == 2
    assert run.summary()['approval_wait_s'] == 5
    assert run.summary()['elapsed_s'] == 7
    clock.value += 7
    with pytest.raises(RunLimit, match='active work time budget'):
        run.check_time()


async def test_engine_sdk_budget_denial_finishes_as_error(clock, prepared_engine):
    from claude_agent_sdk import ResultMessage
    from dream.core.backends.anthropic import AnthropicBackend
    engine = prepared_engine
    engine.profile = replace(engine.profile, max_wall_seconds=20)
    async def permission(name, args):
        clock.value += 100
        return True
    engine._user_can_use_tool = permission
    backend = AnthropicBackend(system_prompt='fixture', mcp_server={},
        preapproved_tool_ids=[], agents=None, permission_cb=engine._can_use_tool, model='fixture')
    class Client:
        async def query(self, prompt):
            engine.runtime_meter.started = clock.value
        async def receive_response(self):
            await backend._permission_handler('Write', {}, None)
            yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id='fixture', result='Done')
    backend.client = Client()
    engine.backend = backend
    events = [event async for event in engine.ask('fixture')]
    result = next(event.data for event in events if event.kind == 'result')
    assert result['is_error'] is True
    assert 'wall time budget' in result['completion_error']
    assert engine.turn_timing.summary()['outcome'] == 'error'
