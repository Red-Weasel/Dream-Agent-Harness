"""Browser key → authenticated PTY → real Dream multiline draft, local HTTP only."""
import asyncio
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading

from playwright.async_api import async_playwright

from dream import config
from dream.core import providers
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_deepsleep_live_integration import ModelHTTP, eventually
from test_deepsleep_ui import STATIC


async def test_workflow_shift_enter_inserts_newline_without_submitting(tmp_path, monkeypatch):
    http = ThreadingHTTPServer(('127.0.0.1', 0), ModelHTTP)
    http.requests = []
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    isolated, workspace = tmp_path / 'runtime', tmp_path / 'sample-repo'
    isolated.mkdir(); workspace.mkdir()
    monkeypatch.setattr(config, 'ROOT', isolated)
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'],
        base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    server = StudioServer(EventBus(), session={'session_id': 'shift-enter-fixture',
        'workspace': str(workspace), 'provider': 'machx'})
    url = await server.start()
    try:
        node = await server._board().create({'provider': 'machx', 'profile': 'dream', 'model': 'controlled-model'})
        await eventually(lambda: node.state == 'ready')
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
            try:
                page = await browser.new_page(viewport={'width':1400,'height':950})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                await page.route('**/api/accounts', lambda route:route.fulfill(json={'accounts':[]}))
                capture=r'''
const OriginalTerminal=window.Terminal;window.terminals=[];
window.Terminal=class extends OriginalTerminal {constructor(...args){super(...args);window.terminals.push(this);}};
window.transcript=()=>{const b=terminals[0].buffer.normal;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString(true)).join('\n');};
'''
                await page.route('**/assets/vendor/xterm.js',lambda route:route.fulfill(content_type='text/javascript',body=(STATIC/'vendor/xterm.js').read_text()+capture))
                await page.goto(url.replace('/#','/?companion=1#'))
                await page.get_by_role('button',name='Go to DeepSleep',exact=True).click()
                await page.wait_for_function('window.terminals?.length && transcript().includes("you ›")',timeout=10000)
                await page.locator('.xterm-helper-textarea').focus()
                await page.keyboard.type('FIRST_LINE_284')
                await page.keyboard.press('Shift+Enter')
                await page.keyboard.type('SECOND_LINE_284')
                await page.wait_for_function('transcript().includes("SECOND_LINE_284")')
                text=await page.evaluate('transcript()')
                lines=text.splitlines()
                first=next(i for i,line in enumerate(lines) if 'FIRST_LINE_284' in line)
                second=next(i for i,line in enumerate(lines) if 'SECOND_LINE_284' in line)
                assert second>first,text
                assert http.requests==[], 'Shift+Enter must not submit the first line'
                Path('/tmp/dream284-ui-evidence').mkdir(exist_ok=True)
                await page.locator('.ds-node').screenshot(path='/tmp/dream284-ui-evidence/workflow-shift-enter-draft.png')
                await page.keyboard.press('Enter')
                await eventually(lambda:len(http.requests)>0)
                await page.wait_for_function('transcript().includes("Fixture verified.")')
                assert len(http.requests)==1
                messages=[message for message in http.requests[0]['messages'] if message.get('role')=='user']
                # The engine appends the first message's stable ID to model input.
                assert messages[-1]['content']=='FIRST_LINE_284\nSECOND_LINE_284\n\n[id:m0001]'
                assert not errors
            finally:
                await browser.close()
    finally:
        await server.stop()
        http.shutdown();http.server_close();thread.join(timeout=2)
    assert node.process.returncode is not None and node.master is None
