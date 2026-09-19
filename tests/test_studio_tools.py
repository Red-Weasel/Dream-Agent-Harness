"""The Studio tools, driven through the same handlers the backends call, against
a real hidden frame. The user's panel is a captured `emit`."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream import config
from dream.gui import preview as preview_mod
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.studio import (
    STUDIO_TOOLS, done, eval_js, get_webview_logs, multi_screenshot, save_screenshot,
    show_html, show_to_user,
)

BROKEN = "<!doctype html><h1>x</h1><script>console.log('hi'); undefinedThing();</script>"
CLEAN = "<!doctype html><h1 id='h'>fine</h1><script>window.v = 7;</script>"


@pytest.fixture
async def ws(tmp_path, monkeypatch):
    emitted: list = []
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=emitted.append))
    set_studio(object())  # a Studio server is up; show/done may reach the panel
    monkeypatch.setattr(config, "SCREENSHOT_DIR", tmp_path / "var" / "shots")
    # a fresh hidden frame per test, closed after
    preview_mod._PREVIEW = None
    yield tmp_path, emitted
    set_studio(None)
    pv = preview_mod._PREVIEW
    if pv is not None:
        await pv.aclose()
    preview_mod._PREVIEW = None
    tool_context._CTX = None


def _text(res) -> str:
    return res["content"][0]["text"]


def _failed(res) -> bool:
    return bool(res.get("is_error"))


@pytest.mark.asyncio
async def test_show_html_loads_the_hidden_frame_only_and_reports_errors(ws):
    root, emitted = ws
    (root / "p.html").write_text(BROKEN)
    res = await show_html.handler({"path": "p.html"})
    assert not _failed(res)
    out = _text(res)
    assert "hidden frame" in out and "1 error(s)" in out and "undefinedThing" in out
    assert emitted == [], "show_html must not touch the user's panel"


@pytest.mark.asyncio
async def test_get_webview_logs_needs_a_load_then_returns_the_console(ws):
    root, _ = ws
    res = await get_webview_logs.handler({})
    assert _failed(res) and "show_html first" in _text(res)
    (root / "p.html").write_text(BROKEN)
    await show_html.handler({"path": "p.html"})
    out = _text(await get_webview_logs.handler({}))
    assert "console.log: hi" in out and "undefinedThing" in out


@pytest.mark.asyncio
async def test_show_to_user_emits_a_studio_show_event_keyed_like_a_write(ws):
    root, emitted = ws
    (root / "page.html").write_text(CLEAN)
    res = await show_to_user.handler({"path": "page.html"})
    assert not _failed(res) and "Studio and the hidden frame" in _text(res)
    (ev,) = emitted
    assert ev.kind == "studio" and ev.data["op"] == "show"
    assert ev.data["path"] == "page.html" and ev.data["title"] == "page.html"
    assert ev.data["content"] == CLEAN


@pytest.mark.asyncio
async def test_show_to_user_without_studio_says_so(ws):
    """No Studio SERVER means no panel — even though the emit hook is wired."""
    root, emitted = ws
    set_studio(None)
    (root / "page.html").write_text(CLEAN)
    assert "Studio is not open" in _text(await show_to_user.handler({"path": "page.html"}))
    res = await done.handler({"path": "page.html"})
    assert not _failed(res) and "Studio is not open" in _text(res)
    assert emitted == [], "no event into an empty bus"


@pytest.mark.asyncio
async def test_done_is_an_error_result_until_the_page_is_clean(ws):
    root, emitted = ws
    (root / "page.html").write_text(BROKEN)
    res = await done.handler({"path": "page.html"})
    assert _failed(res) and "NOT clean" in _text(res) and "undefinedThing" in _text(res)
    assert emitted and emitted[-1].kind == "studio", "the user still lands on the page"
    (root / "page.html").write_text(CLEAN)
    res = await done.handler({"path": "page.html"})
    assert not _failed(res) and "clean" in _text(res)


@pytest.mark.asyncio
async def test_the_page_tools_refuse_missing_and_non_html_files(ws):
    root, _ = ws
    (root / "notes.txt").write_text("x")
    for t in (show_html, show_to_user, done, save_screenshot, multi_screenshot):
        res = await t.handler({"path": "nope.html", "steps": [{"code": "1"}]})
        assert _failed(res) and "No file" in _text(res), t.name
        if t is done:
            continue  # done accepts any existing deliverable (Dream fix #20); previewing still needs HTML
        res = await t.handler({"path": "notes.txt", "steps": [{"code": "1"}]})
        assert _failed(res) and "not an HTML" in _text(res), t.name
    assert _failed(await show_html.handler({}))


@pytest.mark.asyncio
async def test_eval_js_returns_json_and_errors_plainly(ws):
    root, _ = ws
    assert _failed(await eval_js.handler({"code": "1"}))  # nothing loaded yet
    (root / "page.html").write_text(CLEAN)
    await show_html.handler({"path": "page.html"})
    assert _text(await eval_js.handler({"code": "window.v * 6"})) == "42"
    assert _text(await eval_js.handler({"code": "document.getElementById('h').textContent"})) == '"fine"'
    assert _text(await eval_js.handler({"code": "({a: [1, 2]})"})) == '{"a": [1, 2]}'
    res = await eval_js.handler({"code": "nope()"})
    assert _failed(res) and "nope" in _text(res)


@pytest.mark.asyncio
async def test_save_screenshot_writes_prefixed_files_into_the_workspace(ws):
    root, _ = ws
    (root / "page.html").write_text(CLEAN)
    res = await save_screenshot.handler({
        "path": "page.html",
        "steps": [{}, {"code": "document.body.style.background='#000'", "delay": 30}],
        "save_path": "shots/hero.png",
    })
    assert not _failed(res)
    a, b = root / "shots" / "01-hero.png", root / "shots" / "02-hero.png"
    assert a.is_file() and b.is_file() and a.read_bytes() != b.read_bytes()
    assert str(a) in _text(res) and "`see`" in _text(res)


@pytest.mark.asyncio
async def test_save_screenshot_in_memory_and_argument_rules(ws):
    root, _ = ws
    (root / "page.html").write_text(CLEAN)
    res = await save_screenshot.handler({"path": "page.html", "steps": [{}],
                                         "in_memory_png_key": "deck"})
    assert not _failed(res) and preview_mod.get_preview().captures["deck"]
    both = await save_screenshot.handler({"path": "page.html", "steps": [{}],
                                          "save_path": "x.png", "in_memory_png_key": "k"})
    assert _failed(both) and "exactly one" in _text(both)
    neither = await save_screenshot.handler({"path": "page.html", "steps": [{}]})
    assert _failed(neither)
    bad_ext = await save_screenshot.handler({"path": "page.html", "steps": [{}], "save_path": "x.gif"})
    assert _failed(bad_ext) and ".png or .jpg" in _text(bad_ext)


@pytest.mark.asyncio
async def test_multi_screenshot_requires_code_per_step_caps_at_12_and_uses_the_shot_dir(ws):
    root, _ = ws
    (root / "page.html").write_text(CLEAN)
    res = await multi_screenshot.handler({"path": "page.html", "steps": [{}]})
    assert _failed(res) and "needs 'code'" in _text(res)
    res = await multi_screenshot.handler({"path": "page.html",
                                          "steps": [{"code": "1"}] * 13})
    assert _failed(res) and "at most 12" in _text(res)
    res = await multi_screenshot.handler({"path": "page.html", "steps": [{"code": "1", "delay": "abc"}]})
    assert _failed(res) and "delay must be a number" in _text(res)
    res = await multi_screenshot.handler({"path": "page.html", "steps": [
        {"code": "document.body.style.background='#111'", "delay": 20},
        {"code": "document.body.style.background='#eee'", "delay": 20},
    ]})
    assert not _failed(res)
    files = sorted((root / "var" / "shots").glob("*.jpg"))
    assert len(files) == 2 and all(str(f) in _text(res) for f in files)


def test_the_eleven_tools_are_exported():
    assert [t.name for t in STUDIO_TOOLS] == [
        "show_html", "get_webview_logs", "show_to_user", "done",
        "save_screenshot", "multi_screenshot", "eval_js",
        "eval_js_user_view", "screenshot_user_view", "run_script",
        "present_fs_item_for_download",
    ]


# --- criterion 6: the loop a model runs — write broken, done, fix, done, screenshot, see


@pytest.mark.asyncio
async def test_a_model_can_close_the_loop_write_done_fix_done_screenshot(ws):
    from dream.tools.files import str_replace_edit
    from dream.tools.native import write_file
    from dream.tools.vision import see

    root, emitted = ws
    page = ("<!doctype html><html><body><h1 id='h'>Deck</h1>\n"
            "<script>document.getElementById('h').textContnet = 'Hi'; window.ready = true;</script>\n"
            "</body></html>")
    assert not _failed(await write_file.handler({"path": "deck.html", "content": page}))
    # 1. done reports the runtime error the page throws (a misspelled property is
    #    silent, so make it a real error the way a model's typo usually is)
    (root / "deck.html").write_text(page.replace("textContnet = 'Hi'", "textContent = missingVar"))
    first = await done.handler({"path": "deck.html"})
    assert _failed(first) and "missingVar" in _text(first) and "call done again" in _text(first)
    # 2. the model repairs it surgically
    fix = await str_replace_edit.handler({"path": "deck.html",
                                          "old_string": "textContent = missingVar",
                                          "new_string": "textContent = 'Hi'"})
    assert not _failed(fix)
    # 3. done comes back clean and the user's panel was shown the page both times
    second = await done.handler({"path": "deck.html"})
    assert not _failed(second) and "clean" in _text(second)
    assert [e.data["op"] for e in emitted] == ["show", "show"]
    # 4. a screenshot lands where `see` can open it
    shot = await save_screenshot.handler({"path": "deck.html", "steps": [{}], "save_path": "deck.jpg"})
    assert not _failed(shot)
    seen = await see.handler({"path": str(root / "deck.jpg")})
    assert not _failed(seen)
    assert any(c.get("type") == "image" for c in seen["content"]), "see must return the image"


# --- wiring: registry, policy, engine, app, prompt ------------------------------------


def test_the_studio_tools_ship_in_the_registry_and_carry_their_classes():
    from dream.core import policy
    from dream.tools import registry

    names = {t.name for t in registry._BASE_TOOLS}
    assert {t.name for t in STUDIO_TOOLS} <= names
    for n in ("show_html", "get_webview_logs", "show_to_user", "done", "eval_js", "multi_screenshot"):
        assert policy.capability(n) == policy.READONLY, n
    assert policy.capability("save_screenshot") == policy.WRITE
    ws = Path("/tmp/ws-studio")
    inside = {"path": "a.html", "steps": [{}], "save_path": "shots/a.png"}
    assert policy.decide("save_screenshot", inside, "auto", ws)[0] == "allow"
    outside = {"path": "a.html", "steps": [{}], "save_path": "/tmp/elsewhere/a.png"}
    d, why = policy.decide("save_screenshot", outside, "auto", ws)
    assert d == "ask" and why.startswith("outside workspace")
    assert policy.decide("done", {"path": "a.html"}, "ask", ws)[0] == "allow"


def test_the_emit_hook_runs_from_the_app_through_the_engine_to_the_tools():
    root = Path(__file__).parent.parent / "dream"
    eng = (root / "core" / "engine.py").read_text()
    assert "emit=self.emit" in eng and "self.emit = emit" in eng
    app = (root / "tui" / "app.py").read_text()
    assert "emit=self._render_event" in app
    prompt = (root / "core" / "system_prompt.py").read_text()
    assert "`done(path)`" in prompt and "hidden frame" in prompt


# --- Phase 5 criterion 7: a model builds a three-slide deck and screenshots it ---------


@pytest.mark.asyncio
async def test_a_model_builds_a_deck_from_the_starter_and_screenshots_three_slides(ws):
    from PIL import Image

    from dream.gui.preview import VIEWPORT
    from dream.tools.native import write_file
    from dream.tools.starters import copy_starter_component

    root, _ = ws
    starter = await copy_starter_component.handler({"kind": "deck_stage.js"})
    assert not _failed(starter)
    deck = ("<!doctype html><html><head><meta charset='utf-8'>"
            "<style>section{font:72px system-ui;padding:120px;background:#123;color:#fff}</style></head>"
            "<body><deck-stage><section><h1>One</h1></section><section><h1>Two</h1></section>"
            "<section><h1>Three</h1></section></deck-stage>"
            "<script src='deck_stage.js'></script></body></html>")
    assert not _failed(await write_file.handler({"path": "deck.html", "content": deck}))
    assert not _failed(await done.handler({"path": "deck.html"}))
    res = await multi_screenshot.handler({"path": "deck.html", "steps": [
        {"code": f"goToSlide({i})", "delay": 40} for i in range(3)]})
    assert not _failed(res), _text(res)
    files = sorted((root / "var" / "shots").glob("*.jpg"))
    assert len(files) == 3
    for f in files:
        with Image.open(f) as im:
            assert im.size == (VIEWPORT["width"], VIEWPORT["height"])
    assert len({f.read_bytes() for f in files}) == 3


async def test_done_with_a_non_html_deliverable_is_not_a_failure(ws):
    """Dream fix #20: a HANDOFF.md passed to done made a finished turn end as
    delivery_failed. The file is the deliverable; it just is not previewed."""
    tmp, emitted = ws
    (tmp / "HANDOFF.md").write_text("# Handoff\n")
    out = await done.handler({"path": "HANDOFF.md"})
    assert not out.get("is_error")
    text = out["content"][0]["text"]
    assert "Delivered" in text and "not previewed" in text
