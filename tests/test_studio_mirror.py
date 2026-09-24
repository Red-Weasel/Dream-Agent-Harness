"""DREAM-104: the owner's Studio pane follows the model's hidden frame.

The model inspected its pages privately (`show_html` 68 times across the runtime logs,
`show_to_user` once), so the owner never saw work in progress and could not steer it.
Now a `show_html` mirrors the same file into the pane, an edit to the shown file reloads
it, a saved screenshot shows as an image, and a per-session toggle turns it off. Unit
tests drive the tool handlers against a captured `emit`; the last test drives the real
pane and the real hidden frame at the owner's 2554x1338.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from dream.core.backends.base import Event
from dream.gui import preview as preview_mod
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer, follow_default
from dream.tools import context as tool_context, mirror, studio
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.files import copy_files, str_replace_edit
from dream.tools.native import run_bash, write_file
from dream.tools.studio import done, save_screenshot, show_html, show_to_user

UI = Path(__file__).parent.parent / "dream" / "gui" / "static"
PAGE = "<!doctype html><title>t</title><h1 id='h'>Hello</h1>"
PAGE2 = "<!doctype html><title>t</title><h1 id='h'>Hello v2</h1>"


def _text(res) -> str:
    return res["content"][0]["text"]


def _shows(emitted):
    return [e for e in emitted if e.kind == "studio" and e.data.get("op") == "show"]


class FakePreview:
    """The hidden frame without Chromium: loads by name, captures a 1280x800 PNG."""

    def __init__(self):
        self.loaded = None
        self.logs = []
        self.captures = {}

    async def load(self, p):
        self.loaded = p.resolve()
        return []

    def renderer_note(self):
        return ""

    async def screenshot(self, steps, *, hq=False, save_to=None, key=None):
        if key is not None:   # a memory capture: bytes under the key, no file
            self.captures[key] = [b"\x89PNG" for _ in steps]
            return []
        out = []
        for i, _ in enumerate(steps, 1):
            target = save_to if len(steps) == 1 else save_to.with_name(f"{i:02d}-{save_to.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1280, 800), (10 * i, 20, 30)).save(target)
            out.append(target)
        return out


@pytest.fixture
def pane(tmp_path, monkeypatch):
    """A workspace, a captured emit, and a stand-in Studio server that retains shows."""
    emitted: list = []
    panel = SimpleNamespace(follow_model_view=True, client_count=1, ready=True, retained=[])
    panel.retain_show = panel.retained.append
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=emitted.append))
    set_studio(panel)
    fake = FakePreview()
    monkeypatch.setattr(studio, "get_preview", lambda: fake)
    (tmp_path / "page.html").write_text(PAGE)
    (tmp_path / "other.html").write_text("<!doctype html><p>other</p>")
    yield tmp_path, emitted, panel
    set_studio(None)
    tool_context._CTX = None


# --- (1) the mirror ------------------------------------------------------------------------


async def test_show_html_mirrors_the_page_into_the_pane_and_says_so(pane):
    root, emitted, panel = pane
    res = await show_html.handler({"path": "page.html"})
    assert not res.get("is_error")
    (ev,) = _shows(emitted)
    assert ev.data == {"op": "show", "path": "page.html", "title": "page.html", "content": PAGE,
                       "source": "mirror"}
    assert panel.retained == [ev], "the pane replays the model's current artifact on reconnect"
    text = _text(res)
    assert "hidden frame" in text
    assert "Studio pane shows it too" in text and "steer" in text
    assert "not shown" not in text, "the result must not let the model claim the page is private"
    assert mirror.shown()["path"] == (root / "page.html").resolve()


async def test_show_html_with_the_mirror_off_stays_private_and_names_show_to_user(pane):
    root, emitted, panel = pane
    panel.follow_model_view = False
    res = await show_html.handler({"path": "page.html"})
    assert not res.get("is_error") and emitted == []
    text = _text(res)
    assert "turned off" in text and "show_to_user" in text
    assert mirror.shown() is None


async def test_show_html_without_a_studio_server_says_nothing_is_shown(pane):
    root, emitted, _ = pane
    set_studio(None)
    res = await show_html.handler({"path": "page.html"})
    assert not res.get("is_error") and emitted == []
    assert "Studio is not open" in _text(res)


async def test_explicit_show_to_user_and_done_keep_their_exact_events(pane, monkeypatch):
    root, emitted, panel = pane
    res = await show_to_user.handler({"path": "page.html"})
    assert not res.get("is_error")
    assert emitted[-1].data == {"op": "show", "path": "page.html", "title": "page.html", "content": PAGE}
    monkeypatch.setattr(studio, "verify_page", lambda ws, p: "checks: ok")
    res = await done.handler({"path": "page.html"})
    assert not res.get("is_error")
    assert emitted[-1].data == {"op": "show", "path": "page.html", "title": "page.html", "content": PAGE}
    # an explicit show is the shown file too: later edits reload it
    assert mirror.shown()["path"] == (root / "page.html").resolve()
    panel.follow_model_view = False
    res = await show_to_user.handler({"path": "other.html"})
    assert not res.get("is_error") and emitted[-1].data["path"] == "other.html", "explicit shows ignore the toggle"


# --- (2) live reload ---------------------------------------------------------------------


async def test_edits_to_the_shown_file_reload_it_and_other_files_do_not(pane):
    root, emitted, panel = pane
    await show_html.handler({"path": "page.html"})
    assert len(_shows(emitted)) == 1

    res = await write_file.handler({"path": "page.html", "content": PAGE2})
    assert not res.get("is_error") and _text(res).startswith("Wrote ")
    assert len(_shows(emitted)) == 2
    assert _shows(emitted)[-1].data == {"op": "show", "path": "page.html", "title": "page.html",
                                         "content": PAGE2, "source": "mirror"}

    res = await str_replace_edit.handler({"path": "page.html", "old_string": "v2", "new_string": "v3"})
    assert not res.get("is_error")
    assert len(_shows(emitted)) == 3 and "v3" in _shows(emitted)[-1].data["content"]

    await write_file.handler({"path": "other.html", "content": "<p>changed</p>"})
    await str_replace_edit.handler({"path": "other.html", "old_string": "changed", "new_string": "again"})
    await write_file.handler({"path": "notes.txt", "content": "plain"})
    assert len(_shows(emitted)) == 3, "edits to other files do not touch the pane"

    panel.follow_model_view = False
    await write_file.handler({"path": "page.html", "content": PAGE})
    assert len(_shows(emitted)) == 3, "with the mirror off the pane changes only on explicit shows"


async def test_a_change_the_shell_made_to_the_shown_file_is_caught_by_one_stat(pane, monkeypatch):
    root, emitted, _ = pane
    await show_html.handler({"path": "page.html"})
    assert mirror.refresh_shown() is False, "nothing changed: nothing to reload"

    from dream.core import execution

    async def shell_writes(command, context, *, timeout, max_output):
        target = root / "page.html"
        target.write_text(PAGE2)
        stat = target.stat()
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        return SimpleNamespace(output=b"done", truncated=False, timed_out=False, returncode=0), True

    monkeypatch.setattr(execution, "execute_bash", shell_writes)
    monkeypatch.setattr(execution, "current_execution", lambda ws: object())
    res = await run_bash.handler({"command": "sed -i s/Hello/Hello v2/ page.html"})
    assert not res.get("is_error")
    assert len(_shows(emitted)) == 2 and _shows(emitted)[-1].data["content"] == PAGE2
    assert mirror.refresh_shown() is False, "the reload recorded the new signature"


# --- (3) renders --------------------------------------------------------------------------


async def test_a_saved_screenshot_shows_as_an_image_and_the_newest_wins(pane):
    root, emitted, panel = pane
    res = await save_screenshot.handler({"path": "page.html", "steps": [{}, {"delay": 1}],
                                         "save_path": "shots/cap.png"})
    assert not res.get("is_error"), _text(res)
    (ev,) = _shows(emitted)
    assert ev.data["kind"] == "image" and ev.data["path"] == "shots/02-cap.png"
    assert ev.data["title"] == "02-cap.png" and ev.data["source"] == "mirror"
    assert ev.data["size"] == (root / "shots" / "02-cap.png").stat().st_size and ev.data["stamp"] > 0
    assert panel.retained == [ev]
    assert "Studio pane shows this capture" in _text(res)
    assert mirror.shown()["kind"] == "image"

    await show_html.handler({"path": "page.html"})
    assert _shows(emitted)[-1].data.get("kind") is None, "an HTML shown later replaces the image"
    assert mirror.shown()["kind"] == "page"

    res = await save_screenshot.handler({"path": "page.html", "steps": [{}], "in_memory_png_key": "k"})
    assert not res.get("is_error") and len(_shows(emitted)) == 2, "a memory capture is not a file to show"


async def test_images_written_by_dream_tools_show_and_other_files_do_not(pane, tmp_path):
    root, emitted, panel = pane
    src = tmp_path.parent / f"{tmp_path.name}-src.png"
    Image.new("RGB", (4, 4), "red").save(src)
    res = await copy_files.handler({"files": [{"src": str(src), "dest": "art/tex.png"}]})
    assert not res.get("is_error")
    (ev,) = _shows(emitted)
    assert ev.data["kind"] == "image" and ev.data["path"] == "art/tex.png"

    await write_file.handler({"path": "readme.txt", "content": "notes"})
    assert len(_shows(emitted)) == 1

    await write_file.handler({"path": "logo.svg", "content": "<svg xmlns='http://www.w3.org/2000/svg'/>"})
    ev = _shows(emitted)[-1]
    assert ev.data["path"] == "logo.svg" and ev.data.get("kind") is None and "<svg" in ev.data["content"]

    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    Image.new("RGB", (4, 4), "blue").save(outside)
    assert mirror.show_image(outside) is False, "the pane renders workspace files only"
    assert mirror.show_image(root / "missing.png") is False

    panel.follow_model_view = False
    Image.new("RGB", (4, 4), "green").save(root / "art" / "later.png")
    assert mirror.file_written(root / "art" / "later.png") is False


async def test_run_script_reports_the_files_it_saved(pane, monkeypatch):
    """The contained worker names each save; the tool mirrors images among them."""
    from dream.core import script_execution

    async def fake_run_script(code, context, captures, *, timeout=None, saved=None):
        (pane[0] / "out").mkdir(exist_ok=True)
        Image.new("RGB", (3, 3), "red").save(pane[0] / "out" / "made.png")
        if saved is not None:
            saved.append("out/made.png")
        return ["log line"]

    monkeypatch.setattr(script_execution, "run_script", fake_run_script)
    monkeypatch.setattr(studio, "current_execution", lambda ws: object())
    res = await studio.run_script.handler({"code": "await saveFile('out/made.png', createCanvas(3, 3))"})
    assert not res.get("is_error"), _text(res)
    (ev,) = _shows(pane[1])
    assert ev.data["kind"] == "image" and ev.data["path"] == "out/made.png"
    # the worker protocol itself carries the save
    assert 'emit("saved"' in script_execution._WORKER
    assert 'event.get("kind") == "saved"' in Path(script_execution.__file__).read_text()


# --- (5) the toggle and its settings key -----------------------------------------------


async def test_the_follow_route_toggles_the_server_flag_and_broadcasts(tmp_path):
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={"workspace": str(tmp_path)})
    assert srv.follow_model_view is True
    with srv.bus.subscribe() as sub:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test") as c:
            assert (await c.post("/api/follow", json={"on": False})).status_code == 401
            r = await c.post("/api/follow", json={"on": False}, headers={"X-Dream-Token": srv.token})
            assert r.status_code == 200 and r.json() == {"ok": True, "on": False}
            assert srv.follow_model_view is False
            r = await c.post("/api/follow", json={"on": "yes"}, headers={"X-Dream-Token": srv.token})
            assert r.status_code == 400 and srv.follow_model_view is False
            r = await c.post("/api/follow", json={"on": True}, headers={"X-Dream-Token": srv.token})
            assert r.status_code == 200 and srv.follow_model_view is True
        ev = await asyncio.wait_for(sub.get(), 1)
        assert ev.kind == "studio" and ev.data == {"op": "follow", "on": False}
        ev = await asyncio.wait_for(sub.get(), 1)
        assert ev.data == {"op": "follow", "on": True}


def test_a_connecting_pane_learns_the_mirror_is_off_after_the_retained_show(monkeypatch):
    from starlette.testclient import TestClient

    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    srv = StudioServer(EventBus())
    show = {"op": "show", "path": "latest.html", "content": "<p>x</p>"}
    srv.retain_show(Event("studio", show))
    with TestClient(srv.app) as client:
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            assert ws.receive_json()["kind"] == "hello"
            assert ws.receive_json() == {"kind": "studio", "data": show}
            srv.bus.publish(Event("system", "live"))
            assert ws.receive_json() == {"kind": "system", "data": "live"}, "on by default: no extra message"
        srv.follow_model_view = False
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            assert ws.receive_json()["kind"] == "hello"
            assert ws.receive_json() == {"kind": "studio", "data": show}
            assert ws.receive_json() == {"kind": "studio", "data": {"op": "follow", "on": False}}


def test_follow_default_reads_the_settings_key_and_never_raises(tmp_path, monkeypatch):
    from dream.core import profiles

    path = tmp_path / "runtime-settings.json"
    monkeypatch.setattr(profiles, "settings_path", lambda: path)
    assert follow_default() is True
    path.write_text(json.dumps({"version": 1, "studio": {"follow_model_view": False}}))
    assert follow_default() is False
    path.write_text(json.dumps({"version": 1, "studio": {"follow_model_view": True}}))
    assert follow_default() is True
    path.write_text("{not json")
    assert follow_default() is True
    # the key survives a profile save, so it is a durable setting
    path.write_text(json.dumps({"version": 1, "studio": {"follow_model_view": False}}))
    profiles.save_settings("lean", path=path)
    assert json.loads(path.read_text())["studio"] == {"follow_model_view": False}


def test_the_page_has_the_toggle_and_the_image_branch():
    ui = (UI / "index.html").read_text()
    for needle in ('id="artfollow"', 'id="artfollowbox"', "Follow the model's view", "d.op === 'follow'",
                   "d.kind === 'image'", "'/api/follow'", "function openImage", "source === 'mirror'"):
        assert needle in ui, needle
    companion = (UI / "companion.js").read_text()
    assert "openArtifact(opts)" in companion and "opts?.mirror" in companion
    # the image is served by the token-checked download route, never a file: URL
    paint = ui[ui.index("function paintArtifact"):ui.index("window.addEventListener('message', e => {\n  const f=artbody")]
    assert "imageHref(" in paint and "file:" not in paint


# --- (6) the model hears that the owner is watching -------------------------------------


def test_the_runtime_note_tells_the_model_the_owner_follows_its_frame():
    from dream.core.system_prompt import build_system_prompt

    text = Path("dream/core/system_prompt.py").read_text()
    bullet = text[text.index("- Studio (see what you made)"):text.index("- Visual answers (Studio)")]
    assert "follows your hidden frame" in bullet and "steer" in bullet
    assert "`show_html`" in bullet and "`done(path)`" in bullet
    assert callable(build_system_prompt)


# --- gate 1 (2026-09-23): three blocking findings and the notes ----------------------------


def test_mirrored_shows_are_retained_for_replay_but_never_count_as_handoffs(monkeypatch):
    """BLOCKING 1: the GTK desktop polls show_sequence every 700 ms and treats a bump as
    "Dream opened an artifact" (navigate + status). A mirrored show or screenshot is the
    model's own view: retained for replay, never a handoff."""
    from starlette.testclient import TestClient

    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    srv = StudioServer(EventBus())
    srv.retain_show(Event("studio", {"op": "show", "path": "a.html", "content": "one"}))
    assert srv._show_sequence == 1
    mirrored = Event("studio", {"op": "show", "path": "a.html", "content": "two", "source": "mirror"})
    srv.retain_show(mirrored)
    srv.retain_show(mirrored)
    image = Event("studio", {"op": "show", "kind": "image", "path": "shots/x.png", "title": "x.png",
                             "source": "mirror", "size": 3, "stamp": 9})
    srv.retain_show(image)
    assert srv._show_sequence == 1
    with TestClient(srv.app) as client:
        assert client.get(f"/api/desktop?token={srv.token}").json()["show_sequence"] == 1
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            assert ws.receive_json()["kind"] == "hello"
            assert ws.receive_json() == {"kind": "studio", "data": image.data}, "still replayed on reconnect"
    # the shown file deleted: the retained show goes, so a reconnect does not replay a ghost
    srv.retain_show(Event("studio", {"op": "removed", "path": "shots/x.png", "title": "x.png", "source": "mirror"}))
    assert srv._last_show is None and srv._show_sequence == 1


async def test_show_html_outside_the_workspace_loads_but_is_not_mirrored(pane, tmp_path):
    """BLOCKING 3: the pane renders workspace files only. A page outside it -- an absolute
    path, a ../ escape, a symlink leading out -- goes to the hidden frame alone, and the
    result says so instead of claiming the owner sees it."""
    root, emitted, _ = pane
    outside = tmp_path.parent / f"{tmp_path.name}-outside.html"
    outside.write_text("<!doctype html><p>secret</p>")
    (root / "link.html").symlink_to(outside)
    try:
        for given in (str(outside), f"../{outside.name}", "link.html"):
            res = await show_html.handler({"path": given})
            assert not res.get("is_error"), _text(res)
            assert "hidden frame" in _text(res) and "not mirrored: outside the workspace" in _text(res), given
            assert "shows it too" not in _text(res)
        assert emitted == [] and mirror.shown() is None
        # explicit shows keep their pre-existing behaviour (stated in the record); a later
        # edit to such a page still does not mirror
        res = await show_to_user.handler({"path": "link.html"})
        assert not res.get("is_error") and emitted[-1].data["path"] == "link.html"
        assert mirror.shown()["path"] == outside.resolve()
        assert mirror.file_written(outside) is False and len(_shows(emitted)) == 1
    finally:
        outside.unlink()


async def test_a_capture_after_a_reload_refreshes_the_stale_hidden_frame(pane):
    """Note 6: _ensure_loaded skipped the reload while the frame already held the file, so a
    screenshot after mirrored edit reloads captured the OLD page."""
    root, emitted, _ = pane
    fake = studio.get_preview()
    real_load, fake.loads = fake.load, 0

    async def counting_load(p):
        fake.loads += 1
        return await real_load(p)
    fake.load = counting_load
    await show_html.handler({"path": "page.html"})
    assert fake.loads == 1
    await write_file.handler({"path": "page.html", "content": PAGE2})
    res = await save_screenshot.handler({"path": "page.html", "steps": [{}], "save_path": "shots/a.png"})
    assert not res.get("is_error"), _text(res)
    assert fake.loads == 2, "the file changed since the frame loaded it: reload before capturing"
    res = await save_screenshot.handler({"path": "page.html", "steps": [{}], "save_path": "shots/b.png"})
    assert not res.get("is_error") and fake.loads == 2, "unchanged: no needless reload"


async def test_delete_file_of_the_shown_page_clears_the_pane(pane):
    """Note 5: the model deletes the file the owner is looking at."""
    from dream.tools.files import delete_file

    root, emitted, panel = pane
    await show_html.handler({"path": "page.html"})
    await write_file.handler({"path": "notes.txt", "content": "x"})
    res = await delete_file.handler({"paths": ["notes.txt"]})
    assert not res.get("is_error") and len(emitted) == 1, "another file: nothing happens"
    res = await delete_file.handler({"paths": ["page.html"]})
    assert not res.get("is_error")
    assert emitted[-1].data == {"op": "removed", "path": "page.html", "title": "page.html", "source": "mirror"}
    assert mirror.shown() is None and panel.retained[-1] is emitted[-1]
    # a folder holding the shown file counts too; with the mirror off the pane keeps its place
    (root / "sub").mkdir()
    (root / "sub" / "p.html").write_text(PAGE)
    await show_html.handler({"path": "sub/p.html"})
    panel.follow_model_view = False
    before = len(emitted)
    res = await delete_file.handler({"paths": ["sub"]})
    assert not res.get("is_error") and len(emitted) == before, "off: no pane change"
    assert mirror.shown() is None, "but no reload can target a file that is gone"


async def test_the_other_writer_tools_reach_the_mirror(pane):
    """Note 5: copy_starter_component, github_import_files, library_materialize and
    super_inline_html write files too; super_inline_html is driven for real."""
    from dream.tools import export_tools, github_tools, library_tools, starters

    for module in (starters, github_tools, library_tools, export_tools):
        assert "mirror.file_written(" in Path(module.__file__).read_text(), module.__name__
    root, emitted, _ = pane
    (root / "page.html").write_text(
        PAGE + '<template id="__bundler_thumbnail"><svg xmlns="http://www.w3.org/2000/svg"/></template>')
    (root / "bundle.html").write_text("<!doctype html><p>old</p>")
    await show_html.handler({"path": "bundle.html"})
    res = await export_tools.super_inline_html.handler({"input_path": "page.html", "output_path": "bundle.html"})
    assert not res.get("is_error"), _text(res)
    ev = _shows(emitted)[-1]
    assert ev.data["path"] == "bundle.html" and "Hello" in ev.data["content"] and ev.data["source"] == "mirror"


def test_the_workspace_view_stays_put_for_mirrored_shows():
    """BLOCKING 2 (the static half; the Playwright test drives it): paintArtifact says whether
    a show was mirrored, and workspace.js switches views only for a handoff -- or once, for
    the first mirrored show of a session while the owner is on the default chat/home view."""
    ui = (UI / "index.html").read_text()
    assert 'new CustomEvent("dream:artifact", {detail: {mirror' in ui
    ws = (UI / "workspace.js").read_text()
    listener = ws[ws.index("window.addEventListener('dream:artifact'"):ws.index("const councilTasks")]
    assert "e.detail?.mirror" in listener and "mirrorBroughtStudio" in listener
    assert "dialog[open]" in listener and "understand-open" in listener
    assert "mirrorBroughtStudio=false" in ws[ws.index("dream:project-reset"):]
    css = ui[:ui.index("</style>")]
    assert ".companion #artbody.image" in css, "must beat companion.css's .companion #artbody{background:#fff}"
    assert "d.op === 'removed'" in ui


# --- (7) the real pane at the owner's screen -------------------------------------------


@pytest.mark.asyncio
async def test_the_pane_follows_show_html_reloads_on_edit_and_shows_a_screenshot(tmp_path, monkeypatch):
    from playwright.async_api import async_playwright, expect

    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    shots = Path(os.environ.get("DREAM_MIRROR_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    ws = tmp_path / "rocket"
    ws.mkdir()
    (ws / "page.html").write_text(PAGE)
    (ws / "other.html").write_text("<!doctype html><title>o</title><h1 id='h'>Other</h1>")
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session={"workspace": str(ws), "model": "fixture"})

    def funnel(ev):  # the App's _render_event: retain, then publish
        srv.retain_show(ev)
        srv.bus.publish(ev)

    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=ws, emit=funnel))
    preview_mod._PREVIEW = None
    url = await srv.start()
    set_studio(srv)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 2554, "height": 1338}, reduced_motion="reduce")
            page.set_default_timeout(8000)
            await page.goto(url + "&companion=1", wait_until="load")
            await page.wait_for_function("document.getElementById('stat').textContent !== 'connecting'")
            await expect(page.locator("#artbody iframe")).to_have_count(0)
            view = "document.documentElement.dataset.dreamView"
            assert await page.evaluate(view) == "chat", "the default view"

            # (1) show_html: the page the model loads privately appears in the owner's pane;
            #     the FIRST mirrored show of a session brings Studio up once from the default view
            res = await show_html.handler({"path": "page.html"})
            assert not res.get("is_error"), _text(res)
            frame = page.frame_locator("#artbody iframe")
            await expect(frame.locator("#h")).to_have_text("Hello")
            await page.wait_for_function(f"{view} === 'studio'")
            await expect(page.locator("#artfollowbox")).to_be_checked()
            await expect(page.locator("#artname")).to_have_text("page.html")

            # (4) the owner is typing a correction; a reload must not steal it or the focus
            if not await page.locator("#input").is_visible():
                await page.click("#studio-compose")
            await page.fill("#input", "make the water a little more teal")
            await page.focus("#input")
            # (2) the model edits the shown file: the pane reloads it
            res = await str_replace_edit.handler({"path": "page.html", "old_string": "Hello", "new_string": "Hello v2"})
            assert not res.get("is_error"), _text(res)
            await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Hello v2")
            assert await page.evaluate("document.activeElement && document.activeElement.id") == "input"
            assert await page.input_value("#input") == "make the water a little more teal"
            await page.screenshot(path=str(shots / "studio-mirror-page-2554x1338.png"))

            # (gate, BLOCKING 2) the owner goes to Files: later mirrored updates never pull them back
            await page.click("#dream-nav-files")
            await expect(page.locator("#dream-files-page")).to_be_visible()
            assert await page.evaluate(view) == "files"
            res = await write_file.handler({"path": "page.html", "content": PAGE2.replace("v2", "v3")})
            assert not res.get("is_error"), _text(res)
            await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Hello v3")
            assert await page.evaluate(view) == "files"
            await expect(page.locator("#dream-files-page")).to_be_visible()

            # (3) a screenshot the model takes appears as an image -- still without a view switch
            res = await save_screenshot.handler({"path": "page.html", "steps": [{}], "save_path": "shots/cap.png"})
            assert not res.get("is_error"), _text(res)
            img = page.locator("#artbody img")
            await expect(img).to_be_attached()
            assert await page.evaluate(view) == "files"
            await expect(page.locator("#dream-files-page")).to_be_visible()
            await page.screenshot(path=str(shots / "studio-mirror-files-view-2554x1338.png"))
            # the owner comes back to Studio themselves and finds the capture
            await page.click("#dream-nav-studio")
            await expect(img).to_be_visible()
            await page.wait_for_function("(i => i.complete && i.naturalWidth > 0)(document.querySelector('#artbody img'))")
            assert await img.evaluate("i => i.naturalWidth") == 1280, "the hidden frame's 1280x800 capture"
            await img.evaluate("i => i.decode()")
            assert await page.evaluate("getComputedStyle(document.getElementById('artbody')).backgroundColor") == "rgb(17, 17, 17)"
            await expect(page.locator("#artname")).to_have_text("cap.png")
            await page.screenshot(path=str(shots / "studio-mirror-image-2554x1338.png"))

            # an HTML shown later replaces the image
            await show_html.handler({"path": "page.html"})
            await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Hello v3")

            # (5) the toggle: off, and show_html no longer moves the pane; show_to_user still does
            await page.click("#artfollowbox")
            for _ in range(40):
                if srv.follow_model_view is False:
                    break
                await asyncio.sleep(0.05)
            assert srv.follow_model_view is False
            res = await show_html.handler({"path": "other.html"})
            assert "turned off" in _text(res)
            await page.wait_for_timeout(400)
            await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Hello v3")
            await show_to_user.handler({"path": "other.html"})
            await expect(page.frame_locator("#artbody iframe").locator("#h")).to_have_text("Other")
            await page.screenshot(path=str(shots / "studio-mirror-off-2554x1338.png"))
            await browser.close()
    finally:
        set_studio(None)
        tool_context._CTX = None
        await srv.stop()
        pv = preview_mod._PREVIEW
        if pv is not None:
            await pv.aclose()
        preview_mod._PREVIEW = None
