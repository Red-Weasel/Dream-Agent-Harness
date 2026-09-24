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

from pathlib import Path
from typing import Any

from ..core.backends.base import Event
from .context import ctx, studio

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


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


def record_shown(p: Path, given: str, kind: str) -> None:
    """The pane now holds ``p`` (kind page | image), shown under ``given``."""
    c = _context()
    if c is not None:
        c.studio_shown = {"path": p.resolve(), "given": given, "kind": kind, "signature": signature(p)}


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
