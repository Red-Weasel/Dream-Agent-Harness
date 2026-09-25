"""The owner's Studio pane follows the model's hidden frame (DREAM-104).

Across the runtime logs the model called ``show_html`` (its private hidden frame) 68
times and ``show_to_user`` once: it checked its pages and renders privately, the
owner watched nothing, and so could not steer while the work happened. These
helpers put the model's current artifact in the pane through the same ``studio``
``show`` event ``show_to_user`` uses -- the page it loads, that page again when a
tool edits it, the screenshot it saves -- unless the owner turned the mirror off
("follow" in the pane's artifact bar; ``studio.follow_model_view`` in the runtime
settings sets a session's starting state). Explicit ``show_to_user`` / ``done``
calls ignore the toggle. The pane renders workspace files only: a page goes as
content into the sandboxed frame, an image as a workspace path the token-checked
download route serves.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import zlib
from pathlib import Path
from typing import Any

from ..core.backends.base import Event
from .context import ctx, in_thread, studio

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# DREAM-111: what a run_bash call renders -- the owner's live session had the model
# capture PNGs with its own Playwright scripts and never call show_html.
SHELL_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# The walk after a run_bash call stays cheap on a big workspace: at most this many
# entries and this long; the first time a walk stops early, the log says so.
SCAN_MAX_ENTRIES = 20_000
SCAN_MAX_S = 0.150
# At most one run_bash-driven image show per this many seconds; a newer capture that
# lands inside the window replaces the waiting one and shows when the window ends.
SHELL_SHOW_MIN_S = 1.5
# The start-of-call mark sits this far before the call: file timestamps come from the
# kernel's coarse clock, which trails time.time_ns() by up to a scheduler tick (1-10 ms).
_MARK_SLACK_NS = 20_000_000
# Installed and vendored trees are not the model's renders. Names, plus the markers
# DREAM-106's `understand` skill uses to recognise an installed tree
# (skills/understand/glue.py, install_reason: pyvenv.cfg, conda-meta, a Playwright
# INSTALLATION_COMPLETE); that script is not importable (it is copied into a project
# and reads its working directory at import), so the short list lives here.
_SKIP_DIRS = {".git", ".svn", ".hg", "node_modules", ".venv", "venv", "__pycache__",
              "site-packages", "dist-packages", "ms-playwright"}
_INSTALL_MARKERS = {"pyvenv.cfg", "conda-meta", "INSTALLATION_COMPLETE"}
_CAP_LOGGED = False
_log = logging.getLogger(__name__)


def following() -> bool:
    """A Studio server is up and the owner has not turned the mirror off."""
    panel = studio()
    return panel is not None and getattr(panel, "follow_model_view", True) is not False


def _context() -> Any:
    try:
        return ctx()
    except RuntimeError:
        return None


def signature(p: Path) -> tuple[int, int] | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def shown() -> dict[str, Any] | None:
    """What the pane holds for this session: resolved path, the name it was shown
    under, its kind (page | image) and the file's (mtime, size) at that moment."""
    c = _context()
    return getattr(c, "studio_shown", None) if c is not None else None


def record_shown(p: Path, given: str, kind: str, digest: str | None = None) -> None:
    """The pane now holds ``p`` (kind page | image), shown under ``given``; a page also
    carries the digest of the content sent, which the pane's load report names."""
    c = _context()
    if c is not None:
        c.studio_shown = {"path": p.resolve(), "given": given, "kind": kind, "signature": signature(p),
                          "digest": digest}


def digest(text: str) -> str:
    """What the pane calls the page it ran (DREAM-111): CRC-32 of the content's UTF-16
    code units, the same function as studioDigest in index.html, so a load report is
    matched to the exact version sent and never to an older one."""
    return f"{zlib.crc32(text.encode('utf-16-le', 'surrogatepass')):08x}"


def _emit(event: Event) -> bool:
    c = _context()
    emit = getattr(c, "emit", None) if c is not None else None
    panel = studio()
    if emit is None or panel is None or getattr(panel, "ready", True) is False:
        return False
    retain = getattr(panel, "retain_show", None)
    if callable(retain):
        retain(event)
    emit(event)
    return True


def workspace_relative(p: Path) -> str | None:
    c = _context()
    if c is None:
        return None
    ws = Path(c.workspace).resolve()
    rp = p.resolve()
    if rp == ws or not rp.is_relative_to(ws):
        return None
    return rp.relative_to(ws).as_posix()


def show_page(p: Path, given: str, *, source: str | None = None) -> bool:
    """Queue an HTML/SVG file for the pane. The primitive lives with the Studio tools
    (``studio._show_in_studio``: the same event ``show_to_user`` sends, plus
    ``source="mirror"`` for a show the model did not ask for); imported late because
    the Studio tools import this module. Raises ValueError when the page's local
    media cannot be embedded. A mirrored show is refused for a page outside the
    workspace (see below)."""
    if source and workspace_relative(p) is None:
        # Containment (gate 1, blocking 3): the pane renders workspace files only, so a
        # mirrored show or reload of a page outside it -- an absolute path, a ../ escape,
        # a symlink leading out -- is refused; an explicit show keeps its old behaviour.
        return False
    from .studio import _show_in_studio

    return _show_in_studio(p, given, source=source)


def show_image(p: Path) -> bool:
    """Show a workspace image in the pane (the newest wins). Only while following."""
    if not following():
        return False
    rel = workspace_relative(p)
    if rel is None or p.suffix.lower() not in IMAGE_SUFFIXES or not p.is_file():
        return False
    stamp, size = signature(p) or (0, 0)
    event = Event("studio", {"op": "show", "kind": "image", "path": rel, "title": p.name,
                             "source": "mirror", "size": size, "stamp": stamp})
    if not _emit(event):
        return False
    record_shown(p, rel, "image")
    return True


def _reshow(state: dict[str, Any]) -> bool:
    p: Path = state["path"]
    if not p.is_file():
        return False
    if state["kind"] == "image":
        return show_image(p)
    try:
        return show_page(p, state["given"], source="mirror")
    except ValueError:
        return False


def file_written(p: Path) -> bool:
    """A Dream tool wrote ``p``: reload it when it is the shown file; show a new image
    (or SVG, rendered like a page); leave every other file alone. Never raises -- a
    mirror failure must not turn a successful write into an error."""
    if not following():
        return False
    rp = p.resolve()
    state = shown()
    if state is not None and state["path"] == rp:
        return _reshow(state)
    suffix = rp.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return show_image(rp)
    if suffix == ".svg" and rp.is_file():
        rel = workspace_relative(rp)
        if rel is None:
            return False
        try:
            return show_page(rp, rel, source="mirror")
        except ValueError:
            return False
    return False


def refresh_shown() -> bool:
    """After a shell command or a script: one stat of the shown file, and a reload
    when its bytes changed. Files a script created are not searched for here; the
    script tool reports its own saves."""
    if not following():
        return False
    state = shown()
    if state is None or signature(state["path"]) == state["signature"]:
        return False
    return _reshow(state)


def file_removed(p: Path) -> bool:
    """A Dream tool deleted ``p`` (or a folder holding it): the pane drops the shown file
    (a ``removed`` event while following; the retained show goes with it, see
    StudioServer.retain_show) and no later write can reload a file that is gone. True
    when the pane held it."""
    state = shown()
    if state is None:
        return False
    rp = p.resolve()
    held: Path = state["path"]
    if held != rp and not held.is_relative_to(rp):
        return False
    c = _context()
    if c is not None:
        c.studio_shown = None
    if following():
        _emit(Event("studio", {"op": "removed", "path": state["given"], "title": held.name,
                               "source": "mirror"}))
    return True


# --- the model's own renders (DREAM-111) ---------------------------------------------------


def show_newest(paths: list[Path]) -> Path | None:
    """`see`: of the workspace images the model viewed, show the newest (by mtime; a
    tie goes to the later-listed). The one it shows, or None."""
    if not following():
        return None
    best: tuple[int, Path] | None = None
    for p in paths:
        if p.suffix.lower() not in IMAGE_SUFFIXES or workspace_relative(p) is None:
            continue
        stamp = signature(p)
        if stamp is not None and (best is None or stamp[0] >= best[0]):
            best = (stamp[0], p)
    return best[1] if best is not None and show_image(best[1]) else None


def shell_mark() -> int | None:
    """Taken just before a run_bash command runs: images modified from here on were
    written by it. None while not following, and then nothing is scanned afterwards."""
    return time.time_ns() - _MARK_SLACK_NS if following() else None


async def after_shell(mark: int | None) -> None:
    """After a run_bash command: the shown file reloads when the shell changed it
    (DREAM-104's one stat); then, while following, the newest PNG/JPEG/WebP the call
    wrote under the workspace shows -- one per call, throttled."""
    refresh_shown()
    if mark is None or not following():
        return
    c = _context()
    if c is None:
        return
    # A file the previous call's scan already covered is not this call's work.
    since, _SHELL["scanned"] = max(mark, _SHELL["scanned"]), time.time_ns()
    newest = await in_thread(_newest_image_since, Path(c.workspace), since)
    if newest is not None:
        _shell_show(newest)


def _newest_image_since(root: Path, mark_ns: int) -> Path | None:
    """The newest image under ``root`` modified at or after ``mark_ns``. Never follows a
    symlink (a linked file or folder may lead out of the workspace), skips hidden,
    installed and vendored trees, and stops at SCAN_MAX_ENTRIES entries or SCAN_MAX_S
    seconds with what it found so far."""
    global _CAP_LOGGED
    start, seen, capped = time.monotonic(), 0, False
    best: tuple[int, str] | None = None
    stack = [str(root.resolve())]
    while stack and not capped:
        folder = stack.pop()
        images: list[tuple[int, str]] = []
        subdirs: list[str] = []
        installed = False
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    seen += 1
                    if seen > SCAN_MAX_ENTRIES or time.monotonic() - start > SCAN_MAX_S:
                        capped = True
                        if not _CAP_LOGGED:
                            _CAP_LOGGED = True
                            _log.warning("the Studio's image scan after run_bash stopped at %d entries / %.0f ms "
                                         "(limits %d / %.0f ms) in %s; later renders there may not show",
                                         seen - 1, (time.monotonic() - start) * 1000, SCAN_MAX_ENTRIES,
                                         SCAN_MAX_S * 1000, root)
                        break
                    name = entry.name
                    if name in _INSTALL_MARKERS:
                        installed = True
                    if name.startswith(".") or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if name not in _SKIP_DIRS:
                            subdirs.append(entry.path)
                    elif (os.path.splitext(name)[1].lower() in SHELL_IMAGE_SUFFIXES
                            and entry.is_file(follow_symlinks=False)):
                        mtime = entry.stat(follow_symlinks=False).st_mtime_ns
                        if mtime >= mark_ns:
                            images.append((mtime, entry.path))
        except OSError:
            continue
        if installed:
            continue
        stack.extend(subdirs)
        for candidate in images:
            if best is None or candidate > best:
                best = candidate
    return Path(best[1]) if best is not None else None


_SHELL: dict[str, Any] = {"last": float("-inf"), "pending": None, "timer": None, "scanned": 0}


def _shell_show(p: Path) -> None:
    """Newest wins, at most one show per SHELL_SHOW_MIN_S: inside the window the image
    waits (a newer one replaces it) and shows when the window ends."""
    wait = SHELL_SHOW_MIN_S - (time.monotonic() - _SHELL["last"])
    if wait <= 0 and _SHELL["timer"] is None:
        _SHELL["last"] = time.monotonic()
        show_image(p)
        return
    _SHELL["pending"] = p
    if _SHELL["timer"] is None:
        _SHELL["timer"] = asyncio.get_running_loop().call_later(max(wait, 0.0), _flush_shell)


def _flush_shell() -> None:
    p, _SHELL["pending"], _SHELL["timer"] = _SHELL["pending"], None, None
    _SHELL["last"] = time.monotonic()
    if p is not None and following():
        show_image(p)


def _reset_shell_throttle() -> None:
    """Forget the throttle and the last scan (tests start from a clean state)."""
    timer = _SHELL["timer"]
    if timer is not None:
        timer.cancel()
    _SHELL.update(last=float("-inf"), pending=None, timer=None, scanned=0)
