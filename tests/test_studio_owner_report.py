"""DREAM-111 (#66): Dream learns what the owner's Studio actually shows.

Live, the model broke falcon9/post.js, fixed it, and its hidden frame was clean; show_to_user
and done said "Preview queued ...; rendering is not confirmed" while the owner's Studio showed
"Failed to start / module code@about:srcdoc:255:34". Now the Studio page reports its own load
outcome -- console errors, uncaught exceptions, unhandled rejections, a lost WebGL context, or
a clean load -- and the next show_html / show_to_user / done result carries one line on it
("Studio (owner's view): ..."), or says plainly that no Studio is connected or none answered.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dream.gui import preview as preview_mod
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tools import context as tool_context, mirror, studio
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.studio import done, show_html, show_to_user

UI = Path(__file__).parent.parent / "dream" / "gui" / "static" / "index.html"
SHOTS = Path(os.environ.get("DREAM_STUDIO_SHOTS") or "/nonexistent")


def _text(res) -> str:
    return res["content"][0]["text"]


def _report(srv, path, content, *, errors=(), status="loaded", phase="load", lost=0):
    return srv.add_report({"path": path, "digest": mirror.digest(content), "status": status,
                           "phase": phase, "errors": list(errors), "webgl_lost": lost})


# --- the route ---------------------------------------------------------------------------


async def test_the_report_route_needs_the_token_and_the_origin_and_validates(tmp_path):
    srv = StudioServer(EventBus(), session={"workspace": str(tmp_path)})
    good = {"path": "page.html", "digest": "0123abcd", "status": "loaded", "phase": "load",
            "errors": ["error: boom"] + ["x" * 900] * 30, "webgl_lost": 0}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test") as c:
        assert (await c.post("/api/studio_report", json=good)).status_code == 401
        head = {"X-Dream-Token": srv.token}
        r = await c.post("/api/studio_report", json=good, headers={**head, "Origin": "http://evil.example"})
        assert r.status_code == 403
        for bad in ({**good, "status": "fine"}, {**good, "digest": "../x"}, {**good, "errors": "boom"},
                    {**good, "errors": [1]}, {**good, "path": ""}, {**good, "phase": "later"},
                    {**good, "webgl_lost": -1}, ["not", "an", "object"]):
            assert (await c.post("/api/studio_report", json=bad, headers=head)).status_code == 400, bad
        assert (await c.post("/api/studio_report", content=b"{nope", headers=head)).status_code == 400
        huge = b'{"path": "page.html", "pad": "' + b"x" * 70_000 + b'"}'
        assert (await c.post("/api/studio_report", content=huge, headers=head)).status_code == 413
        assert srv.report_mark() == 0
        r = await c.post("/api/studio_report", json=good, headers={**head, "Origin": "http://test"})
        assert r.status_code == 200 and r.json() == {"ok": True}
    (rep,) = srv.reports_since(0)
    assert rep["seq"] == 1 and rep["path"] == "page.html" and rep["digest"] == "0123abcd"
    assert len(rep["errors"]) == 20 and max(len(e) for e in rep["errors"]) <= 500, "bounded"


async def test_the_pane_and_the_tools_compute_the_same_digest():
    """The pane names the page it ran by a digest of the exact content it was sent."""
    from playwright.async_api import async_playwright

    ui = UI.read_text()
    fn = re.search(r"function studioDigest\(s\)\{.*?\n\}", ui, re.S)
    assert fn, "index.html defines studioDigest"
    samples = ["", "<p>x</p>", "const s = `a ${b}`; // GLSL `comment`", "é ü ñ", "🚀 rocket 🌊",
               "line\u2028sep", "\ufffd", "x" * 200_000]
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
        page = await browser.new_page()
        got = await page.evaluate(f"samples => {{ {fn.group(0)}; return samples.map(studioDigest); }}", samples)
        await browser.close()
    assert got == [mirror.digest(s) for s in samples]
    assert all(re.fullmatch(r"[0-9a-f]{8}", d) for d in got)


# --- the tools, against a pane stand-in ------------------------------------------------------


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """A real StudioServer (not listening) with one browser counted as connected; the
    `pane` callable plays the owner's Studio: it answers each show the way the page did."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "page.html").write_text("<!doctype html><h1>v1</h1>")
    srv = StudioServer(EventBus(), session={"workspace": str(ws)})
    srv._ready = True
    srv._task = SimpleNamespace(done=lambda: False)
    srv._clients.add(object())
    answers: list = []   # what the pane does for each page show: None = stays silent; "times": n = n copies

    def emit(ev):
        srv.retain_show(ev)
        if ev.data.get("op") == "show" and ev.data.get("kind") != "image":
            answer = answers.pop(0) if answers else None
            if answer is not None:
                for _ in range(answer.pop("times", 1)):
                    asyncio.get_running_loop().call_later(
                        0.1, lambda: _report(srv, ev.data["path"], ev.data["content"], **answer))

    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=ws, emit=emit))
    set_studio(srv)

    async def load(p):
        return []
    monkeypatch.setattr(studio, "_load", load)
    monkeypatch.setattr(studio, "verify_page", lambda w, p: "checks: ok")
    monkeypatch.setattr(studio, "STUDIO_REPORT_WAIT_S", 1.0)
    studio._forget_reports()
    yield ws, srv, answers
    set_studio(None)
    tool_context._CTX = None
    studio._forget_reports()


@pytest.mark.asyncio
async def test_each_show_tool_says_what_the_owners_studio_reported(owner):
    ws, srv, answers = owner
    answers.extend([{"errors": ["error: SyntaxError: Unexpected token '`' (about:srcdoc:255:34)"]},
                    {}, {"status": "code"}, {"lost": 1}])
    res = await show_html.handler({"path": "page.html"})
    line = [l for l in _text(res).splitlines() if l.startswith("Studio (owner's view):")]
    assert line == ["Studio (owner's view): 1 error: error: SyntaxError: Unexpected token '`' "
                    "(about:srcdoc:255:34) -- the owner sees this failing, whatever your hidden frame says."]
    res = await show_to_user.handler({"path": "page.html"})
    assert "Studio (owner's view): loaded clean (no errors)." in _text(res)
    assert "rendering is not confirmed" not in _text(res), "confirmed now"
    res = await done.handler({"path": "page.html"})
    assert "Studio (owner's view): the owner has the code view open; the page is not running there." in _text(res)
    res = await show_to_user.handler({"path": "page.html"})
    assert ("Studio (owner's view): WebGL context lost (the canvas stops drawing) -- the owner sees this "
            "failing, whatever your hidden frame says.") in _text(res)


@pytest.mark.asyncio
async def test_a_silent_or_absent_studio_is_said_plainly(owner):
    ws, srv, answers = owner
    res = await show_to_user.handler({"path": "page.html"})   # the pane never answers
    assert ("Studio (owner's view): no report from the owner's Studio within 1 s; "
            "what it shows is unconfirmed.") in _text(res)
    assert "rendering is not confirmed" in _text(res)
    srv._clients.clear()
    res = await show_html.handler({"path": "page.html"})
    assert "Studio (owner's view): no Studio browser is connected, so the owner sees nothing yet." in _text(res)
    set_studio(None)
    res = await show_to_user.handler({"path": "page.html"})
    assert "Studio is not open" in _text(res) and "Studio (owner's view)" not in _text(res)


@pytest.mark.asyncio
async def test_a_report_about_an_older_version_never_answers_for_the_new_one(owner):
    """The fix-then-show sequence of #66: the broken version's late report must not be read
    as the fixed version's."""
    ws, srv, answers = owner
    old = (ws / "page.html").read_text()
    (ws / "page.html").write_text("<!doctype html><h1>v2 fixed</h1>")
    answers.append({})                                   # the fixed page loads clean ...
    loop = asyncio.get_running_loop()
    loop.call_later(0.02, lambda: _report(srv, "page.html", old, errors=["error: the old version"]))
    res = await show_to_user.handler({"path": "page.html"})   # ... after a late report about the old one
    assert "Studio (owner's view): loaded clean (no errors)." in _text(res)


@pytest.mark.asyncio
async def test_a_late_report_about_a_page_since_replaced_is_told_in_the_past(owner):
    """DREAM-113 (the DREAM-111 gate's note): the old version's late report came back at the next check
    ending "the owner sees this failing" -- present tense, about a page the Studio no longer shows."""
    ws, srv, answers = owner
    old = (ws / "page.html").read_text()
    (ws / "page.html").write_text("<!doctype html><h1>v2 fixed</h1>")
    answers.append({})
    loop = asyncio.get_running_loop()
    loop.call_later(0.02, lambda: _report(srv, "page.html", old, errors=["error: the old version"]))
    await show_to_user.handler({"path": "page.html"})
    answers.append({})
    res = await show_to_user.handler({"path": "page.html"})   # the next check says it once, as past
    late = [line for line in _text(res).splitlines() if "since your last check" in line]
    assert late == ["Studio (owner's view), since your last check: page.html: 1 error: error: the old version "
                    "-- the owner saw this failing before the page changed."]
    assert "sees this failing" not in _text(res)
    assert "Studio (owner's view): loaded clean (no errors)." in _text(res)


@pytest.mark.asyncio
async def test_what_happens_after_the_answer_reaches_the_next_result(owner):
    """A lost WebGL context usually comes later than the load: the next result carries it."""
    ws, srv, answers = owner
    answers.append({})
    res = await show_html.handler({"path": "page.html"})
    assert "loaded clean" in _text(res)
    content = (ws / "page.html").read_text()
    _report(srv, "page.html", content, phase="update", lost=1)
    answers.append({})
    res = await show_to_user.handler({"path": "page.html"})
    text = _text(res)
    assert ("Studio (owner's view), since your last check: page.html: WebGL context lost "
            "(the canvas stops drawing) -- the owner saw this failing, whatever your hidden frame says.") in text
    res = await show_to_user.handler({"path": "page.html"})
    assert "since your last check" not in _text(res), "said once"


@pytest.mark.asyncio
async def test_with_following_off_show_html_waits_for_nothing(owner):
    ws, srv, answers = owner
    srv.follow_model_view = False
    res = await show_html.handler({"path": "page.html"})
    assert "turned off" in _text(res) and "Studio (owner's view)" not in _text(res)


@pytest.mark.asyncio
async def test_a_repeated_identical_report_is_not_news(owner):
    """R5 (gate 1): in code view the pane paints a new version of the open page twice (addArtifact,
    then openArtifact) and so reports twice; the copy came back at the next call as a stale "since
    your last check" line. What the model was already told is not news; a change still is."""
    ws, srv, answers = owner
    answers.extend([{"status": "code", "times": 2}, {"times": 2}])
    res = await show_html.handler({"path": "page.html"})
    assert "the owner has the code view open" in _text(res)
    res = await show_to_user.handler({"path": "page.html"})
    assert "since your last check" not in _text(res), _text(res)
    assert "Studio (owner's view): loaded clean (no errors)." in _text(res)
    content = (ws / "page.html").read_text()
    _report(srv, "page.html", content, phase="update", lost=1)   # later: the context is lost ...
    _report(srv, "page.html", content, phase="update", lost=1)   # ... and a second pane says the same
    answers.append({})
    res = await show_to_user.handler({"path": "page.html"})
    assert _text(res).count("since your last check") == 1, _text(res)
    assert "since your last check: page.html: WebGL context lost" in _text(res)


# --- the real pane --------------------------------------------------------------------------


STUDIO_ONLY = ("<!doctype html><title>s</title><h1 id='h'>booting...</h1><script>"
               "if (location.protocol === 'about:') throw new Error('fails only in the Studio frame');"
               "document.getElementById('h').textContent = 'running';</script>")
MODULE_BREAK = ("<!doctype html><title>m</title><h1 id='h'>booting...</h1><script type='module'>\n"
                "const shader = `precision mediump float; // a GLSL `comment`\nvoid main(){}`;\n"
                "document.getElementById('h').textContent = 'running';\n</script>")
WEBGL_LOSS = ("<!doctype html><title>g</title><h1 id='h'>gl</h1><script>"
              "const c = document.createElement('canvas'); document.body.append(c);"
              "const gl = c.getContext('webgl'); gl.getExtension('WEBGL_lose_context').loseContext();"
              "</script>")
REJECTS = ("<!doctype html><title>r</title><h1>r</h1><script>"
           "Promise.reject(new Error('nobody caught this')); console.error('asset missing:', 'terrain.bin');"
           "</script>")
CLEAN = "<!doctype html><title>c</title><h1 id='h'>fine</h1>"
# The falcon9 shape (gate 1): the page's code is a separate module file, which the Studio frame
# cannot load (it loads nothing by URL) while the hidden frame reads it from disk.
FALCON = ("<!doctype html><title>f</title><h1 id='h'>booting...</h1>"
          "<script type='module' src='post.js'></script>")


@pytest.mark.asyncio
async def test_the_owners_real_pane_reports_what_only_it_shows(tmp_path, monkeypatch):
    """The real pane (the desktop's ?companion=1 page) in headless Chromium and the real
    hidden frame: a page that fails only in the Studio frame, a module that does not parse,
    a lost WebGL context, an unhandled rejection with a console error, and a clean page."""
    from playwright.async_api import async_playwright, expect

    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    monkeypatch.setattr(preview_mod, "display_render_node", lambda: None)
    ws = tmp_path / "falcon9"
    ws.mkdir()
    for name, body in (("studio-only.html", STUDIO_ONLY), ("module.html", MODULE_BREAK),
                       ("webgl.html", WEBGL_LOSS), ("rejects.html", REJECTS), ("clean.html", CLEAN),
                       ("falcon9.html", FALCON), ("post.js", "document.getElementById('h').textContent = 'running';")):
        (ws / name).write_text(body)
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={"workspace": str(ws), "model": "fixture"})

    def funnel(ev):
        srv.retain_show(ev)
        srv.bus.publish(ev)

    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=ws, emit=funnel))
    preview_mod._PREVIEW = None
    studio._forget_reports()
    url = await srv.start()
    set_studio(srv)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 2554, "height": 1338})
            page.set_default_timeout(8000)
            await page.goto(url + "&companion=1", wait_until="load")
            await page.wait_for_function("document.getElementById('stat').textContent !== 'connecting'")
            for _ in range(50):
                if srv.client_count:
                    break
                await asyncio.sleep(0.05)

            res = await show_html.handler({"path": "studio-only.html"})
            text = _text(res)
            assert "clean — no console output" in text, "the hidden frame (file:) runs it fine"
            assert "Studio (owner's view): 1 error: " in text and "fails only in the Studio frame" in text, text
            frame = page.frame_locator("#artbody iframe")
            await expect(frame.locator("#h")).to_have_text("booting...")
            if SHOTS.is_dir():
                await page.screenshot(path=str(SHOTS / "studio-report-studio-only-2554x1338.png"))

            res = await show_to_user.handler({"path": "module.html"})
            assert "Studio (owner's view): 1 error: " in _text(res) and "SyntaxError" in _text(res), _text(res)

            res = await show_to_user.handler({"path": "webgl.html"})
            assert "Studio (owner's view): WebGL context lost" in _text(res), _text(res)

            res = await show_to_user.handler({"path": "rejects.html"})
            assert "Studio (owner's view): 2 errors: " in _text(res), _text(res)
            assert "nobody caught this" in _text(res) and "asset missing: terrain.bin" in _text(res)

            res = await done.handler({"path": "clean.html"})
            assert not res.get("is_error"), _text(res)
            assert "Studio (owner's view): loaded clean (no errors)." in _text(res)

            # gate 1: a page whose code is a module file (falcon9/post.js): the frame names it
            res = await show_to_user.handler({"path": "falcon9.html"})
            assert ("Studio (owner's view): 1 error: failed to load <script post.js> "
                    "(the Studio frame loads nothing by URL)") in _text(res), _text(res)

            # gate 1 (R5): the owner reads the code; the model edits the open page twice in a row
            await show_to_user.handler({"path": "clean.html"})
            await page.click("#artcode")
            (ws / "clean.html").write_text(CLEAN.replace("fine", "fine v2"))
            res = await show_html.handler({"path": "clean.html"})
            assert "Studio (owner's view): the owner has the code view open" in _text(res), _text(res)
            res = await show_html.handler({"path": "clean.html"})
            assert "Studio (owner's view): the owner has the code view open" in _text(res), _text(res)
            assert "since your last check" not in _text(res), _text(res)
            await browser.close()
    finally:
        set_studio(None)
        tool_context._CTX = None
        studio._forget_reports()
        await srv.stop()
        pv = preview_mod._PREVIEW
        if pv is not None:
            await pv.aclose()
        preview_mod._PREVIEW = None
