"""User Council actions are serialized with the actual App input lifecycle."""
import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest
from rich.console import Console

from dream.core.backends.base import Event
from dream.core.moe import MoeConfig
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tui.app import App


@pytest.fixture
def app(tmp_path):
    obj = App.__new__(App)
    obj.workspace = tmp_path
    obj.provider = 'machx'
    obj.model = 'local-model'
    obj.provider_label = 'MachX'
    obj.provider_kind = 'machx'
    obj._gui_prompts = asyncio.Queue(maxsize=32)
    obj._deferred_gui_prompt = None
    obj._council_pending = None
    obj._accepting_input = True
    obj._interrupt_target = None
    obj._active_loop = None
    obj.interrupted = False
    obj.moe = None
    obj.bus = EventBus()
    obj.bus.publish(Event('user', 'Keep my existing conversation'))
    obj.renderer = SimpleNamespace(console=Console(record=True), system=lambda text: None)
    obj.engine = SimpleNamespace(_moe=None, model='local-model', provider=SimpleNamespace(key='machx'),
                                 provider_label='MachX', session_id='original-session', effort=None,
                                 vision_status=lambda: {'state': 'off', 'enabled': False, 'source': 'fixture'})
    return obj


async def test_configure_runs_on_consumer_task_and_preserves_history(app):
    owner = asyncio.current_task()
    async def configure(cfg, model=None):
        assert asyncio.current_task() is owner, 'Provider lifetime crossed into HTTP task'
        app.engine._moe = cfg
        app.engine.model = model
        app.engine.provider = SimpleNamespace(key=cfg.orchestrator)
        app.engine.provider_label = 'ChatGPT · Codex'
    app.engine.configure_council = configure
    cfg = asdict(MoeConfig('codex', ['machx']))
    waiter = asyncio.create_task(app._runtime_control({'action': 'council_configure', 'config': cfg, 'model': 'chosen'}))
    try:
        request = await asyncio.wait_for(app._gui_prompts.get(), 1)
        assert app.provider == 'machx'
        await app._execute_council_control(request)
        result = await waiter
        assert result['config']['orchestrator'] == 'codex'
        assert app.provider == 'codex' and app.model == 'chosen'
        assert app.engine.session_id == 'original-session'
        assert app.bus.conversation.snapshot()['events'][0]['data'] == 'Keep my existing conversation'
        assert app._council_pending is None
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.parametrize('busy', ['turn', 'queued', 'pending'])
async def test_mutation_refuses_busy_session_without_queuing(app, busy):
    if busy == 'turn':
        app._accepting_input = False
    elif busy == 'queued':
        app._gui_prompts.put_nowait('already queued')
    else:
        app._council_pending = object()
    before = app._gui_prompts.qsize()
    with pytest.raises(ValueError, match='Wait|busy'):
        await app._runtime_control({'action': 'council_configure', 'config': asdict(MoeConfig('codex', []))})
    assert app._gui_prompts.qsize() == before


async def test_new_prompt_cannot_race_pending_handoff(app):
    app._council_pending = object()
    with pytest.raises(ValueError, match='Council|council'):
        app._queue_gui_prompt('do work')
    assert app._gui_prompts.empty()


async def test_configuration_failure_is_visible_and_selection_stays(app):
    async def reject(*args, **kwargs):
        raise RuntimeError('provider login failed; previous provider restored')
    app.engine.configure_council = reject
    waiter = asyncio.create_task(app._runtime_control({'action': 'council_configure', 'config': asdict(MoeConfig('codex', []))}))
    try:
        request = await asyncio.wait_for(app._gui_prompts.get(), 1)
        await app._execute_council_control(request)
        with pytest.raises(ValueError, match='login failed'):
            await waiter
        assert app.provider == 'machx'
        assert app._council_pending is None
        assert any('login failed' in str(row['data']) for row in app.bus.conversation.snapshot()['events'])
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


async def test_direct_consultation_passes_exact_roster_limits_and_records_advice(app, monkeypatch):
    from dream.core import moe
    cfg = MoeConfig('machx', ['codex', 'gemini'], max_concurrency=1, timeout_seconds=123,
                    advisor_models={'codex': 'chosen-advisor'})
    app.engine._moe = cfg
    app.engine.council_context = lambda: 'Existing user context'
    recorded = []
    async def record(question, results):
        recorded.append((question, results))
    app.engine.record_council_results = record
    async def consult(advisors, question, context, **kwargs):
        assert advisors == ['codex'] and question == 'Review this'
        assert context == 'Existing user context'
        assert kwargs['max_concurrency'] == 1 and kwargs['timeout'] == 123
        assert kwargs['models'] == {'codex': 'chosen-advisor'}
        return [{'advisor': 'codex', 'label': 'Codex', 'answer': 'Check the edge case', 'model': 'chosen-advisor'}]
    monkeypatch.setattr(moe, 'council', consult)
    result = await app._consult_council('Review this', 'codex')
    assert recorded == [('Review this', result['results'])]
    assert 'Check the edge case' in str(app.bus.conversation.snapshot())


async def test_unknown_advisor_never_invoked(app, monkeypatch):
    app.engine._moe = MoeConfig('machx', ['codex'])
    with pytest.raises(ValueError, match='configured advisor'):
        await app._consult_council('Review', 'gemini')


async def test_explicit_council_gets_fresh_attributed_budget(app, monkeypatch, tmp_path):
    from dream import config
    from dream.core import moe
    from dream.telemetry.runtime import RunMeter
    from dream.tools.context import ToolContext, bound_runtime_meter
    app.engine._moe = MoeConfig('machx', ['codex'])
    app.engine.council_context = lambda: ''
    app.engine.profile = SimpleNamespace(max_run_tools=10, max_run_tokens=1000, max_run_seconds=20)
    stale = RunMeter('old', 1, app.engine.profile, started=0)
    app.engine._tool_context = ToolContext(None, None, None, 'original-session', runtime_meter=stale)
    monkeypatch.setattr(config, 'LOG_DIR', tmp_path)
    async def record(*args):
        pass
    app.engine.record_council_results = record
    async def consult(*args, **kwargs):
        meter = bound_runtime_meter()
        meter.check()
        meter.usage({'input_tokens': 10, 'output_tokens': 5}, phase='council:codex')
        return [{'advisor': 'codex', 'label': 'Codex', 'answer': 'Independent advice'}]
    monkeypatch.setattr(moe, 'council', consult)
    await app._consult_council('Another question')
    assert app.engine.runtime_meter.prompt_tokens == 10
    assert stale.prompt_tokens == 0


async def test_council_endpoint_requires_authentication(app):
    srv = StudioServer(app.bus, on_control=app._runtime_control)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test') as client:
        denied = await client.post('/api/control', json={'action': 'council_configure', 'config': {}})
        assert denied.status_code == 401
        assert app._gui_prompts.empty()


async def test_repl_consumes_structured_control_without_sending_model_prompt(app):
    owner = asyncio.current_task()
    async def configure(cfg, model=None):
        assert asyncio.current_task() is owner
        app.engine._moe = cfg
        app.engine.provider = SimpleNamespace(key=cfg.orchestrator)
        app.engine.provider_label = 'Codex'
        app.engine.model = 'default-model'
    app.engine.configure_council = configure
    async def noop():
        pass
    app.start = noop
    app._shutdown = noop
    app.renderer.sep = lambda: None
    requests = []
    async def incoming():
        if requests:
            raise EOFError
        pending = asyncio.create_task(app._runtime_control({'action': 'council_configure',
                                      'config': asdict(MoeConfig('codex', ['machx']))}))
        requests.append(pending)
        return await app._gui_prompts.get()
    app._next_input = incoming
    await app.run()
    result = await requests[0]
    assert result['config']['orchestrator'] == 'codex'
    assert app.provider == 'codex'
    assert app._council_pending is None


async def test_native_low_effort_is_readable_in_terminal(app):
    app.engine.store = None
    app.engine.effort = 'low'
    await app._command('/effort')
    assert 'low' in app.renderer.console.export_text()


def test_native_low_effort_does_not_break_footer(tmp_path):
    obj = App(provider='codex', workspace=tmp_path)
    obj.show_monitor = False
    obj.engine = SimpleNamespace(last_context_tokens=None, session_tokens=0, total_cost_usd=0,
                                 effort='low')
    assert obj._status_data()['effort'] == 'low'


async def test_claude_terminal_effort_reconfigures_on_lifecycle_task(app):
    owner = asyncio.current_task()
    app.provider = app.provider_kind = 'anthropic'
    app.engine.provider = SimpleNamespace(key='anthropic')
    app.engine.store = None
    app.engine.effort = None
    async def configure(cfg, model=None):
        assert asyncio.current_task() is owner
        app.engine._moe = cfg
        app.engine.effort = cfg.orchestrator_effort
    app.engine.configure_council = configure
    def obsolete_setter(level):
        raise AssertionError('Sync setter cannot apply a connected Claude SDK effort')
    app.engine.set_effort = obsolete_setter
    await app._command('/effort high')
    assert app.engine.effort == 'high'
    assert app.moe.orchestrator_effort == 'high'


def test_disconnected_main_is_visible_in_council_status(app):
    app.engine._backend_available = False
    status = app._council_status()
    assert status['main_available'] is False
    assert 'disconnected' in status['warning'].lower()


@pytest.mark.parametrize('command,expected', [('high', 'high'), ('med', 'medium'), ('max', 'max')])
async def test_terminal_effort_is_retained_by_council_roster_edit(app, command, expected):
    from dream.core.engine import Engine
    app.provider = app.provider_kind = 'codex'
    app.engine.provider = SimpleNamespace(key='codex')
    app.engine.store = None
    app.engine._moe = MoeConfig('codex', ['gemini'])
    app.engine.backend = SimpleNamespace(set_effort=lambda value: None)
    app.engine.set_effort = Engine.set_effort.__get__(app.engine)
    await app._command('/effort ' + command)
    status = app._council_status()
    assert status['config']['orchestrator_effort'] == expected
    assert status['config']['advisors'] == ['gemini']


async def test_http_waiter_disconnect_does_not_cancel_handoff(app):
    started, finish = asyncio.Event(), asyncio.Event()
    async def configure(cfg, model=None):
        started.set()
        await finish.wait()
        app.engine._moe = cfg
        app.engine.provider = SimpleNamespace(key=cfg.orchestrator)
        app.engine.provider_label = 'Codex'
    app.engine.configure_council = configure
    waiter = asyncio.create_task(app._runtime_control({'action': 'council_configure',
                                  'config': asdict(MoeConfig('codex', []))}))
    request = await asyncio.wait_for(app._gui_prompts.get(), 1)
    consumer = asyncio.create_task(app._execute_council_control(request))
    try:
        await asyncio.wait_for(started.wait(), 1)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert not request.future.cancelled()
        finish.set()
        await consumer
        assert request.future.result()['config']['orchestrator'] == 'codex'
        assert app.provider == 'codex' and app._council_pending is None
    finally:
        finish.set()
        await asyncio.gather(consumer, return_exceptions=True)


async def test_shutdown_resolves_queued_control_and_closes_input(app, monkeypatch):
    from dream import config
    from dream.gui import preview
    monkeypatch.setattr(config, 'CONSOLIDATE_ON_EXIT', False)
    monkeypatch.setattr(preview, '_PREVIEW', None)
    app.studio = app.gpu = None
    app.renderer.info = lambda text: None
    async def answer(*args):
        return 'n'
    async def stop(**kwargs):
        assert kwargs == {'consolidate': False}
    app._read_answer = answer
    app.engine.stop = stop
    waiter = asyncio.create_task(app._runtime_control({'action': 'council_configure',
                                  'config': asdict(MoeConfig('codex', []))}))
    await asyncio.wait_for(app._gui_prompts.get(), 1)
    await app._shutdown()
    with pytest.raises(ValueError, match='closed before'):
        await waiter
    assert app._council_pending is None
    assert app._accepting_input is False


@pytest.mark.parametrize('provider', ['gemini', 'grok'])
async def test_unsupported_terminal_effort_is_reported_without_mutation(app, provider):
    app.provider_kind = app.provider = provider
    app.engine.store = None
    app.engine.effort = None
    def reject(level):
        raise ValueError('Effort is not implemented for this provider')
    app.engine.set_effort = reject
    await app._command('/effort high')
    assert app.engine.effort is None
    assert 'could not change effort' in app.renderer.console.export_text()


async def test_cancel_council_closes_advisors_and_releases_input_slot(app, monkeypatch):
    from dream.core import moe
    cfg = MoeConfig('machx', ['codex'])
    app.engine._moe = cfg
    app.engine.council_context = lambda: ''
    started, finished = asyncio.Event(), asyncio.Event()
    async def consult(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()
    monkeypatch.setattr(moe, 'council', consult)
    waiter = asyncio.create_task(app._runtime_control({'action': 'council_ask', 'question': 'Slow question'}))
    try:
        request = await asyncio.wait_for(app._gui_prompts.get(), 1)
        consumer = asyncio.create_task(app._execute_council_control(request))
        await asyncio.wait_for(started.wait(), 1)
        assert (await app._runtime_control({'action': 'interrupt'}))['interrupted'] is True
        await consumer
        with pytest.raises(ValueError, match='interrupted'):
            await waiter
        assert finished.is_set()
        assert app._council_pending is None
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


async def test_active_work_control_uses_lifecycle_and_restores_main(app):
    owner = asyncio.current_task()
    cfg = MoeConfig('machx', ['codex'], advisor_models={'codex': 'gpt-6-astra'})
    app.engine._moe = cfg
    calls = []
    async def configure(selection, model=None):
        assert asyncio.current_task() is owner
        app.engine._moe = selection
        app.engine.provider = SimpleNamespace(key=selection.orchestrator)
        app.engine.provider_label = selection.orchestrator
        app.engine.model = model
    async def ask(prompt):
        calls.append(app.engine.provider.key)
        return True
    app.engine.configure_council = configure
    app._ask = ask
    waiter = asyncio.create_task(app._runtime_control({'action': 'council_work', 'question': 'Polish', 'advisor': 'codex'}))
    request = await asyncio.wait_for(app._gui_prompts.get(), 1)
    await app._execute_council_control(request)
    assert (await waiter) == {'completed': ['codex'], 'main_restored': True}
    assert calls == ['codex'] and app.engine._moe == cfg
    assert app._council_pending is None
    assert app.provider == 'machx'
