"""Skills tab screenshots at the owner's window sizes (headless Chromium, fake app, no inference).

Opt-in: runs only when DREAM_SHOTS names a label (before/after); pictures go to DREAM_SHOTS_DIR."""
import os
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

LABEL = os.environ.get('DREAM_SHOTS')
OUT = Path(os.environ.get('DREAM_SHOTS_DIR', 'shots'))
pytestmark = pytest.mark.skipif(not LABEL, reason='screenshots are opt-in: set DREAM_SHOTS=<label>')

TEXT = ('Shape an API before building it: resources, requests, responses, errors, versioning and examples in one short spec.')
NAMES = ['account-research', 'api-design', 'blender-animation', 'brainstorming', 'brand-voice', 'code-review', 'coding',
         'debugging', 'dependency-upgrade', 'frontend-design', 'gated-build', 'research', 'test-writing', 'understand',
         'understand-dashboard', 'understand-domain', 'verifying', 'writing']
BODY = '---\nname: api-design\ndescription: "' + TEXT + '"\n---\n\n# API design\n\n' + '\n'.join(
    f'{i}. Start from the callers. List who calls it, what they are trying to do and what they already hold.' for i in range(1, 30))


@pytest.mark.parametrize('size', [(2549, 1337), (1100, 800), (390, 844)])
async def test_skills_tab_screenshot(size):
    skills = {n: {'name': n, 'description': TEXT, 'source': 'bundled', 'origin': 'builtin', 'managed': False,
                  'enabled': True, 'sha256': 'v1', 'content': BODY.replace('api-design', n)} for n in NAMES}
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, on_control=lambda c: None,
                       session={'provider': 'openai', 'model': 'ChatGPT · Codex', 'session_id': 'shot',
                                'workspace': '/home/owner/Desktop/Dream-Agent-Harness', 'reasoning_effort': None,
                                'vision': {'state': 'on'}})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': size[0], 'height': size[1]})
            page.set_default_timeout(5000)

            async def skill_api(route):
                path = route.request.url.split('/api/skills', 1)[1]
                await route.fulfill(json=skills[path[1:]] if path else {'skills': list(skills.values())})
            await page.route('**/api/skills**', skill_api)
            await page.goto(url.replace('/#', '/?companion=1#'))
            await expect(page.locator('#stat')).to_have_text('Ready')
            OUT.mkdir(parents=True, exist_ok=True)
            await srv._accept_prompt('Review the API spec and tell me what is missing')
            srv.bus.publish(Event('text_delta', 'I will read the spec first.'))
            srv.bus.publish(Event('tool_use', {'id': 'r1', 'name': 'read_file', 'input': {'path': 'docs/api.md'}}))
            srv.bus.publish(Event('tool_result', {'id': 'r1', 'content': '# Orders API', 'is_error': False}))
            srv.bus.publish(Event('text_delta', ' The spec lists **three endpoints** but no error table.\n\n```json\n{"error": "not_found"}\n```'))
            srv.bus.publish(Event('result', {}))
            await expect(page.locator('#stream')).to_contain_text('no error table')
            await page.screenshot(path=str(OUT / f'{LABEL}-{size[0]}-chat.png'))
            await page.locator('#dream-nav-skills').click()
            await page.get_by_label('Preset', exact=True).select_option('Software Development')
            await page.locator('[data-key=api-design]').first.click()
            await expect(page.get_by_label('Skill Markdown', exact=True)).to_have_value(re.compile('API design'))
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(OUT / f'{LABEL}-{size[0]}.png'))
            await page.locator('#dream-skills-page').evaluate('e => e.scrollTop = 420')
            await page.wait_for_timeout(200)
            await page.screenshot(path=str(OUT / f'{LABEL}-{size[0]}-scrolled.png'))
            await browser.close()
    finally:
        await srv.stop()


@pytest.mark.parametrize('size', [(2549, 1337), (1100, 800), (390, 844)])
async def test_settings_tab_screenshot(size, tmp_path):
    from dream.tui.app import App
    app = App(provider='machx', workspace=tmp_path)
    srv = StudioServer(EventBus(), session={'workspace': str(tmp_path), 'provider': 'machx', 'model': 'fixture'},
                       on_control=app._runtime_control)
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': size[0], 'height': size[1]})
            page.set_default_timeout(5000)
            await page.goto(url.replace('/#', '/?companion=1#'))
            await expect(page.locator('#stat')).to_have_text('Ready')
            await page.locator('#dream-controls-open').click()
            await page.get_by_role('tab', name='Settings', exact=True).click()
            await page.wait_for_timeout(600)
            OUT.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(OUT / f'{LABEL}-{size[0]}-settings.png'))
            await browser.close()
    finally:
        await srv.stop()


@pytest.mark.parametrize('size', [(2549, 1337), (1100, 800), (390, 844)])
async def test_lucid_control_screenshot(size, tmp_path, monkeypatch):
    """DREAM-183: the Lucid Control board with a few files and memories, and the easter egg's card opened."""
    from dream import config
    from dream.core import settings as runtime_settings
    from dream.tui.app import App
    root = tmp_path / 'lucid-memory'
    root.mkdir()
    for name, attr in (('INSTRUCTIONS.md', 'INSTRUCTIONS_FILE'), ('IDENTITY.md', 'IDENTITY_FILE'),
                       ('THREADS.md', 'THREADS_FILE'), ('MEMORY.md', 'MEMORY_INDEX_FILE')):
        monkeypatch.setattr(config, attr, root / name)
    monkeypatch.setattr(config, 'MEMORY_DIR', root)
    (root / 'INSTRUCTIONS.md').write_text('Plain English. Short answers.\n')
    (root / 'IDENTITY.md').write_text('I am Dream.\n')
    for slug, title in (('owner-prefers-plain-english', 'Plain English'), ('falcon-9-scene', 'Falcon 9 scene notes'),
                        ('engine-lanes', 'Engine lanes')):
        (root / f'{slug}.md').write_text(f'---\nname: {slug}\ntitle: {title}\nkind: semantic\n---\n{title}.\n')
    ws = tmp_path / 'Dream-Agent-Harness'
    ws.mkdir()
    (ws / 'DREAM.md').write_text('# Dream\nUse the harness tests.\n')
    (ws / 'CLAUDE.md').write_text('Instructions for another assistant.\n')
    app = App(provider='machx', workspace=ws)
    runtime_settings.set_workspace(ws)
    srv = StudioServer(EventBus(), session={'workspace': str(ws), 'provider': 'machx', 'model': 'fixture'},
                       on_control=app._runtime_control)
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': size[0], 'height': size[1]})
            page.set_default_timeout(5000)
            await page.goto(url.replace('/#', '/?companion=1#'))
            await page.locator('#dream-nav-memory').click()
            await expect(page.locator('#dream-memory-page .lucid-style')).to_have_count(5)
            OUT.mkdir(parents=True, exist_ok=True)
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(OUT / f'{LABEL}-{size[0]}-lucid.png'))
            await page.locator('.lucid-style[data-style=weasel-ee]').click()
            await page.wait_for_timeout(200)
            await page.screenshot(path=str(OUT / f'{LABEL}-{size[0]}-lucid-ee.png'), full_page=True)
            await browser.close()
    finally:
        runtime_settings.set_workspace(None)
        await srv.stop()
