"""Lucid Control (DREAM-183): the files that shape what Dream knows and how it answers, in one place.

Global: the owner's standing instructions, Dream's identity, open threads and the memory index. Current project: the
workspace's DREAM.md (its instructions), a CLAUDE.md Dream also reads when present, and PLAN.md. The memories
themselves stay on /api/memory; the response styles are listed here and set through the Settings writer.

Every file is one of a fixed list, never a path from the request. A save must name the version it was opened at (its
`stamp`), so an edit made elsewhere in the meantime is refused rather than overwritten. Writes are atomic. A file
that is a link, or that resolves outside its folder, is refused.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import stat
import threading
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import config
from ..core import response_styles, settings

MAX_TEXT = 256_000
MAX_JSON = 768_000

# key -> (file, label, what it is, editable)
_GLOBAL = {
    "instructions": (lambda: config.INSTRUCTIONS_FILE, "Your instructions",
                     "Your standing preferences. Dream reads them at the start of every session.", True),
    "identity": (lambda: config.IDENTITY_FILE, "Identity", "Who Dream is to you: its name, voice and role.", True),
    "threads": (lambda: config.THREADS_FILE, "Open threads", "Work Dream is keeping track of between sessions. Dream "
                "writes it from its task list, so it is read-only here: change the tasks instead.", False),
    "memory-index": (lambda: config.MEMORY_INDEX_FILE, "Memory index",
                     "One line per memory. Dream rewrites it whenever a memory changes, so it is read-only here.",
                     False),
}
_PROJECT = {
    "dream-md": ("DREAM.md", "DREAM.md", "This project's instructions. Dream reads them at the start of every session "
                 "in this folder.", True),
    "claude-md": ("CLAUDE.md", "CLAUDE.md", "Instructions left for other assistants. Dream reads them too when this "
                  "file is here; put Dream's own in DREAM.md.", False),
    "plan": ("PLAN.md", "PLAN.md", "The phased plan and its lessons, which Dream keeps through compaction and "
             "Handoff.", True),
}


def _workspace() -> Path | None:
    return settings.workspace()


def _resolve(scope: str, key: str) -> tuple[Path, str, str, bool, Path]:
    """(file, label, description, editable, the folder it must stay inside) for a known key; ValueError otherwise."""
    if scope == "global" and key in _GLOBAL:
        where, label, about, editable = _GLOBAL[key]
        return where(), label, about, editable, config.MEMORY_DIR
    if scope == "project" and key in _PROJECT:
        root = _workspace()
        if root is None:
            raise ValueError("No project is open: start Dream in a project folder to edit its files.")
        name, label, about, editable = _PROJECT[key]
        return root / name, label, about, editable, root
    raise ValueError("Unknown file.")


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), threading.Lock())


def _folder(path: Path, root: Path) -> int:
    """A descriptor for the file's folder, which must be `root` itself (every Lucid file sits directly in it). Every
    later step works through it, so a folder swapped for a link after this check cannot redirect a read or a write
    (Codex review #1)."""
    folder = root.resolve()
    if path.parent.resolve() != folder:
        raise ValueError(f"{path.name} is outside its folder.")
    return os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _open(name: str, folder: int) -> int | None:
    """The file, opened without following a link; None when it does not exist."""
    try:
        # O_NONBLOCK: a pipe where a file belongs must not hang the board (Codex re-review #3); fstat rejects it next
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:          # the name is a link
            raise ValueError(f"{name} is a link; Lucid Control only edits real files.") from exc
        raise


def _snapshot(path: Path, root: Path) -> tuple[str, str | None, int]:
    """(text, stamp, size) read from ONE opened file, so the text and the version it is saved against agree (Codex
    review #3): the stamp is the open file's own, taken before and after the read; a change during it is retried."""
    folder = _folder(path, root)
    try:
        return _snapshot_at(folder, path)
    finally:
        os.close(folder)


def _snapshot_at(folder: int, path: Path) -> tuple[str, str | None, int]:
    for _ in range(3):
        fd = _open(path.name, folder)
        if fd is None:
            return "", None, 0
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"{path.name} is a folder or special file here, not a text file; rename it to "
                                 "use it from Lucid Control.")
            if before.st_size > MAX_TEXT:
                # Never shown in part: an edit of a cut copy would drop the rest at the next save (gate S3 round 2).
                raise ValueError(f"{path.name} is too large to edit here (over {MAX_TEXT // 1000} KB); open it in "
                                 "an editor instead.")
            with os.fdopen(os.dup(fd), "rb") as handle:
                data = handle.read(MAX_TEXT + 1)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size):
            # the stamp is the content's own digest: two different versions never share one (Codex re-review #2)
            return data.decode("utf-8", errors="replace"), hashlib.sha256(data).hexdigest()[:32], after.st_size
    raise ValueError(f"{path.name} keeps changing; try again in a moment.")


def _entry(scope: str, key: str, snap: tuple[str, str | None, int] | None = None) -> dict:
    path, label, about, editable, root = _resolve(scope, key)
    problem = None
    if snap is None:
        try:
            snap = _snapshot(path, root)
        except ValueError as exc:      # a folder or a link where the file should be: that card only, never the board
            snap, problem = ("", None, 0), str(exc)
        except OSError as exc:         # an unreadable file, likewise (Codex re-review #9)
            snap, problem = ("", None, 0), f"{path.name} cannot be read ({exc.strerror or 'access denied'})."
    _text, stamp, size = snap
    return {"scope": scope, "key": key, "label": label, "about": about, "editable": editable and problem is None,
            "exists": stamp is not None, "path": str(path), "size": size, "stamp": stamp, "problem": problem}


def _style_source() -> str:
    try:
        return settings.effective()[settings.STYLE_SETTING].source
    except (ValueError, OSError):
        return "default"


def overview() -> dict:
    """Both file columns, the response styles (EE shows its weasel facts) and where the memories live."""
    active = settings.response_style()
    workspace = _workspace()
    return {
        "global": [_entry("global", key) for key in _GLOBAL],
        "project": ([_entry("project", key) for key in _PROJECT] if workspace is not None else []),
        "workspace": str(workspace) if workspace is not None else None,
        "memory_dir": str(config.MEMORY_DIR),
        "styles": [{"key": key, "label": response_styles.label(key), "summary": response_styles.SUMMARIES[key],
                    "text": response_styles.shown_text(key), "active": key == active}
                   for key in response_styles.STYLES],
        # where the style in use comes from: a project value wins, so a pick is saved there (Codex re-review #8)
        "style_source": _style_source(),
    }


def read(scope: str, key: str) -> dict:
    path, *_rest, root = _resolve(scope, key)
    snap = _snapshot(path, root)
    return {**_entry(scope, key, snap), "text": snap[0]}


def _write(folder: int, path: Path, text: str, new_mode: int = 0o600) -> bool:
    """Atomic replace inside the pinned folder: a new file created there, synced, renamed over the old one. It keeps
    the old file's permissions (owner-only for a new one), writes every byte, and returns whether the rename was
    synced to disk too (Codex re-review #1, #2, #4)."""
    tmp = f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    # the mode of the file this save checked, from its own descriptor: a link put there since is refused, never copied
    # (Codex third review)
    old = _open(path.name, folder)
    if old is None:
        mode = new_mode
    else:
        try:
            info = os.fstat(old)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"{path.name} changed into something that is not a file; nothing was saved.")
            mode = stat.S_IMODE(info.st_mode)
        finally:
            os.close(old)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        try:
            data = memoryview(text.encode("utf-8"))
            while data:
                written = os.write(fd, data)
                if written <= 0:
                    raise OSError(errno.EIO, "short write")
                data = data[written:]
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path.name, src_dir_fd=folder, dst_dir_fd=folder)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=folder)
        except OSError:
            pass
        raise
    try:
        os.fsync(folder)      # the rename's durability; the new text is already in place
        return True
    except OSError:
        return False


def _umask() -> int:
    """The process umask, read without changing it (os.umask would, for a moment, in every server thread)."""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError):
        pass
    return 0o077          # unknown: owner-only, never broader than the owner meant


def save(scope: str, key: str, payload: dict) -> dict:
    path, label, _about, editable, root = _resolve(scope, key)
    if not editable:
        raise ValueError(f"{label} is read-only here.")
    text = payload.get("text")
    if not isinstance(text, str):
        raise ValueError("Send the file's text.")
    text = text if text.endswith("\n") or not text else text + "\n"      # measured as it will be written
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise ValueError(f"{label} is too long to save here (over {MAX_TEXT // 1000} KB).")
    # One save at a time per file: the stamp check and the replace happen together (Codex review #2). Another program
    # writing the same file is not locked out; its change shows as a newer stamp and a refusal at the next save.
    # The folder is opened once; the version check and the write both go through that one descriptor, so the folder
    # cannot be swapped between them (Codex review #1).
    with _lock(path):
        folder = _folder(path, root)
        try:
            fcntl.flock(folder, fcntl.LOCK_EX)   # other Dream processes saving in this folder wait too
            if payload.get("stamp") != _snapshot_at(folder, path)[1]:
                raise ValueError(f"{label} changed since you opened it. Reload it, then make your edit again.")
            # a new global file is the owner's alone; a new project file is an ordinary repository file (gate S3 r4)
            durable = _write(folder, path, text, 0o600 if scope == "global" else 0o666 & ~_umask())
            snap = _snapshot_at(folder, path)
        finally:
            os.close(folder)
        return {**_entry(scope, key, snap), "saved": True, **({} if durable else {
            "warning": "Saved, but the disk did not confirm it; check the file after a restart."})}


def routes(server):
    async def endpoint(request):
        if not server._authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "Origin does not match this session."}, status_code=403)
        try:
            scope, key = request.path_params.get("scope"), request.path_params.get("key")
            if request.method == "GET":
                result = await run_in_threadpool(read, scope, key) if key else await run_in_threadpool(overview)
            else:
                body = await request.body()
                if len(body) > MAX_JSON:
                    return JSONResponse({"error": "That request is too large."}, status_code=413)
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object.")
                result = await run_in_threadpool(save, scope, key, payload)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({"error": "Cannot safely access that file."}, status_code=400)
    return [Route("/api/lucid", endpoint, methods=["GET"]),
            Route("/api/lucid/{scope}/{key}", endpoint, methods=["GET", "POST"])]
