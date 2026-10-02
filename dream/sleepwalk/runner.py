"""One Sleepwalk run (DREAM-156): the Council's one-shot call with Sleepwalk's own system prompt, read-only.

consult_advisor never raises; an "unavailable" or "failed" answer is recorded as FAILED and never shown as output.
Every run is isolated (DREAM-157): none of the owner's MCP servers, plugins, hooks or project instructions, in a
fresh private folder (under $XDG_RUNTIME_DIR when that is the owner's own 0700 directory under parents nobody else can
write, else under dream/sleepwalk-runs in the owner's cache directory, held to the same rules; never the shared system
temp directory) outside any git repository; a runner that cannot be isolated is refused and recorded FAILED."""
from __future__ import annotations

import asyncio
import os
import re
import stat
import tempfile
from datetime import datetime
from pathlib import Path

from ..core import moe
from . import connectors, store

SYSTEM = (
    "You are running a Sleepwalk automation inside Dream on its owner's behalf. Nobody is present to answer "
    "questions or approve anything. Read and research only: never send messages or email, create or change events, "
    "post, buy, or write, change or delete files or anything else outside this reply. If a step would need such an "
    "action, say what you would have done instead. Reply with the finished result, ready to read.")
TIMEOUT = 600.0
_UNAVAILABLE = re.compile(r"^\[[^\]\n]*unavailable — |(^|\n\n)\[failed — ")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def note(automation: dict, label: str, status: str, reason: str) -> dict:
    """A run record for a run that did not happen (SKIPPED) or never finished (FAILED)."""
    stamp = _now()
    return store.add_run(automation["id"], {
        "title": automation["title"], "status": status, "trigger": label, "started": stamp, "finished": stamp,
        "runner": automation["runner"], "output": "", "error": reason, "notified": ["app"]})


async def _logged_out(background: bool) -> bool:
    """A background run re-checks the login state before the runner call and before notifications (DREAM-165)."""
    from .background import logged_in
    return background and not await asyncio.to_thread(logged_in)


async def _still_logged_in(background: bool) -> bool:
    return not await _logged_out(background)


async def _local_loaded() -> bool:
    """Dream's own check that the local engine has a model loaded (GET /v1/models, 2 s); it never starts or loads one."""
    from ..local import machx
    return await asyncio.to_thread(machx.model_loaded)


_stat = os.stat     # tests point this at a view with trusted parents (pytest's tmp_path is under the shared /tmp)


def _inside_repository(folder: Path) -> bool:
    return any((p / ".git").exists() for p in (folder, *folder.parents))


def _untrusted(path: Path) -> str | None:
    """Why `path` is no private root -- None when it is one: an absolute, link-free path to a directory that is this
    uid's, mode exactly 0700 (S_IMODE: no special bit either), in a place _untrusted_place accepts."""
    uid = os.getuid()
    try:
        if path.resolve(strict=True) != path:
            return "not an absolute, link-free path"
        own = _stat(path)
    except OSError as exc:
        return exc.strerror or str(exc)
    if not stat.S_ISDIR(own.st_mode):
        return "not a directory"
    if own.st_uid != uid or stat.S_IMODE(own.st_mode) != 0o700:
        return f"not this user's with mode 0700 (uid {own.st_uid}, mode {stat.S_IMODE(own.st_mode):04o})"
    return _untrusted_place(path)


def _untrusted_place(path: Path) -> str | None:
    """Why `path`'s place is no private one -- None when it is: no link above `path` (which need not exist yet),
    parents that are root's or this uid's and not group or other writable (nobody else can plant a .git above it or
    swap it), and not inside a git repository. Checked before a folder is made there, and again once it is."""
    uid = os.getuid()
    try:
        if path.parent.resolve(strict=True) != path.parent:
            return "not an absolute, link-free path"
        above = [_stat(p) for p in path.parents]
    except OSError as exc:
        return exc.strerror or str(exc)
    for parent, info in zip(path.parents, above):
        if info.st_uid not in (0, uid) or info.st_mode & 0o022:
            return (f"{parent} is not root's or this user's, or is group or other writable (uid {info.st_uid}, "
                    f"mode {stat.S_IMODE(info.st_mode):04o})")
    return "inside a git repository" if _inside_repository(path) else None


def _runtime_root() -> tuple[Path | None, str]:
    """$XDG_RUNTIME_DIR (/run/user/<uid>) when it can be trusted -> (its path, ''); else (None, why not)."""
    raw = os.environ.get("XDG_RUNTIME_DIR")
    if not raw:
        return None, "$XDG_RUNTIME_DIR is not set"
    reason = _untrusted(Path(raw))
    return (Path(raw), "") if reason is None else (None, f"$XDG_RUNTIME_DIR {raw}: {reason}")


def _cache_root() -> tuple[Path | None, str]:
    """dream/sleepwalk-runs under the user's cache directory ($XDG_CACHE_HOME when absolute, else ~/.cache, by its real
    path): each folder of it made 0700 when missing -- only in a place _untrusted_place accepts, so nothing is left
    behind where the run is refused -- and none changed when present, then trusted like the runtime directory ->
    (its path, ''); else (None, why not)."""
    raw = os.environ.get("XDG_CACHE_HOME")
    base = (Path(raw) if raw and os.path.isabs(raw) else Path.home() / ".cache").resolve()
    path = base / "dream" / "sleepwalk-runs"
    try:
        for folder in (base, base / "dream", path):
            if not folder.is_dir():
                if reason := _untrusted_place(folder):
                    return None, f"{path}: {reason}"
                os.mkdir(folder, 0o700)
    except OSError as exc:
        return None, f"{path}: {exc.strerror or exc}"
    reason = _untrusted(path)
    return (path, "") if reason is None else (None, f"{path}: {reason}")


_ROOTS = (_runtime_root, _cache_root)   # in this order; tests point this at a root of their own


class NotIsolated(Exception):
    """No private folder can be made for the run. The shared system temp directory is never used instead: anyone's
    .git in a parent there (a Codex sandbox left /tmp/.git behind, 2026-09-29) would refuse every run."""


def _work_folder(prefix: str = "dream-sleepwalk-") -> tempfile.TemporaryDirectory:
    """The run's fresh 0700 folder under the first root that is trusted and takes it: $XDG_RUNTIME_DIR, else the run
    root under the user's cache directory; NotIsolated, saying what is wrong with each, when neither does. The Frontier
    loop's calls (DREAM-218) take theirs here too, named by their own prefix."""
    why = []
    for root in _ROOTS:
        path, reason = root()
        if path is not None:
            try:
                return tempfile.TemporaryDirectory(prefix=prefix, dir=path)
            except OSError as exc:   # the root vanished after its check, say
                reason = f"{path}: no folder can be made there ({exc.strerror or exc})"
        why.append(reason)
    raise NotIsolated("no private folder for the run (the shared system temp directory is never used): " + "; ".join(why))


async def run(automation: dict, label: str = "Run now", *, background: bool = False) -> dict:
    with store.running(automation["id"]):
        runner = automation["runner"]
        if runner["provider"] == "machx" and (background or not await _local_loaded()):
            if not automation.get("fallback"):       # background runs never use or start the local engine
                return note(automation, label, "SKIPPED", "Background runs use cloud runners only, and no cloud fallback "
                            "is set." if background else "The local model was not loaded, and no cloud fallback is set.")
            runner = automation["fallback"]
        started, found = _now(), await asyncio.to_thread(connectors.discover)
        context, problems = await connectors.gather(found, automation.get("connectors", []))
        try:
            with _work_folder() as work:
                folder = Path(work).resolve()
                files = _attach(automation["id"], folder, problems)
                question = (f"Automation: {automation['title']}\nRun: {label}, {datetime.now():%A %Y-%m-%d %H:%M} local time\n\n"
                            + (f"Context from the owner's connectors, read-only:\n\n{context}\n\n" if context else "")
                            + (f"Attached files (also in your working folder):\n\n{files}\n\n" if files else "")
                            + f"Instructions:\n{automation['instructions']}")
                if await _logged_out(background):   # (a logout during the runner's own start-up is accepted)
                    return note(automation, label, "SKIPPED", "You logged out, so the run was not started.")
                if _inside_repository(folder):
                    answer = f"[Sleepwalk: unavailable — the run folder {folder} is inside a git repository, so it is not isolated]"
                else:
                    answer = await moe.consult_advisor(runner["provider"], question, cwd=str(folder), model=runner.get("model"),
                                                       effort=runner.get("effort"), mode="ask", system=SYSTEM, isolate=True,
                                                       timeout=TIMEOUT)
        except NotIsolated as exc:                  # raised by _work_folder() alone: the run never started
            answer = f"[Sleepwalk: unavailable — {exc}]"
        answer = answer.strip()
        failed = not answer or bool(_UNAVAILABLE.search(answer))
        error = (answer or "The runner returned nothing.") if failed else None
        if await _logged_out(background):           # logged out while it ran: keep the result, send nothing
            return store.add_run(automation["id"], {
                "title": automation["title"], "status": "SKIPPED", "trigger": label, "started": started,
                "finished": _now(), "runner": runner, "output": answer, "notified": [],
                "error": "You logged out during this run, so nothing was sent."})
        sent, failures = await connectors.notify(found, automation.get("notify", ["app"]), automation["title"],
                                                 error and f"The run failed: {error}" or answer,
                                                 allowed=lambda: _still_logged_in(background))
        return store.add_run(automation["id"], {
            "title": automation["title"], "status": "FAILED" if failed else "OK", "trigger": label,
            "started": started, "finished": _now(), "runner": runner, "output": "" if failed else answer,
            "error": error, "notified": sent, **({"problems": problems + failures} if problems or failures else {})})


def _attach(identifier: str, folder: Path, notes: list[str]) -> str:
    """Copy the automation's attachments into the run folder, reading through directory descriptors opened with
    O_NOFOLLOW from the store down, so no link (swapped in at any moment) is followed; files with other links to them
    (st_nlink > 1) are skipped. Text is also quoted, 64 KB (bytes) in all. Anything skipped gets a note."""
    quoted, budget, fds = [], 64 * 1024, []
    try:
        fds.append(os.open(store.ROOT, os.O_RDONLY | os.O_DIRECTORY))
        for part in ("attachments", identifier):
            fds.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
    except OSError as exc:
        for fd in fds:
            os.close(fd)
        if not isinstance(exc, FileNotFoundError):     # no folder: no attachments
            notes.append("the attachments folder is a link or unreadable and was not read")
        return ""
    for fd in fds[:-1]:
        os.close(fd)
    here = fds[-1]
    try:
        for name in sorted(os.listdir(here)):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=here)
                with os.fdopen(fd, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                        raise OSError("not a plain file with a single link")
                    data = handle.read(2_000_001)
            except OSError as exc:
                notes.append(f"attachment {name} skipped: {exc.strerror or exc}")
                continue
            (folder / name).write_bytes(data)
            try:
                part = data.decode("utf-8") and data[:budget].decode("utf-8", "ignore")
            except UnicodeDecodeError:
                part = ""
            quoted.append(f"--- {name}\n{part}" if part else f"--- {name} ({len(data):,} bytes; in the folder only)")
            budget -= len(part.encode())
    finally:
        os.close(here)
    return "\n".join(quoted)
