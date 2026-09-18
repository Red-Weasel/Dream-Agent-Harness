"""Desktop conversation, reconnect and permission regressions; no inference."""
import asyncio

import httpx
import pytest
from playwright.async_api import async_playwright, expect

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


@pytest.fixture
async def chat(monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    prompts = []
    controls = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, on_control=controls.append,
                       session={'provider': 'test', 'model': 'fixture', 'session_id': 'chat-test'})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1100, 'height': 800})
            page.set_default_timeout(3000)
            await page.goto(url + '&companion=1')
            await expect(page.locator('#stat')).to_have_text('Ready')
            yield srv, page, prompts, controls
            await browser.close()
    finally:
        await srv.stop()


async def test_desktop_streams_text_and_tool_results(chat):
    srv, page, prompts, _ = chat
    await expect(page.locator('#input')).to_be_visible()
    await page.locator('#input').fill('Read the project notes')
    await page.locator('#send').click()
    assert prompts == ['Read the project notes']
    srv.bus.publish(Event('text_delta', 'I will read the notes.'))
    srv.bus.publish(Event('tool_use', {'id': 'r1', 'name': 'read_file', 'input': {'path': 'README.md'}}))
    srv.bus.publish(Event('tool_result', {'id': 'r1', 'content': 'Project notes', 'is_error': False}))
    srv.bus.publish(Event('text_delta', 'The **notes** are ready.'))
    srv.bus.publish(Event('result', {}))
    await expect(page.locator('#stream')).to_contain_text('The notes are ready.')
    await expect(page.locator('.tool summary')).to_contain_text('read_file')
    await page.locator('.tool summary').click()
    await expect(page.locator('.tool')).to_contain_text('Project notes')
    await expect(page.locator('#stat')).to_have_text('Ready')


async def test_refresh_recovers_conversation_without_running_tool_again(chat):
    srv, page, prompts, _ = chat
    await srv._accept_prompt('Remember this message')
    srv.bus.publish(Event('text_delta', 'A retained answer.'))
    srv.bus.publish(Event('tool_use', {'id': 'r2', 'name': 'skill_open', 'input': {'name': 'verifying'}}))
    srv.bus.publish(Event('tool_result', {'id': 'r2', 'content': 'Read skill instructions'}))
    srv.bus.publish(Event('result', {}))
    await page.reload()
    await expect(page.locator('#stream')).to_contain_text('A retained answer.')
    await expect(page.locator('#stream .you')).to_have_count(1)
    await expect(page.locator('#stream .tool')).to_have_count(1)
    assert prompts == ['Remember this message']


async def test_failed_send_keeps_draft_and_does_not_claim_sent(chat):
    _, page, _, _ = chat
    await page.route('**/api/prompt', lambda r: r.fulfill(status=503, json={'error': 'Model is unavailable'}))
    await page.locator('#input').fill('Keep this draft')
    await page.locator('#send').click()
    await expect(page.locator('#input')).to_have_value('Keep this draft')
    await expect(page.locator('#stream .you')).to_have_count(0)
    await expect(page.locator('#studio-error')).to_contain_text('Model is unavailable')


async def test_stop_and_permission_are_available_in_chat(chat):
    srv, page, _, controls = chat
    srv.bus.publish(Event('turn_start', {}))
    await expect(page.get_by_role('button', name='Stop', exact=True)).to_be_visible()
    await page.get_by_role('button', name='Stop', exact=True).click()
    assert controls == [{'action': 'permission_mode_status'}, {'action': 'interrupt'}]
    pending = asyncio.create_task(srv.request_permission('run_bash', {'command': 'pwd'}, 'Run a command', {'y': 'Allow once', 'n': 'Deny'}))
    try:
        await expect(page.get_by_role('button', name='Allow once', exact=True)).to_be_visible()
        await page.get_by_role('button', name='Allow once', exact=True).click()
        assert await asyncio.wait_for(pending, 2) == 'y'
        await expect(page.get_by_role('button', name='Allow once', exact=True)).to_have_count(0)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_missing_or_async_prompt_handler_has_truthful_result():
    srv = StudioServer(EventBus())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test') as client:
        response = await client.post('/api/prompt', headers={'X-Dream-Token': srv.token}, json={'prompt': 'hello'})
        assert response.status_code == 503
        async def reject(text):
            raise ValueError('Choose a model first')
        srv._on_prompt = reject
        response = await client.post('/api/prompt', headers={'X-Dream-Token': srv.token}, json={'prompt': 'hello'})
        assert response.status_code == 400
        assert response.json()['error'] == 'Choose a model first'


async def test_pending_permission_survives_page_refresh(chat):
    srv, page, _, _ = chat
    pending = asyncio.create_task(srv.request_permission('write_file', {'path': 'a.txt'}, 'Allow file edit', {'y': 'Allow once', 'n': 'Deny'}))
    try:
        await expect(page.get_by_role('button', name='Deny', exact=True)).to_be_visible()
        await page.reload()
        await page.get_by_role('button', name='Deny', exact=True).click()
        assert await asyncio.wait_for(pending, 2) == 'n'
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_app_stop_cancels_active_turn():
    from dream.tui.app import App
    app = App.__new__(App)
    pending = asyncio.create_task(asyncio.Event().wait())
    app._interrupt_target = pending
    try:
        result = await app._runtime_control({'action': 'interrupt'})
        assert result['interrupted'] is True
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_app_routes_permission_to_connected_desktop(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from dream.tui.app import App
    from dream.core import policy
    app = App.__new__(App)
    app.engine = SimpleNamespace()
    app._active_loop = None
    app.workspace = tmp_path
    app.mode = 'ask'
    app._always_allow = set()
    app._perm_lock = asyncio.Lock()
    app.renderer = Mock()
    app._read_answer = AsyncMock(side_effect=AssertionError('Permission hidden in CLI'))
    app.studio = SimpleNamespace(client_count=1, request_permission=AsyncMock(return_value='y'))
    monkeypatch.setattr(policy, 'decide', lambda *a, **kw: ('ask', 'review file edit'))
    assert await app._decide_permission('write_file', {'path': 'hello.txt', 'content': 'hello'})
    app.studio.request_permission.assert_awaited_once()


def test_conversation_coalesces_stream_and_bounds_tool_display():
    from dream.gui.conversation import Conversation
    history = Conversation(max_events=4)
    for word in ['Hello', ' ', 'world']:
        history.append(Event('text_delta', word))
    history.append(Event('tool_result', {'content': 'x' * 30000}))
    snapshot = history.snapshot()
    assert snapshot['events'][0]['data'] == 'Hello world'
    assert len(snapshot['events'][1]['data']['content']) < 20200
    history.append(Event('studio', {'op': 'eval', 'code': 'must not replay'}))
    assert len(history.snapshot()['events']) == 2
    history.clear()
    assert history.snapshot()['events'] == []


async def test_permission_is_visible_in_regular_gui_too(chat):
    srv, page, _, _ = chat
    await page.goto(srv.url)
    pending = asyncio.create_task(srv.request_permission('run_bash', {'command': 'pwd'}, 'Run a command', {'y': 'Allow once', 'n': 'Deny'}))
    try:
        await page.get_by_role('button', name='Allow once', exact=True).click()
        assert await asyncio.wait_for(pending, 2) == 'y'
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_skills_page_adds_to_draft_without_sending(chat):
    _, page, prompts, _ = chat
    skill = {'name':'verifying','description':'Check the result.','enabled':True,
             'content':'Full verification instructions.','managed':False,'sha256':'fixture'}
    await page.route('**/api/skills', lambda r: r.fulfill(json={'skills':[skill]}))
    await page.route('**/api/skills/verifying', lambda r: r.fulfill(json=skill))
    await page.get_by_role('button', name='Go to Skills', exact=True).click()
    await page.locator('#dream-skills-page .library-list button').click()
    await expect(page.get_by_label('Skill Markdown', exact=True)).to_have_value('Full verification instructions.')
    await page.get_by_role('button', name='Use in chat', exact=True).click()
    await expect(page.locator('#input')).to_have_value('Use the verifying skill.')
    assert prompts == []


async def test_stop_finishes_pending_tool_badges(chat):
    srv, page, _, _ = chat
    srv.bus.publish(Event('tool_use', {'id': 'slow', 'name': 'run_bash', 'input': {'command': 'sleep 10'}}))
    srv.bus.publish(Event('turn_end', {'interrupted': True}))
    await expect(page.locator('.tool .badge')).to_have_text('Interrupted')
    await expect(page.locator('#stat')).to_have_text('Ready')


async def test_waiting_model_shows_elapsed_progress_outside_metrics(chat):
    srv, page, _, _ = chat
    await page.route('**/api/telemetry', lambda r: r.fulfill(json={'inference': {'state': 'waiting', 'elapsed_s': 65, 'out_tokens_est': 0}}))
    srv.bus.publish(Event('turn_start', {}))
    await expect(page.locator('#chat-progress')).to_contain_text('1m 5s')
    await expect(page.locator('#chat-progress')).to_contain_text('Waiting for first text')
    await expect(page.locator('#rail')).to_be_hidden()


async def test_unretainable_event_does_not_break_live_stream():
    class Resource:
        def __deepcopy__(self, memo):
            raise TypeError('not copyable')
    bus = EventBus()
    with bus.subscribe() as sub:
        event = Event('system', Resource())
        bus.publish(event)
        assert await asyncio.wait_for(sub.get(), 1) is event


async def test_incomplete_answer_is_visible_and_continue_is_explicit(chat):
    srv, page, prompts, _ = chat
    srv.bus.publish(Event('text_delta', 'A useful partial answer'))
    srv.bus.publish(Event('result', {'subtype': 'length', 'is_error': True}))
    await expect(page.locator('#stream')).to_contain_text('output limit')
    assert prompts == []
    await page.get_by_role('button', name='Continue answer', exact=True).click()
    assert len(prompts) == 1 and 'do not repeat completed actions' in prompts[0]


async def test_question_file_failure_keeps_form_and_success_has_one_user_message(chat):
    srv, page, prompts, _ = chat
    form = {'title': 'Send source', 'questions': [
        {'id': 'notes', 'kind': 'freeform', 'title': 'Notes'},
        {'id': 'source', 'kind': 'file', 'title': 'Source file'}]}
    srv.bus.publish(Event('studio', {'op': 'ask', 'form': form}))
    await page.locator('.qform textarea').fill('Use these notes')
    await page.locator('.qform input[type=file]').set_input_files({'name':'source.txt', 'mimeType':'text/plain', 'buffer':b'content'})
    await page.route('**/api/upload', lambda r: r.fulfill(status=413, json={'error':'File too large'}))
    await page.locator('.qform button[type=submit]').click()
    await expect(page.locator('#stream')).to_contain_text('File too large')
    await expect(page.locator('.qform')).to_be_visible()
    assert prompts == []
    await page.unroute('**/api/upload')
    await page.route('**/api/upload', lambda r: r.fulfill(json={'path':'uploads/source.txt'}))
    await page.locator('.qform button[type=submit]').click()
    await expect(page.locator('.qform')).to_have_count(0)
    await expect(page.locator('#stream .you')).to_have_count(1)
    assert len(prompts) == 1 and 'uploads/source.txt' in prompts[0]
