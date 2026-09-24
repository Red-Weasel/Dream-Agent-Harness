"""DREAM-108: file legacy memory under its project -- from evidence only.

Until DREAM-108 nothing in memory said which project it belonged to, so a session in one
repo recalled, listed and was woken with another repo's memories and tasks (the owner's
"terrain and sea water" request planned around another project's sea task). Every row
now names a project; a row from before carries '' until this migration files it.

It runs at startup, before the session opens, and only when legacy items exist:

1. Back up ``data/dream.db`` (SQLite's online backup, WAL included) and ``memory/`` to
   ``data/backups/memory-scope-<stamp>/``, keeping the last ``KEEP_BACKUPS``.
2. Decide every legacy item from evidence, never by guessing: all the evidence an item
   has must name ONE project, else it is ``unassigned`` -- never shown by default,
   listed with all_projects=true, reassigned with memory_move / task_update(project=).
3. Write the decisions (rows, and a ``project:`` line in each memory file, which is the
   source of truth) and a readable report, ``data/memory-migration-<date>.md``.

A second run finds nothing legacy and does nothing: idempotent. ``dry_run`` writes only
the report. A completed run marks the database (PRAGMA user_version 2): from then on a
memory file without a project line is not legacy but one the owner wrote by hand, and
the session that starts adopts it (longterm.adopt_orphans); sessions, notes and tasks
that an older Dream still running writes without a project stay legacy and are filed here.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import sqlite3
import time
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config
from . import longterm
from . import project as P
from .store import SCOPE_MIGRATED_VERSION

BACKUP_PREFIX = "memory-scope-"
KEEP_BACKUPS = 3
# A session with no direct record of its workspace is filed by the absolute paths in its
# transcript only when one known workspace holds at least PATH_MIN of them and at least
# PATH_SHARE of every home-folder path it names (Dream's own state folders and dot-folders
# are tool plumbing and do not count). A session in an unknown folder that names a known
# one in passing is therefore not claimed for it.
PATH_MIN = 3
PATH_SHARE = 0.8
# A folder name shorter than this ("ws") is too generic to count as a mention in text.
MIN_NAME = 4
_TABLES = {"sessions": "id", "memories": "slug", "working_notes": "id", "tasks": "id"}
_PLUMBING = ("memory", "data", "var", "skills", "plugins")


@dataclass
class Decision:
    kind: str            # session | memory | task | note
    ident: str           # the session id, the memory's name, the task/note id
    label: str           # a title for the report
    project: str         # a key, or P.UNASSIGNED
    reasons: list[str] = field(default_factory=list)
    session: str = ""    # notes: the session they belong to


@dataclass
class Result:
    applied: bool
    decisions: list[Decision] = field(default_factory=list)
    backup: Path | None = None
    report: Path | None = None
    known: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# --- is there anything to do? ----------------------------------------------------------------


def _migrated(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA user_version").fetchone()[0] >= SCOPE_MIGRATED_VERSION


def _legacy_rows(conn: sqlite3.Connection) -> bool:
    """Rows with no project. After the one-time migration a memory without one is a file the
    owner wrote by hand, waiting for the next session start, not legacy."""
    migrated = _migrated(conn)
    for table in _TABLES:
        if migrated and table == "memories":
            continue
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,)).fetchone():
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        sql = (f"SELECT 1 FROM {table} WHERE project='' LIMIT 1" if "project" in cols
               else f"SELECT 1 FROM {table} LIMIT 1")
        if conn.execute(sql).fetchone():
            return True
    return False


def _legacy_files() -> bool:
    for path in longterm.memory_files():
        fm, _ = longterm.parse_markdown(path.read_text(encoding="utf-8", errors="replace"))
        if not P.valid_owner(fm.get("project")):
            return True
    return False


def pending(store: Any) -> bool:
    """Whether any row still has no project (''), in any of the four tables."""
    with store._lock:
        return _legacy_rows(store._conn)


def prepare(db_path: Path | str | None = None) -> Path | None:
    """Called before anything at startup touches the database or memory/: when legacy
    items exist, back both up as they are and return the backup. None otherwise."""
    db = Path(db_path or config.DB_PATH)
    legacy = migrated = False
    if db.is_file():
        conn = sqlite3.connect(db)
        try:
            legacy, migrated = _legacy_rows(conn), _migrated(conn)
        finally:
            conn.close()
    if not legacy and not migrated:
        legacy = _legacy_files()
    return backup(db) if legacy else None


# --- the backup ------------------------------------------------------------------------------


def backup(db_path: Path | str | None = None, *, conn: sqlite3.Connection | None = None) -> Path:
    """data/backups/memory-scope-<stamp>/ with dream.db and memory/, as they are now.
    Only this migration's own older backups are pruned, to the last KEEP_BACKUPS."""
    root = config.DATA_DIR / "backups"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / f"{BACKUP_PREFIX}{stamp}"
    n = 1
    while dest.exists():
        n += 1
        dest = root / f"{BACKUP_PREFIX}{stamp}-{n}"
    dest.mkdir()
    db = Path(db_path or config.DB_PATH)
    if conn is not None or db.is_file():
        source = conn or sqlite3.connect(db)
        target = sqlite3.connect(dest / "dream.db")
        try:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise OSError(f"the database backup in {dest} failed its integrity check")
        finally:
            target.close()
            if conn is None:
                source.close()
    if config.MEMORY_DIR.is_dir():
        shutil.copytree(config.MEMORY_DIR, dest / "memory", symlinks=True)
    mine = sorted((p for p in root.iterdir() if p.is_dir() and p.name.startswith(BACKUP_PREFIX)),
                  key=lambda p: p.name)
    for old in mine[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


# --- evidence --------------------------------------------------------------------------------


def _boundary(text: str, end: int) -> bool:
    """A path ends at `end`: the next character cannot continue a folder name."""
    if end >= len(text):
        return True
    ch = text[end]
    if ch.isalnum() or ch in "_-":
        return False
    if ch == "." and end + 1 < len(text) and text[end + 1].isalnum():
        return False
    return True


def _forms(ws: str) -> list[str]:
    quoted = urllib.parse.quote(ws)
    return [ws] if quoted == ws else [ws, quoted]


@dataclass
class _Transcript:
    paths: Counter = field(default_factory=Counter)        # mentions per known workspace
    other: int = 0                                          # other home-folder paths


def _read_transcript(sid: str) -> list[dict[str, Any]] | None:
    path = config.SESSIONS_DIR / f"{sid}.jsonl"
    if not path.is_file():
        return None
    out = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("content"), str):
                out.append(rec)
    return out


_NOTE_RE = re.compile(r"/projects/([a-z0-9][a-z0-9._-]*)/PROJECT\.md")
# A tool result that is a JSON object whose first key is the session's workspace (media_read
# reports it). Transcripts keep 2,000 characters of a result, so the object is often cut
# short: only that leading key is read, as a JSON string.
_WORKSPACE_RE = re.compile(r'\A\s*\{\s*"workspace"\s*:\s*("(?:[^"\\]|\\.)*")')


def _direct_records(records: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    keys, recorded = set(), set()
    for rec in records:
        content = rec["content"]
        if rec.get("role") != "tool_result":
            continue
        if rec.get("tool") == "project_note":
            keys.update(k for k in _NOTE_RE.findall(content) if P.valid_owner(k))
        m = _WORKSPACE_RE.match(content)
        if m:
            try:
                ws = json.loads(m.group(1))
            except ValueError:
                continue
            if isinstance(ws, str) and ws.startswith("/"):
                recorded.add(ws.rstrip("/") or "/")
    return keys, recorded


def _count_paths(records: list[dict[str, Any]], real: dict[str, str], t: _Transcript) -> None:
    """Mentions of each real project workspace (the deepest one when they nest), and of
    every other home-folder path, over the whole transcript."""
    home = str(Path.home())
    root = str(config.ROOT)
    forms = sorted(((form, ws) for ws in real.values() for form in _forms(ws)),
                   key=lambda f: -len(f[0]))
    for rec in records:
        text = rec["content"]
        taken: dict[int, str] = {}
        for form, ws in forms:
            for m in re.finditer(re.escape(form), text):
                if m.start() not in taken and _boundary(text, m.end()):
                    taken[m.start()] = ws
        for ws in taken.values():
            t.paths[ws] += 1
        for m in re.finditer(re.escape(home) + "/", text):
            if m.start() in taken or text[m.end():m.end() + 1] == ".":
                continue  # a known workspace, or a dot-folder (tool plumbing)
            rest = text[m.start():]
            if rest.startswith(root + "/"):
                if rest[len(root) + 1:].split("/", 1)[0] in _PLUMBING:
                    continue  # Dream's own memory, data, skills: every session names them
            t.other += 1


def _mentions(text: str, real: dict[str, str]) -> dict[str, str]:
    """Known projects a text names: its workspace path, its key, or its folder name
    (case as written, whole words, at least MIN_NAME characters)."""
    found: dict[str, str] = {}
    for key, ws in real.items():
        name = Path(ws).name
        if any(form in text for form in _forms(ws)):
            found[key] = f"its text names {ws}"
        elif key in text:
            found[key] = f"its text names {key}"
        elif len(name) >= MIN_NAME and re.search(
                r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text):
            found[key] = f"its text names {name!r}"
    return found


def _decide(kind: str, ident: str, label: str, cands: dict[str, list[str]], notes: list[str],
            none: str = "no evidence") -> Decision:
    if len(cands) == 1:
        key, why = next(iter(cands.items()))
        return Decision(kind, ident, label, key, [*why, *notes])
    if not cands:
        return Decision(kind, ident, label, P.UNASSIGNED, [*notes, none] if notes else [none])
    listing = "; ".join(f"{k}: {', '.join(why)}" for k, why in sorted(cands.items()))
    return Decision(kind, ident, label, P.UNASSIGNED,
                    [f"evidence names {len(cands)} projects ({listing}); not guessed", *notes])


def plan(store: Any) -> tuple[list[Decision], dict[str, str]]:
    """Every legacy item's decision, and the known projects {key: workspace}."""
    conn = store._conn
    with store._lock:
        sessions = [dict(r) for r in conn.execute(
            "SELECT s.id, s.title, s.started_at, s.project, COALESCE(s.ended_at, "
            "(SELECT MAX(ts) FROM turns WHERE session_id=s.id), s.started_at) AS until "
            "FROM sessions s ORDER BY s.started_at")]
        # After the one-time migration a memory without a project is a hand-written file the
        # starting session adopts; only rows an older Dream writes are still filed here.
        memories = [] if _migrated(conn) else [dict(r) for r in conn.execute(
            "SELECT slug, title, body, description, tags, source_session, project FROM memories "
            "WHERE project='' ORDER BY slug")]
        notes = [dict(r) for r in conn.execute(
            "SELECT id, session_id, project FROM working_notes WHERE project='' ORDER BY id")]
        has_tasks = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()
        tasks = []
        if has_tasks:
            # A task table from before DREAM-108 has no project column yet: every row is legacy.
            legacy = ("WHERE project=''" if "project" in
                      {r[1] for r in conn.execute("PRAGMA table_info(tasks)")} else "")
            tasks = [dict(r) for r in conn.execute(
                f"SELECT id, title, notes, created_at FROM tasks {legacy} ORDER BY id")]

    legacy_sessions = [s for s in sessions if not s["project"]]
    transcripts = {s["id"]: _read_transcript(s["id"]) for s in legacy_sessions}

    # Known projects: the ones Dream recorded (memory/projects/<key>/.workspace) and the
    # workspaces a transcript's tool results recorded for their session.
    known = dict(P.known())
    direct: dict[str, tuple[set[str], set[str]]] = {}
    for sid, records in transcripts.items():
        if records is not None:
            direct[sid] = _direct_records(records)
            for ws in direct[sid][1]:
                known.setdefault(P.project_key(ws), ws)
    real = {k: ws for k, ws in known.items() if P.is_project(ws)}

    decisions: list[Decision] = []
    owner: dict[str, str] = {s["id"]: s["project"] for s in sessions}
    for s in legacy_sessions:
        sid, label = s["id"], s["title"] or "(untitled)"
        records = transcripts[sid]
        if records is None:
            d = Decision("session", sid, label, P.UNASSIGNED, ["no transcript (data/sessions) to read"])
        else:
            keys, recorded = direct[sid]
            cands: dict[str, list[str]] = defaultdict(list)
            for k in keys:
                cands[k].append(f"a project_note result names memory/projects/{k}/")
            for ws in recorded:
                cands[P.project_key(ws)].append(f"a tool result recorded its workspace {ws}")
            if cands:
                d = _decide("session", sid, label, cands, [])
            else:
                t = _Transcript()
                _count_paths(records, real, t)
                total = sum(t.paths.values()) + t.other
                if not t.paths:
                    d = Decision("session", sid, label, P.UNASSIGNED,
                                 [f"no path under a known workspace ({t.other} other home-folder paths)"])
                elif len(t.paths) > 1:
                    listing = ", ".join(f"{ws} ({n})" for ws, n in t.paths.most_common())
                    d = Decision("session", sid, label, P.UNASSIGNED,
                                 [f"paths under several known workspaces: {listing}; not guessed"])
                else:
                    ws, n = next(iter(t.paths.items()))
                    share = f"{n} of {total} home-folder paths under {ws}"
                    if n >= PATH_MIN and n >= PATH_SHARE * total:
                        d = Decision("session", sid, label, P.project_key(ws), [share])
                    else:
                        d = Decision("session", sid, label, P.UNASSIGNED,
                                     [f"only {share} (needs {PATH_MIN}+ and {PATH_SHARE:.0%})"])
        owner[sid] = d.project
        decisions.append(d)

    for m in memories:
        cands = defaultdict(list)
        extra: list[str] = []
        src = m["source_session"]
        if src:
            sp = owner.get(src)
            if sp and sp not in (P.UNASSIGNED, P.USER):
                cands[sp].append(f"its source session {src}")
            else:
                extra.append(f"its source session {src} is "
                             + ("not in the database" if sp is None else "unassigned"))
        text = "\n".join(str(m.get(k) or "") for k in ("title", "body", "description", "tags"))
        for key, why in _mentions(text, real).items():
            cands[key].append(why)
        decisions.append(_decide("memory", m["slug"], m["title"], cands, extra))

    windows = [(s["started_at"], s["until"], s["id"]) for s in sessions]
    names = {k: {k, Path(ws).name.lower()} for k, ws in real.items()}
    for t in tasks:
        cands, extra = defaultdict(list), []
        created = t["created_at"]
        open_then = [sid for start, until, sid in windows if start <= created <= (until or start)]
        if len(open_then) == 1:
            sp = owner.get(open_then[0])
            if sp and sp not in (P.UNASSIGNED, P.USER):
                cands[sp].append(f"created while exactly one session was open ({open_then[0]})")
            else:
                extra.append(f"created during session {open_then[0]}, which is unassigned")
        elif open_then:
            extra.append(f"created while {len(open_then)} sessions were open")
        prefix = re.match(r"\s*([^:\n]{1,60}):", t["title"])
        if prefix:
            said = prefix.group(1).strip().lower()
            hits = [k for k, ns in names.items() if said in ns]
            if len(hits) == 1:
                cands[hits[0]].append(f"its title prefix {prefix.group(1).strip() + ':'!r}")
        for key, why in _mentions(f"{t['title']}\n{t['notes']}", real).items():
            cands[key].append(why)
        decisions.append(_decide("task", str(t["id"]), t["title"], cands, extra))

    for n in notes:
        sp = owner.get(n["session_id"])
        if sp and sp not in (P.UNASSIGNED, P.USER):
            d = Decision("note", str(n["id"]), "", sp, [f"its session {n['session_id']}"],
                         session=n["session_id"])
        else:
            why = "no such session" if sp is None else f"its session {n['session_id']} is unassigned"
            d = Decision("note", str(n["id"]), "", P.UNASSIGNED, [why], session=n["session_id"])
        decisions.append(d)
    return decisions, known


# --- apply and report ------------------------------------------------------------------------


def _apply(store: Any, decisions: list[Decision]) -> list[str]:
    """Rows in one transaction (only rows still '' -- never an overwrite), then each
    memory file's project line, then MEMORY.md. A file that cannot be written is named;
    the next boot's sync and this migration heal it."""
    sql = {"session": "UPDATE sessions SET project=? WHERE id=? AND project=''",
           "memory": "UPDATE memories SET project=? WHERE slug=? AND project=''",
           "task": "UPDATE tasks SET project=? WHERE id=? AND project=''",
           "note": "UPDATE working_notes SET project=? WHERE id=? AND project=''"}
    with store._lock:
        with store._conn:
            for d in decisions:
                store._conn.execute(sql[d.kind], (d.project, d.ident))
    errors = []
    files = longterm.memory_file_map()
    for d in decisions:
        if d.kind != "memory":
            continue
        path = files.get(d.ident, config.MEMORY_DIR / f"{d.ident}.md")
        try:
            longterm.set_project_line(path, d.project)
        except (OSError, ValueError) as exc:     # ValueError: a file that is not UTF-8
            errors.append(f"memory file {path.name}: {exc}")
    try:
        longterm.write_index()
    except OSError as exc:
        errors.append(f"MEMORY.md: {exc}")
    return errors


def _report(result: Result, *, dry_run: bool, when: datetime) -> str:
    by_kind: dict[str, list[Decision]] = defaultdict(list)
    for d in result.decisions:
        by_kind[d.kind].append(d)
    rel = (lambda p: os.path.relpath(p, config.ROOT) if p else "")
    lines = [f"# Memory moved to project scope (DREAM-108) -- {when.isoformat(timespec='seconds')}", ""]
    if dry_run:
        lines += ["**Dry run:** nothing was written and no backup was made; this is what a real run "
                  "would do.", ""]
    else:
        lines += [f"Backup of the database and memory/ before any change: `{rel(result.backup)}`"
                  if result.backup else "No backup was needed.", ""]
    lines += ["Every item now belongs to one project: the workspace it was written in. A new "
              "session sees its own project's items and user-wide ones only. Items the evidence "
              "could not place are **unassigned**: shown in no project by default, listed with "
              "`all_projects=true`, moved with `memory_move(name=... | session=..., project=...)` "
              "or `task_update(id, project=...)`, or by editing a memory file's `project:` line.",
              "", "## Known projects", ""]
    for key, ws in sorted(result.known.items()):
        lines.append(f"- `{key}` -- {ws}")
    if not result.known:
        lines.append("- none")
    lines += ["", "## Summary", "", "| items | legacy | filed under a project | unassigned |",
              "|---|---:|---:|---:|"]
    for kind, title in (("session", "sessions"), ("memory", "memories"), ("task", "tasks"),
                        ("note", "working notes")):
        ds = by_kind.get(kind, [])
        un = sum(d.project == P.UNASSIGNED for d in ds)
        lines.append(f"| {title} | {len(ds)} | {len(ds) - un} | {un} |")
    per_project = Counter(d.project for d in result.decisions)
    lines += ["", "Per project: " + ", ".join(f"`{k}` {n}" for k, n in per_project.most_common()), "",
              "## Evidence rules", "",
              "- A session: a `project_note` result naming `memory/projects/<key>/`, or a tool result "
              "that recorded its workspace; else the absolute paths in its transcript, when one known "
              f"workspace holds at least {PATH_MIN} of them and {PATH_SHARE:.0%} of its home-folder paths.",
              "- A memory: its source session's project, and every known project its text names; "
              "they must agree.",
              "- A task: the session open when it was created (exactly one), its title prefix "
              "(`Name:`), and every known project its text names; they must agree.",
              "- A working note: its session.",
              "- Two different projects, or none, means unassigned. Nothing is guessed.", ""]
    for kind, title in (("session", "Sessions"), ("memory", "Memories"), ("task", "Tasks")):
        lines += [f"## {title}", ""]
        for d in by_kind.get(kind, []):
            name = f"`{d.ident}`" + (f" {d.label!r}" if d.label else "")
            lines.append(f"- {name} -> **{d.project}** -- {'; '.join(d.reasons)}")
        if not by_kind.get(kind):
            lines.append("- none")
        lines.append("")
    lines += ["## Working notes", "", "Notes follow their session.", ""]
    grouped: dict[tuple[str, str], int] = Counter((d.session, d.project) for d in by_kind.get("note", []))
    for (sid, key), count in sorted(grouped.items()):
        lines.append(f"- {count} note(s) of session `{sid}` -> **{key}**")
    if not grouped:
        lines.append("- none")
    if result.errors:
        lines += ["", "## Errors", ""] + [f"- {e}" for e in result.errors]
    return "\n".join(lines) + "\n"


def _report_path(when: datetime) -> Path:
    path = config.DATA_DIR / f"memory-migration-{when:%Y-%m-%d}.md"
    if path.exists():
        path = config.DATA_DIR / f"memory-migration-{when:%Y-%m-%d-%H%M%S}.md"
    return path


def run(store: Any, *, dry_run: bool = False, report_path: Path | None = None,
        backup_path: Path | None = None) -> Result:
    """File every legacy item. Nothing legacy: nothing happens (no backup, no report).
    ``backup_path`` is a backup ``prepare`` already made before startup changed anything."""
    lock_path = config.DATA_DIR / "memory-migration.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as lock:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError("another Dream is migrating memory; this start skipped it")
                time.sleep(0.2)
        if not pending(store):
            if not dry_run:
                store.mark_scope_migrated()
            return Result(applied=False)
        when = datetime.now(timezone.utc)
        decisions, known = plan(store)
        result = Result(applied=not dry_run, decisions=decisions, known=known)
        if not dry_run:
            if store._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
                from .tasks import TaskStore
                TaskStore(store)  # gives an old task table its project column
            if backup_path is None:
                with store._lock:
                    backup_path = backup(store.db_path, conn=store._conn)
            result.backup = backup_path
            result.errors = _apply(store, decisions)
            store.mark_scope_migrated()
        result.report = Path(report_path) if report_path else _report_path(when)
        result.report.parent.mkdir(parents=True, exist_ok=True)
        result.report.write_text(_report(result, dry_run=dry_run, when=when), encoding="utf-8")
        return result
