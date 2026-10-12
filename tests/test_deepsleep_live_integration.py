"""Shipped browser + real Dream child CLIs + controlled HTTP model, no GPUs."""
import asyncio
from dataclasses import replace
import json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import threading

import pytest
from playwright.async_api import async_playwright, expect

from dream import config
from dream.core import providers
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


class ModelHTTP(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith('/v1/models'):
            data = {'object': 'list', 'data': [{'id': 'controlled-model', 'object': 'model'}]}
        else:
            data = {'total_slots': 1, 'slot_ctx': 32768, 'prompt_cache_slots': 1,
                    'vision': {'ready': False, 'reason': 'controlled text fixture'},
                    'lanes': 1, 'lanes_active': 0, 'inflight': 0, 'queued': 0}
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(payload)
        chunks = [dict(id='fixture', object='chat.completion.chunk', model='controlled-model',
            choices=[{'index': 0, 'delta': {'role': 'assistant', 'content': 'Fixture verified.'}, 'finish_reason': None}]),
            dict(id='fixture', object='chat.completion.chunk', model='controlled-model',
            choices=[{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
            usage={'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13})]
        raw = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks).encode() + b'data: [DONE]\n\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


async def eventually(predicate, seconds=25):
    end = asyncio.get_running_loop().time() + seconds
    while not predicate():
        assert asyncio.get_running_loop().time() < end, 'Actual Dream child did not reach the expected state'
        await asyncio.sleep(0.1)


async def test_shipped_ui_real_dream_terminals_and_linked_completion(tmp_path, monkeypatch):
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
    s = StudioServer(EventBus(), session={'session_id': 'actual-child-fixture', 'workspace': str(workspace), 'provider': 'machx'})
    url = await s.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1700, 'height': 1050})
            page.set_default_timeout(10000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            await page.route('**/api/accounts', lambda route: route.fulfill(json={'accounts': []}))
            await page.goto(url.replace('/#', '/?companion=1#'))
            await page.get_by_role('button', name='Go to DeepSleep', exact=True).click()
            await page.get_by_label('Terminal provider').select_option('machx')
            await page.get_by_role('button', name='Add terminal', exact=True).click()
            await eventually(lambda: s._deepsleep and len(s._deepsleep.nodes) == 1)
            a = next(iter(s._deepsleep.nodes.values()))
            await eventually(lambda: a.state == 'ready')
            await page.get_by_role('button', name='Add terminal', exact=True).click()
            await eventually(lambda: len(s._deepsleep.nodes) == 2)
            b = list(s._deepsleep.nodes.values())[1]
            await eventually(lambda: b.state == 'ready')
            await page.get_by_role('button', name='Refresh workspace', exact=True).click()
            await expect(page.locator('.ds-node')).to_have_count(2)
            assert a.process.pid != b.process.pid
            await page.get_by_role('button', name='Show connections and delivery activity').click()
            await page.get_by_text('Connections and delivery details').click()   # the box sits in this fold (board redesign)
            await page.get_by_label('Automatically forward completed replies on new links').check()
            await page.get_by_role('button', name='Show connections and delivery activity').click()
            first, second = page.locator('.ds-node').nth(0), page.locator('.ds-node').nth(1)
            await first.get_by_role('button', name='Link from ' + a.title, exact=True).click()
            await second.get_by_role('button', name='Link to ' + b.title, exact=True).click()
            await expect(page.locator('.ds-wire')).to_have_count(1)
            await page.get_by_role('button', name='Show connections and delivery activity').click()
            await first.locator('.xterm-helper-textarea').focus()
            await page.keyboard.type('Reply with a brief test result.')
            await page.keyboard.press('Enter')
            await eventually(lambda: any(r['target'] == b.id and r['state'] == 'completed' for r in s._deepsleep.receipts.values()), 30)
            assert len(http.requests) == 2
            assert 'Fixture verified.' in http.requests[1]['messages'][-1]['content']
            await page.get_by_role('button', name='Refresh workspace').click()
            await page.screenshot(path='/tmp/dream-deepsleep-real-children.png')
            assert not errors
            await browser.close()
    finally:
        await s.stop()
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)
    assert all(n.process.returncode is not None and n.master is None for n in s._deepsleep.nodes.values())
