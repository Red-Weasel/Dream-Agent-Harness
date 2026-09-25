"""DREAM-111 (#55): the Studio follows the model's real visual work, not only show_html.

Live, the model rendered through its own run_bash scripts (Playwright captures into
review/terrain-sea/*.png) and looked at them with `see` -- 21 run_bash, 8 `see`, 0
show_html -- so the owner's Studio showed nothing. Under the existing follow setting:
a `see` of workspace images shows the newest of them; a run_bash call that writes
PNG/JPEG/WebP files under the workspace shows the newest one, once per call, throttled;
nothing outside the workspace is shown; follow off stops both (and the shell scan).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from dream.core import execution
from dream.tools import context as tool_context, mirror
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.native import run_bash
from dream.tools.vision import see

pytestmark = pytest.mark.asyncio


def _shows(emitted):
    return [e for e in emitted if e.kind == "studio" and e.data.get("op") == "show"]


def _png(path: Path, color="red", mtime_ns: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), color).save(path)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


@pytest.fixture
def pane(tmp_path, monkeypatch):
    """A workspace, a captured emit, a following Studio that retains shows, and no
    throttle unless a test sets one."""
    ws = tmp_path / "ws"
    ws.mkdir()
    emitted: list = []
    panel = SimpleNamespace(follow_model_view=True, client_count=1, ready=True, retained=[])
    panel.retain_show = panel.retained.append
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=ws, emit=emitted.append))
    set_studio(panel)
    monkeypatch.setattr(mirror, "SHELL_SHOW_MIN_S", 0.0, raising=False)
    reset = getattr(mirror, "_reset_shell_throttle", lambda: None)
    reset()
    yield ws, emitted, panel
    reset()
    set_studio(None)
    tool_context._CTX = None


def _shell(monkeypatch, work):
    """run_bash with a fake contained executor: `work()` is what the command does."""
    async def fake_execute(command, context, *, timeout, max_output):
        work()
        return SimpleNamespace(output=b"ok", truncated=False, timed_out=False, returncode=0), True

    monkeypatch.setattr(execution, "execute_bash", fake_execute)
    monkeypatch.setattr(execution, "current_execution", lambda ws: object())


# --- `see` ---------------------------------------------------------------------------------


async def test_see_shows_the_newest_viewed_workspace_image(pane):
    ws, emitted, panel = pane
    now = os.stat(ws).st_mtime_ns
    older = _png(ws / "review" / "a.png", "blue", now - 5_000_000_000)
    newer = _png(ws / "review" / "b.png", "green", now)
    res = await see.handler({"paths": ["review/b.png", "review/a.png"]})
    assert not res.get("is_error")
    assert sum(1 for b in res["content"] if b["type"] == "image") == 2, "the model still sees both"
    (ev,) = _shows(emitted)
    assert ev.data["kind"] == "image" and ev.data["path"] == "review/b.png", "newest by mtime, not list order"
    assert panel.retained == [ev]
    note = res["content"][-1]["text"]
    assert "b.png" in note and "Studio" in note, note
    # the same image again: shown again (the owner may have gone back to the live page since)
    await see.handler({"path": str(newer)})
    assert [e.data["path"] for e in _shows(emitted)] == ["review/b.png", "review/b.png"]
    # an older image viewed alone is the newest of that call: it shows
    await see.handler({"path": "review/a.png"})
    assert _shows(emitted)[-1].data["path"] == "review/a.png" and older.exists()


async def test_see_never_shows_an_image_outside_the_workspace(pane, tmp_path):
    ws, emitted, _ = pane
    outside = _png(tmp_path / "outside" / "secret.png")
    (ws / "leak.png").symlink_to(outside)
    for given in (str(outside), "leak.png", "../outside/secret.png"):
        res = await see.handler({"path": given})
        assert not res.get("is_error"), given   # the model may look; the owner's pane is workspace-only
        assert _shows(emitted) == [], given


async def test_see_with_follow_off_changes_nothing_in_the_pane(pane):
    ws, emitted, panel = pane
    panel.follow_model_view = False
    _png(ws / "a.png")
    res = await see.handler({"path": "a.png"})
    assert not res.get("is_error") and emitted == []


# --- run_bash --------------------------------------------------------------------------------


async def test_run_bash_shows_the_newest_image_it_wrote_once_per_call(pane, monkeypatch):
    ws, emitted, _ = pane
    before = _png(ws / "review" / "old.png", "black", 1_000_000_000)   # written long before the call

    def render():
        import time
        for i in range(1, 6):                                            # a batch of five captures, in order
            _png(ws / "review" / "terrain-sea" / f"{i:02d}.png", (40 * i, 0, 0), time.time_ns() + i)
        (ws / "review" / "notes.txt").write_text("not an image")
    _shell(monkeypatch, render)
    res = await run_bash.handler({"command": "python3 capture.py"})
    assert not res.get("is_error"), res
    (ev,) = _shows(emitted)
    assert ev.data["kind"] == "image" and ev.data["path"] == "review/terrain-sea/05.png"
    assert ev.data["source"] == "mirror"
    assert before.exists()

    # a later call that writes nothing new shows nothing
    _shell(monkeypatch, lambda: None)
    await run_bash.handler({"command": "ls"})
    assert len(_shows(emitted)) == 1

    # a call that overwrites an existing capture counts: the file was written by this call
    import time
    _shell(monkeypatch, lambda: _png(ws / "review" / "terrain-sea" / "01.png", "white", time.time_ns()))
    await run_bash.handler({"command": "python3 capture.py --only 1"})
    assert _shows(emitted)[-1].data["path"] == "review/terrain-sea/01.png" and len(_shows(emitted)) == 2


async def test_run_bash_image_shows_are_throttled_and_the_newest_wins(pane, monkeypatch):
    ws, emitted, _ = pane
    monkeypatch.setattr(mirror, "SHELL_SHOW_MIN_S", 0.4)
    for n in (1, 2, 3):
        _shell(monkeypatch, lambda n=n: _png(ws / "shots" / f"frame{n}.png"))
        await run_bash.handler({"command": f"render {n}"})
    shown = [e.data["path"] for e in _shows(emitted)]
    assert shown == ["shots/frame1.png"], "inside the window the later captures wait"
    await asyncio.sleep(0.7)
    shown = [e.data["path"] for e in _shows(emitted)]
    assert shown == ["shots/frame1.png", "shots/frame3.png"], "then only the newest shows (frame2 never flashes)"


async def test_run_bash_with_follow_off_does_no_scan_at_all(pane, monkeypatch):
    ws, emitted, panel = pane
    panel.follow_model_view = False

    def forbidden(*a, **k):
        raise AssertionError("the workspace was scanned while follow is off")
    monkeypatch.setattr(mirror, "_newest_image_since", forbidden)
    _shell(monkeypatch, lambda: _png(ws / "shots" / "x.png"))
    res = await run_bash.handler({"command": "render"})
    assert not res.get("is_error") and emitted == []


async def test_run_bash_never_shows_a_symlinked_image_pointing_outside(pane, tmp_path, monkeypatch):
    ws, emitted, _ = pane
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws / "linked-dir").symlink_to(outside, target_is_directory=True)

    def render():
        secret = _png(outside / "secret.png")                  # new, but outside the workspace
        (ws / "latest.png").symlink_to(secret)                # a new symlinked image inside, pointing out
        _png(outside / "deep.png")                             # reachable only through linked-dir/
    _shell(monkeypatch, render)
    await run_bash.handler({"command": "render"})
    assert _shows(emitted) == [], [e.data for e in emitted]


async def test_the_scan_skips_installed_and_vendored_trees(pane, monkeypatch):
    ws, emitted, _ = pane
    (ws / "env").mkdir()
    (ws / "env" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (ws / "browsers" / "chromium-1187").mkdir(parents=True)
    (ws / "browsers" / "chromium-1187" / "INSTALLATION_COMPLETE").write_text("")
    (ws / "conda").mkdir()
    (ws / "conda" / "conda-meta").mkdir()

    def render():
        import time
        _png(ws / "src" / "render.png", "green", time.time_ns())
        for i, vendored in enumerate((".git/x.png", "node_modules/pkg/x.png", ".venv/x.png", "venv/x.png",
                                      "__pycache__/x.png", "lib/site-packages/pkg/x.png", "env/lib/x.png",
                                      "browsers/chromium-1187/x.png", "conda/pkgs/x.png"), 1):
            _png(ws / vendored, "red", time.time_ns() + i)   # each newer than the project's render
    _shell(monkeypatch, render)
    await run_bash.handler({"command": "npm install && render"})
    assert [e.data["path"] for e in _shows(emitted)] == ["src/render.png"]


async def test_the_scan_is_capped_by_entries_and_time_and_logs_once(pane, monkeypatch, caplog):
    ws, emitted, _ = pane
    assert (mirror.SCAN_MAX_ENTRIES, mirror.SCAN_MAX_S) == (20_000, 0.150), "the stated defaults"
    for i in range(60):
        (ws / f"f{i:03d}.txt").write_text("x")
    monkeypatch.setattr(mirror, "SCAN_MAX_ENTRIES", 20)
    monkeypatch.setattr(mirror, "_CAP_LOGGED", False)
    _shell(monkeypatch, lambda: _png(ws / "zz" / "late.png"))   # sorted after the 60 files
    with caplog.at_level(logging.WARNING, logger="dream.tools.mirror"):
        await run_bash.handler({"command": "render"})
        assert _shows(emitted) == [], "the capped walk stopped before reaching the image"
        capped = [r for r in caplog.records if "stopped" in r.getMessage()]
        assert len(capped) == 1 and "20" in capped[0].getMessage()
        await run_bash.handler({"command": "render again"})
        assert len([r for r in caplog.records if "stopped" in r.getMessage()]) == 1, "logged once"
    # the time cap: a walk that has used its budget stops too
    monkeypatch.setattr(mirror, "SCAN_MAX_ENTRIES", 20_000)
    monkeypatch.setattr(mirror, "SCAN_MAX_S", 0.0)
    _shell(monkeypatch, lambda: _png(ws / "zz" / "later.png"))
    await run_bash.handler({"command": "render"})
    assert _shows(emitted) == []


# --- gate 1 (2026-09-24): guards no test caught -------------------------------------------


async def test_see_picks_the_newest_even_when_it_is_listed_last(pane):
    ws, emitted, _ = pane
    now = os.stat(ws).st_mtime_ns
    _png(ws / "a_old.png", "blue", now - 5_000_000_000)
    _png(ws / "b_new.png", "green", now)
    await see.handler({"paths": ["a_old.png", "b_new.png"]})
    assert [e.data["path"] for e in _shows(emitted)] == ["b_new.png"]


async def test_the_mark_is_taken_before_the_command_runs(pane, monkeypatch):
    """A render written early in a command that then keeps running: a mark taken after the
    command (20 ms of slack) would miss it."""
    ws, emitted, _ = pane

    async def render_then_work(command, context, *, timeout, max_output):
        _png(ws / "shots" / "early.png")
        await asyncio.sleep(0.25)
        return SimpleNamespace(output=b"ok", truncated=False, timed_out=False, returncode=0), True
    monkeypatch.setattr(execution, "execute_bash", render_then_work)
    monkeypatch.setattr(execution, "current_execution", lambda ws: object())
    res = await run_bash.handler({"command": "python3 capture.py && python3 encode.py"})
    assert not res.get("is_error"), res
    assert [e.data["path"] for e in _shows(emitted)] == ["shots/early.png"]


async def test_the_walk_never_descends_a_symlink(tmp_path, monkeypatch):
    """The walk itself, not the pane's containment behind it: a linked folder, a linked file
    and a link to / are never entered or counted."""
    import time

    ws, outside = tmp_path / "ws", tmp_path / "outside"
    ws.mkdir()
    mark = time.time_ns() - 1_000_000_000
    _png(outside / "deep" / "new.png")
    (ws / "linked").symlink_to(outside, target_is_directory=True)
    (ws / "file.png").symlink_to(outside / "deep" / "new.png")
    (ws / "root").symlink_to("/", target_is_directory=True)
    scanned: list = []
    real_scandir = os.scandir

    def counting(path):
        scanned.append(str(path))
        return real_scandir(path)
    monkeypatch.setattr(mirror.os, "scandir", counting)
    assert mirror._newest_image_since(ws, mark) is None
    assert scanned == [str(ws.resolve())], scanned
    # control: the same render as a real file in a real folder is found
    _png(ws / "real" / "new.png")
    scanned.clear()
    assert mirror._newest_image_since(ws, mark) == (ws / "real" / "new.png").resolve()
    assert sorted(scanned) == sorted([str(ws.resolve()), str((ws / "real").resolve())])
