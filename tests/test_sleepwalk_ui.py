"""Sleepwalk phase 1 gate (DREAM-156): create -> save -> Run now (consult stubbed) -> OK row -> reader,
at 1100 px and 390 px; a template's Add; an unavailable runner shown as FAILED."""
import os
import sys
import types

import pytest
from playwright.async_api import async_playwright, expect

from dream import config, plugins
from dream.core import council_config, moe
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.sleepwalk import store

pytestmark = pytest.mark.asyncio
SHOTS = os.environ.get('SLEEPWALK_SHOTS')          # optional folder for the gate's screenshots
CHOICES = [
    {'key': 'codex', 'label': 'ChatGPT · Codex', 'available': True, 'efforts': ['low', 'medium', 'high'],
     'models': [{'id': 'gpt-6-astra', 'label': 'GPT-6 Astra', 'efforts': ['low', 'medium', 'high']},
                {'id': 'gpt-6-luna', 'label': 'GPT-6 Luna', 'efforts': ['low', 'medium']}]},
    {'key': 'grok', 'label': 'Grok · xAI', 'available': False, 'efforts': [], 'models': []},
]


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'sleepwalk')
    monkeypatch.setattr(council_config, 'provider_choices', lambda **_: CHOICES)
    monkeypatch.setattr(config, 'PLUGINS_DIR', tmp_path / 'plugins')
    monkeypatch.setattr(plugins, '_LOADED', [])
    ring = types.ModuleType('keyring')
    ring.items = {}
    ring.get_password = lambda service, key: ring.items.get(key)
    ring.set_password = lambda service, key, value: ring.items.__setitem__(key, value)
    monkeypatch.setitem(sys.modules, 'keyring', ring)
    bin_dir = tmp_path / 'bin'           # a fake systemctl: no test reads or changes the real user manager
    bin_dir.mkdir()
    (bin_dir / 'systemctl').write_text('#!/bin/sh\nstate="$(dirname "$0")/state"\ncase "$2" in\n'
                                       '  is-enabled) cat "$state" 2>/dev/null || echo disabled ;;\n'
                                       '  enable) echo enabled > "$state" ;;\n  disable) echo disabled > "$state" ;;\nesac\n')
    (bin_dir / 'systemctl').chmod(0o755)
    monkeypatch.setenv('PATH', f"{bin_dir}:/usr/bin:/bin")
    from dream.sleepwalk import background
    monkeypatch.setattr(background, '_logind', lambda prop: '')        # never ask the real logind
    calls = []

    async def consult(provider, question, context='', **kw):
        calls.append((provider, kw.get('model'), kw.get('effort')))
        if 'broken' in question:
            return '[ChatGPT · Codex: unavailable — CLIIsolationError: codex CLI is not installed]'
        return 'Lesson: the moon keeps one face toward Earth.'
    monkeypatch.setattr(moe, 'consult_advisor', consult)
    return StudioServer(EventBus(), on_prompt=lambda p: None,
                        session={'workspace': str(tmp_path), 'model': 'fixture'}), calls


async def shot(page, name):
    if SHOTS:
        await page.screenshot(path=os.path.join(SHOTS, name), full_page=False)


@pytest.mark.parametrize('width,height', [(1100, 800), (390, 844)])
async def test_create_save_run_read(studio, width, height):
    srv, calls = studio
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': width, 'height': height})
            page.set_default_timeout(5000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto(url.replace('/#', '/?companion=1#'))
            await page.locator('#dream-nav-sleepwalk').click()
            sw = page.locator('#dream-sleepwalk-page')
            await expect(sw).to_be_visible()
            await expect(sw.locator('h1')).to_have_text('Sleepwalk Automations')
            await expect(sw.locator('.sw-template')).to_have_count(3)
            assert await sw.get_by_text('do not fire').count() == 0
            await shot(page, f'page-{width}.png')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')

            await sw.get_by_role('button', name='New automation').click()
            editor = page.locator('#sw-editor')
            await expect(editor).to_be_visible()
            await expect(editor.locator('.sw-runner select').first).to_have_value('codex')
            await expect(editor.locator('.sw-runner select').nth(1)).to_have_value('gpt-6-astra')
            await expect(editor.locator('.sw-runner select').nth(2)).to_have_value('high')
            await editor.get_by_label('Automation name').fill('Moon facts')
            await editor.locator('.sw-icon-tile').click()
            await editor.locator('.sw-icon-grid button[data-icon=moon]').click()
            await editor.get_by_label('Instructions').fill('Teach one fact about the moon.')
            await editor.get_by_role('button', name='Add another time').click()
            await expect(editor.locator('.sw-trigger')).to_have_count(2)
            await editor.locator('.sw-trigger').nth(1).get_by_label('Repeat').select_option('week')
            await editor.locator('.sw-trigger').nth(1).get_by_role('button', name='Thursday').click()
            await editor.locator('.sw-trigger').nth(1).get_by_label('Label').fill('quiz')
            await editor.get_by_text('When it can’t run').click()
            await editor.get_by_label('Missed while Dream was closed').select_option('skip')
            await editor.get_by_label('Local model not loaded').select_option('codex')
            await editor.get_by_label('Local model not loaded').scroll_into_view_if_needed()
            await shot(page, f'editor-{width}.png')
            assert await editor.evaluate('e => e.scrollWidth <= e.clientWidth + 1')
            await editor.get_by_role('button', name='Save').click()
            await expect(editor).to_be_hidden()

            card = sw.locator('.sw-card', has_text='Moon facts')
            await expect(card).to_be_visible()
            await expect(card).to_contain_text('Daily at 8:00 AM')
            saved = store.load()[0]
            assert saved['icon'] == 'moon' and saved['triggers'][1] == {'every': 'week', 'at': '08:00', 'days': [0, 3],
                                                                         'label': 'quiz'}
            await expect(card).to_contain_text('+1 more')
            await expect(card.locator('.sw-when')).to_contain_text(' · next ')
            assert saved['missed'] == 'skip' and saved['fallback'] == {'provider': 'codex', 'model': None, 'effort': None}
            switch = card.get_by_role('switch')
            await expect(switch).to_have_attribute('aria-checked', 'true')
            await switch.click()
            await expect(card.get_by_role('switch')).to_have_attribute('aria-checked', 'false')
            await expect(card.locator('.sw-when')).to_contain_text(' · off')
            assert store.load()[0]['enabled'] is False
            await card.get_by_role('switch').click()
            await expect(card.get_by_role('switch')).to_have_attribute('aria-checked', 'true')
            await card.get_by_role('button', name='Edit').click()
            days = editor.locator('.sw-trigger').nth(1)
            for day, pressed in [('Monday', 'true'), ('Thursday', 'true'), ('Friday', 'false')]:
                await expect(days.get_by_role('button', name=day)).to_have_attribute('aria-pressed', pressed)
            await editor.get_by_role('button', name='Cancel').click()
            await expect(sw.locator('.sw-build button', has_text='Start')).to_have_class('sw-pill')
            await card.get_by_role('button', name='Run now').click()
            await expect(page.locator('.sw-toast')).to_contain_text('Moon facts finished')
            await expect(card).to_contain_text('Last ran')
            assert calls == [('codex', 'gpt-6-astra', 'high')]
            await card.scroll_into_view_if_needed()
            await shot(page, f'card-{width}.png')

            await sw.get_by_role('tab', name='Runs').click()
            row = sw.locator('.sw-run').first
            await expect(row.locator('.sw-status')).to_have_text('OK')
            await expect(row).to_contain_text('Moon facts')
            await shot(page, f'runs-{width}.png')
            await row.get_by_role('button', name='Open').click()
            reader = page.locator('#sw-reader')
            await expect(reader).to_contain_text('the moon keeps one face toward Earth')
            await shot(page, f'reader-{width}.png')
            await reader.get_by_role('button', name='Close').click()
            for box in [sw, editor]:
                if await box.is_visible():
                    assert await box.evaluate('e => e.scrollWidth <= e.clientWidth + 1')
            assert errors == []
            await browser.close()
    finally:
        await srv.stop()


async def test_template_add_and_failed_runner(studio):
    srv, _ = studio
    url = await srv.start()
    try:
        async with async_playwright() as p:
            damaged = store.ROOT / 'runs' / 'abcdefabcdef'
            damaged.mkdir(parents=True)
            (damaged / '20260101-000000-000000.json').write_text('null')
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1100, 'height': 800})
            page.set_default_timeout(5000)
            await page.goto(url.replace('/#', '/?companion=1#'))
            await page.locator('#dream-nav-sleepwalk').click()
            sw = page.locator('#dream-sleepwalk-page')
            await sw.locator('.sw-template', has_text='Learn Something New').get_by_role('button', name='Add').click()
            editor = page.locator('#sw-editor')
            await expect(editor.get_by_label('Automation name')).to_have_value('Learn Something New Every Day')
            await expect(editor.locator('.sw-trigger')).to_have_count(2)
            await editor.get_by_label('Instructions').fill('This one is broken on purpose.')
            await editor.get_by_role('button', name='Save').click()
            card = sw.locator('.sw-card', has_text='Learn Something New')
            await card.get_by_role('button', name='Run now').click()
            await expect(page.locator('.sw-toast')).to_contain_text('failed')
            await sw.get_by_role('tab', name='Runs').click()
            row = sw.locator('.sw-run').first
            await expect(row.locator('.sw-status')).to_have_text('FAILED')
            await expect(sw.locator('.sw-panel:not([hidden]) .sw-notice')).to_contain_text('damaged run record')
            await row.get_by_role('button', name='Open').click()
            reader = page.locator('#sw-reader')
            await expect(reader).to_contain_text('unavailable')
            await expect(reader).to_contain_text('did not produce a result')
            await reader.get_by_role('button', name='Close').click()
            await sw.get_by_role('tab', name='Automations').click()
            await card.get_by_role('button', name='Edit').click()
            page.once('dialog', lambda d: d.accept())
            await editor.get_by_role('button', name='Delete').click()
            await expect(card).to_have_count(0)
            assert store.load() == []
            await browser.close()
    finally:
        await srv.stop()


@pytest.mark.parametrize('width,height', [(1100, 800), (390, 844)])
async def test_connectors_notifications_and_attachments(studio, width, height):
    srv, _ = studio
    auto = store.save({'title': 'Morning notes', 'icon': 'sun', 'instructions': 'Summarise.', 'triggers': [],
                       'runner': {'provider': 'codex', 'model': 'gpt-6-astra'}})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': width, 'height': height})
            page.set_default_timeout(5000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.goto(url.replace('/#', '/?companion=1#'))
            await page.locator('#dream-nav-sleepwalk').click()
            await page.locator('.sw-card', has_text='Morning notes').get_by_role('button', name='Edit').click()
            editor = page.locator('#sw-editor')
            await editor.get_by_role('button', name='Connectors 0').click()
            telegram = editor.locator('.sw-conn', has_text='Telegram')
            await expect(telegram).to_contain_text('Not set up')
            await telegram.get_by_role('button', name='Set up').click()
            await telegram.get_by_label('Bot token (from BotFather)').fill('FIXTURE-TOKEN')
            await telegram.get_by_label('Your chat id').fill('4242')
            await telegram.get_by_role('button', name='Save Telegram').click()
            await expect(editor.locator('.sw-conn', has_text='Telegram')).to_contain_text('Set up')
            calendar = editor.locator('.sw-conn', has_text='Google Calendar')
            await calendar.get_by_role('button', name='Set up').click()
            before = page.url
            await calendar.get_by_label('Secret address in iCal format').fill('https://calendar.example.invalid/private.ics')
            await calendar.get_by_label('Secret address in iCal format').press('Enter')     # never a GET with the secret
            await expect(editor.locator('.sw-conn', has_text='Google Calendar')).to_contain_text('Set up')
            assert page.url == before and 'private.ics' not in page.url
            assert sys.modules['keyring'].items['gcal.ical_url'] == 'https://calendar.example.invalid/private.ics'
            await editor.get_by_label('Use Gmail').check()
            await expect(editor.get_by_role('button', name='Connectors 1')).to_be_visible()
            await editor.locator('.sw-notify summary').click()
            await editor.locator('.sw-notify label', has_text='Telegram').locator('input').check()
            await expect(editor.locator('.sw-notify summary')).to_have_text('Dream + Telegram')
            await expect(editor.locator('.sw-notify label', has_text='Telegram')).not_to_contain_text('not set up')
            await expect(editor.locator('.sw-notify label', has_text='Gmail')).to_contain_text('not set up')
            await editor.locator('input[type=file]').set_input_files(
                files=[{'name': 'notes.md', 'mimeType': 'text/markdown', 'buffer': b'# Notes\n'}])
            await expect(editor.locator('.sw-files')).to_contain_text('notes.md')
            await editor.get_by_label('Use Gmail').scroll_into_view_if_needed()
            await shot(page, f'connectors-{width}.png')
            assert await editor.evaluate('e => e.scrollWidth <= e.clientWidth + 1')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await editor.get_by_role('button', name='Save', exact=True).click()
            await expect(editor).to_be_hidden()
            saved = store.get(auto['id'])
            assert saved['connectors'] == ['gmail'] and saved['notify'] == ['app', 'telegram']
            assert sys.modules['keyring'].items['telegram.bot_token'] == 'FIXTURE-TOKEN'
            assert 'FIXTURE-TOKEN' not in (store.ROOT / 'connectors.json').read_text()
            assert (store.attachments(auto['id']) / 'notes.md').read_bytes() == b'# Notes\n'
            assert errors == []
            await browser.close()
    finally:
        await srv.stop()


@pytest.mark.parametrize('width,height', [(1100, 800), (390, 844)])
async def test_background_runs_switch(studio, tmp_path, monkeypatch, width, height):
    from dream.sleepwalk import background
    if not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
        # Playwright's Linux default, computed before HOME moves: $XDG_CACHE_HOME or ~/.cache, then ms-playwright
        cache = os.environ.get('XDG_CACHE_HOME') or os.path.join(os.path.expanduser('~'), '.cache')
        monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', os.path.join(cache, 'ms-playwright'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'home' / '.config'))
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'home' / 'dream' / 'python'))
    monkeypatch.setattr(config, 'ROOT', tmp_path / 'home' / 'dream')
    monkeypatch.delenv('DREAM_EXTENSION_SETTINGS', raising=False)
    srv, _ = studio
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': width, 'height': height})
            page.set_default_timeout(5000)
            await page.goto(url.replace('/#', '/?companion=1#'))
            await page.locator('#dream-nav-sleepwalk').click()
            switch = page.locator('#dream-sleepwalk-page .sw-bg')
            await expect(switch).to_have_text('Background runs · Off')
            await expect(switch).to_have_attribute('aria-checked', 'false')
            await switch.click()
            await expect(switch).to_have_text('Background runs · On')
            assert background.state() == 'on'
            assert (tmp_path / 'home' / '.config' / 'systemd' / 'user' / 'dream-sleepwalk.timer').is_file()
            await shot(page, f'background-{width}.png')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await switch.click()
            await expect(switch).to_have_text('Background runs · Off')
            assert not (tmp_path / 'home' / '.config' / 'systemd' / 'user' / 'dream-sleepwalk.timer').exists()
            await browser.close()
    finally:
        await srv.stop()


@pytest.mark.parametrize('width,height', [(1100, 800), (390, 844)])
async def test_first_gmail_setup_with_default_hosts_and_an_edit_keeps_the_secret(studio, width, height):
    srv, _ = studio
    store.save({'title': 'Mail notes', 'icon': 'mail', 'instructions': 'Summarise.', 'triggers': [],
                'runner': {'provider': 'codex', 'model': 'gpt-6-astra'}})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': width, 'height': height})
            page.set_default_timeout(5000)
            await page.goto(url.replace('/#', '/?companion=1#'))
            await page.locator('#dream-nav-sleepwalk').click()
            await page.locator('.sw-card', has_text='Mail notes').get_by_role('button', name='Edit').click()
            editor = page.locator('#sw-editor')
            await editor.get_by_role('button', name='Connectors 0').click()
            gmail = editor.locator('.sw-conn', has_text='Gmail')
            await gmail.get_by_role('button', name='Set up').click()
            await expect(gmail.get_by_label('IMAP server')).to_have_attribute('placeholder', 'imap.gmail.com')
            await gmail.get_by_label('Gmail address').fill('owner@example.invalid')
            await gmail.get_by_label('App password').fill('abcd efgh ijkl mnop')
            await gmail.get_by_role('button', name='Save Gmail').click()          # the host boxes left at their defaults
            gmail = editor.locator('.sw-conn', has_text='Gmail')
            await expect(gmail.locator('small')).to_have_text('Set up')
            await gmail.get_by_role('button', name='Set up').click()
            await expect(gmail.get_by_label('App password')).to_have_value('')
            await expect(gmail.get_by_label('App password')).to_have_attribute('placeholder', 'Saved in the keyring')
            await gmail.get_by_label('Gmail address').fill('other@example.invalid')
            await gmail.get_by_role('button', name='Save Gmail').click()          # the password box left empty
            await expect(editor.get_by_label('Gmail address')).to_be_hidden()       # saved: the list is drawn again
            await expect(editor.locator('.sw-conn', has_text='Gmail').locator('small')).to_have_text('Set up')
            assert sys.modules['keyring'].items['gmail.app_password'] == 'abcd efgh ijkl mnop'
            assert store.connector_settings()['gmail']['address'] == 'other@example.invalid'
            await browser.close()
    finally:
        await srv.stop()
