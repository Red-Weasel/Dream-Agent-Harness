"""Actual Dream REPL menus through the shipped terminal, with zero model turns."""
import asyncio
from dataclasses import replace
from http.server import ThreadingHTTPServer
import re
import threading

from playwright.async_api import async_playwright

from dream import config
from dream.core import providers
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_deepsleep_live_integration import ModelHTTP, eventually


def plain(text):
    return re.sub(r'\s+', '', re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)).lower()


async def test_actual_repl_slash_completion_model_dialog_and_cancel(tmp_path, monkeypatch):
    http = ThreadingHTTPServer(('127.0.0.1', 0), ModelHTTP)
    http.requests = []
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    isolated = tmp_path / 'isolated-runtime'
    isolated.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.setattr(config, 'ROOT', isolated)
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'], base_url=f'http://127.0.0.1:{http.server_port}/v1'))
    server = StudioServer(EventBus(), session={'session_id': 'menu-fixture', 'workspace': str(workspace), 'provider': 'machx'})
    url = await server.start()
    try:
        node = await server._board().create({'provider': 'machx', 'profile': 'dream', 'model': 'controlled-model'})
        await eventually(lambda: node.state == 'ready')
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
            try:
                page = await browser.new_page(viewport={'width': 1400, 'height': 950})
                await page.route('**/api/accounts', lambda route: route.fulfill(json={'accounts': []}))
                await page.goto(url.replace('/#', '/?companion=1#'))
                await page.get_by_role('button', name='Go to DeepSleep', exact=True).click()
                await page.locator('.xterm-helper-textarea').focus()
                await page.keyboard.type('/')
                await eventually(lambda: 'showterminalcommands' in plain(node.output))
                await page.keyboard.press('Escape')
                await page.keyboard.press('Control+u')
                await page.keyboard.type('/model')
                await page.keyboard.press('Enter')
                await eventually(lambda: 'choosemodel' in plain(node.output))
                assert 'controlled-model' in node.output
                await page.screenshot(path='/tmp/dream-model-picker-real.png')
                before = len(node.output)
                await page.keyboard.press('Escape')
                await page.keyboard.type('/help')
                await page.keyboard.press('Enter')
                await eventually(lambda: 'chooseorswitchmodel' in plain(node.output[before:]))
                assert http.requests == []
            finally:
                await browser.close()
    finally:
        await server.stop()
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)
    assert node.process.returncode is not None and node.master is None
