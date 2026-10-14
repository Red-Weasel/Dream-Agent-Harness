"""Shipped slash menu, isolated browser and command API; no model calls."""
import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

STATIC = Path(__file__).resolve().parents[1] / 'dream/gui/static'
COMMANDS = [
    {'name': 'research', 'kind': 'skill', 'description': 'Find primary sources and compare evidence.', 'insert': '/skill research '},
    {'name': 'tables', 'kind': 'plugin', 'description': 'Work with spreadsheet documents.', 'insert': '/plugin tables '},
    {'name': 'settings', 'kind': 'config', 'description': 'Configure Dream.', 'action': 'settings'},
    {'name': 'council', 'kind': 'council', 'description': 'Open frontier collaboration.', 'action': 'council'},
    {'name': 'deepsleep', 'kind': 'workspace', 'description': 'Arrange connected model terminals.', 'action': 'navigate', 'view': 'deepsleep'},
    {'name': 'login gemini', 'kind': 'login', 'description': 'Sign in to Gemini with Antigravity.', 'insert': '/login gemini'},
]


@pytest.fixture
async def command_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
        page = await browser.new_page(viewport={'width': 1280, 'height': 800})
        page.set_default_timeout(4000)
        state = {'fail': False, 'requests': [], 'errors': []}
        async def route(request):
            if request.request.url.endswith('/api/commands'):
                state['requests'].append(request.request.headers)
                if state.get('hold'):
                    await state['hold'].wait()
                await request.fulfill(status=503 if state['fail'] else 200, json={'commands': COMMANDS})
            else:
                await request.fulfill(content_type='text/html', body='''<!doctype html><html><body>
<style>body{margin:0;background:#101017;color:#eee;font:14px system-ui}main{padding:24px}section{position:fixed;bottom:20px;left:24px;right:24px;display:flex;gap:12px}textarea{background:#211d29;color:#eee;border:1px solid #4b4055;border-radius:9px;padding:12px;width:45%;height:65px;box-sizing:border-box}</style>
<main><h1>Dream</h1><p>Type / to choose a skill, plugin, or command.</p></main>
<section><textarea id="input" aria-label="Chat"></textarea><textarea id="nd-compose" aria-label="Nested"></textarea></section>
<script>const TOKEN='fixture-token'; window.DREAM_SESSION={session_id:'fixture'};window.sent=[];window.events=[]; for(const id of ['input','nd-compose'])document.getElementById(id).addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sent.push(e.target.value)}});for(const type of ['dream:navigate','dream:council-open','dream:settings-open'])window.addEventListener(type,e=>events.push({type,detail:e.detail}));</script></body></html>''')
        await page.route('**/*', route)
        page.on('pageerror', lambda e: state['errors'].append(str(e)))
        await page.goto('http://dream.test/')
        await page.add_style_tag(path=str(STATIC / 'command-menu.css'))
        await page.add_script_tag(path=str(STATIC / 'command-menu.js'))
        yield page, state
        await browser.close()


@pytest.mark.parametrize('composer', ['input', 'nd-compose'])
async def test_keyboard_selects_skill_without_sending_and_send_still_works(command_page, composer):
    page, state = command_page
    area = page.locator('#' + composer)
    await area.fill('/')
    await expect(page.get_by_role('option')).to_have_count(6)
    await area.press('ArrowDown')
    await expect(page.get_by_role('option', selected=True)).to_contain_text('/tables')
    await area.press('ArrowUp')
    await area.press('Enter')
    await expect(area).to_have_value('/skill research ')
    await expect(page.locator('#dream-command-menu')).to_be_hidden()
    assert await page.evaluate('sent') == []
    await area.press('End')
    await area.press_sequentially('compare the evidence')
    await expect(page.locator('#dream-command-menu')).to_be_hidden()
    await area.press('Enter')
    assert await page.evaluate('sent') == ['/skill research compare the evidence']
    assert state['requests'][0]['x-dream-token'] == 'fixture-token'
    assert not state['errors']


@pytest.mark.parametrize('query,expected', [('spreadsheet', '/plugin tables '), ('Antigravity', '/login gemini')])
async def test_search_description_and_click_insert(command_page, query, expected):
    page, _ = command_page
    await page.locator('#input').fill('/' + query)
    await expect(page.get_by_role('option')).to_have_count(1)
    await page.get_by_role('option').click()
    await expect(page.locator('#input')).to_have_value(expected)
    assert await page.evaluate('sent') == []


@pytest.mark.parametrize('name,event', [('settings', 'dream:settings-open'), ('council', 'dream:council-open'), ('deepsleep', 'dream:navigate')])
async def test_navigation_commands_open_view_without_submitting(command_page, name, event):
    page, _ = command_page
    await page.locator('#nd-compose').fill('/' + name)
    await expect(page.get_by_role('option')).to_have_count(1)
    await page.locator('#nd-compose').press('Enter')
    events = await page.evaluate('events')
    assert events[-1]['type'] == event
    if name == 'deepsleep':
        assert events[-1]['detail'] == {'view': 'deepsleep', 'session_id': 'fixture'}
    assert await page.evaluate('sent') == []


@pytest.mark.parametrize('composer', ['input', 'nd-compose'])
@pytest.mark.parametrize('text', ['/login', '/login codex', '/LOGIN grok', '/skill "research" compare evidence', '/plugin tables make a table', '/loginx hello'])
async def test_written_commands_reach_normal_submit_instead_of_menu_selection(command_page, composer, text):
    page, state = command_page
    area = page.locator('#' + composer)
    await area.fill('/')
    await expect(page.get_by_role('option')).to_have_count(6)
    await area.fill(text)
    await area.press('Enter')
    assert await page.evaluate('sent') == [text]
    assert not state['errors']


async def test_enter_on_a_message_sends_while_the_list_is_still_loading(command_page):
    """2026-10-12: under the full suite's load the command list arrived after Enter, and Enter, with nothing to choose,
    did nothing ('/loginx hello' stayed in the box). Slash text with a space in it reaches the normal submit while
    loading. One word may still name a command (gate round 1: '/research' would go to the model before the list loads
    but choose the skill after), so it waits for the list; so does a bare '/'. Residual (gate round 2): a phrase that
    matches a command's description once loaded still goes as a message while loading."""
    page, state = command_page
    state['hold'] = asyncio.Event()
    area = page.locator('#input')
    await area.fill('/loginx hello')
    await expect(page.get_by_role('status')).to_contain_text('Loading commands')
    await area.press('Enter')
    assert await page.evaluate('sent') == ['/loginx hello']
    for word in ('/research', '/'):
        await area.fill(word)
        await area.press('Enter')
        await expect(area).to_have_value(word)
    assert await page.evaluate('sent') == ['/loginx hello']
    state['hold'].set()
    await area.fill('/research')
    await expect(page.get_by_role('option')).to_have_count(1)
    await area.press('Enter')
    await expect(area).to_have_value('/skill research ')                  # once loaded, the same keys choose it
    assert await page.evaluate('sent') == ['/loginx hello']
    assert not state['errors']


async def test_enter_keeps_the_draft_when_the_list_could_not_load(command_page):
    """(gate round 1) The failure state says "Your message is still here": Enter on slash text keeps it unsent."""
    page, state = command_page
    state['fail'] = True
    area = page.locator('#input')
    await area.fill('/research the topic')
    await expect(page.get_by_role('status')).to_contain_text('could not load')
    await area.press('Enter')
    await expect(area).to_have_value('/research the topic')
    assert await page.evaluate('sent') == []
    assert not state['errors']


async def test_failure_retry_empty_escape_and_preserved_draft(command_page):
    page, state = command_page
    state['fail'] = True
    area = page.locator('#input')
    await area.fill('/')
    await expect(page.get_by_role('status')).to_contain_text('could not load')
    await area.press('Enter')
    assert await page.evaluate('sent') == []
    state['fail'] = False
    await page.get_by_role('button', name='Retry').click()
    await expect(page.get_by_role('option')).to_have_count(6)
    await area.fill('/unmatched-draft')
    await expect(page.get_by_role('status')).to_contain_text('No matching')
    await area.press('Escape')
    await expect(page.locator('#dream-command-menu')).to_be_hidden()
    await expect(area).to_have_value('/unmatched-draft')
    assert not state['errors']


@pytest.mark.parametrize('width,height', [(1280, 800), (390, 720)])
async def test_render_viewports_and_untrusted_metadata(command_page, width, height):
    page, state = command_page
    await page.set_viewport_size({'width': width, 'height': height})
    await page.route('**/api/commands', lambda route: route.fulfill(json={'commands': COMMANDS + [
        {'name': '<img src=x onerror=alert(1)>', 'kind': 'skill', 'description': '<script>bad</script>', 'insert': '/skill safe '},
    ]}))
    await page.locator('#input').fill('/')
    await expect(page.get_by_role('option')).to_have_count(7)
    assert await page.locator('#dream-command-menu img, #dream-command-menu script').count() == 0
    bounds = await page.locator('#dream-command-menu').bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
    assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= height
    await page.screenshot(path=f'/tmp/dream-command-menu-{width}.png')
    assert not state['errors']
