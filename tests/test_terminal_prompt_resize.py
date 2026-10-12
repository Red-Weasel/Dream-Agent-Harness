"""Idle Dream prompts consumed by the shipped xterm over a real, isolated PTY."""
import asyncio
from dataclasses import replace
from http.server import ThreadingHTTPServer
import threading

from playwright.async_api import async_playwright

from dream import config
from dream.core import providers
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_deepsleep_live_integration import ModelHTTP, eventually
from test_deepsleep_ui import STATIC


async def test_idle_prompt_resize_keeps_single_prompt_and_draft(tmp_path, monkeypatch):
    http = ThreadingHTTPServer(('127.0.0.1', 0), ModelHTTP)
    http.requests = []
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    isolated, workspace = tmp_path / 'runtime', tmp_path / 'sample-repo'
    isolated.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(config, 'ROOT', isolated)
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'],
        base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    server = StudioServer(EventBus(), session={'session_id': 'idle-resize-fixture',
        'workspace': str(workspace), 'provider': 'machx'})
    url = await server.start()
    try:
        node = await server._board().create({'provider': 'machx', 'profile': 'dream',
                                           'model': 'controlled-model'})
        await eventually(lambda: node.state == 'ready')
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
            try:
                page = await browser.new_page(viewport={'width': 1400, 'height': 950})
                await page.route('**/api/accounts', lambda route: route.fulfill(json={'accounts': []}))
                capture = r'''
const OriginalTerminal=window.Terminal;window.terminals=[];
window.Terminal=class extends OriginalTerminal {constructor(...args){super(...args);window.terminals.push(this);}};
window.transcript=()=>{const b=terminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n');};
'''
                await page.route('**/assets/vendor/xterm.js', lambda r: r.fulfill(
                    content_type='text/javascript', body=(STATIC/'vendor/xterm.js').read_text()+capture))
                await page.goto(url.replace('/#', '/?companion=1#'))
                await page.get_by_role('button', name='Go to DeepSleep', exact=True).click()
                await page.wait_for_function('window.terminals?.length && transcript().includes("you ›")')
                sizes = [(720, 540), (400, 800), (940, 700), (360, 340)]

                async def resize(width, height):
                    node.layout.update(width=width, height=height)
                    await page.locator('.ds-node').evaluate(
                        '(el,size)=>{el.style.width=size[0]+"px";el.style.height=size[1]+"px";}', [width, height])
                    await asyncio.sleep(.3)
                    await page.wait_for_function('transcript().includes("you ›")', timeout=5000)
                    return await page.evaluate('transcript()')

                for width, height in sizes * 3:
                    text = await resize(width, height)
                    assert text.count('you ›') == 1, (width, height, text)

                await resize(940, 700)

                # Long wrapped Unicode input must retain both its text and caret.
                # /help's argument is ignored, so submitting verifies the actual
                # REPL buffer without making an inference request.
                await page.locator('.xterm-helper-textarea').focus()
                draft = '/help DRAFT_BEGIN_' + '界0123456789' * 15 + '_DRAFT_END'
                await page.keyboard.type(draft)
                await asyncio.sleep(.3)
                assert (await page.evaluate('transcript()')).count('DRAFT_BEGIN_') == 1
                for _ in range(10):
                    await page.keyboard.press('ArrowLeft')
                for width, height in sizes * 2:
                    await resize(width, height)
                    await page.keyboard.type('CARET')
                    await asyncio.sleep(.1)
                    compact = ''.join((await page.evaluate('transcript()')).split())
                    assert 'DRAFT_BEGIN_' in compact and compact.count('DRAFT_END') == 1, (width, height, compact)
                    assert 'CARET_DRAFT_END' in compact, (width, height, compact)
                    for _ in range(5):
                        await page.keyboard.press('Backspace')
                for presentation in ('conversation', 'activity', 'terminal'):
                    await page.get_by_label('Text layout', exact=True).select_option(presentation)
                    await asyncio.sleep(.3)
                await page.locator('.xterm-helper-textarea').focus()
                await page.keyboard.type('VIEW_CARET')
                await asyncio.sleep(.1)
                assert 'VIEW_CARET_DRAFT_END' in ''.join((await page.evaluate('transcript()')).split())
                for _ in range(10):
                    await page.keyboard.press('Backspace')
                await resize(940, 700)
                await page.keyboard.press('Enter')
                await page.wait_for_function('transcript().includes("show this")')
                await asyncio.sleep(.2)
                history = await page.evaluate('transcript()')
                assert ''.join(history.split()).count(''.join(draft.split())) == 1

                # Resize while slash completion is open, then cancel and run a
                # command. History and the one active prompt must remain intact.
                await page.keyboard.type('/')
                await page.wait_for_function('transcript().toLowerCase().includes("show terminal commands")')
                for width, height in sizes:
                    text = await resize(width, height)
                    assert text.count('DRAFT_BEGIN_') == 1, text
                await page.keyboard.press('Escape')
                await page.keyboard.press('Control+u')
                await page.keyboard.type('/help')
                await page.keyboard.press('Enter')
                await asyncio.sleep(.4)
                for presentation in ('conversation', 'activity', 'terminal'):
                    await page.get_by_label('Text layout', exact=True).select_option(presentation)
                    await asyncio.sleep(.3)
                text = await resize(720, 540)
                assert text.count('you ›') == 3, text  # two submitted commands + idle
                assert ''.join(text.split()).count(''.join(draft.split())) == 1, text
                await page.locator('.ds-terminal').hover()
                await page.mouse.wheel(0, -1000)
                await page.wait_for_function('terminals[0].buffer.active.viewportY < terminals[0].buffer.active.baseY')
                await asyncio.sleep(.3)  # allow the wheel's native viewport event to settle
                for width, height in sizes:
                    await resize(width, height)
                    assert await page.evaluate('terminals[0].buffer.active.viewportY < terminals[0].buffer.active.baseY'), (
                        width, height, await page.evaluate('''()=>{const t=terminals[0],b=t.buffer.active;return {cols:t.cols,rows:t.rows,viewport:b.viewportY,base:b.baseY};}'''))
                await page.get_by_role('button', name='Follow latest output in '+node.title, exact=True).click()
                await resize(720, 540)
                assert await page.evaluate('terminals[0].buffer.active.viewportY === terminals[0].buffer.active.baseY')
                await page.locator('.ds-node').screenshot(path='/tmp/dream277-idle-resize-after.png')
                assert http.requests == []
            finally:
                await browser.close()
    finally:
        await server.stop()
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)
    assert node.process.returncode is not None and node.master is None
