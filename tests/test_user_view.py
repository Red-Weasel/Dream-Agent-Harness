"""Checkpoint 4b — the user's view: the server round trip, the bridge in the panel,
the two tools, and one end-to-end run with the real panel in a real browser."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dream import config
from dream.core.backends.base import Event
from dream.gui import preview as preview_mod
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.studio import STUDIO_TOOLS, eval_js_user_view, screenshot_user_view

UI = Path(__file__).parent.parent / "dream" / "gui" / "static" / "index.html"


def _text(res):
    return res["content"][0]["text"]


def _failed(res):
    return bool(res.get("is_error"))


# --- the server leg ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_frame_publishes_an_event_and_waits_for_the_reply_by_id():
    srv = StudioServer(EventBus())
    with srv.bus.subscribe() as sub:
        task = asyncio.ensure_future(srv.ask_frame("eval", {"code": "1+1"}, timeout=2))
        ev = await asyncio.wait_for(sub.get(), 1)
        assert ev.kind == "studio" and ev.data["op"] == "eval" and ev.data["code"] == "1+1"
        fid = ev.data["id"]
        assert fid in srv._pending
        srv._pending[fid].set_result({"ok": True, "value": 2})
        assert await task == 2
    assert fid not in srv._pending


@pytest.mark.asyncio
async def test_ask_frame_times_out_and_surfaces_a_frame_error():
    srv = StudioServer(EventBus())
    with pytest.raises(TimeoutError) as e:
        await srv.ask_frame("eval", {"code": "1"}, timeout=0.05)
    assert "is the panel open" in str(e.value) and not srv._pending
    task = asyncio.ensure_future(srv.ask_frame("snapshot", {}, timeout=2))
    await asyncio.sleep(0.01)
    (fid,) = srv._pending
    srv._pending[fid].set_result({"ok": False, "value": "no artifact is open in the Studio panel"})
    with pytest.raises(RuntimeError, match="no artifact is open"):
        await task


def test_the_reply_route_needs_the_token_and_a_live_id():
    srv = StudioServer(EventBus())
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    srv._pending["abc"] = fut
    with TestClient(srv.app) as client:
        assert client.post("/api/frame_reply", json={"id": "abc", "ok": True, "value": 1}).status_code == 401
        h = {"X-Dream-Token": srv.token}
        assert client.post("/api/frame_reply", json={"id": "nope", "ok": True}, headers=h).status_code == 404
        assert client.post("/api/frame_reply", json={"id": "abc", "ok": True, "value": 7}, headers=h).status_code == 200
        assert client.post("/api/frame_reply", json={"id": "abc", "ok": True, "value": 8}, headers=h).status_code == 404
    assert fut.result() == {"ok": True, "value": 7}
    loop.close()


# --- the bridge in the panel -------------------------------------------------------------


def test_the_bridge_and_csp_are_in_every_artifact_document():
    ui = UI.read_text()
    assert "const ART_BRIDGE" in ui and "__dream_eval" in ui and "__dream_snapshot" in ui
    csp = re.search(r"const ART_CSP\s*=\s*(.+?);\n", ui, re.S).group(1)
    assert "'unsafe-eval'" in csp and "allow-same-origin" not in csp
    body = ui[ui.index("function paintArtifact"):ui.index("$('artver').onchange")]
    # the bridge rides right behind the CSP at the head of every srcdoc — svg,
    # full document, fragment alike (Gate 8: no more splicing at a regex match)
    assert "`<!doctype html>${csp}${ART_BRIDGE}" in body
    assert ".replace(/<head" not in body
    # the bridge's closing tag cannot terminate the panel's own script
    assert "<\\/script>`" in ui
    relay = ui[ui.index("function askFrame"):ui.index("$('artver').onchange")]
    assert "e.source !== f.contentWindow" in relay, "only the open frame may answer"
    assert "no artifact is open" in relay
    # Gate 4b observations: an ask waits for the frame's load; code view says so
    assert "frameReady" in relay and "addEventListener('load', post" in relay
    assert "code view" in relay


# --- the tools, against a fake server ------------------------------------------------------


class _FakeStudio:
    def __init__(self, reply=None, raise_=None):
        self.reply, self.raise_, self.calls = reply, raise_, []

    async def ask_frame(self, op, payload=None, timeout=20.0):
        self.calls.append((op, payload))
        if self.raise_:
            raise self.raise_
        return self.reply


@pytest.fixture
def ws(tmp_path, monkeypatch):
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path))
    monkeypatch.setattr(config, "SCREENSHOT_DIR", tmp_path / "shots")
    preview_mod._PREVIEW = None
    yield tmp_path
    set_studio(None)
    tool_context._CTX = None


@pytest.mark.asyncio
async def test_eval_js_user_view_needs_studio_and_returns_json(ws):
    set_studio(None)
    res = await eval_js_user_view.handler({"code": "1"})
    assert _failed(res) and "Studio is not open" in _text(res)
    fake = _FakeStudio(reply={"a": [1, 2]})
    set_studio(fake)
    assert _text(await eval_js_user_view.handler({"code": "x"})) == '{"a": [1, 2]}'
    assert fake.calls == [("eval", {"code": "x"})]
    set_studio(_FakeStudio(raise_=TimeoutError("no Studio browser answered")))
    res = await eval_js_user_view.handler({"code": "x"})
    assert _failed(res) and "no Studio browser answered" in _text(res)


@pytest.mark.asyncio
async def test_screenshot_user_view_renders_the_snapshot_in_the_hidden_frame(ws):
    set_studio(_FakeStudio(reply="<!doctype html><html><body style='background:#0a0'><h1>live</h1>"
                                 "<input value='typed'></body></html>"))
    try:
        res = await screenshot_user_view.handler({})
        assert not _failed(res), _text(res)
        shots = sorted((ws / "shots").glob("user-view-*.jpg"))
        assert len(shots) == 1 and str(shots[0]) in _text(res)
        assert (ws / "shots").glob("user-view-*.html")
    finally:
        if preview_mod._PREVIEW is not None:
            await preview_mod._PREVIEW.aclose()
            preview_mod._PREVIEW = None


@pytest.mark.asyncio
async def test_the_snapshot_render_does_not_rerun_the_pages_scripts(ws):
    """Gate 4b observation 1: a counter clicked to 3 must screenshot as 3, not 0."""
    from dream.tools.studio import _inert_scripts

    html = ("<!doctype html><html><body><p id='c'>count: 3</p>"
            "<script>document.getElementById('c').textContent = 'count: 0'</script></body></html>")
    out = _inert_scripts(html)
    assert 'type="text/plain" data-inert="snapshot"' in out and "count: 3" in out
    set_studio(_FakeStudio(reply=html))
    try:
        res = await screenshot_user_view.handler({})
        assert not _failed(res)
        pv = preview_mod.get_preview()
        assert await pv.eval("document.getElementById('c').textContent") == "count: 3"
    finally:
        if preview_mod._PREVIEW is not None:
            await preview_mod._PREVIEW.aclose()
            preview_mod._PREVIEW = None


def test_the_user_view_tools_are_exported():
    assert {"eval_js_user_view", "screenshot_user_view"} <= {t.name for t in STUDIO_TOOLS}


# --- end to end: the real panel in a real browser ---------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_the_real_panel_answers_an_eval_and_a_snapshot(tmp_path):
    from playwright.async_api import async_playwright

    srv = StudioServer(EventBus())
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="load")
            # the panel writes "live" once its websocket is open
            await page.wait_for_function("document.getElementById('stat').textContent === 'live'", timeout=5000)
            # nothing open: the panel answers "no" itself
            with pytest.raises(RuntimeError, match="no artifact is open"):
                await srv.ask_frame("eval", {"code": "1"}, timeout=5)
            # open an artifact the way done() does
            srv.bus.publish(Event("studio", {"op": "show", "path": "x.html", "title": "x.html",
                                             "content": "<!doctype html><p id='p'>hi from bus</p>"
                                                        "<input id='i'>"}))
            await page.wait_for_selector("#artbody iframe", timeout=5000)
            await page.wait_for_timeout(300)
            assert await srv.ask_frame("eval", {"code": "document.getElementById('p').textContent"}, timeout=5) == "hi from bus"
            assert await srv.ask_frame("eval", {"code": "const a = 2; return a * 21;"}, timeout=5) == 42
            with pytest.raises(RuntimeError):
                await srv.ask_frame("eval", {"code": "nope()"}, timeout=5)
            # the user types something; the snapshot carries it
            frame = page.frame_locator("#artbody iframe")
            await frame.locator("#i").fill("typed by user")
            html = await srv.ask_frame("snapshot", {}, timeout=5)
            assert "hi from bus" in html and 'value="typed by user"' in html
            await browser.close()
    finally:
        await srv.stop()
