"""Isolated drafting is owned by the App input consumer and its Stop lifecycle."""
import asyncio
from types import SimpleNamespace

import pytest

from dream.gui.bus import EventBus
from dream.tui.app import App


@pytest.fixture
def app(tmp_path):
    obj = App.__new__(App)
    obj.workspace = tmp_path
    obj.provider_label = 'Fixture'
    obj.engine = SimpleNamespace(provider=SimpleNamespace(key='fixture'), model='selected',
                                session_id='s1', effort='high', profile={'fixture': True})
    obj._gui_prompts = asyncio.Queue(maxsize=32)
    obj._deferred_gui_prompt = obj._council_pending = obj._interrupt_target = obj._active_loop = None
    obj._accepting_input = True
    obj.interrupted = False
    obj.bus = EventBus()
    return obj


def draft(app):
    return {'draft': 'Improve this prompt.', 'workspace': str(app.workspace), 'session_id': 's1'}


async def queued(app):
    waiter = asyncio.create_task(app._queue_prompt_optimizer(draft(app), []))
    request = await asyncio.wait_for(app._gui_prompts.get(), 1)
    return waiter, request


async def test_dispatch_uses_selected_model_without_evaluator_overrides(app, monkeypatch):
    import dream.prompt_optimizer as service
    monkeypatch.setenv('DREAM_EVALUATOR_PROVIDER', 'not-selected')
    monkeypatch.setenv('DREAM_EVALUATOR_MODEL', 'not-selected')
    calls = []
    async def optimize(body, files, workspace, settings):
        calls.append(settings)
        assert body == {'draft': 'Improve this prompt.'}
        assert files == [] and workspace == app.workspace
        assert app._interrupt_target is asyncio.current_task()
        return {'prompt': 'GOAL\nImprove clarity', 'questions': [], 'notes': [], 'evidence': []}
    monkeypatch.setattr(service, 'optimize', optimize)
    waiter, request = await queued(app)
    assert not calls
    await app._execute_council_control(request)
    assert (await waiter)['prompt'].startswith('GOAL')
    assert calls[0].provider is app.engine.provider and calls[0].model == 'selected'
    assert calls[0].profile == app.engine.profile
    assert app._council_pending is None and app._interrupt_target is None


@pytest.mark.parametrize('change', ['session', 'model', 'profile', 'generation', 'workspace'])
async def test_changed_selection_before_dispatch_never_calls_provider(app, monkeypatch, change):
    import dream.prompt_optimizer as service
    async def forbidden(*args):
        raise AssertionError('Stale optimizer context called provider')
    monkeypatch.setattr(service, 'optimize', forbidden)
    waiter, request = await queued(app)
    if change == 'session': app.engine.session_id = 's2'
    elif change == 'model': app.engine.model = 'changed'
    elif change == 'profile': app.engine.profile['fixture'] = False
    elif change == 'generation': app._project_turn_generation = 1
    else: app.workspace = app.workspace / 'different'
    await app._execute_council_control(request)
    with pytest.raises(ValueError, match='changed'):
        await waiter
    assert app._council_pending is None


@pytest.mark.parametrize('busy', ['turn', 'pending', 'queued', 'background'])
async def test_busy_drafting_rejected_before_enqueuing(app, busy):
    if busy == 'turn': app._accepting_input = False
    elif busy == 'pending': app._council_pending = object()
    elif busy == 'queued': app._gui_prompts.put_nowait('work')
    else: app.engine.backend = SimpleNamespace(_idle_work=SimpleNamespace(snapshot=lambda: {'running': 1}))
    before = app._gui_prompts.qsize()
    with pytest.raises(ValueError, match='Wait'):
        await app._queue_prompt_optimizer(draft(app), [])
    assert app._gui_prompts.qsize() == before


async def test_stop_cancels_only_optimizer_and_awaits_cleanup(app, monkeypatch):
    import dream.prompt_optimizer as service
    started, cleaned = asyncio.Event(), asyncio.Event()
    async def optimize(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()
    monkeypatch.setattr(service, 'optimize', optimize)
    waiter, request = await queued(app)
    consumer = asyncio.create_task(app._execute_council_control(request))
    await asyncio.wait_for(started.wait(), 1)
    app._on_sigint(None, None)
    await asyncio.wait_for(consumer, 1)
    with pytest.raises(ValueError, match='stopped'):
        await waiter
    assert cleaned.is_set() and app._council_pending is None
    assert app._interrupt_target is None and app.engine.session_id == 's1'
