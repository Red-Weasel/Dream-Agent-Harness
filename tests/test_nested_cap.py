"""Nested Dream P10 (DREAM-197 UI): the 4 / 8 / 12 / 16 agents control in the top row over the contract in
NESTED_EVENTS.md, section P10. It reads `nested.max_workers` from POST /api/control {action: settings_get} (the
`workers` section's row, its labels and choices) and writes it with {action: settings_save, key, value, scope: global};
the checked choice is only ever the server's answer, refusals are said verbatim, it is a keyboard-operable radio group,
and its hint says when it applies and that workers beyond the engine's lanes queue. A scripted /api/control."""
from __future__ import annotations

import asyncio
import contextlib

import pytest
from playwright.async_api import expect

from test_nested_view import open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
KEY = 'nested.max_workers'
BAD = 'nested.max_workers must be 3, 7, 11 or 15 (with the orchestrator: 4, 8, 12 or 16 agents)'
HINT = "Takes effect from the orchestrator's next reply. Workers beyond the engine's lanes queue."


def view(value):
    """What settings_get answers (dream/gui/settings_panel.view), trimmed to the sections around the row."""
    row = {'key': KEY, 'label': 'Workers per reply', 'value': value, 'display': f'{value + 1} agents ({value} workers)',
           'source': 'global', 'edit': {'kind': 'choice', 'value': value, 'choices': [3, 7, 11, 15],
                                        'labels': {'3': '4 agents', '7': '8 agents', '11': '12 agents', '15': '16 agents'}}}
    return {'sections': [{'id': 'lanes', 'title': 'Engine lanes', 'intro': '', 'rows': [{'key': 'engine.parallel', 'value': 4}]},
                         {'id': 'workers', 'title': 'Workers', 'intro': '', 'rows': [row]}]}


async def scripted(s, bodies, state, hold=None):
    """settings_get answers the state's value; settings_save stores it (after `hold`, when given) and answers
    {key, scope, applies, settings}, or the refusal the state names. Every request carries the session's token."""
    async def handle(route, request):
        assert request.headers.get('x-dream-token') == s.srv.token
        body = request.post_data_json
        bodies.append(body)
        if body['action'] == 'settings_get':
            await route.fulfill(status=200, json={'ok': True, 'result': view(state['value'])})
            return
        if body['action'] == 'settings_save':
            if hold is not None:
                await hold.wait()
            if state.get('refuse'):
                await route.fulfill(status=400, json={'error': state['refuse']})
                return
            state['value'] = int(body['value'])
            await route.fulfill(status=200, json={'ok': True, 'result': {
                'key': KEY, 'scope': 'global', 'applies': "Now: from the main agent's next reply, and in new sessions.",
                'settings': view(state['value'])}})
            return
        await route.fulfill(status=200, json={'ok': True, 'result': {}})
    await s.page.route('**/api/control', handle)


async def test_the_control_reads_the_setting_and_saves_only_what_the_server_answers(studio):
    async with studio() as s:
        bodies, state, hold = [], {'value': 7}, asyncio.Event()
        await scripted(s, bodies, state, hold)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await expect(group).to_be_visible()
        radios = group.get_by_role('radio')
        await expect(radios).to_have_count(4)
        await expect(radios).to_have_text(['4', '8', '12', '16'])
        await expect(group.get_by_role('radio', name='8 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        for name in ('4 agents', '12 agents', '16 agents'):
            await expect(group.get_by_role('radio', name=name, exact=True)).to_have_attribute('aria-checked', 'false')
        await expect(group).to_have_attribute('title', HINT)
        await expect(page.locator('#nd-cap-hint')).to_have_text(HINT)                   # its description, for a screen reader
        assert {'action': 'settings_get'} in bodies
        # a click asks; the checked one changes only with the answer
        await group.get_by_role('radio', name='16 agents', exact=True).click()
        await asyncio.sleep(0.2)
        await expect(group.get_by_role('radio', name='8 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        await expect(group).to_have_attribute('aria-busy', 'true')
        hold.set()
        await expect(group.get_by_role('radio', name='16 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        await expect(group.get_by_role('radio', name='8 agents', exact=True)).to_have_attribute('aria-checked', 'false')
        assert bodies[-1] == {'action': 'settings_save', 'key': KEY, 'value': 15, 'scope': 'global'}
        # the view read again on the next visit shows what the server holds
        state['value'] = 11
        await s.page.locator('#dream-nav-chat').click()
        page = await open_nested(s)
        await expect(page.get_by_role('radio', name='12 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        assert s.errors == []


async def test_the_radio_group_works_from_the_keyboard(studio):
    async with studio() as s:
        bodies, state = [], {'value': 15}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        sixteen = group.get_by_role('radio', name='16 agents', exact=True)
        await expect(sixteen).to_have_attribute('aria-checked', 'true')
        tabbable = await group.get_by_role('radio').evaluate_all('rs => rs.map(r => r.tabIndex)')
        assert tabbable == [-1, -1, -1, 0], tabbable                                   # one tab stop: the checked one
        await sixteen.focus()
        await s.page.keyboard.press('ArrowRight')                                     # wraps to 4 and asks for it
        four = group.get_by_role('radio', name='4 agents', exact=True)
        await expect(four).to_be_focused()
        await expect(four).to_have_attribute('aria-checked', 'true')
        assert bodies[-1] == {'action': 'settings_save', 'key': KEY, 'value': 3, 'scope': 'global'}
        await s.page.keyboard.press('ArrowLeft')                                      # back to 16
        await expect(sixteen).to_have_attribute('aria-checked', 'true')
        await expect(sixteen).to_be_focused()
        await s.page.keyboard.press('ArrowDown')
        await expect(four).to_have_attribute('aria-checked', 'true')
        await s.page.keyboard.press('ArrowUp')
        await expect(sixteen).to_have_attribute('aria-checked', 'true')
        await s.page.keyboard.press('Shift+Tab')                                      # the group is one stop
        assert not await group.evaluate('g => g.contains(document.activeElement)')
        assert [b['value'] for b in bodies if b['action'] == 'settings_save'] == [3, 15, 3, 15]
        assert s.errors == []


async def test_a_refusal_is_said_verbatim_and_changes_nothing(studio):
    async with studio() as s:
        bodies, state = [], {'value': 7, 'refuse': BAD}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await group.get_by_role('radio', name='12 agents', exact=True).click()
        await expect(page.locator('#nd-cap-note')).to_have_text(BAD)
        await expect(page.locator('#nd-cap-note')).to_have_attribute('role', 'status')          # said there, once
        await s.page.wait_for_timeout(200)
        await expect(page.locator('#nd-live')).not_to_have_text(BAD)
        await expect(group.get_by_role('radio', name='8 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        await expect(group).not_to_have_attribute('aria-busy', 'true')
        state['refuse'] = None
        await group.get_by_role('radio', name='12 agents', exact=True).click()
        await expect(group.get_by_role('radio', name='12 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        await expect(page.locator('#nd-cap-note')).to_have_text('')
        assert s.errors == []


async def test_without_the_setting_there_is_no_control_and_a_failed_read_is_said(studio):
    async with studio() as s:
        async def handle(route, request):
            body = request.post_data_json
            if body['action'] == 'settings_get':
                await route.fulfill(status=200, json={'ok': True, 'result': {'sections': [{'id': 'lanes', 'rows': []}]}})
                return
            await route.fulfill(status=200, json={'ok': True, 'result': {}})
        await s.page.route('**/api/control', handle)
        page = await open_nested(s)
        await s.page.wait_for_timeout(300)
        await expect(page.locator('#nd-cap')).to_be_hidden()                          # a Dream without it shows none
        assert s.errors == []
    async with studio() as s:
        async def refuse(route, request):
            body = request.post_data_json
            if body['action'] == 'settings_get':
                await route.fulfill(status=503, json={'error': 'Runtime controls are unavailable'})
                return
            await route.fulfill(status=200, json={'ok': True, 'result': {}})
        await s.page.route('**/api/control', refuse)
        page = await open_nested(s)
        await expect(page.locator('#nd-cap-note')).to_have_text('Runtime controls are unavailable')
        radios = page.locator('#nd-cap [role=radio]')
        await expect(radios).to_have_count(4)                                         # the four choices, every one off
        await expect(radios).to_have_text(['4', '8', '12', '16'])
        for r in await radios.all():
            await expect(r).to_be_disabled()
            await expect(r).to_have_attribute('aria-checked', 'false')
        assert s.errors == []


async def test_the_real_settings_rows_drive_the_control(tmp_path, monkeypatch):
    """The merged backend (694990a, 3984cf8): the Studio route calls the app's own _runtime_control, which answers
    settings_get with the real `workers` row and writes settings_save through the settings writer (the runtime settings
    file is the test's own: tests/conftest.py). The default is 8 agents; a choice lands in the file; a reload reads it."""
    import contextlib
    from playwright.async_api import async_playwright
    from dream.core.profiles import read_settings
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    from dream.tui.app import App
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    app = App(provider='machx', workspace=tmp_path)
    srv = StudioServer(EventBus(), on_prompt=lambda *_: None, on_control=app._runtime_control,
                       session={'provider': 'MachX', 'model': 'fixture', 'session_id': 'cap-real', 'lanes': None})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1600, 'height': 1000})
            page.set_default_timeout(5000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto(url.replace('/#', '/?companion=1#'))
            await expect(page.locator('#stat')).to_have_text('Ready')
            await page.locator('#dream-nav-nested').click()
            group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
            await expect(group.get_by_role('radio', name='8 agents', exact=True)).to_have_attribute('aria-checked', 'true')   # the default, 7 workers
            await expect(group.get_by_role('radio')).to_have_text(['4', '8', '12', '16'])
            await group.get_by_role('radio', name='16 agents', exact=True).click()
            await expect(group.get_by_role('radio', name='16 agents', exact=True)).to_have_attribute('aria-checked', 'true')
            assert read_settings()['nested'] == {'max_workers': 15}
            await page.reload()
            await expect(page.locator('#stat')).to_have_text('Ready')
            await page.locator('#dream-nav-nested').click()
            await expect(page.get_by_role('radio', name='16 agents', exact=True)).to_have_attribute('aria-checked', 'true')
            assert errors == []
            await browser.close()
    finally:
        with contextlib.suppress(Exception):
            await srv.stop()


# --- gate P10 UI round 1 (gate-reports/p10ui-r1.md) -----------------------------------------------------------------

HINT_CLOUD = "Takes effect from the orchestrator's next reply. Parallel workers sets how many run at once; the rest queue."
HINT_ELSEWHERE = 'Applies to MachX and the OpenAI and xAI APIs; this provider runs its own sub-agents.'
# DREAM-213: on Claude the limit binds its local helpers (read at each delegate_local call); its own sub-agents are the SDK's
HINT_CLAUDE = "Applies to Claude's local helpers (delegate_local) from its next call; Claude's own sub-agents are the SDK's."
HINT_ONE = "Takes effect from the orchestrator's next reply. One runs at a time; the rest queue."
LANES_ENGINE = "The engine's lanes: how many of this session's workers can run at once"
LANES_CLOUD = "Parallel workers: how many of this session's workers run at once"
UNUSABLE = {
    'a value it refuses': '{"version": 1, "nested": {"max_workers": 12}}',
    'broken JSON': '{"version": 1, "nested": ',
    'a bad value beside a good limit': '{"version": 1, "engine": {"parallel": 99}, "nested": {"max_workers": 15}}',
    'an unreadable file': '{"version": 1, "nested": {"max_workers": 15}}',
}
LOOK = """e => { const c = getComputedStyle(e); return {bg: c.backgroundColor, fg: c.color, offset: c.outlineOffset, style: c.outlineStyle}; }"""


def rgb(css):
    r, g, b = [int(float(x)) for x in css[css.index('(') + 1:css.index(')')].split(',')[:3]]
    return {'r': r, 'g': g, 'b': b}


@contextlib.asynccontextmanager
async def real_route(tmp_path, monkeypatch):
    """The Studio route calls the app's own _runtime_control, which reads and writes the runtime settings file
    (the test's own: tests/conftest.py); every /api/control body the page sends is kept."""
    from types import SimpleNamespace
    from playwright.async_api import async_playwright
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    from dream.tui.app import App
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    app = App(provider='machx', workspace=tmp_path)
    srv = StudioServer(EventBus(), on_prompt=lambda *_: None, on_control=app._runtime_control,
                       session={'provider': 'MachX', 'model': 'fixture', 'session_id': 'cap-real', 'lanes': None})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1600, 'height': 1000})
            page.set_default_timeout(5000)
            errors, controls = [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda r: controls.append(r.post_data_json) if r.url.endswith('/api/control') else None)
            await page.goto(url.replace('/#', '/?companion=1#'))
            await expect(page.locator('#stat')).to_have_text('Ready')
            yield SimpleNamespace(app=app, page=page, errors=errors, controls=controls)
            await browser.close()
    finally:
        with contextlib.suppress(Exception):
            await srv.stop()


@pytest.mark.parametrize('case', list(UNUSABLE))
async def test_an_unusable_settings_file_keeps_the_control_with_dreams_reason(tmp_path, monkeypatch, case):
    """Gate P10 r1 #1: settings_get answers {ok: false, error, sections: []} for a settings file Dream cannot use; the
    control stayed hidden, as for a Dream without the setting. It shows, its four choices off, Dream's words beside it."""
    import os
    from dream.core.profiles import settings_path
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(UNUSABLE[case])
    if case == 'an unreadable file':
        os.chmod(path, 0)
    try:
        async with real_route(tmp_path, monkeypatch) as s:
            answer = await s.app._runtime_control({'action': 'settings_get'})
            assert answer.get('ok') is False and answer.get('error'), answer         # Dream's own view refuses the file
            await s.page.locator('#dream-nav-nested').click()
            await expect(s.page.locator('#nd-cap')).to_be_visible()
            await expect(s.page.locator('#nd-cap-note')).to_have_text(answer['error'])
            radios = s.page.get_by_role('radiogroup', name='Agents per reply', exact=True).get_by_role('radio')
            await expect(radios).to_have_count(4)
            for r in await radios.all():
                await expect(r).to_be_disabled()
            assert s.errors == []
    finally:
        os.chmod(path, 0o644)


async def test_a_file_that_turns_unusable_turns_the_choices_off_on_the_next_visit(tmp_path, monkeypatch):
    from dream.core.profiles import settings_path
    async with real_route(tmp_path, monkeypatch) as s:
        await s.page.locator('#dream-nav-nested').click()
        group = s.page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await expect(group.get_by_role('radio', name='8 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(UNUSABLE['broken JSON'])
        await s.page.locator('#dream-nav-chat').click()
        await s.page.locator('#dream-nav-nested').click()
        answer = await s.app._runtime_control({'action': 'settings_get'})
        await expect(s.page.locator('#nd-cap-note')).to_have_text(answer['error'])
        for r in await group.get_by_role('radio').all():
            await expect(r).to_be_disabled()
            await expect(r).to_have_attribute('aria-checked', 'false')
        assert s.errors == []


async def test_a_save_in_the_settings_dialog_reaches_the_control_and_a_shown_choice_still_saves(tmp_path, monkeypatch):
    """Gate P10 r1 #2: Controls -> Settings, opened from the Nested view's own header, saved 16 agents while the control
    kept showing 8, and clicking the shown 8 sent nothing. The control reads the setting again when that dialog closes;
    a click on the choice the control shows as checked still saves it (the view may be behind: another window or
    /settings in a terminal changed the file)."""
    from dream.core.profiles import read_settings
    async with real_route(tmp_path, monkeypatch) as s:
        await s.page.locator('#dream-nav-nested').click()
        group = s.page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await expect(group.get_by_role('radio', name='8 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        await s.page.locator('#dream-controls-open').click()
        await s.page.locator('#dc-tab-settings').click()
        row = s.page.locator('#dc-settings-content article[data-key="nested.max_workers"]')
        await row.locator('select').select_option('15')
        await row.get_by_role('button', name='Save').click()
        for _ in range(100):
            if read_settings().get('nested') == {'max_workers': 15}:
                break
            await asyncio.sleep(0.05)
        assert read_settings().get('nested') == {'max_workers': 15}
        await s.page.keyboard.press('Escape')
        await expect(s.page.locator('#dream-controls')).to_be_hidden()
        assert await s.page.evaluate('document.documentElement.dataset.dreamView') == 'nested'
        await expect(group.get_by_role('radio', name='16 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        # a change made elsewhere: the view is behind until it reads again, and the choice it shows still saves
        await s.app._runtime_control({'action': 'settings_save', 'key': KEY, 'value': 3, 'scope': 'global'})
        sent = sum(1 for c in s.controls if c and c.get('action') == 'settings_save')
        await group.get_by_role('radio', name='16 agents', exact=True).click()
        for _ in range(100):
            if read_settings().get('nested') == {'max_workers': 15}:
                break
            await asyncio.sleep(0.05)
        assert read_settings().get('nested') == {'max_workers': 15}
        assert sum(1 for c in s.controls if c and c.get('action') == 'settings_save') == sent + 1
        await expect(group.get_by_role('radio', name='16 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        assert s.errors == []


async def test_one_save_at_a_time_and_it_looks_busy(studio):
    """Gate P10 r1 #3 (mutant G5) and #7: a click while a save is on its way sends nothing; the group looks busy (the
    wait cursor, dimmed), not only aria-busy."""
    async with studio() as s:
        bodies, state, hold = [], {'value': 7}, asyncio.Event()
        await scripted(s, bodies, state, hold)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await group.get_by_role('radio', name='16 agents', exact=True).click()
        await expect(group).to_have_attribute('aria-busy', 'true')
        await group.get_by_role('radio', name='12 agents', exact=True).click()
        await group.get_by_role('radio', name='4 agents', exact=True).click()
        await s.page.wait_for_timeout(300)
        assert [b['value'] for b in bodies if b['action'] == 'settings_save'] == [15]
        look = await group.evaluate("g => [getComputedStyle(g).opacity, getComputedStyle(g.querySelector('[role=radio]')).cursor]")
        assert float(look[0]) < 1 and look[1] == 'progress', look
        hold.set()
        await expect(group.get_by_role('radio', name='16 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        look = await group.evaluate("g => [getComputedStyle(g).opacity, getComputedStyle(g.querySelector('[role=radio]')).cursor]")
        assert float(look[0]) == 1 and look[1] != 'progress', look
        assert s.errors == []


async def test_the_control_shows_what_dream_answered_not_what_it_asked(studio):
    """Gate P10 r1 #3 (mutant G9): the checked choice is the answer's, even when the answer holds another value."""
    async with studio() as s:
        bodies = []

        async def handle(route, request):
            body = request.post_data_json
            bodies.append(body)
            if body['action'] == 'settings_get':
                await route.fulfill(status=200, json={'ok': True, 'result': view(7)})
            elif body['action'] == 'settings_save':
                await route.fulfill(status=200, json={'ok': True, 'result': {'key': KEY, 'scope': 'global', 'applies': '', 'settings': view(11)}})
            else:
                await route.fulfill(status=200, json={'ok': True, 'result': {}})
        await s.page.route('**/api/control', handle)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await group.get_by_role('radio', name='16 agents', exact=True).click()
        await expect(group.get_by_role('radio', name='12 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        await expect(group.get_by_role('radio', name='16 agents', exact=True)).to_have_attribute('aria-checked', 'false')
        assert bodies[-1] == {'action': 'settings_save', 'key': KEY, 'value': 15, 'scope': 'global'} and s.errors == []


async def test_a_good_read_clears_an_old_failed_read(studio):
    """Gate P10 r1 #3 (mutant G22): a read that works clears the reason an earlier one left."""
    async with studio() as s:
        state = {'fail': True}

        async def handle(route, request):
            body = request.post_data_json
            if body['action'] == 'settings_get':
                if state['fail']:
                    await route.fulfill(status=503, json={'error': 'Runtime controls are unavailable'})
                else:
                    await route.fulfill(status=200, json={'ok': True, 'result': view(7)})
                return
            await route.fulfill(status=200, json={'ok': True, 'result': {}})
        await s.page.route('**/api/control', handle)
        page = await open_nested(s)
        await expect(page.locator('#nd-cap-note')).to_have_text('Runtime controls are unavailable')
        state['fail'] = False
        await s.page.locator('#dream-nav-chat').click()
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await expect(group.get_by_role('radio', name='8 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        await expect(page.locator('#nd-cap-note')).to_have_text('')
        await expect(group.get_by_role('radio', name='16 agents', exact=True)).to_be_enabled()
        assert s.errors == []


async def test_a_read_older_than_a_save_changes_nothing(studio):
    """Gate P10 r1 #8: a read that began before a save's answer carries the value from before it; the later save's
    answer stands."""
    async with studio() as s:
        state, release, held = {'value': 7, 'hold': False}, asyncio.Event(), []

        async def handle(route, request):
            body = request.post_data_json
            if body['action'] == 'settings_get':
                value = state['value']                                              # what the file held when it began
                if state['hold']:
                    held.append(1)
                    await release.wait()
                await route.fulfill(status=200, json={'ok': True, 'result': view(value)})
            elif body['action'] == 'settings_save':
                state['value'] = int(body['value'])
                await route.fulfill(status=200, json={'ok': True, 'result': {'key': KEY, 'scope': 'global', 'applies': '', 'settings': view(state['value'])}})
            else:
                await route.fulfill(status=200, json={'ok': True, 'result': {}})
        await s.page.route('**/api/control', handle)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await expect(group.get_by_role('radio', name='8 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        state['hold'] = True
        await s.page.locator('#dream-nav-chat').click()
        page = await open_nested(s)                                                   # this visit's read waits
        for _ in range(100):
            if held:
                break
            await asyncio.sleep(0.02)
        await group.get_by_role('radio', name='16 agents', exact=True).click()
        await expect(group.get_by_role('radio', name='16 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        release.set()                                                                 # the older read answers 8
        await s.page.wait_for_timeout(400)
        await expect(group.get_by_role('radio', name='16 agents', exact=True)).to_have_attribute('aria-checked', 'true')
        assert s.errors == []


@pytest.mark.parametrize('provider,hint,lanes', [('MachX', HINT, LANES_ENGINE), ('OpenAI', HINT_CLOUD, LANES_CLOUD),
                                                 ('Grok · xAI (API)', HINT_CLOUD, LANES_CLOUD),
                                                 ('Claude · Anthropic', HINT_CLAUDE, None), ('ChatGPT · Codex', HINT_ELSEWHERE, None)])
async def test_the_hint_says_what_sets_the_pace_on_this_provider(studio, provider, hint, lanes):
    """Gate P10 r1 #4 and #5: on the local engine, the lanes; on OpenAI and xAI, Parallel workers; the coding CLIs run
    their own sub-agents and take no limit from Dream; on Claude it binds the local helpers, not the SDK's own
    sub-agents (DREAM-213). The group's name says what it counts."""
    async with studio(provider=provider, lanes={'served': 4, 'busy': 1, 'queued': 0}) as s:
        bodies, state = [], {'value': 7}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await expect(group).to_have_attribute('title', hint)
        await expect(page.locator('#nd-cap-hint')).to_have_text(hint)
        await expect(page.locator('#nd-cap-l')).to_have_text('Agents')                  # the visible label stays short
        if lanes:
            await expect(page.locator('#nd-lanes')).to_have_attribute('title', lanes)
        assert s.errors == []


async def test_the_checked_choice_keeps_its_fill_under_focus_and_meets_non_text_contrast(studio):
    """Gate P10 r1 #6: keyboard focus must not repaint the checked segment, nothing clips its focus ring (the group
    clipped the ring's top and bottom), and the checked fill stands at 3:1 or more against the page and against the
    Customize panel (the segments' shared style), its text at 4.5:1."""
    from test_nested_contrast import _ratio
    async with studio() as s:
        bodies, state = [], {'value': 7}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        eight = group.get_by_role('radio', name='8 agents', exact=True)
        await expect(eight).to_have_attribute('aria-checked', 'true')
        await s.page.mouse.move(0, 0)
        rest = await eight.evaluate(LOOK)
        await page.locator('#nd-custom-btn').focus()
        for _ in range(6):
            if await eight.evaluate('e => e === document.activeElement'):
                break
            await s.page.keyboard.press('Tab')
        await expect(eight).to_be_focused()
        focused = await eight.evaluate(LOOK)
        assert focused['bg'] == rest['bg'] and focused['style'] != 'none', (rest, focused)
        assert await group.evaluate('g => getComputedStyle(g).overflow') == 'visible'           # nothing clips the ring
        page_bg = await s.page.evaluate("getComputedStyle(document.getElementById('dream-nested-page')).backgroundColor")
        assert _ratio(rgb(rest['bg']), rgb(page_bg)) >= 3, (rest, page_bg)
        assert _ratio(rgb(rest['fg']), rgb(rest['bg'])) >= 4.5, rest
        corners = await group.evaluate("""g => [...g.querySelectorAll('[role=radio]')].map(b => { const c = getComputedStyle(b);
            return [c.borderTopLeftRadius, c.borderTopRightRadius]; })""")
        assert corners[0][0] != '0px' and corners[-1][1] != '0px' and corners[1] == ['0px', '0px'], corners   # the group's rounded ends
        await page.locator('#nd-custom-btn').click()
        panel = page.locator('#nd-custom')
        await expect(panel).to_be_visible()
        on = panel.locator('.nd-seg button[aria-pressed=true]').first
        look = await on.evaluate(LOOK)
        panel_bg = await panel.evaluate('p => getComputedStyle(p).backgroundColor')
        assert _ratio(rgb(look['bg']), rgb(panel_bg)) >= 3 and _ratio(rgb(look['fg']), rgb(look['bg'])) >= 4.5, (look, panel_bg)
        await panel.locator('#nd-custom-close').focus()                                   # the keyboard onto Customize's pressed one
        for _ in range(8):
            if await on.evaluate('e => e === document.activeElement'):
                break
            await s.page.keyboard.press('Tab')
        await expect(on).to_be_focused()
        assert (await on.evaluate(LOOK))['bg'] == look['bg'], (look, await on.evaluate(LOOK))
        assert s.errors == []


async def test_the_choices_are_named_as_dream_names_them(studio):
    """Gate P10 r1 #3 (mutant G15): each choice's name is the view's own label for it, not one made up on the page."""
    async with studio() as s:
        named = view(7)
        named['sections'][1]['rows'][0]['edit']['labels'] = {'3': '4 agents (3 workers)', '7': '8 agents (7 workers)',
                                                             '11': '12 agents (11 workers)', '15': '16 agents (15 workers)'}

        async def handle(route, request):
            body = request.post_data_json
            await route.fulfill(status=200, json={'ok': True, 'result': named if body['action'] == 'settings_get' else {}})
        await s.page.route('**/api/control', handle)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await expect(group.get_by_role('radio', name='8 agents (7 workers)', exact=True)).to_have_attribute('aria-checked', 'true')
        await expect(group.get_by_role('radio')).to_have_text(['4', '8', '12', '16'])
        assert s.errors == []


async def test_on_a_local_openai_server_the_hint_says_one_runs_at_a_time(studio):
    """Gate P10 r2 #4: an OpenAI provider on this computer runs its task calls one at a time (its lanes say served 1);
    the hint says so rather than naming Parallel workers."""
    async with studio(provider='OpenAI', lanes={'served': 1, 'busy': 0, 'queued': 0}) as s:
        bodies, state = [], {'value': 7}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        group = page.get_by_role('radiogroup', name='Agents per reply', exact=True)
        await expect(group).to_have_attribute('title', HINT_ONE)
        await expect(page.locator('#nd-cap-hint')).to_have_text(HINT_ONE)
        assert s.errors == []


@pytest.mark.parametrize('w,h', [(901, 700), (1280, 600)])
async def test_the_group_stays_on_one_line_beside_a_long_reason(studio, w, h):
    """Gate P10 r2 #1: beside a long reason (an unusable settings file's, with its path) the group broke into rows at
    short sizes; the note wraps instead."""
    reason = ('The settings file /srv/workspace/dream-runtime/data/runtime-settings.json cannot be used: the '
              'engine.parallel entry is not valid. Dream never rewrites it; repair it by hand, then refresh. Nothing can '
              'be saved until then.')
    async with studio(width=w, height=h) as s:
        async def handle(route, request):
            body = request.post_data_json
            if body['action'] == 'settings_get':
                await route.fulfill(status=200, json={'ok': True, 'result': {'ok': False, 'error': reason, 'sections': []}})
                return
            await route.fulfill(status=200, json={'ok': True, 'result': {}})
        await s.page.route('**/api/control', handle)
        page = await open_nested(s)
        await expect(page.locator('#nd-cap-note')).to_have_text(reason)
        tops = await page.locator('#nd-cap [role=radio]').evaluate_all('rs => rs.map(r => Math.round(r.getBoundingClientRect().top))')
        assert len(tops) == 4 and len(set(tops)) == 1, (w, h, tops)
        assert s.errors == []
