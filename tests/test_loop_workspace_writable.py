"""Dream's autonomous loop must be able to write its own scratch directory.

`var/loops/<run>/` is created BY Dream and holds progress.md — the file the loop
tells the worker to keep current and the file its independent grader reads. It
lives outside the session workspace, so every write to it tripped the
"outside workspace" boundary and asked for approval. In headless `--loop` there
is nobody to ask, so it was denied and the loop ran with its own deliverable
file unwritable.
"""

import asyncio
from pathlib import Path

import pytest

from dream.tui.app import App


class _Loop:
    def __init__(self, ws):
        self.workspace = ws


def _app(tmp_path, loop_ws=None):
    app = App.__new__(App)
    app.workspace = (tmp_path / "session").resolve()
    app.workspace.mkdir(parents=True, exist_ok=True)
    app.mode = "accept-edits"
    app._always_allow = set()
    app._perm_lock = asyncio.Lock()
    app._active_loop = _Loop(loop_ws) if loop_ws else None
    return app


def test_loop_scratch_directory_is_writable_without_asking(tmp_path):
    loop_ws = (tmp_path / "var" / "loops" / "run-1").resolve()
    loop_ws.mkdir(parents=True)
    app = _app(tmp_path, loop_ws)

    allowed = asyncio.run(
        app._decide_permission("Write", {"file_path": str(loop_ws / "progress.md")})
    )

    assert allowed, "the loop cannot write its own progress.md"


def test_writes_elsewhere_outside_the_workspace_still_ask(tmp_path, monkeypatch):
    loop_ws = (tmp_path / "var" / "loops" / "run-1").resolve()
    loop_ws.mkdir(parents=True)
    app = _app(tmp_path, loop_ws)
    # No stdin in a test: an unanswered question is a refusal.
    monkeypatch.setattr(App, "_read_answer", lambda self, prompt: _none())
    app.renderer = _SilentRenderer()

    allowed = asyncio.run(
        app._decide_permission("Write", {"file_path": str(tmp_path / "elsewhere.txt")})
    )

    assert not allowed, "the workspace boundary stopped being enforced"


async def _none():
    return None


class _SilentRenderer:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def test_no_loop_running_means_no_extra_allowance(tmp_path, monkeypatch):
    app = _app(tmp_path, loop_ws=None)
    monkeypatch.setattr(App, "_read_answer", lambda self, prompt: _none())
    app.renderer = _SilentRenderer()

    allowed = asyncio.run(
        app._decide_permission("Write", {"file_path": str(tmp_path / "var" / "x.md")})
    )

    assert not allowed
