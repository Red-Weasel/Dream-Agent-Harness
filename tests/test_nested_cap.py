"""Nested Dream, Max agents (the owner's decision of 2026-10-01; it replaces the 4 / 8 / 12 / 16 agents control of P10,
DREAM-197). "Max agents" is how many sub-agents may run AT THE SAME TIME: extra agents wait in line and start when one
finishes; empty is no limit, the default. The top bar has a small number field labelled "Max agents" that reads the
setting `nested.max_agents` -- an integer of 1 or more, or absent for no limit -- from POST /api/control {action:
settings_get} and saves it with {action: settings_save, key, value, scope: global}, as the old control did for
nested.max_workers: the field shows only the server's answer, refusals are said verbatim, one save goes at a time, a
read older than a save changes nothing, a Dream without the setting shows no field and a failed read shows it off with
Dream's reason. The backend of the setting is another builder's (the settings branch): these tests answer the route as
the contract says; the ones on the real settings rows run once that branch is merged, and skip until then."""
from __future__ import annotations

import asyncio
import contextlib

import pytest
from playwright.async_api import expect

from test_nested_view import open_nested, studio  # noqa: F401

pytestmark = pytest.mark.asyncio
KEY = 'nested.max_agents'
BAD = 'nested.max_agents must be a whole number of 1 or more, or empty for no limit.'
HINT = 'How many sub-agents may run at the same time. Extra agents wait in line and start when one finishes. Empty means no limit.'
HELP = 'Max agents: how many sub-agents run at once; the rest wait in line. Empty: no limit.'


def view(value, help_text=None, edit=True, locked=None):
    """What settings_get answers (the shape of dream/gui/settings_panel.view), trimmed to the sections around the row."""
    row = {'key': KEY, 'label': 'Max agents', 'value': value, 'display': 'No limit' if value is None else str(value),
           'source': 'default' if value is None else 'global',
           'edit': {'kind': 'number', 'min': 1, 'value': value} if edit else None, 'locked': locked}
    if help_text:
        row['help'] = help_text
    return {'sections': [{'id': 'lanes', 'title': 'Engine lanes', 'intro': '', 'rows': [{'key': 'engine.parallel', 'value': 4}]},
                         {'id': 'workers', 'title': 'Workers', 'intro': '', 'rows': [row]}]}


def saved(value):
    return {'ok': True, 'result': {'key': KEY, 'scope': 'global', 'applies': 'Saved. From the next sub-agent it starts.',
                                   'settings': view(value)}}


async def scripted(s, bodies, state, hold=None):
    """settings_get answers the state's value; settings_save stores it (after `hold`, when given) and answers
    {key, scope, applies, settings}, or the refusal the state names. Every request carries the session's token."""
    async def handle(route, request):
        assert request.headers.get('x-dream-token') == s.srv.token
        body = request.post_data_json
        bodies.append(body)
        if body['action'] == 'settings_get':
            await route.fulfill(status=200, json={'ok': True, 'result': view(state['value'], state.get('help'))})
            return
        if body['action'] == 'settings_save':
            if hold is not None:
                await hold.wait()
            if state.get('refuse'):
                await route.fulfill(status=400, json={'error': state['refuse']})
                return
            state['value'] = body['value']
            await route.fulfill(status=200, json=saved(state['value']))
            return
        await route.fulfill(status=200, json={'ok': True, 'result': {}})
    await s.page.route('**/api/control', handle)


def saves(bodies):
    return [b['value'] for b in bodies if b['action'] == 'settings_save']


async def test_the_field_reads_the_setting_and_saves_only_what_the_server_answers(studio):  # noqa: F811
    async with studio() as s:
        bodies, state, hold = [], {'value': 6}, asyncio.Event()
        await scripted(s, bodies, state, hold)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await expect(field).to_be_visible()
        await expect(field).to_have_value('6')
        await expect(field).to_have_attribute('placeholder', 'No limit')
        await expect(page.locator('#nd-maxw')).to_be_visible()
        assert await page.locator('.nd-top #nd-max').count() == 1                          # in the top bar
        assert {'action': 'settings_get'} in bodies
        await expect(page.locator('#nd-cap')).to_have_count(0)                             # the 4/8/12/16 group is gone
        await field.fill('10')
        await field.press('Enter')
        await expect(field).to_have_attribute('aria-busy', 'true')
        await expect(field).to_have_value('10')                                             # what was typed, while it is on its way
        assert bodies[-1] == {'action': 'settings_save', 'key': KEY, 'value': 10, 'scope': 'global'}
        hold.set()
        await expect(field).not_to_have_attribute('aria-busy', 'true')
        await expect(field).to_have_value('10')
        # the view read again on the next visit shows what the server holds
        state['value'] = 3
        await s.page.locator('#dream-nav-chat').click()
        page = await open_nested(s)
        await expect(page.get_by_label('Max agents', exact=True)).to_have_value('3')
        assert s.errors == []


async def test_empty_is_no_limit_both_ways(studio):  # noqa: F811
    async with studio() as s:
        bodies, state = [], {'value': None}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await expect(field).to_have_value('')                                               # absent: no limit, the placeholder says so
        await expect(field).to_have_attribute('placeholder', 'No limit')
        await type_and_await_save(s.page, field, '4')   # answered before the next one: a save while one is busy is dropped
        await expect(field).to_have_value('4')
        await type_and_await_save(s.page, field, '')    # cleared: back to no limit
        await expect(field).to_have_value('')
        assert saves(bodies) == [4, None], bodies
        assert state['value'] is None and s.errors == []


async def test_a_refusal_is_said_verbatim_and_the_answer_comes_back(studio):  # noqa: F811
    async with studio() as s:
        bodies, state = [], {'value': 6, 'refuse': BAD}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await expect(field).to_have_value('6')
        await field.fill('0')
        await field.press('Enter')
        note = page.locator('#nd-max-note')
        await expect(note).to_have_text(BAD)
        await expect(note).to_have_attribute('role', 'status')                              # said there, once
        await expect(note).to_be_visible()
        await s.page.wait_for_timeout(200)
        await expect(page.locator('#nd-live')).not_to_have_text(BAD)
        await expect(field).to_have_value('6')                                              # the server's value, not the refused one
        await expect(field).not_to_have_attribute('aria-busy', 'true')
        assert saves(bodies) == [0]
        state['refuse'] = None
        await field.fill('2')
        await field.press('Enter')
        await expect(field).to_have_value('2')
        await expect(note).to_have_text('')
        await expect(page.locator('#nd-notes')).to_be_hidden()                             # nothing left to say: the line goes
        assert s.errors == []


async def test_text_that_is_not_a_whole_number_goes_as_typed_and_the_server_says_why(studio):  # noqa: F811
    async with studio() as s:
        bodies, state = [], {'value': None, 'refuse': BAD}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        for typed in ('two', '-3', '2.5'):
            await field.fill(typed)
            await field.press('Enter')
            await expect(page.locator('#nd-max-note')).to_have_text(BAD)
            await expect(field).to_have_value('')                                           # still no limit: nothing was saved
        assert saves(bodies) == ['two', '-3', '2.5'], bodies                                # never guessed into a number, never a silent clear
        assert s.errors == []


async def test_leaving_the_field_saves_and_escape_puts_the_answer_back(studio):  # noqa: F811
    async with studio() as s:
        bodies, state = [], {'value': 8}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await expect(field).to_have_value('8')
        await field.fill('5')
        await s.page.keyboard.press('Tab')                                                 # left changed: saved as typed
        await expect(field).to_have_value('5')
        assert saves(bodies) == [5]
        await field.focus()
        await s.page.keyboard.press('Control+A')
        await s.page.keyboard.type('9')
        await s.page.keyboard.press('Escape')                                              # not saved: the answer comes back
        await expect(field).to_have_value('5')
        await expect(field).to_be_focused()
        await s.page.keyboard.press('Tab')
        await s.page.wait_for_timeout(200)
        assert saves(bodies) == [5]                                                        # leaving it unchanged saves nothing
        assert s.errors == []


async def test_typing_digits_and_letters_in_the_field_never_fires_the_view_shortcuts(studio):  # noqa: F811
    from test_nested_drawer import lanes
    async with studio() as s:
        bodies, state = [], {'value': None}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        lanes(s, 3, {})
        await expect(page.locator('.nd-pips .nd-pip')).to_have_count(3)
        field = page.get_by_label('Max agents', exact=True)
        await field.click()
        await s.page.keyboard.type('2a1')                                                  # digits open worker N, `a` the drawer: not here
        await s.page.wait_for_timeout(800)
        await expect(page.locator('#nd-drawer')).to_be_hidden()
        await expect(field).to_have_value('2a1')
        assert saves(bodies) == [] and s.errors == []


async def test_without_the_setting_there_is_no_field_and_a_failed_read_is_said(studio):  # noqa: F811
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
        await expect(page.locator('#nd-maxw')).to_be_hidden()                              # a Dream without it shows none
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
        await expect(page.locator('#nd-max-note')).to_have_text('Runtime controls are unavailable')
        field = page.get_by_label('Max agents', exact=True)
        await expect(field).to_be_visible()
        await expect(field).to_be_disabled()                                              # off, with Dream's reason beside it
        await expect(field).to_have_value('')
        assert s.errors == []


async def test_one_save_at_a_time_and_it_looks_busy(studio):  # noqa: F811
    async with studio() as s:
        bodies, state, hold = [], {'value': 7}, asyncio.Event()
        await scripted(s, bodies, state, hold)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await field.fill('12')
        await field.press('Enter')
        await expect(field).to_have_attribute('aria-busy', 'true')
        await field.press('Enter')                                                          # again while it is on its way
        await field.evaluate("f => f.dispatchEvent(new Event('change', {bubbles: true}))")
        await s.page.wait_for_timeout(300)
        assert saves(bodies) == [12]
        look = await field.evaluate("f => [getComputedStyle(f).opacity, getComputedStyle(f).cursor, f.readOnly]")
        assert float(look[0]) < 1 and look[1] == 'progress' and look[2] is True, look
        hold.set()
        await expect(field).not_to_have_attribute('aria-busy', 'true')
        look = await field.evaluate("f => [getComputedStyle(f).opacity, getComputedStyle(f).cursor, f.readOnly]")
        assert float(look[0]) == 1 and look[1] != 'progress' and look[2] is False, look
        assert s.errors == []


async def test_the_field_shows_what_dream_answered_not_what_it_asked(studio):  # noqa: F811
    async with studio() as s:
        bodies = []

        async def handle(route, request):
            body = request.post_data_json
            bodies.append(body)
            if body['action'] == 'settings_get':
                await route.fulfill(status=200, json={'ok': True, 'result': view(7)})
            elif body['action'] == 'settings_save':
                await route.fulfill(status=200, json=saved(11))
            else:
                await route.fulfill(status=200, json={'ok': True, 'result': {}})
        await s.page.route('**/api/control', handle)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await field.fill('16')
        await field.press('Enter')
        await expect(field).to_have_value('11')
        assert bodies[-1] == {'action': 'settings_save', 'key': KEY, 'value': 16, 'scope': 'global'} and s.errors == []


async def test_a_good_read_clears_an_old_failed_read(studio):  # noqa: F811
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
        await expect(page.locator('#nd-max-note')).to_have_text('Runtime controls are unavailable')
        state['fail'] = False
        await s.page.locator('#dream-nav-chat').click()
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await expect(field).to_have_value('7')
        await expect(field).to_be_enabled()
        await expect(page.locator('#nd-max-note')).to_have_text('')
        assert s.errors == []


async def test_a_read_older_than_a_save_changes_nothing(studio):  # noqa: F811
    """A read that began before a save's answer carries the value from before it; the later save's answer stands."""
    async with studio() as s:
        state, release, held = {'value': 7, 'hold': False}, asyncio.Event(), []

        async def handle(route, request):
            body = request.post_data_json
            if body['action'] == 'settings_get':
                value = state['value']                                                      # what the file held when it began
                if state['hold']:
                    held.append(1)
                    await release.wait()
                await route.fulfill(status=200, json={'ok': True, 'result': view(value)})
            elif body['action'] == 'settings_save':
                state['value'] = body['value']
                await route.fulfill(status=200, json=saved(state['value']))
            else:
                await route.fulfill(status=200, json={'ok': True, 'result': {}})
        await s.page.route('**/api/control', handle)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await expect(field).to_have_value('7')
        state['hold'] = True
        await s.page.locator('#dream-nav-chat').click()
        page = await open_nested(s)                                                         # this visit's read waits
        for _ in range(100):
            if held:
                break
            await asyncio.sleep(0.02)
        await field.fill('16')
        await field.press('Enter')
        await expect(field).to_have_value('16')
        release.set()                                                                       # the older read answers 7
        await s.page.wait_for_timeout(400)
        await expect(field).to_have_value('16')
        assert s.errors == []


async def test_the_hint_is_dreams_own_words_when_it_has_them(studio):  # noqa: F811
    async with studio() as s:
        bodies, state = [], {'value': None}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await expect(field).to_have_attribute('title', HINT)                               # the owner's words, by default
        await expect(page.locator('#nd-max-hint')).to_have_text(HINT)                      # its description, for a screen reader
        await expect(field).to_have_attribute('aria-describedby', 'nd-max-hint')
        await expect(page.locator('.nd-max-l')).to_have_text('Max agents')                 # the visible label stays short
        state['help'] = HELP
        await s.page.locator('#dream-nav-chat').click()
        page = await open_nested(s)
        await expect(field).to_have_attribute('title', HELP)                               # the settings row's own help, when it has one
        await expect(page.locator('#nd-max-hint')).to_have_text(HELP)
        assert s.errors == []


async def test_a_row_dream_will_not_change_here_shows_read_only_with_its_reason(studio):  # noqa: F811
    locked = 'Set DREAM_MAX_AGENTS before starting Dream to change it.'
    async with studio() as s:
        async def handle(route, request):
            body = request.post_data_json
            if body['action'] == 'settings_get':
                await route.fulfill(status=200, json={'ok': True, 'result': view(5, edit=False, locked=locked)})
                return
            await route.fulfill(status=200, json={'ok': True, 'result': {}})
        await s.page.route('**/api/control', handle)
        page = await open_nested(s)
        field = page.get_by_label('Max agents', exact=True)
        await expect(field).to_have_value('5')
        await expect(field).to_be_disabled()
        await expect(field).to_have_attribute('title', f'{HINT} {locked}')
        assert s.errors == []


LANES_ENGINE = "The engine's lanes: how many of this session's workers can run at once"
LANES_CLOUD = "Parallel workers: how many of this session's workers run at once"
LANES_CLAUDE = "The local engine's lanes: how many of Claude's local helpers can run at once"


@pytest.mark.parametrize('provider,lanes', [('MachX', LANES_ENGINE), ('OpenAI', LANES_CLOUD), ('Grok · xAI (API)', LANES_CLOUD),
                                            ('Claude · Anthropic', LANES_CLAUDE)])
async def test_the_lanes_line_says_what_sets_the_pace_on_this_provider(studio, provider, lanes):  # noqa: F811
    """Kept from the P10 gate (r1 #4 and #5): on the local engine the lanes, on OpenAI and xAI Parallel workers, on Claude
    the local engine's lanes for its helpers."""
    async with studio(provider=provider, lanes={'served': 4, 'busy': 1, 'queued': 0}) as s:
        bodies, state = [], {'value': None}
        await scripted(s, bodies, state)
        page = await open_nested(s)
        await expect(page.locator('#nd-lanes')).to_have_attribute('title', lanes)
        assert s.errors == []


@pytest.mark.parametrize('w,h', [(901, 700), (1280, 600)])
async def test_a_long_reason_wraps_on_its_own_line_and_keeps_the_field_whole(studio, w, h):  # noqa: F811
    """Kept from the P10 gate (r2 #1): beside a long reason (an unusable settings file's, with its path) the control
    broke at short sizes; the reason sits on its own line under the top bar and wraps, and the field keeps its size."""
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
        await expect(page.locator('#nd-max-note')).to_have_text(reason)
        geo = await page.evaluate("""p => { const r = s => p.querySelector(s).getBoundingClientRect();
            return {field: r('#nd-max').width, note: [r('#nd-max-note').top, r('#nd-max-note').bottom, r('#nd-max-note').right],
                    top: r('.nd-top').bottom, page: r('.nd-app').right, scroll: p.scrollWidth <= p.clientWidth}; }""")
        assert geo['field'] >= 80 and geo['note'][0] >= geo['top'] - 1 and geo['note'][2] <= geo['page'] + 1 and geo['scroll'], (w, h, geo)
        assert s.errors == []


# --- the real settings rows (dream/gui/settings_panel.py through App._runtime_control) ---------------------------------

UNUSABLE = {
    'broken JSON': '{"version": 1, "nested": ',
    # the retired engine section is accepted whatever it holds (settings-redo), so the bad value is one Dream still checks
    'a bad value beside a good one': '{"version": 1, "nested": {"max_agents": 0}}',
    'an unreadable file': '{"version": 1}',
}


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


async def type_and_await_save(page, field, text):
    """Type `text` and press Enter, then wait for the save's answer: the field shows what was typed before the save
    lands (maxDirty), so its value alone does not say the settings file was written."""
    def is_save(r):
        return r.url.endswith('/api/control') and (r.request.post_data_json or {}).get('action') == 'settings_save'
    async with page.expect_response(is_save):
        await field.fill(text)
        await field.press('Enter')


async def max_agents_row(app):
    """The real view's nested.max_agents row, or a skip: the setting is the settings branch's (merged by the lead)."""
    answer = await app._runtime_control({'action': 'settings_get'})
    for section in answer.get('sections') or []:
        for row in section.get('rows') or []:
            if row.get('key') == KEY:
                return row
    pytest.skip("nested.max_agents is not in this tree's settings view yet (the settings branch adds it)")


@pytest.mark.parametrize('case', list(UNUSABLE))
async def test_an_unusable_settings_file_keeps_the_field_with_dreams_reason(tmp_path, monkeypatch, case):
    """Kept from the P10 gate (r1 #1): settings_get answers {ok: false, error, sections: []} for a settings file Dream
    cannot use; the field shows, off, Dream's words beside it (this needs no backend for the key itself)."""
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
            await expect(s.page.locator('#nd-maxw')).to_be_visible()
            await expect(s.page.locator('#nd-max-note')).to_have_text(answer['error'])
            await expect(s.page.get_by_label('Max agents', exact=True)).to_be_disabled()
            assert s.errors == []
    finally:
        os.chmod(path, 0o644)


async def test_the_real_settings_rows_drive_the_field(tmp_path, monkeypatch):
    """With the settings branch merged: the default is no limit; a number lands in the runtime settings file as
    nested.max_agents; a reload reads it; clearing the field removes it again; a refused value is said verbatim."""
    from dream.core.profiles import read_settings
    async with real_route(tmp_path, monkeypatch) as s:
        row = await max_agents_row(s.app)
        assert row.get('value') is None, row                                            # the default: no limit
        await s.page.locator('#dream-nav-nested').click()
        field = s.page.get_by_label('Max agents', exact=True)
        await expect(field).to_have_value('')
        await type_and_await_save(s.page, field, '3')
        await expect(field).to_have_value('3')
        assert read_settings().get('nested', {}).get('max_agents') == 3, read_settings()
        await s.page.reload()
        await expect(s.page.locator('#stat')).to_have_text('Ready')
        await s.page.locator('#dream-nav-nested').click()
        await expect(field).to_have_value('3')
        await type_and_await_save(s.page, field, '')
        await expect(field).to_have_value('')
        assert 'max_agents' not in read_settings().get('nested', {}), read_settings()
        await field.fill('0')                                                            # the real refusal, verbatim
        await field.press('Enter')
        await expect(s.page.locator('#nd-max-note')).not_to_have_text('')
        await expect(field).to_have_value('')
        assert s.errors == []


async def test_a_save_in_the_settings_dialog_reaches_the_field(tmp_path, monkeypatch):
    """Kept from the P10 gate (r1 #2): the field reads the setting again when the Controls dialog closes, so a change
    made while it was open (its Settings tab, another window) shows here."""
    async with real_route(tmp_path, monkeypatch) as s:
        await max_agents_row(s.app)
        await s.page.locator('#dream-nav-nested').click()
        field = s.page.get_by_label('Max agents', exact=True)
        await expect(field).to_have_value('')
        await s.page.locator('#nd-menu summary').click()          # Controls is in the column's More menu here (DREAM-239)
        await s.page.locator('#nd-menu [data-proxy="dream-controls-open"]').click()
        await s.app._runtime_control({'action': 'settings_save', 'key': KEY, 'value': 2, 'scope': 'global'})
        await s.page.keyboard.press('Escape')
        await expect(s.page.locator('#dream-controls')).to_be_hidden()
        await expect(field).to_have_value('2')
        assert s.errors == []
