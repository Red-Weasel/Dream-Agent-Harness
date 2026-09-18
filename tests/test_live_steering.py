"""Model-free steering receipts and real HTTP boundary behavior."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_backend_resilience import _backend, _ScriptedClient, _text_round, _multi_call_round, _tool
from test_task_guidance_integration import engine  # noqa: F401


def make_inbox(tmp_path, capture=None):
    from dream.core.steering import SteeringInbox
    return SteeringInbox(tmp_path / 'receipts.json', 'session', 1,
        capture or (lambda role, text, on_commit: on_commit(3)))


@pytest.mark.asyncio
async def test_steering_channel_persists_and_retains_on_stop(tmp_path):
    from dream.core import engine
    assert hasattr(engine.Engine, 'ask_chat'), 'ordinary chat needs explicit steering ownership'
    from dream.core.steering import SteeringInbox
    logged = []
    def capture(role, text, *, on_commit):
        logged.append((role, text))
        on_commit(17)
    inbox = SteeringInbox(tmp_path / 'receipts.json', 'session', 1, capture)
    receipt = await inbox.submit('change the ending', 'a' * 32)
    assert receipt['status'] == 'pending'
    assert logged == [('user', 'change the ending')]
    assert json.loads(inbox.path.read_text())['receipts'][0]['text'] == 'change the ending'
    assert await inbox.submit('change the ending', 'a' * 32) == receipt
    assert len(logged) == 1
    await inbox.close()
    saved = json.loads(inbox.path.read_text())['receipts'][0]
    assert saved['status'] == 'retained'
    assert saved['user_turn_id'] == 17
    with pytest.raises(ValueError, match='closed'):
        await inbox.submit('late', 'b' * 32)


@pytest.mark.asyncio
async def test_server_steering_does_not_silently_use_ordinary_callback():
    from dream.gui.server import StudioServer
    from dream.gui.bus import EventBus
    import httpx
    ordinary = []
    server = StudioServer(EventBus(), on_prompt=ordinary.append)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test') as client:
        response = await client.post('/api/prompt', headers={'X-Dream-Token': server.token},
            json={'prompt': 'correction', 'delivery': 'steer', 'steering_id': 'a' * 32})
    assert response.status_code == 400
    assert ordinary == []


@pytest.mark.asyncio
async def test_final_response_checks_correction_before_finishing(tmp_path):
    from dream.core.steering import SteeringInbox
    inbox = SteeringInbox(tmp_path / 'receipts.json', 's', 1,
        lambda role, text, on_commit: on_commit(9))
    backend = _backend()
    backend.steering_inbox = inbox
    backend._client = _ScriptedClient([_text_round('old answer'), _text_round('corrected answer')])
    events = []
    async for event in backend.ask('original'):
        events.append(event)
        if event.kind == 'text_delta' and event.data == 'old answer':
            await inbox.submit('use blue instead', 'c' * 32)
    assert backend._client.attempts == 2
    assert [e.data for e in events if e.kind == 'assistant_done'] == ['old answer', 'corrected answer']
    assert sum(e.kind == 'result' for e in events) == 1
    assert inbox.receipts['c' * 32]['status'] == 'submitted'
    assert not inbox.open
    assert any('use blue instead' in str(m.get('content')) for m in backend._client.payloads[1]['messages'])


@pytest.mark.asyncio
async def test_steering_waits_for_whole_tool_batch(tmp_path):
    from dream.core.steering import SteeringInbox
    inbox = SteeringInbox(tmp_path / 'receipts.json', 's', 1,
        lambda role, text, on_commit: on_commit(9))
    observed = []
    async def tool(args):
        observed.append(len(observed))
        if len(observed) == 1:
            await inbox.submit('change direction', 'd' * 32)
        assert all('change direction' not in str(m.get('content')) for m in backend.messages)
        return 'real observation'
    backend = _backend([_tool('read_file', tool)])
    backend.steering_inbox = inbox
    backend._client = _ScriptedClient([_multi_call_round(), _text_round()])
    events = [e async for e in backend.ask('original')]
    assert len(observed) == 2
    assert inbox.receipts['d' * 32]['status'] == 'submitted'
    messages = backend._client.payloads[1]['messages']
    correction_index = next(i for i, m in enumerate(messages) if 'change direction' in str(m.get('content')))
    assert [m['role'] for m in messages[correction_index-2:correction_index]] == ['tool', 'tool']


@pytest.mark.asyncio
async def test_persistence_failure_rejects_acceptance(tmp_path, monkeypatch):
    inbox = make_inbox(tmp_path)
    def fail(*args): raise OSError('disk fixture')
    monkeypatch.setattr(inbox, '_save', fail)
    with pytest.raises(OSError):
        await inbox.submit('correction', 'a' * 32)
    assert not inbox.receipts


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_session_logging_failure_keeps_saved_text(tmp_path, committed):
    def fail(role, text, on_commit):
        if committed: on_commit(33)
        raise OSError('JSONL fixture')
    inbox = make_inbox(tmp_path, fail)
    receipt = await inbox.submit('recover me', 'a' * 32)
    assert receipt['status'] == ('pending' if committed else 'retained')
    assert receipt['warning']
    assert json.loads(inbox.path.read_text())['receipts'][0]['text'] == 'recover me'


@pytest.mark.asyncio
async def test_cancelled_submit_waits_for_owned_capture_and_close_retains(tmp_path):
    import threading
    entered, release = threading.Event(), threading.Event()
    def capture(role, text, on_commit):
        entered.set()
        assert release.wait(5)
        on_commit(33)
    inbox = make_inbox(tmp_path, capture)
    submit = asyncio.create_task(inbox.submit('recover me', 'a' * 32))
    await asyncio.to_thread(entered.wait, 5)
    submit.cancel()
    await asyncio.sleep(0)
    submit.cancel()
    close = asyncio.create_task(inbox.close())
    assert not close.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await submit
    await close
    saved = json.loads(inbox.path.read_text())['receipts'][0]
    assert saved['status'] == 'retained' and saved['user_turn_id'] == 33


@pytest.mark.asyncio
async def test_concurrent_duplicate_app_submit_is_idempotent(tmp_path):
    from dream.tui.app import App
    app = App.__new__(App)
    app._ordinary_chat_active = True
    inbox = make_inbox(tmp_path)
    app.engine = SimpleNamespace(backend=_backend(), _steering_inbox=inbox)
    receipts = await asyncio.gather(*(app._steer_gui_prompt('blue', 'a' * 32,
        {'session_id': inbox.session, 'turn': inbox.turn}) for _ in range(2)))
    assert all(r['status'] == 'pending' for r in receipts)
    assert len(inbox.receipts) == 1


@pytest.mark.asyncio
async def test_final_boundary_closes_intake_without_losing_a_draft(tmp_path):
    inbox = make_inbox(tmp_path)
    assert await inbox.drain(final=True) == []
    with pytest.raises(ValueError, match='closed'):
        await inbox.submit('too late', 'a' * 32)
    assert not inbox.receipts


@pytest.mark.asyncio
async def test_pending_input_is_not_submitted_on_context_admission_error(tmp_path, monkeypatch):
    from dream.core.context_budget import ContextOverflow
    inbox = make_inbox(tmp_path)
    backend = _backend()
    backend.steering_inbox = inbox
    backend._client = _ScriptedClient([_text_round()])
    await inbox.submit('correction', 'a' * 32)
    # Force admission through the existing explicit options branch.
    backend._local_options = {'fixture': True}
    def fail(*args): raise ContextOverflow('fixture full')
    monkeypatch.setattr(backend, '_admit_request', fail)
    events = [e async for e in backend.ask('original')]
    await inbox.close()
    assert backend._client.attempts == 0
    assert inbox.receipts['a' * 32]['status'] == 'retained'
    assert any(e.kind == 'result' and e.data['subtype'] == 'context_overflow' for e in events)


@pytest.mark.asyncio
async def test_native_and_loop_use_explicit_afterturn_queue():
    from dream.tui.app import App
    for backend in (SimpleNamespace(), _backend()):
        app = App.__new__(App)
        app.engine = SimpleNamespace(backend=backend)
        app._interrupt_target = object()
        app._gui_prompts = asyncio.Queue(maxsize=32)
        app._deferred_gui_prompt = None
        receipt = await app._steer_gui_prompt('next', 'a' * 32)
        assert receipt['status'] == 'queued_after_turn'
        assert await app._gui_prompts.get() == 'next'
        assert (await app._steer_gui_prompt('next', 'a' * 32))['duplicate']
        assert app._gui_prompts.empty()


@pytest.mark.asyncio
async def test_all_corrections_survive_context_compaction(tmp_path):
    from dream.core.backends.openai_compat import _compact_messages
    inbox = make_inbox(tmp_path)
    backend = _backend()
    backend.steering_inbox = inbox
    for index in range(3):
        await inbox.submit(f'constraint {index} ' + ('keep this instruction ' * 100), f'{index:032x}')
    await backend._apply_steering()
    backend.messages.append({'role': 'assistant', 'content': 'temporary result'})
    _compact_messages(backend.messages, 1)
    for index in range(3):
        assert any(f'constraint {index} ' + ('keep this instruction ' * 100) in str(m.get('content')) for m in backend.messages)


@pytest.mark.asyncio
async def test_cancel_during_submission_receipt_never_claims_unsent(tmp_path, monkeypatch):
    import threading
    inbox = make_inbox(tmp_path)
    await inbox.submit('already sent', 'a' * 32)
    await inbox.drain()
    entered, release = threading.Event(), threading.Event()
    save = inbox._save
    def hold(receipts):
        if receipts['a' * 32]['status'] == 'submitted':
            entered.set()
            assert release.wait(5)
        save(receipts)
    monkeypatch.setattr(inbox, '_save', hold)
    task = asyncio.create_task(inbox.submitted())
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    await inbox.close()
    assert inbox.receipts['a' * 32]['status'] == 'submitted'
    assert json.loads(inbox.path.read_text())['receipts'][0]['status'] == 'submitted'


@pytest.mark.asyncio
async def test_real_engine_logs_correction_and_same_turn_budget(engine):
    backend = engine.backend = _backend()
    backend._client = _ScriptedClient([_text_round('old'), _text_round('new')])
    events = []
    async for event in engine.ask_chat('Hello'):
        events.append(event)
        if event.kind == 'text_delta' and event.data == 'old':
            receipt = await engine._steering_inbox.submit('Use blue.', 'a' * 32)
            assert receipt['user_turn_id'] is not None
    assert engine._turn_index == 1
    users = [t['content'] for t in engine.store.session_turns(engine.session_id) if t['role'] == 'user']
    assert users == ['Hello', 'Use blue.']
    assert engine._steering_inbox.receipts['a' * 32]['status'] == 'submitted'
    assert backend.steering_inbox is None
    assert not engine._chat_steering_requested
    assert len([e for e in events if e.kind == 'result']) == 1


@pytest.mark.asyncio
async def test_real_engine_stop_retains_correction_without_second_request(engine):
    from contextlib import aclosing
    backend = engine.backend = _backend()
    backend._client = _ScriptedClient([_text_round('old')])
    async with aclosing(engine.ask_chat('Hello')) as stream:
        async for event in stream:
            if event.kind == 'text_delta':
                await engine._steering_inbox.submit('Use blue.', 'a' * 32)
                break
    assert backend._client.attempts == 1
    assert engine._steering_inbox.receipts['a' * 32]['status'] == 'retained'
    assert backend.steering_inbox is None
    assert not engine._chat_steering_requested


@pytest.mark.asyncio
async def test_engine_nonchat_does_not_open_steering(engine):
    backend = engine.backend = _backend()
    backend._client = _ScriptedClient([_text_round()])
    events = [e async for e in engine.ask('Hello')]
    assert getattr(engine, '_steering_inbox', None) is None
    assert getattr(backend, 'steering_inbox', None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('target', [None, {'session_id': 'other', 'turn': 1}, {'session_id': 'session', 'turn': 2}])
async def test_stale_target_never_redirects_into_current_worker(tmp_path, target):
    from dream.tui.app import App
    app = App.__new__(App)
    app._ordinary_chat_active = True
    inbox = make_inbox(tmp_path)
    app.engine = SimpleNamespace(backend=_backend(), _steering_inbox=inbox)
    with pytest.raises(ValueError, match='turn changed'):
        await app._steer_gui_prompt('blue', 'a' * 32, target)
    assert not inbox.receipts


@pytest.mark.asyncio
async def test_round_cap_retains_correction_without_sending_to_salvage(tmp_path, monkeypatch):
    from dream.core.backends import openai_compat
    monkeypatch.setattr(openai_compat, '_MAX_TOOL_ROUNDS', 1)
    inbox = make_inbox(tmp_path)
    backend = _backend()
    backend.steering_inbox = inbox
    backend._client = _ScriptedClient([_text_round('old')])
    async def salvage(*args):
        assert not inbox.open
        assert all('UNSENT_CORRECTION' not in str(m.get('content')) for m in backend.messages)
        return ''
    monkeypatch.setattr(backend, '_salvage', salvage)
    events = []
    async for event in backend.ask('original'):
        events.append(event)
        if event.kind == 'text_delta': await inbox.submit('UNSENT_CORRECTION', 'a' * 32)
    assert backend._client.attempts == 1
    assert inbox.receipts['a' * 32]['status'] == 'retained'
    assert events[-1].data['subtype'] == 'tool_round_limit'


@pytest.mark.asyncio
async def test_steering_never_bypasses_an_unresolved_permission(tmp_path):
    inbox = make_inbox(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    ran = []
    async def permission(name, args):
        entered.set()
        await release.wait()
        return False
    async def tool(args):
        ran.append(True)
        return 'not expected'
    backend = _backend([_tool('write_file', tool)])
    backend.permission_cb = permission
    backend.steering_inbox = inbox
    backend._client = _ScriptedClient([_multi_call_round(1, 'write_file'), _text_round()])
    async def drive(): return [e async for e in backend.ask('original')]
    worker = asyncio.create_task(drive())
    await asyncio.wait_for(entered.wait(), 3)
    await inbox.submit('use blue', 'a' * 32)
    assert backend._client.attempts == 1 and not ran
    assert inbox.receipts['a' * 32]['status'] == 'pending'
    release.set()
    events = await worker
    assert not ran
    assert inbox.receipts['a' * 32]['status'] == 'submitted'


@pytest.mark.asyncio
async def test_aggregate_capacity_commands_and_conflicting_ids(tmp_path):
    from dream.tui.app import App
    app = App.__new__(App)
    app._ordinary_chat_active = True
    inbox = make_inbox(tmp_path)
    app.engine = SimpleNamespace(backend=_backend(), _steering_inbox=inbox)
    app._gui_prompts = asyncio.Queue(maxsize=32)
    app._deferred_gui_prompt = None
    target = {'session_id': inbox.session, 'turn': inbox.turn}
    await app._steer_gui_prompt('blue', 'a' * 32, target)
    for index in range(31): app._queue_gui_prompt(str(index))
    with pytest.raises(ValueError, match='full'): app._queue_gui_prompt('overflow')
    with pytest.raises(ValueError, match='full'): await app._steer_gui_prompt('overflow', 'b' * 32, target)
    with pytest.raises(ValueError, match='different text'): await app._steer_gui_prompt('different', 'a' * 32, target)
    with pytest.raises(ValueError, match='Commands'): await app._steer_gui_prompt('/quit', 'b' * 32, target)
    assert (await app._steer_gui_prompt('blue', 'a' * 32, target))['duplicate']


@pytest.mark.asyncio
async def test_steering_api_passes_target_and_preserves_regular_callback():
    import httpx
    from dream.gui.server import StudioServer
    from dream.gui.bus import EventBus
    ordinary, corrections = [], []
    async def steer(text, identifier, target):
        corrections.append((text, identifier, target))
        return {'id': identifier, 'status': 'pending'}
    server = StudioServer(EventBus(), on_prompt=ordinary.append, on_steer=steer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test',
                               headers={'X-Dream-Token': server.token}) as client:
        assert (await client.post('/api/prompt', json={'prompt': 'ordinary'})).json() == {'ok': True}
        response = await client.post('/api/prompt', json={'prompt': 'correction', 'delivery': 'steer',
            'steering_id': 'a' * 32, 'steering_target': {'session_id': 's', 'turn': 7}})
    assert response.json()['steering']['status'] == 'pending'
    assert ordinary == ['ordinary']
    assert corrections == [('correction', 'a' * 32, {'session_id': 's', 'turn': 7})]


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [True, False])
async def test_stale_http_target_cannot_fall_back_into_another_worker_queue(native):
    from dream.tui.app import App
    app = App.__new__(App)
    app.engine = SimpleNamespace(backend=SimpleNamespace() if native else _backend())
    app._interrupt_target = object()
    app._gui_prompts = asyncio.Queue(maxsize=32)
    app._deferred_gui_prompt = None
    with pytest.raises(ValueError, match='target|turn changed'):
        await app._steer_gui_prompt('old correction', 'a' * 32, {'session_id': 'old', 'turn': 1})
    assert app._gui_prompts.empty()
