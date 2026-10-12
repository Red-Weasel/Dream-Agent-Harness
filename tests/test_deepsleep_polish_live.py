"""Full Studio shell with six owned PTYs; no provider generation or owner process."""
import asyncio
from dataclasses import replace
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import sys
import threading

import pytest
from playwright.async_api import async_playwright, expect

from dream import config
from dream.core import providers
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_deepsleep_live_integration import eventually
from test_deepsleep_ui import STATIC
from test_workflow_terminal_live import StreamingHTTP


@pytest.mark.parametrize('size', [(2348,1251),(1920,1080)])
async def test_six_owned_pty_layouts_input_stream_and_empty_space(tmp_path, monkeypatch, size):
    monkeypatch.delenv('NO_COLOR', raising=False)
    runtime, workspace = tmp_path/'runtime', tmp_path/'sample-repo'
    runtime.mkdir(); workspace.mkdir()
    native_script=tmp_path/'local_terminal.py'
    native_script.write_text("import sys\nprint('Local terminal fixture — no provider connection',flush=True)\nfor line in sys.stdin:\n print('ECHO_RECEIVED '+line.strip(),flush=True)\n")
    monkeypatch.setattr(config,'ROOT',runtime)
    monkeypatch.setattr('dream.gui.deepsleep.native_launch',lambda *args,**kw:([sys.executable,'-u',str(native_script)],dict(os.environ,TERM='xterm-256color')))
    http=ThreadingHTTPServer(('127.0.0.1',0),StreamingHTTP);http.requests=[]
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    monkeypatch.setitem(providers.PROVIDERS,'machx',replace(providers.PROVIDERS['machx'],base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    server=StudioServer(EventBus(),session={'session_id':'polish-owned-fixture','workspace':str(workspace),'provider':'machx'})
    url=await server.start(); board=server._board()
    try:
        for i in range(6):
            new_node=await board.create({'provider':'machx' if i%2==0 else ['anthropic','antigravity','grok'][i//2], 'profile':'dream' if i%2==0 else 'native','model':'controlled-model' if i%2==0 else ''})
            await eventually(lambda:new_node.state=='ready')
        owned=list(board.nodes.values())
        try:await eventually(lambda:all(n.state=='ready' for n in owned))
        except AssertionError:raise AssertionError([(n.profile,n.state,n.output[-1800:]) for n in owned])
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=True,args=['--disable-gpu'])
            try:
                page=await browser.new_page(viewport={'width':size[0],'height':size[1]},reduced_motion='reduce')
                page.set_default_timeout(15000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                await page.route('**/api/accounts',lambda r:r.fulfill(json={'accounts':[]}))
                capture='''\nwindow.fixtureTerminals=[];const BaseTerminal=window.Terminal;window.Terminal=class extends BaseTerminal{constructor(...args){super(...args);fixtureTerminals.push(this);}};window.bufferText=i=>{const b=fixtureTerminals[i].buffer.normal;return Array.from({length:b.length},(_,j)=>b.getLine(j).translateToString(true)).join('\\n');};'''
                await page.route('**/assets/vendor/xterm.js',lambda r:r.fulfill(content_type='text/javascript',body=(STATIC/'vendor/xterm.js').read_text()+capture))
                await page.goto(url.replace('/#','/?companion=1#'))
                await page.get_by_role('button',name='Go to DeepSleep',exact=True).click()
                await page.wait_for_function('fixtureTerminals.length===6 && [0,2,4].every(i=>bufferText(i).includes("you"))')
                await page.evaluate('window.originalTerminals=[...fixtureTerminals]')
                await page.get_by_role('button',name='Tile terminals',exact=True).click()
                await expect(page.get_by_role('button',name='Tile terminals',exact=True)).to_be_enabled()
                await page.get_by_role('button',name='Characters',exact=True).click()
                cards=page.locator('.ds-node')
                for mode in ['conversation','activity','terminal','conversation']:
                    await page.get_by_label('Text layout',exact=True).select_option(mode)
                    await expect(cards.first.locator('.xterm')).to_be_visible()
                    if mode!='terminal':await page.wait_for_function('Array.from(document.querySelectorAll(".ds-content")).every(n=>n.classList.contains("ds-events-empty"))')
                    await page.wait_for_timeout(150)
                    measurements=await cards.evaluate_all('ns=>ns.map(n=>{const p=n.getBoundingClientRect(),t=n.querySelector(".ds-terminal").getBoundingClientRect();return {height:p.height,terminal:t.height,overflow:n.scrollWidth>n.clientWidth};})')
                    assert all(m['terminal']/m['height']>.70 and not m['overflow'] for m in measurements),measurements
                # Empty event views never push the idle prompt into history.
                assert await page.evaluate('[0,2,4].every(i=>(bufferText(i).match(/you ›/g)||[]).length===1)')
                assert http.requests==[]
                await page.screenshot(path=f'/tmp/dream277-six-mixed-empty-{size[0]}.png')
                # Native fixture PTY receives real keyboard input while modes change.
                await cards.nth(1).locator('.xterm-helper-textarea').focus()
                await page.keyboard.type('native-input-marker');await page.keyboard.press('Enter')
                await page.wait_for_function('bufferText(1).includes("ECHO_RECEIVED native-input-marker")')
                # The unsent task draft that used to be kept here went with the draft box (2026-10-10).
                await cards.first.locator('.xterm-helper-textarea').focus()
                await page.keyboard.type('short controlled local stream');await page.keyboard.press('Enter')
                await page.wait_for_function('bufferText(0).includes("ROW0004_")')
                for mode in ['activity','terminal','conversation']:
                    await page.get_by_label('Text layout',exact=True).select_option(mode)
                    await page.wait_for_timeout(120)
                await page.wait_for_function('bufferText(0).includes("END_STREAM_269")')
                text=await page.evaluate('bufferText(0)')
                for i in range(32):assert text.count(f'ROW{i:04d}_')==1,(i,text)
                assert text.count('END_STREAM_269')==1 and len(http.requests)==1
                await cards.first.get_by_role('button',name='Terminal tools for ',exact=False).click()
                await cards.first.get_by_role('button',name='Session details for ',exact=False).click()
                await expect(page.get_by_role('dialog')).to_be_visible()
                await page.screenshot(path=f'/tmp/dream277-six-mixed-tools-drawer-{size[0]}.png')
                await page.get_by_role('button',name='Close session details').click()
                await cards.first.get_by_role('button',name='Terminal tools for ',exact=False).click()
                await cards.first.get_by_role('button',name='Collapse ',exact=False).click()
                await cards.first.get_by_role('button',name='Expand ',exact=False).click()
                await cards.first.get_by_role('button',name='Maximize ',exact=False).click()
                await cards.first.get_by_role('button',name='Restore ',exact=False).click()
                assert await page.evaluate('fixtureTerminals.length===6 && fixtureTerminals.every((t,i)=>t===originalTerminals[i])')
                assert not errors
            finally:await browser.close()
    finally:
        await server.stop();http.shutdown();http.server_close();thread.join(timeout=2)
    assert all(n.process.returncode is not None and n.master is None for n in owned)
