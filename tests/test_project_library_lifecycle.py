"""Project switching stays on the owning consumer and never starts a turn."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.projects.library import ProjectLibrary
from dream.tui.app import App


@pytest.fixture
def app(tmp_path, monkeypatch):
    obj = App.__new__(App)
    obj.workspace = tmp_path
    obj.provider = 'machx'
    obj.model = 'kept-model'
    obj.provider_label = obj.provider_kind = 'machx'
    obj.moe = None
    obj.mode = 'auto'
    obj._gui_prompts = asyncio.Queue(maxsize=32)
    obj._deferred_gui_prompt = obj._council_pending = obj._active_loop = obj._interrupt_target = None
    obj._accepting_input = True
    obj.interrupted = False
    obj._always_allow = {'previous grant'}
    obj.bus = EventBus()
    obj.renderer = SimpleNamespace(system=lambda text: None)
    obj.engine = SimpleNamespace(session_id='original', provider=SimpleNamespace(key='machx'),
                                 provider_label='machx', model='kept-model', effort='high', _moe=None)
    obj.events = []
    obj.owner = None
    async def stop(*, consolidate):
        assert asyncio.current_task() is obj.owner
        assert consolidate is False
        obj.events.append('stop')
    obj.engine.stop = stop
    def boot():
        candidate = SimpleNamespace(session_id='new-session', provider=SimpleNamespace(key=obj.provider),
                                    model=obj.model, provider_label='machx', effort=None, _moe=obj.moe)
        async def start():
            assert asyncio.current_task() is obj.owner
            obj.events.append('start')
        candidate.start = start
        return candidate
    monkeypatch.setattr(obj, '_boot_engine', boot)
    obj.bus.publish(Event('user', 'Previous project conversation'))
    return obj


async def test_open_runs_on_owner_preserves_selection_and_clears_grants(app, tmp_path):
    target = tmp_path / 'second'
    target.mkdir()
    project = ProjectLibrary().create('Second', str(target), 'Project instructions')
    reset = []
    app.studio = SimpleNamespace(reset_project_view=lambda: reset.append(app.engine.session_id))
    waiter = asyncio.create_task(app._runtime_control({'action':'project_open', 'project_id':project['id']}))
    request = await asyncio.wait_for(app._gui_prompts.get(), 1)
    assert app.events == []
    app.owner = asyncio.current_task()
    await app._execute_council_control(request)
    result = await waiter
    assert app.events == ['stop', 'start']
    assert app.workspace == target
    assert app.engine.model == 'kept-model' and app.engine.effort == 'high'
    assert app.mode == 'auto' and app._always_allow == set()
    assert reset == ['new-session']
    assert result['restoration'] == 'new_chat'
    assert result['session']['session_id'] == 'new-session'
    assert not any(e['kind'] == 'user' for e in app.bus.conversation.snapshot()['events'])


@pytest.mark.parametrize('busy', ['turn', 'queued', 'deferred', 'pending', 'loop'])
async def test_open_refuses_any_active_or_queued_work(app, tmp_path, busy):
    project = ProjectLibrary().create('Here', str(tmp_path))
    if busy == 'turn':
        app._accepting_input = False
    elif busy == 'queued':
        app._gui_prompts.put_nowait('queued task')
    elif busy == 'deferred':
        app._deferred_gui_prompt = 'terminal race'
    elif busy == 'pending':
        app._council_pending = object()
    else:
        app._active_loop = object()
    with pytest.raises(ValueError, match='Wait'):
        await app._runtime_control({'action':'project_open', 'project_id':project['id']})
    assert app.events == []


async def test_project_prompt_associates_session_without_duplicating_engine_instructions(app, tmp_path):
    project = ProjectLibrary().create('Here', str(tmp_path), 'Use the saved instruction.')
    text = await app._project_prompt('Do this next')
    assert text == 'Do this next'
    assert ProjectLibrary().get(project['id'])['session_count'] == 1
    assert app._active_project_id == project['id'] and app.events == []


async def test_queued_project_cannot_redirect_input_that_arrived_after_queue(app, tmp_path):
    project = ProjectLibrary().create('Here', str(tmp_path))
    waiter = asyncio.create_task(app._runtime_control({'action':'project_open', 'project_id':project['id']}))
    request = await asyncio.wait_for(app._gui_prompts.get(), 1)
    app._deferred_gui_prompt = 'queued work must keep original workspace'
    app.owner = asyncio.current_task()
    await app._execute_council_control(request)
    with pytest.raises(ValueError, match='queued|changed'):
        await waiter
    assert app.events == []


async def test_continue_loads_bounded_context_only_and_keeps_other_sessions_out(app, tmp_path, monkeypatch):
    from dream import config
    from dream.memory.store import MemoryStore
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'history.db')
    store = MemoryStore(config.DB_PATH)
    try:
        store.start_session('saved', 'Earlier work')
        store.add_turn('saved', 'user', 'Make a documented change')
        store.add_turn('saved', 'assistant', 'The documented change is complete')
        store.add_turn('saved', 'tool', 'run dangerous historical command')
        store.end_session('saved', 'Verified prior summary')
        project = ProjectLibrary().create('Here', str(tmp_path), session_id='saved')
        waiter = asyncio.create_task(app._runtime_control({'action':'project_open', 'project_id':project['id'], 'session_id':'saved'}))
        request = await asyncio.wait_for(app._gui_prompts.get(), 1)
        app.owner = asyncio.current_task()
        await app._execute_council_control(request)
        assert (await waiter)['restoration'] == 'saved_context'
        assert app.events == ['stop', 'start']
        prompt = await app._project_prompt('Continue with a new task')
        assert 'Verified prior summary' in prompt and 'documented change is complete' in prompt
        assert 'run dangerous historical command' not in prompt
        assert 'not native session resumption' in prompt
        app._reset_session_accounting()
        assert getattr(app, '_project_restored_context', None) is None
    finally:
        store.close()


async def test_project_start_failure_is_reported_and_blocks_work_until_reopened(app, tmp_path, monkeypatch):
    target = tmp_path / 'second'
    target.mkdir()
    project = ProjectLibrary().create('Second', str(target))
    original_boot = app._boot_engine
    def broken_boot():
        candidate = original_boot()
        async def fail_start():
            raise RuntimeError('fixture connection failed')
        candidate.start = fail_start
        return candidate
    monkeypatch.setattr(app, '_boot_engine', broken_boot)
    waiter = asyncio.create_task(app._runtime_control({'action':'project_open', 'project_id':project['id']}))
    request = await asyncio.wait_for(app._gui_prompts.get(), 1)
    app.owner = asyncio.current_task()
    await app._execute_council_control(request)
    with pytest.raises(ValueError, match='connection failed'):
        await waiter
    assert app.workspace == tmp_path
    assert app.engine.session_id == 'original'
    with pytest.raises(ValueError, match='prior connection may be closed'):
        await app._project_prompt('Do not send this')


async def test_pending_project_blocks_demonstration_queue_bypass(app, tmp_path, monkeypatch):
    from dream import demonstrations
    app._council_pending = object()
    monkeypatch.setattr(demonstrations, 'read', lambda identifier: {'id':identifier,'status':'ready'})
    with pytest.raises(ValueError, match='Wait'):
        await app._runtime_control({'action':'learn_analyze','id':'fixture'})
    assert app._gui_prompts.empty()


async def test_cancelled_http_waiter_does_not_cancel_lifecycle(app, tmp_path):
    project = ProjectLibrary().create('Here', str(tmp_path))
    waiter = asyncio.create_task(app._runtime_control({'action':'project_open', 'project_id':project['id']}))
    request = await asyncio.wait_for(app._gui_prompts.get(), 1)
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert not request.future.cancelled()
    app.owner = asyncio.current_task()
    await app._execute_council_control(request)
    assert request.future.result()['restoration'] == 'new_chat'
    assert app._council_pending is None and app.events == ['stop', 'start']


async def test_lifecycle_cancellation_cleans_candidate_on_owner(app, tmp_path, monkeypatch):
    project = ProjectLibrary().create('Here', str(tmp_path))
    original_boot = app._boot_engine
    def interrupted_boot():
        candidate = original_boot()
        async def interrupt_start():
            raise asyncio.CancelledError()
        async def cleanup():
            assert asyncio.current_task() is app.owner
            app.events.append('cleanup')
        candidate.start = interrupt_start
        candidate._cleanup = cleanup
        return candidate
    monkeypatch.setattr(app, '_boot_engine', interrupted_boot)
    waiter = asyncio.create_task(app._runtime_control({'action':'project_open', 'project_id':project['id']}))
    request = await asyncio.wait_for(app._gui_prompts.get(), 1)
    app.owner = asyncio.current_task()
    with pytest.raises(asyncio.CancelledError):
        await app._execute_council_control(request)
    with pytest.raises(ValueError, match='interrupted'):
        await waiter
    assert app.events == ['stop', 'cleanup']
    assert app._council_pending is None


@pytest.mark.parametrize('background', ['queued', 'running', 'recording'])
async def test_project_open_does_not_cancel_background_work(app, tmp_path, background):
    project = ProjectLibrary().create('Here', str(tmp_path))
    if background == 'recording':
        app.recorder = SimpleNamespace(process=object())
    else:
        app.engine.backend = SimpleNamespace(_idle_work=SimpleNamespace(
            snapshot=lambda: {'queued':1 if background == 'queued' else 0,
                              'running':'filer' if background == 'running' else None}))
    with pytest.raises(ValueError, match='Wait'):
        await asyncio.wait_for(app._runtime_control({'action':'project_open','project_id':project['id']}), .1)
    assert app.events == [] and app._gui_prompts.empty()


async def test_saved_context_is_retained_on_failure_and_consumed_after_success(app, tmp_path, monkeypatch):
    ProjectLibrary().create('Here', str(tmp_path))
    app._reset_session_accounting()
    app._project_restored_context = 'Archived context that should be sent once'
    prompts = []
    outcomes = [True, False, False]
    async def ask(prompt):
        prompts.append(prompt)
        yield Event('result', {'is_error':outcomes.pop(0), 'subtype':'success'})
    app.engine.ask = ask
    async def run_turn(coro):
        return await coro
    async def seal():
        pass
    monkeypatch.setattr(app, '_run_turn', run_turn)
    monkeypatch.setattr(app, '_seal_checkpoint', seal)
    monkeypatch.setattr(app, '_render_event', app.bus.publish)
    app.renderer = SimpleNamespace(live_begin=lambda _:None, working=lambda:None,
                                   live_end=lambda:None, end_line=lambda:None)
    assert not await app._ask('First request fails')
    assert await app._ask('Explicit retry succeeds')
    assert await app._ask('Next request')
    assert 'Archived context' in prompts[0] and 'Archived context' in prompts[1]
    assert prompts[2] == 'Next request'
    assert app._project_restored_context is None


async def test_project_switch_preserves_real_anyio_scope_ownership(app, tmp_path, monkeypatch):
    from test_council_handoff import Backend
    events = []
    original = Backend('original', events)
    replacement = Backend('replacement', events)
    app.engine.backend = original
    await original.connect()
    async def stop(*, consolidate):
        assert consolidate is False
        await original.disconnect()
    app.engine.stop = stop
    boot = app._boot_engine
    def scoped_boot():
        candidate = boot()
        candidate.backend = replacement
        candidate.start = replacement.connect
        candidate._cleanup = replacement.disconnect
        return candidate
    monkeypatch.setattr(app, '_boot_engine', scoped_boot)
    project = ProjectLibrary().create('Here', str(tmp_path))
    waiter = asyncio.create_task(app._runtime_control({'action':'project_open','project_id':project['id']}))
    try:
        request = await asyncio.wait_for(app._gui_prompts.get(), 1)
        await app._execute_council_control(request)
        assert (await waiter)['session']['session_id'] == 'new-session'
        assert [(kind, label) for kind, label, _ in events] == [
            ('connect','original'), ('disconnect','original'), ('connect','replacement')]
        assert all(task is asyncio.current_task() for _, _, task in events)
        assert original.prompts == replacement.prompts == []
    finally:
        await replacement.disconnect()
        await original.disconnect()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


async def test_startup_links_saved_workspace_before_any_council_or_chat_turn(app, tmp_path):
    project = ProjectLibrary().create('Here', str(tmp_path))
    await app._associate_project_on_start()
    assert ProjectLibrary().get(project['id'])['session_count'] == 1
    assert app._active_project_id == project['id'] and app.events == []


async def test_broken_catalog_reports_startup_warning_without_stopping_agent(app):
    path = ProjectLibrary().path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{broken catalog')
    await app._associate_project_on_start()
    assert app.engine.session_id == 'original' and app.events == []
    assert any(e['kind'] == 'error' and 'Project association unavailable' in e['data']
               for e in app.bus.conversation.snapshot()['events'])
    assert path.read_text() == '{broken catalog'
