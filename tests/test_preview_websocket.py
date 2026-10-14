"""2026-10-12 security review (DREAM-290): the hidden model frame's network rule (https to a public host only,
DREAM-234) was applied through context.route, which Playwright does not apply to WebSockets -- a page loaded with
show_html/eval_js reached a ws:// service on this computer. WebSockets now get the same line: wss:// to a public host,
nothing else."""
from __future__ import annotations

import pytest
import websockets

from dream.gui.preview import Preview

pytestmark = pytest.mark.asyncio

_OPEN = """new Promise(r => {{ const s = new WebSocket({url!r});
  s.onopen = () => s.send('ping'); s.onmessage = e => r('reached:' + e.data);
  s.onerror = () => r('refused'); s.onclose = () => r('refused'); setTimeout(() => r('timeout'), 4000); }})"""


@pytest.fixture
async def pv():
    p = Preview()
    yield p
    await p.aclose()


async def test_a_websocket_to_this_computer_is_refused_and_logged(pv, tmp_path):
    heard = []

    async def answer(ws):
        heard.append(await ws.recv())
        await ws.send("pong")

    server = await websockets.serve(answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        page = tmp_path / "a.html"
        page.write_text("<!doctype html><p>x</p>")
        await pv.load(page)
        plain = await pv.eval(_OPEN.format(url=f"ws://127.0.0.1:{port}/"))
        secure = await pv.eval(_OPEN.format(url=f"wss://localhost:{port}/"))
    finally:
        server.close()
    assert plain == "refused" and secure == "refused" and heard == []
    assert f"ws://127.0.0.1:{port}/" in pv.blocked and f"wss://localhost:{port}/" in pv.blocked


_FROM_A_WORKER = """new Promise(r => {{
  const src = `const s = new WebSocket({url!r});
    s.onopen = () => s.send('ping'); s.onmessage = e => postMessage('reached:' + e.data);
    s.onerror = () => postMessage('refused'); s.onclose = () => postMessage('refused');`;
  const w = new Worker(URL.createObjectURL(new Blob([src], {{type: 'text/javascript'}})));
  w.onmessage = e => r(e.data); w.onerror = e => r('worker-error:' + e.message);
  setTimeout(() => r('timeout'), 4000); }})"""


async def test_a_websocket_from_a_worker_cannot_reach_this_computer_either(pv, tmp_path):
    """(gate round 1, finding 5) route_web_socket wraps the page's own WebSocket constructor, which a Worker does not
    use. The frame's context now sits behind Dream's network boundary (the controlled browser's proxy, computer.py):
    every connection is judged there, a Worker's included, and nothing on this computer is reachable."""
    heard = []

    async def answer(ws):
        heard.append(await ws.recv())
        await ws.send("pong")

    server = await websockets.serve(answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        page = tmp_path / "a.html"
        page.write_text("<!doctype html><p>x</p>")
        await pv.load(page)
        result = await pv.eval(_FROM_A_WORKER.format(url=f"ws://127.0.0.1:{port}/"))
    finally:
        server.close()
    assert result == "refused" and heard == []
