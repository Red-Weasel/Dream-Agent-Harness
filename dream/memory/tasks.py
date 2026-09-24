"""Work state that survives: a task store on the same database as memory.

Phase 11. Multi-task means work that outlives a turn and a session. Until now
that was a hand-edited THREADS.md; now it is rows — id, title, status, notes,
created, updated — and THREADS.md is generated from them, never edited by hand.

The store rides the MemoryStore's connection and lock: one database, one
writer, the same conventions (ISO timestamps, a lock around every statement).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .. import config
from .project import UNASSIGNED, USER
from .store import add_column

STATUSES = ("open", "active", "blocked", "done")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, updated_at);
"""

_ALL: Any = object()  # "the store's own scope" -- see MemoryStore.default_scope


def wake_lines_if_present(store: Any, limit: int = 8, scope: Any = _ALL) -> list[str]:
    """Open work for the wake-up, WITHOUT creating the table: building a prompt
    is a read, and a bare database must stay bare (Gate 11)."""
    row = store._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()
    if not row:
        return []
    return TaskStore(store).wake_lines(limit=limit, scope=scope)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskStore:
    def __init__(self, store: Any):
        """`store` is the MemoryStore: the tasks table lives in its database."""
        self._store = store
        self._conn = store._conn
        self._lock = store._lock
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # DREAM-108: a task belongs to a project (its workspace key, 'user', or
            # 'unassigned'); '' is a legacy row the startup migration has not placed.
            if "project" not in {r[1] for r in self._conn.execute("PRAGMA table_info(tasks)")}:
                add_column(self._conn, "tasks", "project", "TEXT NOT NULL DEFAULT ''")
            self._conn.commit()

    def _scope(self, scope: Any) -> tuple[str, ...] | None:
        if scope is _ALL:
            default = getattr(self._store, "default_scope", None)
            return default() if callable(default) else None
        return None if scope is None else tuple(scope)

    @staticmethod
    def _where(scope: tuple[str, ...] | None) -> tuple[str, list[Any]]:
        if scope is None:
            return "", []
        if not scope:
            return " AND 0", []
        return f" AND project IN ({','.join('?' * len(scope))})", list(scope)

    # --- writes ------------------------------------------------------------------------

    def add(self, title: str, notes: str = "", status: str = "open",
            project: str | None = None) -> dict[str, Any]:
        title = " ".join(str(title or "").split())
        if not title:
            raise ValueError("a task needs a title")
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        owner = project if project is not None else (getattr(self._store, "project", None) or "")
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks(title, status, notes, created_at, updated_at, project) "
                "VALUES (?,?,?,?,?,?)",
                (title[:200], status, str(notes or "").strip()[:4000], now, now, owner),
            )
            self._conn.commit()
            return self.get(int(cur.lastrowid))  # type: ignore[return-value]

    def update(self, task_id: int, *, title: str | None = None, status: str | None = None,
               notes: str | None = None, append_note: str | None = None,
               project: str | None = None) -> dict[str, Any] | None:
        """``project`` moves the task to that project (DREAM-108)."""
        cur = self.get(task_id)
        if cur is None:
            return None
        if project is not None and project != cur.get("project"):
            with self._lock:
                self._conn.execute("UPDATE tasks SET project=? WHERE id=?", (project, task_id))
                self._conn.commit()
            cur = self.get(task_id)
        # An update that changes nothing must not reorder the list by touching
        # updated_at (Gate 11): an empty append is not a touch.
        if (title is None and status is None and notes is None
                and not str(append_note or "").strip()):
            return cur
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        new_title = " ".join(str(title).split())[:200] if title is not None else cur["title"]
        if not new_title:
            raise ValueError("a task needs a title")
        new_notes = cur["notes"] if notes is None else str(notes).strip()[:4000]
        if append_note and str(append_note).strip():
            new_notes = (new_notes.rstrip() + "\n" + str(append_note).strip()).strip()[:4000]
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET title=?, status=?, notes=?, updated_at=? WHERE id=?",
                (new_title, status or cur["status"], new_notes, _now(), task_id),
            )
            self._conn.commit()
        return self.get(task_id)

    # --- reads -------------------------------------------------------------------------

    def count_open(self, scope: Any = None) -> int:
        where, params = self._where(self._scope(scope))
        with self._lock:
            return int(self._conn.execute(
                "SELECT count(*) FROM tasks WHERE status != 'done'" + where, params).fetchone()[0])

    def get(self, task_id: int) -> dict[str, Any] | None:
        """One task by id, whatever its project: callers check ``project`` first."""
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list(self, status: str | None = None, include_done: bool = False, limit: int = 100,
             scope: Any = _ALL) -> list[dict[str, Any]]:
        """Open work first (active, blocked, open — in that order), newest-updated
        first within a status; done only when asked. ``scope``: the projects listed
        (DREAM-108) -- default the store's project and user-wide, None every project."""
        where, params = self._where(self._scope(scope))
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE status=?" + where
                    + " ORDER BY updated_at DESC, id DESC LIMIT ?",
                    (status, *params, limit)).fetchall()
            else:
                done = "" if include_done else " AND status != 'done'"
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE 1=1" + done + where
                    + " ORDER BY CASE status WHEN 'active' THEN 0 "
                    "WHEN 'blocked' THEN 1 WHEN 'open' THEN 2 ELSE 3 END, updated_at DESC, id DESC "
                    "LIMIT ?", (*params, limit)).fetchall()
            return [dict(r) for r in rows]

    # --- the file ----------------------------------------------------------------------

    def render_threads(self, limit: int = 40) -> str:
        """THREADS.md as text: generated, never hand-edited. It is one file for every
        project, so from DREAM-108 each task sits under its project's heading."""
        rows = self.list(limit=limit, scope=None)
        lines = ["# Open threads", "",
                 "Generated from the task store — `task_add` / `task_update` / `/tasks` change "
                 "it, not an editor.", ""]
        if not rows:
            lines.append("_(nothing open)_")
        elif self.count_open() > limit:
            lines.append(f"_(the {limit} most recently touched of {self.count_open()}; "
                         "`/tasks all` for the rest)_")
            lines.append("")
        groups: dict[str, list[dict[str, Any]]] = {}
        for t in rows:
            groups.setdefault(t.get("project") or "", []).append(t)
        for owner, tasks in groups.items():
            if owner or len(groups) > 1:
                heading = {USER: "User-wide", UNASSIGNED: "Unassigned", "": "Unassigned"}.get(
                    owner, f"Project {owner}")
                lines += ["", f"## {heading}", ""] if lines[-1] else [f"## {heading}", ""]
            for t in tasks:
                mark = {"active": "▶", "blocked": "■", "open": "○"}.get(t["status"], "·")
                line = f"- {mark} #{t['id']} {t['title']} [{t['status']}]"
                if t["notes"]:
                    first = t["notes"].splitlines()[0].strip()
                    line += f" — {first[:120]}"
                lines.append(line)
        return "\n".join(lines) + "\n"

    def write_threads(self) -> Any:
        p = config.THREADS_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.render_threads(), encoding="utf-8")
        return p

    # --- the one-time import of the hand-written file ---------------------------------

    GENERATED_HEADER = "Generated from the task store"

    def import_threads(self) -> int:
        """The hand-written THREADS.md becomes tasks, once. A `## heading` is a
        thread's title and the bullets under it are its notes; loose bullets are
        one task each. Runs only when the store is empty and the file was NOT
        generated, so it never re-imports what it wrote."""
        p = config.THREADS_FILE
        if self.list(include_done=True, limit=1, scope=None) or not p.is_file():
            return 0
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        if self.GENERATED_HEADER in text:
            return 0
        made = 0
        title: str | None = None
        notes: list[str] = []
        under_heading = False

        def flush() -> None:
            nonlocal title, notes, made
            if title:
                # A hand-written file says nothing about projects: the rows start as
                # legacy ('') and the startup migration files them by evidence.
                self.add(title, "\n".join(notes).strip(), project="")
                made += 1
            title, notes = None, []

        for raw in text.splitlines():
            s = raw.strip()
            if s.startswith("## "):
                flush()
                title, under_heading = s[3:].strip(), True
            elif s.startswith("# "):
                continue                            # the file's own title
            elif under_heading:
                if s:
                    notes.append(s)                 # a heading's bullets are its notes
            elif s.startswith(("- ", "* ")):        # a loose bullet is a task of its own
                flush()
                title = s[2:].strip()
        flush()
        return made

    def wake_lines(self, limit: int = 8, scope: Any = _ALL) -> list[str]:
        """The open work, bounded, for the wake-up context. A title cannot close
        the fence it sits in: its angle brackets become the lookalikes the
        stray-note fence already uses (Gate 11). Only the scope's tasks (DREAM-108)."""
        out = []
        for t in self.list(limit=limit, scope=scope):
            title = t["title"].replace("<", "‹").replace(">", "›")
            out.append(f"- #{t['id']} {title} [{t['status']}]")
        return out
