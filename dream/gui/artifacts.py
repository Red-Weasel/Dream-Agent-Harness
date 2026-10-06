"""The session's artifacts: what a turn builds, as first-class objects (DREAM-233, batch A1).

Fed from the App's event funnel (`ArtifactRegistry.observe`, one call per event, after the bus has the event): a
`tool_use` of a file tool and its successful `tool_result` (the lead's, or a worker's inside `agent_activity`), a
`run_bash` result (the files the command wrote, found by one bounded walk), an explicit `studio` show (the model's
own choice), and the turn's start and end. It reads every file FROM DISK, never from the tool input (an append
write carries only its part), inside the workspace only, and keeps each version's bytes under
``var/artifacts/<session>/<id>/v<n>`` so a reload, a restore and a compare have them after the page reloads.

What is an artifact (DREAM-236): a page (`.html`, `.htm`), a diagram (`.svg`), a report (`.md`), an image, a video,
an audio file and a document the frame cannot render (`.pdf`, `.docx`, `.pptx`, `.xlsx`). The text kinds keep each
version's bytes; a binary kind keeps a numbered record of each rewrite (size and time, no copy: the panel shows the
file as it is). The session's list is written to ``var/artifacts/<session>/index.json`` after every change, so a
resumed session has its cards' content and the Artifacts tab (DREAM-235) lists what earlier sessions built in the
same workspace (`library`).

What it publishes is small: an ``artifact`` event with metadata only ({op, id, title, type, path, version, turn,
open, reason}); the page fetches content by id and version from ``GET /api/artifact/{id}``. ``open`` is Dream's
wish, the page applies its own never-steal rules. The rule for a turn: the first successful previewable write
opens the panel; a later file of a better kind takes its place (`_rank`: ``index.html`` at the shallowest depth,
then a page, a report, a diagram, a video, an audio file, an image, a document); an explicit show (`done` /
`show_to_user`) wins over every automatic choice and ends the turn's automatic opening; every other file gets a
card; a write to a file under an open page's folder reloads it; a worker's page gets its card at once and opens at
turn end when nothing else opened. `observe` never raises: a display registry cannot be allowed to break a turn.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import os
import re
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.backends.base import Event

_log = logging.getLogger(__name__)

PREVIEWABLE = {".html": "page", ".htm": "page", ".svg": "svg", ".md": "report",
               ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image",
               ".mp4": "video", ".webm": "video", ".mp3": "audio", ".wav": "audio",
               ".pdf": "document", ".docx": "document", ".pptx": "document", ".xlsx": "document"}
PAGE_TYPES = {"page", "svg"}                 # what A1 opened; a shell call's pages all register, as before
TEXT_TYPES = {"page", "svg", "report"}       # a version's bytes are kept; every other kind is the file as it is
# Dream's own plan and instruction files are not something a turn built for the owner.
SKIP_NAMES = {"PLAN.md", "DREAM.md", "CLAUDE.md", "AGENTS.md"}
WRITE_TOOLS = {"write_file", "str_replace_edit", "Write", "Edit", "MultiEdit"}
SHELL_TOOLS = {"run_bash"}
SHOW_TOOLS = {"done", "show_to_user"}        # the model's explicit choice, for the kinds no `studio` show carries
# A command that leaves more reports, images, media files or documents than this unpacked or cloned something:
# none of them becomes an artifact (its pages still do, as in A1).
SHELL_MAX_OTHER = 8
LIBRARY_MAX = 200                            # earlier sessions' artifacts the Artifacts tab lists, newest first
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "uploads", ".venv", "venv", "__pycache__",
             "site-packages", "dist-packages"}
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_HEADING = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)
_MD_HEADING = re.compile(r"^ {0,3}#{1,3}[ \t]+(.+?)[ \t#]*$", re.M)
_TAGS = re.compile(r"<[^>]+>")
# The start-of-call mark sits this far before the call (dream.tools.mirror's reason: coarse file clocks).
_MARK_SLACK_NS = 20_000_000


def digest(text: str) -> str:
    """The pane's name for exact content (CRC-32 of the UTF-16 code units, as dream.tools.mirror.digest)."""
    return f"{zlib.crc32(text.encode('utf-16-le', 'surrogatepass')):08x}"


def kind_of(path: str | Path) -> str | None:
    """The artifact kind of a file name, or None when it is not one."""
    p = Path(path)
    return None if p.name in SKIP_NAMES else PREVIEWABLE.get(p.suffix.lower())


def title_of(text: str, name: str, kind: str) -> str:
    """The page's <title>, else its first heading, else the file name; a report's first heading; one line, at
    most 120 characters."""
    if kind == "report":
        m = _MD_HEADING.search(text[:200_000])
        return " ".join(m.group(1).split())[:120] if m and m.group(1).strip() else name
    for pattern in (_TITLE, _HEADING) if kind == "page" else (_TITLE,):
        m = pattern.search(text[:200_000])
        if m:
            words = " ".join(html_mod.unescape(_TAGS.sub("", m.group(1))).split())
            if words:
                return words[:120]
    return name


@dataclass
class Artifact:
    id: str
    path: str                 # workspace-relative, posix
    type: str                 # page | svg | report | image | video | audio | document
    title: str
    turn: int                 # the turn it first appeared in
    worker: bool = False
    shown_by: str | None = None   # model (an explicit show) | auto
    versions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def version(self) -> int:
        return self.versions[-1]["n"] if self.versions else 0

    def meta(self) -> dict[str, Any]:
        last = self.versions[-1] if self.versions else {}
        return {"id": self.id, "title": self.title, "type": self.type, "path": self.path,
                "version": self.version, "turn": self.turn, "worker": self.worker, "shown_by": self.shown_by,
                "bytes": last.get("bytes", 0), "at": last.get("at", 0.0)}

    def stored(self, workspace: str) -> dict[str, Any]:
        return {"id": self.id, "path": self.path, "type": self.type, "title": self.title, "turn": self.turn,
                "worker": self.worker, "shown_by": self.shown_by, "workspace": workspace, "versions": self.versions}


class ArtifactRegistry:
    """One per session (the App owns it; the StudioServer reads it)."""

    MAX_BYTES = 4 * 1024 * 1024
    MAX_VERSIONS = 50

    def __init__(self, workspace: Path | Callable[[], Path], session: str | Callable[[], str],
                 publish: Callable[[Event], None], store: Path | None = None) -> None:
        self._workspace = workspace
        self._session_source = session
        self._publish = publish
        if store is None:
            from .. import config
            store = config.VAR_DIR / "artifacts"
        self._store = Path(store)
        self._lock = threading.RLock()
        self._session: str | None = None
        self._given: str | None = None       # the workspace as its source spelled it (compared on every event)
        self._root: str | None = None        # ... resolved: the workspace the loaded artifacts belong to
        self._reset()

    # --- state ------------------------------------------------------------------------------

    def _reset(self) -> None:
        self._by_id: dict[str, Artifact] = {}
        self._by_path: dict[str, str] = {}
        self._pending: dict[str, dict[str, Any]] = {}   # tool id -> {name, input, worker, mark}
        self._seq = 0
        self._turn = 0
        self._turn_open = False
        self._auto: str | None = None        # the turn's automatic open
        self._explicit: str | None = None    # the turn's explicit show
        self._workers: list[str] = []        # workers' previewables this turn, in order
        self._elsewhere: list[dict[str, Any]] = []   # this session's artifacts of another workspace, kept in the index

    def _check_session(self) -> None:
        """A new session id, or another workspace under the same one (a project switch), starts from what that
        session's index holds for this workspace: nothing for a new session, its artifacts for a resumed one."""
        source = self._session_source
        current = str(source() if callable(source) else source)
        given = str((self._workspace() if callable(self._workspace) else self._workspace) or "")
        if current != self._session or given != self._given:
            ws = self.workspace
            self._session, self._given, self._root = current, given, str(ws) if ws is not None else None
            self._reset()
            self._load()

    def _load(self) -> None:
        data = _read_index(self._store / (self._session or "session"))
        if data is None:
            return
        self._seq = int(data.get("seq") or 0)
        for row in data.get("artifacts") or []:
            a = _artifact_of(row)
            if a is None:
                continue
            if row.get("workspace") != self._root:
                self._elsewhere.append(row)
                continue
            self._by_id[a.id] = a
            self._by_path[a.path] = a.id
            self._turn = max(self._turn, a.turn, *(int(v.get("turn") or 0) for v in a.versions))

    def _save(self) -> None:
        """The session's list, written whole after a change (a small file; replaced atomically)."""
        folder = self._store / (self._session or "session")
        rows = self._elsewhere + [a.stored(self._root or "") for a in self._by_id.values()]
        try:
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / "index.json.tmp"
            tmp.write_text(json.dumps({"seq": self._seq, "artifacts": rows}), encoding="utf-8")
            os.replace(tmp, folder / "index.json")
        except OSError as exc:
            _log.warning("the artifact list of session %s could not be saved (%s); the Artifacts tab will not "
                         "list this session's work after a restart", self._session, exc)

    @property
    def workspace(self) -> Path | None:
        ws = self._workspace() if callable(self._workspace) else self._workspace
        return Path(ws).resolve() if ws else None

    def get(self, aid: str) -> Artifact | None:
        with self._lock:
            self._check_session()
            return self._by_id.get(str(aid))

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            self._check_session()
            return [a.meta() for a in self._by_id.values()]

    def read(self, aid: str, version: int | None = None, session: str | None = None) -> dict[str, Any] | None:
        """Version ``version`` (the latest when None) of an artifact: its text and metadata, or None. ``session``
        names an earlier session of this workspace (the Artifacts tab's list). A binary kind has no text."""
        with self._lock:
            self._check_session()
            if session is None or session == self._session:
                a, folder = self._by_id.get(str(aid)), self._store / (self._session or "session")
            else:
                a, folder = self._earlier(session, str(aid))
            if a is None or not a.versions:
                return None
            n = a.version if version is None else version
            record = next((v for v in a.versions if v["n"] == n), None)
            if record is None:
                return None
            text = None
            if a.type in TEXT_TYPES:
                try:
                    text = (folder / a.id / f"v{n}").read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return None
            return {**a.meta(), "version": n, "versions": a.version, "digest": record["digest"],
                    "bytes": record.get("bytes", 0), "at": record.get("at", 0.0), "text": text}

    def _earlier(self, session: str, aid: str) -> tuple[Artifact | None, Path]:
        """An artifact of another session of THIS workspace, and that session's folder; (None, _) otherwise."""
        folder = self._store / session
        if not session or Path(session).name != session or session in (".", ".."):
            return None, folder
        for row in (_read_index(folder) or {}).get("artifacts") or []:
            if row.get("id") == aid and row.get("workspace") == self._root:
                return _artifact_of(row), folder
        return None, folder

    def shows(self, raw: str) -> str | None:
        """The kind ``raw`` would be registered as when shown (inside the workspace, not in a hidden or installed
        folder, a kind the panel has), or None. The Studio tools ask before they tell the model what the owner sees."""
        rel = self._inside(raw)
        return kind_of(rel) if rel is not None else None

    def library(self) -> dict[str, Any]:
        """The Artifacts tab's list (DREAM-235): what this session built in this workspace, then what earlier
        sessions built in it, each newest first; ``exists`` says whether the file is still in the workspace."""
        with self._lock:
            self._check_session()
            ws = self.workspace

            def row(a: Artifact, session: str) -> dict[str, Any]:
                try:
                    exists = ws is not None and (ws / a.path).is_file()
                except OSError:
                    exists = False
                return {**a.meta(), "versions": len(a.versions), "session": session, "exists": exists}

            mine = sorted((row(a, self._session or "") for a in self._by_id.values() if a.versions),
                          key=lambda r: r["at"], reverse=True)
            earlier: list[dict[str, Any]] = []
            try:
                folders = [f for f in self._store.iterdir() if f.is_dir() and f.name != (self._session or "session")]
            except OSError:
                folders = []
            for folder in folders:
                for data in (_read_index(folder) or {}).get("artifacts") or []:
                    a = _artifact_of(data)
                    if a is not None and a.versions and data.get("workspace") == self._root:
                        earlier.append(row(a, folder.name))
            earlier.sort(key=lambda r: r["at"], reverse=True)
            return {"session_id": self._session or "", "session": mine, "earlier": earlier[:LIBRARY_MAX]}

    def note(self, aid: str) -> str | None:
        """What the model is told under a follow-up while this artifact is open in the panel (D10)."""
        with self._lock:
            self._check_session()
            a = self._by_id.get(str(aid))
            ws = self.workspace
            if a is None or ws is None or not a.versions:
                return None
            target = ws / a.path
            try:
                beside = sorted(p.name for p in target.parent.iterdir()
                                if p.is_file() and p.name != target.name and not p.name.startswith("."))
            except OSError:
                beside = []
            head = f'[Dream, not from the user: the user has "{a.title}" open in the preview panel: {a.path} '
            if a.type in TEXT_TYPES:
                try:
                    text = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
                except OSError:
                    text = ""
                if not text:
                    got = self.read(a.id)
                    text = got["text"] if got else ""
                names = '"It" and "the report" mean' if a.type == "report" else '"It", "the page" and "the site" mean'
                note = head + (f'(v{a.version}, {len(text.splitlines())} lines). {names} this file; '
                               f'change it with str_replace_edit.')
            else:
                note = head + f'(v{a.version}, {a.type}, {a.versions[-1].get("bytes", 0):,} bytes). "It" means this file.'
            if beside:
                note += " Files beside it: " + ", ".join(beside[:8]) + ("…" if len(beside) > 8 else "") + "."
            return note + "]"

    # --- the funnel ---------------------------------------------------------------------------

    def observe(self, ev: Any) -> None:
        try:
            with self._lock:
                self._check_session()
                self._observe(getattr(ev, "kind", None), getattr(ev, "data", None))
        except Exception:   # a display registry never breaks a turn
            _log.debug("artifact registry: an event was not understood", exc_info=True)

    def _observe(self, kind: Any, data: Any) -> None:
        if kind == "turn_start":
            self._end_turn()
            self._turn += 1
            self._turn_open = True
        elif kind == "turn_end":
            self._end_turn()
        elif kind == "tool_use":
            self._tool_use(data, worker=False)
        elif kind == "tool_result":
            self._tool_result(data)
        elif kind == "agent_activity" and isinstance(data, dict):
            inner = data.get("data")
            if data.get("kind") == "tool_use":
                self._tool_use(inner, worker=True)
            elif data.get("kind") == "tool_result":
                self._tool_result(inner)
        elif kind == "studio" and isinstance(data, dict):
            if data.get("op") == "show" and data.get("source") != "mirror" and data.get("kind") != "image":
                self._explicit_show(str(data.get("path") or ""))

    def _ensure_turn(self) -> None:
        if not self._turn_open:
            self._turn += 1
            self._turn_open = True

    def _end_turn(self) -> None:
        if not self._turn_open:
            return
        if self._auto is None and self._explicit is None:
            best = max((self._by_id[i] for i in self._workers if i in self._by_id), key=self._rank, default=None)
            if best is not None:
                best.shown_by = best.shown_by or "auto"
                self._emit("open", best, True, "worker")
        self._turn_open = False
        self._auto = self._explicit = None
        self._workers = []

    def _tool_use(self, data: Any, *, worker: bool) -> None:
        if not isinstance(data, dict):
            return
        name = str(data.get("name") or "").split("__")[-1]
        tid = data.get("id")
        if tid is None or name not in WRITE_TOOLS | SHELL_TOOLS | SHOW_TOOLS:
            return
        self._ensure_turn()
        self._pending[str(tid)] = {"name": name, "input": data.get("input"), "worker": worker,
                                   "mark": time.time_ns() - _MARK_SLACK_NS}
        if len(self._pending) > 256:   # a result that never came is not kept forever
            del self._pending[next(iter(self._pending))]

    def _tool_result(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        call = self._pending.pop(str(data.get("id")), None)
        if call is None or data.get("is_error"):
            return
        self._ensure_turn()
        if call["name"] in SHELL_TOOLS:
            self._after_shell(call)
            return
        args = call["input"] if isinstance(call["input"], dict) else {}
        raw = args.get("file_path") if call["name"] in ("Write", "Edit", "MultiEdit") else args.get("path")
        if not isinstance(raw, str) or not raw:
            return
        if call["name"] in SHOW_TOOLS:
            # A page's explicit show is its `studio` event (it carries the prepared page); a report, an image, a
            # media file or a document has no such event: the successful call is the show.
            if not call["worker"] and kind_of(raw) not in PAGE_TYPES:
                self._explicit_show(raw)
            return
        self._written([raw], worker=call["worker"])

    def _after_shell(self, call: dict[str, Any]) -> None:
        ws = self.workspace
        if ws is None:
            return
        from ..tools import mirror   # late: the Studio server imports this module without the tool stack

        found = [p for p in mirror.files_since(ws, call["mark"], set(PREVIEWABLE)) if kind_of(p) is not None]
        others = [p for p in found if kind_of(p) not in PAGE_TYPES]
        if len(others) > SHELL_MAX_OTHER:
            found = [p for p in found if kind_of(p) in PAGE_TYPES]
        if found:
            self._written([str(p) for p in found], worker=call["worker"])

    def _explicit_show(self, given: str) -> None:
        rel = self._inside(given)
        if rel is None or kind_of(rel) is None:
            return
        self._ensure_turn()
        a, _ = self._record(rel, worker=False)
        if a is None:
            return
        a.shown_by = "model"
        self._explicit = a.id
        self._save()
        self._emit("upsert", a, True, "explicit")

    # --- writes -------------------------------------------------------------------------------

    def _written(self, raws: list[str], *, worker: bool) -> None:
        """Files one call wrote: previewables become artifacts (one open at most), the rest reload the pages
        of their folder. A report, an image, a media file or a document inside the folder of a page this session
        built is that site's file (it reloads the page and gets no card), unless it is an artifact already."""
        pages: list[Artifact] = []
        for raw in raws:
            rel = self._inside(raw)
            if rel is None:
                continue
            kind = kind_of(rel)
            if kind is not None and (kind in PAGE_TYPES or rel in self._by_path or not self._in_a_site(rel)):
                if kind not in PAGE_TYPES:
                    self._dependency(rel)   # a page at the workspace root may use it
                a, changed = self._record(rel, worker=worker)
                if a is not None and changed and a not in pages:   # the same bytes again are not a version
                    pages.append(a)
            else:
                self._dependency(rel)
        if not pages:
            return
        winner = max(pages, key=self._rank)
        for a in pages:
            self._upsert(a, worker=worker, candidate=a is winner)

    def _upsert(self, a: Artifact, *, worker: bool, candidate: bool) -> None:
        """The event for a changed page: a card, a reload of the open one, or the turn's open (``candidate`` =
        the best of its call; the others of the same call get cards)."""
        if worker:
            if a.id not in self._workers:
                self._workers.append(a.id)
            self._emit("upsert", a, False, "worker")
            return
        if not candidate or self._explicit is not None or a.id == self._auto:
            self._emit("upsert", a, False, "explicit" if a.id == self._explicit else "auto")
            return
        current = self._by_id.get(self._auto) if self._auto else None
        if current is None or self._rank(a)[:2] > self._rank(current)[:2]:
            self._auto = a.id
            a.shown_by = a.shown_by or "auto"
            self._emit("upsert", a, True, "auto")
        else:
            self._emit("upsert", a, False, "auto")

    def _dependency(self, rel: str) -> None:
        folder = Path(rel).parent
        for a in self._by_id.values():
            page_folder = Path(a.path).parent
            if a.type in PAGE_TYPES and a.path != rel and (folder == page_folder or folder.is_relative_to(page_folder)):
                self._emit("reload", a, False, "dependency")

    def _in_a_site(self, rel: str) -> bool:
        """Whether ``rel`` lies in the folder (not the workspace root) of a page this session built."""
        folder = Path(rel).parent
        return any(a.type == "page" and Path(a.path).parent != Path(".")
                   and (folder == Path(a.path).parent or folder.is_relative_to(Path(a.path).parent))
                   for a in self._by_id.values())

    def _record(self, rel: str, *, worker: bool) -> tuple[Artifact | None, bool]:
        """The artifact for ``rel`` with the file as it is now as its newest version, and whether a version
        was added (not when the bytes did not change). (None, False) when the file cannot be read or a text
        file is too large. A text kind's bytes are stored; a binary kind's version is its size and write time
        (one stat, no read: this runs in the event funnel)."""
        ws = self.workspace
        if ws is None:
            return None, False
        target = ws / rel
        kind = PREVIEWABLE[Path(rel).suffix.lower()]
        text: str | None = None
        try:
            if not target.is_file():
                return None, False
            st = target.stat()
            if kind in TEXT_TYPES:
                if st.st_size > self.MAX_BYTES:
                    return None, False
                text = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None, False
        name = Path(rel).name
        title = title_of(text, name, kind) if text is not None else name
        d = digest(text) if text is not None else f"{st.st_size:x}.{st.st_mtime_ns:x}"
        aid = self._by_path.get(rel)
        a = self._by_id.get(aid) if aid else None
        if a is None:
            self._seq += 1
            a = Artifact(id=f"a{self._seq}", path=rel, type=kind, title=title, turn=self._turn, worker=worker)
            self._by_id[a.id] = a
            self._by_path[rel] = a.id
        if a.versions and a.versions[-1]["digest"] == d:
            return a, False
        a.title = title
        n = a.version + 1
        folder = self._folder(a.id)
        if text is not None:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"v{n}").write_text(text, encoding="utf-8")
        a.versions.append({"n": n, "digest": d, "bytes": len(text.encode("utf-8")) if text is not None else st.st_size,
                           "turn": self._turn, "at": time.time()})
        while len(a.versions) > self.MAX_VERSIONS:
            gone = a.versions.pop(0)
            try:
                (folder / f"v{gone['n']}").unlink()
            except OSError:
                pass
        self._save()
        return a, True

    def _emit(self, op: str, a: Artifact, open_: bool, reason: str) -> None:
        self._publish(Event("artifact", {"op": op, **a.meta(), "open": bool(open_), "reason": reason}))

    # --- helpers ------------------------------------------------------------------------------

    def _folder(self, aid: str) -> Path:
        return self._store / (self._session or "session") / aid

    def _inside(self, raw: str) -> str | None:
        """A path as a tool spelled it, as a workspace-relative posix path; None outside the workspace, in a
        hidden or installed folder, or unreadable as a path."""
        ws = self.workspace
        if ws is None or not raw or "\0" in raw:
            return None
        try:
            p = Path(raw).expanduser()
            p = (p if p.is_absolute() else ws / p).resolve()
        except (OSError, ValueError, RuntimeError):
            return None
        if p == ws or not p.is_relative_to(ws):
            return None
        rel = p.relative_to(ws)
        if any(part.startswith(".") or part in SKIP_DIRS for part in rel.parts):
            return None
        return rel.as_posix()

    @staticmethod
    def _rank(a: Artifact) -> tuple[int, int, float]:
        """index.html first (the shallowest wins), then any page, a report, a diagram, a video, an audio file,
        an image, a document; the newest among equals. A page is what was asked for when one is built; a report
        is read before the figures beside it; a media file is a turn's result where its images are usually its
        frames or checks; a document the panel cannot render opens last."""
        at = a.versions[-1]["at"] if a.versions else 0.0
        name = Path(a.path).name.lower()
        if a.type == "page" and name in ("index.html", "index.htm"):
            return (8, -len(Path(a.path).parts), at)
        return (_TIER.get(a.type, 0), 0, at)


_TIER = {"page": 7, "report": 6, "svg": 5, "video": 4, "audio": 3, "image": 2, "document": 1}


def _read_index(folder: Path) -> dict[str, Any] | None:
    """A session folder's index.json as a dict, or None when it is missing or unreadable."""
    try:
        data = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _artifact_of(row: Any) -> Artifact | None:
    """One stored row as an Artifact; None for a row this version cannot read."""
    try:
        versions = [v for v in row["versions"] if isinstance(v, dict) and isinstance(v.get("n"), int)]
        return Artifact(id=str(row["id"]), path=str(row["path"]), type=str(row["type"]), title=str(row["title"]),
                        turn=int(row.get("turn") or 0), worker=bool(row.get("worker")),
                        shown_by=row.get("shown_by"), versions=versions)
    except (KeyError, TypeError, ValueError):
        return None
