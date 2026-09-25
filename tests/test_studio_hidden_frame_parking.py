"""DREAM-111 (#67): the hidden preview stops rendering when its work is done.

Live, the model's hidden frame (two chrome-headless-shell processes) kept rendering the
heavy three.js scene after `done`, on the same iGPU as the owner's Studio: a loaded page
stayed up for GPU_IDLE_S (30 min) after its last use. Now the frame parks -- its page goes
to about:blank, so no script and no render loop of it runs -- right after `done`, and after
PARK_IDLE_S (60 s) unused. The next eval_js, screenshot or show_html reopens it.

These drive a real headless Chromium through the tool handlers, on the software renderer
(the display GPU is left alone).
"""

from __future__ import annotations

import asyncio

import pytest

from dream import config
from dream.gui import preview as preview_mod
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.studio import done, eval_js, get_webview_logs, save_screenshot, show_html

pytestmark = pytest.mark.asyncio

# A render loop, the way a three.js scene runs: one console line every 10 frames.
SPIN = """<!doctype html><title>spin</title><canvas id="c" width="64" height="64"></canvas>
<script>
window.v = 7; let n = 0; console.log('spin loaded');
function frame(){ n++; if(n % 10 === 0) console.log('frame ' + n); requestAnimationFrame(frame); }
requestAnimationFrame(frame);
</script>"""


def _text(res) -> str:
    return res["content"][0]["text"]


def _frames(pv) -> int:
    return sum(1 for line in pv.logs if line.startswith("console.log: frame"))


@pytest.fixture
async def ws(tmp_path, monkeypatch):
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=lambda ev: None))
    set_studio(None)
    monkeypatch.setattr(config, "SCREENSHOT_DIR", tmp_path / "var" / "shots")
    monkeypatch.setattr(preview_mod, "display_render_node", lambda: None)
    preview_mod._PREVIEW = None
    (tmp_path / "spin.html").write_text(SPIN)
    yield tmp_path
    pv = preview_mod._PREVIEW
    if pv is not None:
        await pv.aclose()
    preview_mod._PREVIEW = None
    tool_context._CTX = None


async def _rendering(pv, seconds=0.5) -> bool:
    before = _frames(pv)
    await asyncio.sleep(seconds)
    return _frames(pv) > before


async def test_done_parks_the_hidden_frame_and_no_page_is_left_rendering(ws):
    res = await show_html.handler({"path": "spin.html"})
    assert not res.get("is_error"), _text(res)
    pv = preview_mod.get_preview()
    assert await _rendering(pv), "control: the loaded page renders"

    res = await done.handler({"path": "spin.html"})
    assert not res.get("is_error"), _text(res)
    assert "parked" in _text(res), "the model is told its frame stopped"
    assert pv.loaded is None and pv.parked == (ws / "spin.html").resolve()
    pages = pv._context.pages
    assert pages and all(p.url == "about:blank" for p in pages), [p.url for p in pages]
    assert not await _rendering(pv), "nothing of the page runs after done"


async def test_the_next_eval_js_and_screenshot_reopen_the_parked_page(ws):
    await show_html.handler({"path": "spin.html"})
    await done.handler({"path": "spin.html"})
    pv = preview_mod.get_preview()
    logs = _text(await get_webview_logs.handler({}))
    assert "spin.html" in logs and "spin loaded" in logs, "the last load's console is still readable"

    res = await eval_js.handler({"code": "window.v * 6"})
    assert not res.get("is_error"), _text(res)
    assert _text(res).splitlines()[-1] == "42"
    assert "reloaded" in _text(res), "the model hears that earlier eval_js state is gone"
    assert pv.loaded == (ws / "spin.html").resolve() and pv.parked is None

    await done.handler({"path": "spin.html"})
    res = await save_screenshot.handler({"path": "spin.html", "steps": [{}], "save_path": "shots/after.png"})
    assert not res.get("is_error"), _text(res)
    assert (ws / "shots" / "after.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


async def test_eval_js_with_nothing_ever_loaded_still_says_show_html_first(ws):
    res = await eval_js.handler({"code": "1"})
    assert res.get("is_error") and "show_html first" in _text(res)


async def test_the_hidden_frame_parks_after_idle_seconds_and_not_while_in_use(ws, monkeypatch):
    assert preview_mod.PARK_IDLE_S == 60, "the stated default"
    monkeypatch.setattr(preview_mod, "PARK_IDLE_S", 0.6)
    await show_html.handler({"path": "spin.html"})
    pv = preview_mod.get_preview()
    # in use: an eval every 0.2 s keeps it loaded past the idle limit
    for _ in range(6):
        await asyncio.sleep(0.2)
        await eval_js.handler({"code": "1"})
    assert pv.loaded is not None and pv.parked is None
    # left alone: parked within a tick or two of the limit
    for _ in range(40):
        if pv.parked is not None:
            break
        await asyncio.sleep(0.1)
    assert pv.parked == (ws / "spin.html").resolve() and pv.loaded is None
    assert all(p.url == "about:blank" for p in pv._context.pages)
    assert not await _rendering(pv)
    res = await eval_js.handler({"code": "window.v"})
    assert _text(res).splitlines()[-1] == "7"


# --- gate 1 (2026-09-24): a park racing a call ------------------------------------------------

RED = ("<!doctype html><title>red</title><body style='margin:0;background:#c03'><h1>visible page</h1>"
       "<script>window.v = 7</script>")


async def _park_in_flight(pv):
    """The idle reaper's park, caught mid-navigation: it holds the operation lock and awaits its
    about:blank load, while `loaded` still names the page."""
    park = asyncio.create_task(pv.park(unused_since=pv.last_used))
    await asyncio.sleep(0)
    assert pv._op.locked() and pv.loaded is not None, "the park is in flight"
    return park


async def test_a_park_racing_a_call_never_answers_from_the_blank_page(ws):
    """R4: eval_js / save_screenshot / pdf checked `loaded` before taking the lock; a park that
    finished in between made them evaluate, capture or print about:blank."""
    from PIL import Image

    (ws / "red.html").write_text(RED)
    await show_html.handler({"path": "red.html"})
    pv = preview_mod.get_preview()

    park = await _park_in_flight(pv)
    res = await eval_js.handler({"code": "location.pathname.split('/').pop() + ' v=' + window.v"})
    await park
    assert not res.get("is_error"), _text(res)
    assert _text(res).splitlines()[-1] == '"red.html v=7"', _text(res)
    assert "reloaded" in _text(res), "the model hears that the page was loaded afresh"

    await show_html.handler({"path": "red.html"})
    park = await _park_in_flight(pv)
    res = await save_screenshot.handler({"path": "red.html", "steps": [{}], "save_path": "cap.png", "hq": True})
    await park
    assert not res.get("is_error"), _text(res)
    assert Image.open(ws / "cap.png").convert("RGB").getpixel((640, 400)) == (204, 0, 51), "the page, not a blank"

    await show_html.handler({"path": "red.html"})
    park = await _park_in_flight(pv)
    out = await pv.pdf(ws / "red.pdf")
    await park
    assert out.read_bytes()[:5] == b"%PDF-"
