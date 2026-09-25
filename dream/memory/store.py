"""SQLite-backed memory store with an FTS5 full-text index.

One store, four tables:

- ``sessions`` / ``turns``  — the raw record of what happened, in order.
- ``memories``              — long-term memory: semantic (facts), procedural
                              (playbooks), episodic (events, time-searchable).
- ``working_notes``         — short-term scratch, cleared/consolidated per session.

The store is synchronous and guarded by a lock so it is safe to call both from the
asyncio event-loop thread and from worker threads. Callers that care about not
blocking the loop should wrap calls in ``anyio.to_thread.run_sync``.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import config
from .project import UNASSIGNED, USER


MEM_TYPES = ("user", "feedback", "project", "reference")
MAX_RECALL_QUERY_CHARS = 256
MAX_RECALL_RESULTS = 20
MAX_ALTERNATIVE_QUERIES = 3

# A read that names no scope looks where the store's own session is: its project plus
# user-wide items (DREAM-108). ``scope=None`` is every project, for maintenance and for an
# explicit all_projects request.
_DEFAULT: Any = object()


class MemoryIndexError(RuntimeError):
    """The authoritative file was replaced, but SQLite could not commit its index."""


class ScopeError(ValueError):
    """A write named an item that another project owns (DREAM-108)."""

    def __init__(self, what: str, owner: str):
        self.owner = owner or UNASSIGNED
        where = ("an unassigned item" if self.owner == UNASSIGNED
                 else f"project {self.owner}")
        super().__init__(f"{what} belongs to {where}, not this project")


def default_type(kind: str, title: str = "", body: str = "") -> str:
    """The taxonomy value for a memory that did not name one. Derived, never
    guessed silently: an episodic memory is something that happened on the work
    (project), a playbook is reference, and a semantic fact is about the user (user)
    or about the world (reference) by the same cues curation already uses."""
    if kind == "episodic":
        return "project"
    if kind == "procedural":
        return "reference"
    from . import curation

    return "user" if curation.classify_facet(title, body) == curation.PERSONAL else "reference"


@dataclass
class MergeResult:
    """Outcome of merge_duplicates: how many were dropped, which survivors changed, and
    which slugs were deleted — enough for the caller to reconcile the markdown mirror."""

    count: int
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    summary     TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    turn_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    tool_name   TEXT,
    ts          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    slug           TEXT UNIQUE NOT NULL,
    kind           TEXT NOT NULL,               -- semantic | procedural
    title          TEXT NOT NULL,
    body           TEXT NOT NULL,
    tags           TEXT NOT NULL DEFAULT '',
    salience       REAL NOT NULL DEFAULT 1.0,
    source_session TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    access_count   INTEGER NOT NULL DEFAULT 0,
    last_accessed  TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);

CREATE TABLE IF NOT EXISTS working_notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    note         TEXT NOT NULL,
    ts           TEXT NOT NULL,
    consolidated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notes_session ON working_notes(session_id, id);

CREATE TABLE IF NOT EXISTS memory_links (
    from_slug TEXT NOT NULL,
    to_slug   TEXT NOT NULL,
    PRIMARY KEY (from_slug, to_slug)
);
CREATE INDEX IF NOT EXISTS idx_links_to ON memory_links(to_slug);

CREATE TABLE IF NOT EXISTS reconciled_pairs (
    slug_a TEXT NOT NULL,
    slug_b TEXT NOT NULL,
    ts     TEXT NOT NULL,
    PRIMARY KEY (slug_a, slug_b)
);
-- slug_a is covered by the primary key; slug_b needs its own index or the
-- "slug_a=? OR slug_b=?" cleanups scan the table.
CREATE INDEX IF NOT EXISTS idx_reconciled_b ON reconciled_pairs(slug_b);

CREATE TABLE IF NOT EXISTS tool_stats (
    name       TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    last_used  TEXT,
    use_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS memory_chunks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id  INTEGER NOT NULL,
    chunk_idx  INTEGER NOT NULL,
    embedding  BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_memory ON memory_chunks(memory_id);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title, body, tags, content='memories', content_rowid='id',
    tokenize='porter unicode61'
);

-- Phase 9b: past sessions are searchable by topic. Derived from ``turns``
-- the same way memories_fts is derived from memories.
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    content, content='turns', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, body, tags)
    VALUES (new.id, new.title, new.body, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, body, tags)
    VALUES ('delete', old.id, old.title, old.body, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, body, tags)
    VALUES ('delete', old.id, old.title, old.body, old.tags);
    INSERT INTO memories_fts(rowid, title, body, tags)
    VALUES (new.id, new.title, new.body, new.tags);
END;
"""


# How long an un-ended session must be silent before stray_notes assumes it crashed
# rather than that a second Dream instance is still using it. See stray_notes.
_CRASHED_SESSION_HOURS = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> bool:
    """ALTER TABLE ... ADD COLUMN, idempotent under a race: two Dream starts on one old
    database both see the column missing and both add it, and the second ALTER fails with
    "duplicate column name" (DREAM-108 gate). That error means the column is there, which
    is all either start wanted. Returns whether this call added it."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        return True
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return False
        raise


# PRAGMA user_version: 1 = turns_fts built (Phase 9b); 2 = the DREAM-108 one-time project
# migration has run, so a memory file without a project line is no longer legacy: it is
# one the owner wrote by hand, and it joins the project of the session that starts.
SCOPE_MIGRATED_VERSION = 2


def slugify(text: str, maxlen: int = 60) -> str:
    """kebab-case slug from arbitrary text."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:maxlen].rstrip("-")) or "note"


# Common words that shouldn't drive keyword matches (they pollute ranking, and the
# semantic layer handles meaning anyway).
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for", "from", "he",
    "her", "his", "i", "if", "in", "is", "it", "its", "me", "my", "no", "not", "of",
    "on", "or", "our", "so", "the", "to", "up", "us", "was", "we", "with", "you", "your",
    "this", "that", "these", "those", "then", "than", "will", "can", "has", "had", "have",
}


_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def parse_links(body: str) -> list[str]:
    """Extract [[wiki-links]] from a memory body as target slugs."""
    return [slugify(m.strip()) for m in _LINK_RE.findall(body)]


def _chunk_text(text: str, size: int) -> list[str]:
    """Split text into ~size-char chunks on word boundaries."""
    words = text.split()
    chunks: list[str] = []
    cur: list[str] = []
    n = 0
    for w in words:
        cur.append(w)
        n += len(w) + 1
        if n >= size:
            chunks.append(" ".join(cur))
            cur, n = [], 0
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def _fts_query(raw: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH expression (prefix-OR of content tokens)."""
    tokens = [
        t
        for t in re.findall(r"\w+", raw.lower())
        if len(t) > 2 and t not in _STOPWORDS
    ]
    if not tokens:
        return None
    # Prefix-match each token; OR them so any term can hit.
    return " OR ".join(f"{t}*" for t in tokens)


def _bounded_alternative_queries(query: str, alternatives: list[str]) -> list[str]:
    """Canonical caller-provided alternatives for bounded multi-query recall."""
    if type(query) is not str or not query.strip() or len(query) > MAX_RECALL_QUERY_CHARS:
        raise ValueError(f"query must be a non-empty string up to {MAX_RECALL_QUERY_CHARS} characters")
    if not isinstance(alternatives, list) or len(alternatives) > MAX_ALTERNATIVE_QUERIES:
        raise ValueError(f"alternative_queries must contain at most {MAX_ALTERNATIVE_QUERIES} strings")
    seen = {query.strip().casefold()}
    canonical: list[str] = []
    for value in alternatives:
        if type(value) is not str:
            raise ValueError("alternative_queries must contain only strings")
        alternative = value.strip()
        if not alternative or len(alternative) > MAX_RECALL_QUERY_CHARS:
            raise ValueError(
                f"alternative_queries must contain non-empty strings up to {MAX_RECALL_QUERY_CHARS} characters"
            )
        key = alternative.casefold()
        if key not in seen:
            canonical.append(alternative)
            seen.add(key)
    return canonical


class MemoryStore:
    def __init__(
        self, db_path: str | Path, embedder=None, reranker=None, readonly: bool = False
    ):
        self.db_path = str(db_path)
        self.embedder = embedder  # optional; enables semantic (vector) recall
        self.reranker = reranker  # optional; cross-encoder precision rerank
        self.readonly = readonly
        # The project key of the session this store serves (DREAM-108), set by the Engine:
        # the default owner of what is written and the default scope of what is read.
        # None (tests, eval, maintenance) keeps the unscoped behaviour.
        self.project: str | None = None
        self._lock = threading.RLock()
        # Lazy HNSW ANN index state (built from stored vectors above ANN_THRESHOLD).
        self._ann = None
        self._ann_labels: list[int] = []
        self._ann_dirty = True
        if readonly:
            # A measurement must not disturb the thing measured: no directory
            # creation, no schema, no migrations, no WAL flip. SQLite enforces it.
            self._conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._mem_cols = self._memory_columns()
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        # WAL already survives a process crash without fsyncing every commit; FULL
        # only adds durability across an OS/power failure, and costs ~2.8ms of fsync
        # per commit — paid on every turn logged. NORMAL is the standard WAL pairing.
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # WAL allows one writer: a second Dream instance mid-consolidation should make
        # us wait, not raise "database is locked".
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # Migration: the embedding column was added after v0.1.
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(memories)")}
            if "embedding" not in cols:
                add_column(self._conn, "memories", "embedding", "BLOB")
            # Migration: curation tags each memory's facet — 'personal' (about the user,
            # always in the room) vs 'reference' (world/tech, looked up on demand);
            # '' until classified.
            if "facet" not in cols:
                add_column(self._conn, "memories", "facet", "TEXT NOT NULL DEFAULT ''")
            # Migration: Phase 9 — markdown became the source of truth, so a row
            # carries the two fields the file's frontmatter needs and the database
            # did not have: the one-line ``description`` the MEMORY.md index shows,
            # and ``mem_type`` (user|feedback|project|reference), the taxonomy the
            # user asked for. ``kind`` stays beside it: episodic time-scoped recall
            # filters on it, and dropping it would lose what it means.
            if "description" not in cols:
                add_column(self._conn, "memories", "description", "TEXT NOT NULL DEFAULT ''")
            if "mem_type" not in cols:
                add_column(self._conn, "memories", "mem_type", "TEXT NOT NULL DEFAULT ''")
            # Migration: links gained an ``auto`` flag (0 = written as [[link]] in the
            # body, 1 = discovered by embedding similarity).
            lcols = {r[1] for r in self._conn.execute("PRAGMA table_info(memory_links)")}
            if "auto" not in lcols:
                add_column(self._conn, "memory_links", "auto", "INTEGER NOT NULL DEFAULT 0")
            # Migration: the FTS index gained porter stemming ("concentrating"
            # now matches "concentration" — golden-set recall@1 0.75 → 0.80).
            # A tokenizer is baked in at CREATE time, and the index is derived
            # data (content='memories'), so an old-tokenizer table is dropped
            # and rebuilt from the rows it mirrors — nothing to lose.
            fts_sql = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='memories_fts'"
            ).fetchone()
            if fts_sql and "porter" not in (fts_sql[0] or ""):
                self._conn.execute("DROP TABLE memories_fts")
                self._conn.executescript(_SCHEMA)  # recreates with the current tokenizer
                self._conn.execute(
                    "INSERT INTO memories_fts(memories_fts) VALUES('rebuild')"
                )
            # Migration: Phase 9b added turns_fts. A database from before has
            # turns the triggers never saw, so the index is rebuilt from the
            # table once. An external-content FTS table answers a plain scan
            # from the content table even when its index is empty, so
            # "is it empty?" cannot be asked of it; the database's user_version
            # records that the rebuild ran (1 = turns_fts built).
            if self._conn.execute("PRAGMA user_version").fetchone()[0] < 1:
                self._conn.execute("INSERT INTO turns_fts(turns_fts) VALUES('rebuild')")
                self._conn.execute("PRAGMA user_version = 1")
            # Migration: DREAM-108 -- every session, memory and working note names its
            # project: a workspace key (dream.memory.project), 'user' (user-wide) or
            # 'unassigned'. '' is a legacy row the startup migration has not placed yet;
            # it is never shown to a scoped read.
            for table in ("sessions", "memories", "working_notes"):
                tcols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if "project" not in tcols:
                    add_column(self._conn, table, "project", "TEXT NOT NULL DEFAULT ''")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project, started_at)")
            self._conn.commit()
        self._mem_cols = self._memory_columns()

    # --- scope (DREAM-108) ---------------------------------------------------------------

    def scope_migrated(self) -> bool:
        """Whether the DREAM-108 one-time project migration has run on this database."""
        with self._lock:
            return self._conn.execute("PRAGMA user_version").fetchone()[0] >= SCOPE_MIGRATED_VERSION

    def mark_scope_migrated(self) -> None:
        with self._lock:
            if self._conn.execute("PRAGMA user_version").fetchone()[0] < SCOPE_MIGRATED_VERSION:
                self._conn.execute(f"PRAGMA user_version = {SCOPE_MIGRATED_VERSION}")
                self._conn.commit()

    def default_scope(self) -> tuple[str, ...] | None:
        """Where a read looks when its caller names no scope: this store's project plus
        user-wide items, or everything for a store opened without a project."""
        return (self.project, USER) if self.project else None

    def _scope(self, scope: Any) -> tuple[str, ...] | None:
        if scope is _DEFAULT:
            return self.default_scope()
        return None if scope is None else tuple(scope)

    @staticmethod
    def _scope_sql(column: str, scope: tuple[str, ...] | None) -> tuple[str, list[Any]]:
        """`` AND <column> IN (...)`` for a scope; nothing for None (every project)."""
        if scope is None:
            return "", []
        if not scope:
            return " AND 0", []
        return f" AND COALESCE({column}, '') IN ({','.join('?' * len(scope))})", list(scope)

    def known_owners(self) -> set[str]:
        """Every project value the database holds, tasks included when that table exists."""
        tables = ["sessions", "memories", "working_notes"]
        with self._lock:
            if self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
                tables.append("tasks")
            out: set[str] = set()
            for table in tables:
                try:
                    out.update(r[0] for r in self._conn.execute(
                        f"SELECT DISTINCT project FROM {table}") if r[0])
                except sqlite3.OperationalError:
                    continue
            return out

    def _memory_columns(self) -> str:
        """Every ``memories`` column except the embedding BLOB (~1.5KB/row), which no
        caller outside the vector code reads. Read from the live table so a column
        added later can't silently drop out of recall results."""
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(memories)")]
        return ", ".join(c for c in cols if c != "embedding")

    def _embed_available(self) -> bool:
        return self.embedder is not None and self.embedder.available()

    def _store_embedding(
        self, slug: str, title: str, body: str, body_changed: bool = True
    ) -> bool:
        """Embed the memory (and, if long, its chunks), record its [[links]], and
        auto-link it to nearby memories. Runs the model outside the DB lock; marks
        the ANN index dirty. Returns whether a current vector was installed."""
        with self._lock:
            original = self._conn.execute(
                "SELECT id, title, body FROM memories WHERE slug=?", (slug,)
            ).fetchone()
            if original is None or (original["title"], original["body"]) != (title, body):
                return False
            self._store_links(slug, body, clear_auto=body_changed)
        if not self._embed_available():
            return False
        vec = self.embedder.embed(f"{title}\n{body}")
        chunk_vecs: list[tuple[int, bytes]] = []
        if vec is not None and len(body) > config.CHUNK_THRESHOLD:
            for i, chunk in enumerate(_chunk_text(body, config.CHUNK_SIZE)):
                cv = self.embedder.embed(chunk)
                if cv is not None:
                    chunk_vecs.append((i, cv.astype("float32").tobytes()))
        with self._lock:
            current = self._conn.execute(
                "SELECT id, title, body FROM memories WHERE slug=?", (slug,)
            ).fetchone()
            # A save/delete may finish while the optional model is working.
            # Never attach an old vector to newer content or a reused slug.
            if current is None or tuple(current) != tuple(original):
                return False
            if vec is not None:
                self._conn.execute(
                    "UPDATE memories SET embedding=? WHERE slug=?",
                    (vec.astype("float32").tobytes(), slug),
                )
                mid_row = self._conn.execute(
                    "SELECT id FROM memories WHERE slug=?", (slug,)
                ).fetchone()
                if mid_row is not None:
                    mid = mid_row[0]
                    self._conn.execute("DELETE FROM memory_chunks WHERE memory_id=?", (mid,))
                    self._conn.executemany(
                        "INSERT INTO memory_chunks(memory_id, chunk_idx, embedding) VALUES (?,?,?)",
                        [(mid, idx, blob) for idx, blob in chunk_vecs],
                    )
            self._conn.commit()
            self._ann_dirty = True
            if vec is not None:
                self._auto_link(slug, vec)
        return vec is not None

    def _store_links(
        self, from_slug: str, body: str, clear_auto: bool = False, *, commit: bool = True
    ) -> None:
        """Sync manual [[links]] from the body. When the body changed, stale auto
        links (both directions) are cleared HERE — before any fallible embedding
        work — so a transient embedding failure can't leave old-topic associations
        pointing at new-topic content. _auto_link re-inserts fresh ones on success."""
        targets = set(parse_links(body))
        with self._lock:
            self._conn.execute(
                "DELETE FROM memory_links WHERE from_slug=? AND auto=0", (from_slug,)
            )
            if clear_auto:
                self._conn.execute(
                    "DELETE FROM memory_links WHERE (from_slug=? OR to_slug=?) AND auto=1",
                    (from_slug, from_slug),
                )
            self._conn.executemany(
                "INSERT OR REPLACE INTO memory_links(from_slug, to_slug, auto) VALUES (?,?,0)",
                [(from_slug, t) for t in targets if t != from_slug],
            )
            if commit:
                self._conn.commit()

    def _auto_link(self, slug: str, vec) -> None:
        """Link a memory to its nearest semantic neighbors (cosine ≥ AUTOLINK_THRESHOLD)
        so the association graph grows without manual [[links]]. Refreshed on every
        upsert; never downgrades a manual link to auto."""
        import numpy as np

        with self._lock:
            rows = self._conn.execute(
                "SELECT slug, embedding FROM memories "
                "WHERE embedding IS NOT NULL AND slug != ?",
                (slug,),
            ).fetchall()
        if not rows:
            return
        try:
            mat = np.vstack([np.frombuffer(r["embedding"], dtype="float32") for r in rows])
            sims = mat @ np.asarray(vec, dtype="float32")
        except Exception:
            return
        order = sims.argsort()[::-1][: config.AUTOLINK_K]
        neighbors = [rows[int(i)]["slug"] for i in order if float(sims[int(i)]) >= config.AUTOLINK_THRESHOLD]
        with self._lock:
            # Stale-edge cleanup already happened in _store_links (pre-embedding, both
            # directions); this only installs the fresh neighbor set.
            self._conn.executemany(
                "INSERT OR IGNORE INTO memory_links(from_slug, to_slug, auto) VALUES (?,?,1)",
                [(slug, n) for n in neighbors],
            )
            self._conn.commit()

    def backfill_embeddings(self) -> int:
        """Embed any memories that don't have a vector yet (e.g. created before the
        embedder was available). Returns how many were embedded."""
        if not self._embed_available():
            return 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT slug, title, body FROM memories WHERE embedding IS NULL"
            ).fetchall()
        return sum(
            self._store_embedding(r["slug"], r["title"], r["body"])
            for r in rows
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- episodic: sessions & turns -----------------------------------------

    def start_session(
        self, session_id: str, title: str | None = None, project: str | None = None
    ) -> None:
        owner = project if project is not None else (self.project or "")
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO sessions(id, title, started_at, project) VALUES (?,?,?,?)",
                (session_id, title, _now(), owner),
            )
            self._conn.commit()
        # Memory files the owner wrote by hand since the last start belong to the project
        # of the session that starts now (DREAM-108; a no-op before the one-time migration).
        if owner:
            from . import longterm
            longterm.adopt_orphans(self, owner)

    def move_session(self, session_id: str, project: str) -> int | None:
        """Give a session, and the working notes it wrote, to another project. Returns how
        many notes moved, or None when there is no such session."""
        with self._lock:
            cur = self._conn.execute("UPDATE sessions SET project=? WHERE id=?", (project, session_id))
            if cur.rowcount == 0:
                self._conn.rollback()
                return None
            notes = self._conn.execute(
                "UPDATE working_notes SET project=? WHERE session_id=?", (project, session_id)).rowcount
            self._conn.commit()
            return notes

    def end_session(self, session_id: str, summary: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET ended_at=?, summary=COALESCE(?, summary) WHERE id=?",
                (_now(), summary, session_id),
            )
            self._conn.commit()

    def set_session_title(self, session_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title=? WHERE id=?", (title, session_id)
            )
            self._conn.commit()

    def add_turn(
        self, session_id: str, role: str, content: str, tool_name: str | None = None
    ) -> int:
        """Return this INSERT's ID only after its owned transaction commits."""
        with self._lock:
            if self._conn.in_transaction:
                raise RuntimeError("Cannot add a turn while a transaction is already active")
            self._conn.execute("BEGIN")
            try:
                cursor = self._conn.execute(
                    "INSERT INTO turns(session_id, role, content, tool_name, ts) "
                    "VALUES (?,?,?,?,?)",
                    (session_id, role, content, tool_name, _now()),
                )
                turn_id = cursor.lastrowid
                self._conn.execute(
                    "UPDATE sessions SET turn_count=turn_count+1 WHERE id=?", (session_id,)
                )
                self._conn.commit()
            except BaseException as exc:
                try:
                    self._conn.rollback()
                except BaseException as rollback_error:
                    exc.add_note(f"Turn transaction rollback failed: {rollback_error}")
                raise
            return turn_id

    def search_turns(
        self, query: str, limit: int = 10, *, exclude_session_id: str | None = None,
        scope: Any = _DEFAULT,
    ) -> list[dict[str, Any]]:
        """Topic search over the sessions in scope: the matching turn (id, session,
        role, time, an excerpt) with the session's title and project. Newest first
        among equal ranks, so a topic that recurs leads with the latest time."""
        q = _fts_query(query)
        if not q:
            return []
        where, params = self._scope_sql("s.project", self._scope(scope))
        with self._lock:
            rows = self._conn.execute(
                "SELECT t.id, t.session_id, t.role, t.ts, t.tool_name, "
                "snippet(turns_fts, 0, '', '', '…', 24) AS excerpt, "
                "s.title AS session_title, s.started_at, s.project AS project, "
                "bm25(turns_fts) AS rank "
                "FROM turns_fts JOIN turns t ON t.id = turns_fts.rowid "
                "LEFT JOIN sessions s ON s.id = t.session_id "
                "WHERE turns_fts MATCH ? AND (? IS NULL OR t.session_id != ?)" + where
                + " ORDER BY rank, t.id DESC LIMIT ?",
                (q, exclude_session_id, exclude_session_id, *params, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def turns_window(
        self, session_id: str, at: int | None = None, window: int = 6,
        *, through_turn: int | None = None,
    ) -> list[dict[str, Any]]:
        """The turns of one session around turn ``at`` (``window`` each side), or
        its first ``2*window+1`` turns when ``at`` is None.
        ``through_turn`` bounds every branch to an inclusive snapshot ceiling."""
        ceiling = through_turn if through_turn is not None else 9223372036854775807
        with self._lock:
            if at is None:
                rows = self._conn.execute(
                    "SELECT id, role, content, tool_name, ts FROM turns WHERE session_id=? "
                    "AND id <= ? ORDER BY id LIMIT ?", (session_id, ceiling, 2 * window + 1),
                ).fetchall()
            else:
                before = self._conn.execute(
                    "SELECT id, role, content, tool_name, ts FROM turns WHERE session_id=? "
                    "AND id <= ? AND id < ? ORDER BY id DESC LIMIT ?",
                    (session_id, ceiling, at, window),
                ).fetchall()[::-1]
                here = self._conn.execute(
                    "SELECT id, role, content, tool_name, ts FROM turns WHERE session_id=? "
                    "AND id <= ? AND id >= ? ORDER BY id LIMIT ?",
                    (session_id, ceiling, at, window + 1),
                ).fetchall()
                rows = [*before, *here]
            return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def latest_user_turn_id(self, session_id: str) -> int:
        """Latest durable request boundary, or zero before any user request."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM turns WHERE session_id=? AND role='user'",
                (session_id,),
            ).fetchone()
            return row[0]

    def recent_sessions(
        self, limit: int = 10, *, exclude_session_id: str | None = None, scope: Any = _DEFAULT,
    ) -> list[dict[str, Any]]:
        where, params = self._scope_sql("project", self._scope(scope))
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions WHERE (? IS NULL OR id != ?)" + where
                + " ORDER BY started_at DESC LIMIT ?",
                (exclude_session_id, exclude_session_id, *params, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def previous_session(self, current_id: str, scope: Any = _DEFAULT) -> dict[str, Any] | None:
        where, params = self._scope_sql("project", self._scope(scope))
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id!=? AND ended_at IS NOT NULL" + where
                + " ORDER BY started_at DESC LIMIT 1",
                (current_id, *params),
            ).fetchone()
            return dict(row) if row else None

    def session_turns(
        self, session_id: str, limit: int = 200, *, latest: bool = False
    ) -> list[dict[str, Any]]:
        """The session's turns, oldest first: its first `limit` rows, or with `latest` its LAST `limit` rows
        (DREAM-120: /resume shows where a long session ended, not where it began; the default keeps the session's
        opening for engine._transcript_digest)."""
        sql = ("SELECT role, content, tool_name, ts FROM turns "
               "WHERE session_id=? ORDER BY id LIMIT ?")
        if latest:   # the last rows, then back in order
            sql = ("SELECT role, content, tool_name, ts FROM (SELECT id, role, content, tool_name, ts FROM turns "
                   "WHERE session_id=? ORDER BY id DESC LIMIT ?) ORDER BY id")
        with self._lock:
            rows = self._conn.execute(sql, (session_id, limit)).fetchall()
            return [dict(r) for r in rows]

    # --- long-term memory ----------------------------------------------------

    def _free_slug(self, kind: str, title: str, owner: str | None = None) -> str:
        """Slug for an upsert that didn't name one. A derived slug is not an identity
        claim — unrelated memories can share a title, and any two titles that agree in
        their first 60 characters slugify identically — so only a row with the same
        kind AND title (AND project, when the write has one: DREAM-108) is the memory
        this call means to update. Anything else takes the next free ``-N`` suffix
        instead of being silently overwritten. An explicitly passed slug still means
        "update that memory": consolidation depends on it. Call with the lock held."""
        base = config.free_memory_name(slugify(title))
        slug, n = base, 1
        while True:
            row = self._conn.execute(
                "SELECT kind, title, project FROM memories WHERE slug=?", (slug,)
            ).fetchone()
            if row is None or (row["kind"] == kind and row["title"] == title
                               and (owner is None or (row["project"] or "") == owner)):
                return slug
            n += 1
            slug = f"{base}-{n}"

    def upsert_memory(
        self,
        kind: str,
        title: str,
        body: str,
        slug: str | None = None,
        tags: str = "",
        salience: float = 1.0,
        source_session: str | None = None,
        description: str = "",
        mem_type: str = "",
        *,
        persist_markdown: bool = False,
        embed: bool = True,
        created_at: str | None = None,
        project: str | None = None,
        reassign: bool = False,
    ) -> dict[str, Any]:
        """Upsert a searchable memory; optionally persist its file before commit.

        ``embed=False`` leaves semantic indexing to ``backfill_embeddings`` and
        never calls the embedder. Markdown persistence excludes the derived index.

        DREAM-108: a new memory belongs to ``project`` (default: this store's project;
        '' for a store without one). An update keeps the memory's project and is refused
        with ScopeError when that project is neither the write's nor user-wide, unless
        ``reassign`` -- then the memory moves to ``project`` (the file sync and a
        deliberate scope change do that).
        """
        if kind not in ("semantic", "procedural", "episodic"):
            raise ValueError(f"kind must be semantic|procedural|episodic, got {kind!r}")
        if mem_type and mem_type not in MEM_TYPES:
            raise ValueError(f"type must be one of {', '.join(MEM_TYPES)}, got {mem_type!r}")
        owner = project if project is not None else self.project
        now = _now()
        with self._lock, self._conn:
            if not slug:  # the remember tool forwards the model's empty string as-is
                slug = self._free_slug(kind, title, owner)
            else:
                # A caller-supplied slug is model-supplied: normalize it here so a
                # traversal slug can never reach the markdown mirror or an index.
                # slugify is idempotent, so an already-clean slug still updates in
                # place — which consolidation depends on. A reserved stem (Gate 9a:
                # remember(title="Identity") wrote identity.md, which the index
                # excludes, and the next boot dropped it) is suffixed here, once,
                # so the row, the file, and the index all agree on the name.
                slug = config.free_memory_name(slugify(slug))
            existing = self._conn.execute(
                "SELECT id, title, body, project FROM memories WHERE slug=?", (slug,)
            ).fetchone()
            if (existing is not None and owner is not None and not reassign
                    and (existing["project"] or "") not in (owner, USER)):
                raise ScopeError(f"memory {slug!r}", existing["project"] or "")
            body_changed = existing is None or existing["body"] != body
            content_changed = body_changed or existing["title"] != title
            if existing:
                self._conn.execute(
                    "UPDATE memories SET kind=?, title=?, body=?, tags=?, salience=?, "
                    "source_session=COALESCE(?, source_session), updated_at=?, "
                    "description=COALESCE(NULLIF(?, ''), description), "
                    "mem_type=COALESCE(NULLIF(?, ''), mem_type), project=? WHERE slug=?",
                    (kind, title, body, tags, salience, source_session, now,
                     description, mem_type,
                     owner if reassign and owner is not None else existing["project"], slug),
                )
                if existing["body"] != body:
                    # The content changed, so past conflict adjudications involving
                    # this memory no longer apply — let find_conflicts re-surface them.
                    self._conn.execute(
                        "DELETE FROM reconciled_pairs WHERE slug_a=? OR slug_b=?",
                        (slug, slug),
                    )
            else:
                self._conn.execute(
                    "INSERT INTO memories(slug, kind, title, body, tags, salience, "
                    "source_session, created_at, updated_at, description, mem_type, project) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (slug, kind, title, body, tags, salience, source_session, now, now,
                     description, mem_type or default_type(kind, title, body), owner or ""),
                )
            if content_changed and existing:
                self._conn.execute("UPDATE memories SET embedding=NULL WHERE slug=?", (slug,))
                self._conn.execute("DELETE FROM memory_chunks WHERE memory_id=?", (existing["id"],))
                self._ann_dirty = True
            if created_at:
                self._conn.execute(
                    "UPDATE memories SET created_at=? WHERE slug=?", (created_at, slug)
                )
            row = dict(
                self._conn.execute(
                    f"SELECT {self._mem_cols} FROM memories WHERE slug=?", (slug,)
                ).fetchone()
            )
            # Include cheap manual-link indexing in the same transaction.
            self._store_links(slug, body, clear_auto=content_changed, commit=False)
            path = None
            durability_error = None
            if persist_markdown:
                from . import longterm

                try:
                    path = longterm.write_markdown(row, index=False)
                except longterm.MemoryDurabilityError as exc:
                    # The authoritative file now contains this row. Keep its
                    # keyword index and vector invalidation aligned even though
                    # directory durability must still be reported as uncertain.
                    path = exc.path
                    durability_error = exc
            try:
                self._conn.commit()
            except sqlite3.Error as exc:
                if durability_error is not None:
                    raise MemoryIndexError(
                        f"{durability_error} The search index failed: {exc}. "
                        "Restart Dream to synchronize the current file."
                    ) from exc
                if path is not None:
                    raise MemoryIndexError(
                        f"Memory saved to {path}, but the search index failed: {exc}. "
                        "Restart Dream to synchronize the saved file."
                    ) from exc
                raise
        # Raise outside the transaction context so it cannot undo the index
        # commit for a file whose replacement already happened.
        if durability_error is not None:
            raise durability_error
        # Existing callers retain synchronous embedding; remember opts out.
        if embed:
            self._store_embedding(slug, title, body, body_changed=False)
        return row

    def get_memory(self, slug: str) -> dict[str, Any] | None:
        """One memory by its exact slug, whatever its project: the caller checks the
        ``project`` field before it shows or changes another project's memory."""
        with self._lock:
            row = self._conn.execute(
                f"SELECT {self._mem_cols} FROM memories WHERE slug=?", (slug,)
            ).fetchone()
            return dict(row) if row else None

    def move_memory(self, slug: str, project: str) -> dict[str, Any] | None:
        """Give a memory to another project (or user-wide); its content is unchanged. The
        caller rewrites the file so the move survives the next boot's sync."""
        with self._lock:
            cur = self._conn.execute("UPDATE memories SET project=? WHERE slug=?", (project, slug))
            self._conn.commit()
        return self.get_memory(slug) if cur.rowcount else None

    def delete_memory(self, slug: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT id FROM memories WHERE slug=?", (slug,)).fetchone()
            if row is None:
                return False
            mid = row[0]
            self._conn.execute("DELETE FROM memory_chunks WHERE memory_id=?", (mid,))
            self._conn.execute(
                "DELETE FROM memory_links WHERE from_slug=? OR to_slug=?", (slug, slug)
            )
            # Adjudications die with the memory — a future slug reuse must not
            # inherit this memory's reconciliation history.
            self._conn.execute(
                "DELETE FROM reconciled_pairs WHERE slug_a=? OR slug_b=?", (slug, slug)
            )
            self._conn.execute("DELETE FROM memories WHERE id=?", (mid,))
            self._conn.commit()
        self._ann_dirty = True
        return True

    # --- links (associative graph) ------------------------------------------

    def linked_slugs(self, slug: str) -> list[str]:
        """Neighbors of a memory in the [[link]] graph (both directions)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT to_slug AS s FROM memory_links WHERE from_slug=? "
                "UNION SELECT from_slug AS s FROM memory_links WHERE to_slug=?",
                (slug, slug),
            ).fetchall()
            return [r[0] for r in rows]

    def _fts_ids(
        self,
        query: str,
        kind: str | None,
        limit: int,
        since: str | None = None,
        until: str | None = None,
        scope: tuple[str, ...] | None = None,
    ) -> list[int]:
        match = _fts_query(query)
        if match is None:
            return []
        sql = (
            "SELECT m.id FROM memories_fts JOIN memories m ON m.id = memories_fts.rowid "
            "WHERE memories_fts MATCH ?"
        )
        params: list[Any] = [match]
        if kind:
            sql += " AND m.kind=?"
            params.append(kind)
        # Constraints must precede LIMIT: filtering after truncation lets strong
        # out-of-window matches crowd the real in-window answer out of the pool.
        # The project scope is one of them (DREAM-108).
        if since:
            sql += " AND m.created_at >= ?"
            params.append(since)
        if until:
            sql += " AND m.created_at <= ?"
            params.append(until)
        where, extra = self._scope_sql("m.project", scope)
        sql += where
        params.extend(extra)
        # bm25() is NEGATIVE for matches (more-negative = better), sorted ASC.
        # MULTIPLY by (0.5 + salience) so a higher-salience match pushes MORE
        # negative → ranks earlier. Dividing (the old bug) did the opposite,
        # burying the most-salient memories — the live keyword-recall order
        # since the vector/rerank stack was sidelined.
        sql += " ORDER BY bm25(memories_fts) * (0.5 + m.salience) ASC LIMIT ?"
        params.append(limit)
        try:
            return [r[0] for r in self._conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []

    def _load_vectors(
        self, kind: str | None, since: str | None = None, until: str | None = None,
        scope: tuple[str, ...] | None = None,
    ):
        """(labels=memory_ids, matrix) over main + chunk embeddings. One memory may
        contribute several rows (its chunks); callers aggregate by max score."""
        import numpy as np

        conds, params = ["m.embedding IS NOT NULL"], []
        if kind:
            conds.append("m.kind=?")
            params.append(kind)
        if since:
            conds.append("m.created_at >= ?")
            params.append(since)
        if until:
            conds.append("m.created_at <= ?")
            params.append(until)
        scoped, extra = self._scope_sql("m.project", scope)
        if scoped:
            conds.append(scoped[len(" AND "):])
            params.extend(extra)
        where = " AND ".join(conds)
        main = self._conn.execute(
            f"SELECT m.id, m.embedding FROM memories m WHERE {where}", params
        ).fetchall()
        chunk_where = where.replace("m.embedding IS NOT NULL", "1=1")
        chunks = self._conn.execute(
            "SELECT c.memory_id, c.embedding FROM memory_chunks c "
            f"JOIN memories m ON m.id=c.memory_id WHERE {chunk_where}",
            params,
        ).fetchall()
        ids: list[int] = []
        vecs = []
        for mid, emb in list(main) + list(chunks):
            ids.append(mid)
            vecs.append(np.frombuffer(emb, dtype="float32"))
        if not ids:
            return [], None
        try:
            return ids, np.vstack(vecs)
        except Exception:
            return [], None

    def _ensure_ann(self, ids: list[int], mat):
        """Build/reuse a true HNSW ANN index once there are enough vectors. Below the
        threshold, exact brute-force is used instead (and is faster). Kindless only."""
        if len(ids) <= config.ANN_THRESHOLD:
            return None
        if self._ann is not None and not self._ann_dirty and len(self._ann_labels) == len(ids):
            return self._ann
        try:
            import hnswlib
            import numpy as np

            idx = hnswlib.Index(space="cosine", dim=mat.shape[1])
            idx.init_index(max_elements=len(ids), ef_construction=200, M=16)
            idx.add_items(mat, np.arange(len(ids)))
            idx.set_ef(min(len(ids), 128))
            self._ann = idx
            self._ann_labels = list(ids)
            self._ann_dirty = False
            return idx
        except Exception:
            return None

    def _vector_ids(
        self,
        query_vec,
        kind: str | None,
        limit: int,
        since: str | None = None,
        until: str | None = None,
        scope: tuple[str, ...] | None = None,
    ) -> list[int]:
        if query_vec is None:
            return []
        ids, mat = self._load_vectors(kind, since, until, scope)
        if not ids:
            return []
        try:
            import numpy as np

            qv = np.asarray(query_vec, dtype="float32")
            best: dict[int, float] = {}
            # The shared ANN index is unfiltered; any constraint means brute force
            # over the (already filtered) candidate set. A project scope is one.
            ann = (self._ensure_ann(ids, mat)
                   if not (kind or since or until or scope is not None) else None)
            if ann is not None:
                # Map labels through the ids captured at index-build time — a reused
                # index must not depend on this call's row order matching that one's.
                built_ids = self._ann_labels
                labels, dists = ann.knn_query(qv, k=min(len(built_ids), limit * 4))
                for lbl, dist in zip(labels[0], dists[0]):
                    mid = built_ids[int(lbl)]
                    score = 1.0 - float(dist)  # hnswlib cosine distance = 1 - cosine
                    best[mid] = max(best.get(mid, -1e9), score)
            else:
                sims = mat @ qv
                for i, mid in enumerate(ids):
                    best[mid] = max(best.get(mid, -1e9), float(sims[i]))
            return sorted(best, key=lambda m: -best[m])[:limit]
        except Exception:
            return []

    @staticmethod
    def _rrf(*rankings: list[int], k: int = 60) -> list[int]:
        """Reciprocal Rank Fusion — blends multiple ranked id lists into one."""
        scores: dict[int, float] = {}
        for ranking in rankings:
            for rank, mid in enumerate(ranking):
                scores[mid] = scores.get(mid, 0.0) + 1.0 / (k + rank + 1)
        return sorted(scores, key=lambda m: -scores[m])

    def _rows_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        by_id = {
            r["id"]: dict(r)
            for r in self._conn.execute(
                f"SELECT {self._mem_cols} FROM memories WHERE id IN ({marks})", ids
            )
        }
        return [by_id[i] for i in ids if i in by_id]

    def _augment_with_links(
        self, ranked: list[dict[str, Any]], kind: str | None, limit: int,
        scope: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Blend 1-hop [[linked]] memories into the result set. Links hold up to
        LINK_RESERVE slots even when ranked hits could fill ``limit`` — associations
        surface alongside similarity, not only when recall comes up short. Never
        exceeds ``limit``, never violates the ``kind`` filter or the project scope (a
        link, even an auto-link, is no way into another project), and always keeps at
        least the top ranked hit."""
        primary = ranked[:limit]
        if not primary:
            return primary
        free = max(limit - len(primary), 0)
        cap = min(free + config.LINK_RESERVE, max(limit - 1, 0))
        if cap <= 0:
            return primary
        present = {r["slug"] for r in primary}
        extras: list[dict[str, Any]] = []
        for r in primary:
            if len(extras) >= cap:
                break
            for slug in self.linked_slugs(r["slug"]):
                if slug in present:
                    continue
                m = self.get_memory(slug)
                if m is None or (kind and m["kind"] != kind):
                    continue
                if scope is not None and (m.get("project") or "") not in scope:
                    continue
                m["via_link"] = True
                extras.append(m)
                present.add(slug)
                if len(extras) >= cap:
                    break
        if not extras:
            return primary
        keep = max(limit - len(extras), 1)
        return primary[:keep] + extras

    @staticmethod
    def _normalize_until(until: str | None) -> str | None:
        """A bare YYYY-MM-DD ``until`` means 'through that whole day'. Timestamps are
        ISO-8601 UTC strings, so plain string comparison orders them."""
        if until and len(until) == 10:
            return until + "T23:59:59"
        return until

    def search_memories(
        self,
        query: str,
        kind: str | None = None,
        limit: int = 8,
        since: str | None = None,
        until: str | None = None,
        bump: bool = True,
        scope: Any = _DEFAULT,
    ) -> list[dict[str, Any]]:
        """Hybrid recall: keyword (FTS5/BM25) fused with semantic (vector cosine, HNSW
        ANN at scale) via Reciprocal Rank Fusion, then re-scored by a cross-encoder
        reranker, then augmented with linked memories. Degrades gracefully at each stage.
        ``since``/``until`` (ISO dates) restrict results to a creation window — the way
        into episodic memory ("what happened last week"). Time-scoped recall skips link
        augmentation: an association isn't evidence something happened in the window.
        When nothing matches, the recency fallback is labeled ``via_recency`` so callers
        can present it as browsing, not as an answer to the query. ``scope`` is the
        projects searched (DREAM-108): default this store's project plus user-wide.
        """
        scope = self._scope(scope)
        until = self._normalize_until(until)
        time_scoped = bool(since or until)
        query_vec = self.embedder.embed(query) if self._embed_available() else None
        rerank_on = self.reranker is not None and self.reranker.available()
        pool = max(config.RERANK_CANDIDATES, limit) if rerank_on else limit * 3
        with self._lock:
            fts_ids = self._fts_ids(query, kind, pool, since, until, scope)
            vec_ids = self._vector_ids(query_vec, kind, pool, since, until, scope)
            if fts_ids and vec_ids:
                ordered = self._rrf(fts_ids, vec_ids)
            else:
                ordered = fts_ids or vec_ids
            if not ordered:
                fallback = self.recent_memories(
                    limit=limit, kind=kind, since=since, until=until, scope=scope
                )
                for r in fallback:
                    r["via_recency"] = True
                return fallback
            cand_rows = self._rows_by_ids(ordered[:pool])

        # Cross-encoder rerank outside the lock (model inference).
        if rerank_on and len(cand_rows) > 1:
            docs = [f"{r['title']}. {r['body']}" for r in cand_rows]
            scores = self.reranker.rerank(query, docs)
            if scores is not None and len(scores) == len(cand_rows):
                cand_rows = [cand_rows[i] for i in sorted(range(len(cand_rows)), key=lambda i: -scores[i])]

        if time_scoped:
            top = cand_rows[:limit]
        else:
            top = self._augment_with_links(cand_rows, kind, limit, scope)
        # Reads must not perturb what they measure: the eval harness and read-only
        # stores skip the access bump.
        if bump and not self.readonly:
            with self._lock:
                self._bump_access([r["id"] for r in top])
        return top

    def search_memories_with_alternatives(
        self,
        query: str,
        alternative_queries: list[str],
        kind: str | None = None,
        limit: int = 8,
        since: str | None = None,
        until: str | None = None,
        bump: bool = True,
        scope: Any = _DEFAULT,
    ) -> list[dict[str, Any]]:
        """Run caller-supplied query alternatives in one bounded recall operation.

        The primary query keeps the first result position. Alternatives contribute
        only direct candidates, never the recency browse fallback, and each
        alternative-only row records the query that found it.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_RECALL_RESULTS:
            raise ValueError(f"limit must be an integer from 1 to {MAX_RECALL_RESULTS}")
        alternative_queries = _bounded_alternative_queries(query, alternative_queries)
        scope = self._scope(scope)
        primary = self.search_memories(
            query, kind=kind, limit=limit, since=since, until=until, bump=False, scope=scope
        )
        primary_direct = [row for row in primary if not row.get("via_recency")]
        alternative_hits: list[dict[str, Any]] = []
        seen = {row["id"] for row in primary_direct}
        for alternative in alternative_queries:
            hits = self.search_memories(
                alternative, kind=kind, limit=limit, since=since, until=until, bump=False,
                scope=scope,
            )
            for row in hits:
                if row.get("via_recency") or row["id"] in seen:
                    continue
                row["matched_query"] = alternative
                alternative_hits.append(row)
                seen.add(row["id"])

        if primary_direct or alternative_hits:
            # Protect the primary top result, then expose alternative evidence before
            # broader primary matches consume the result budget.
            top = (primary_direct[:1] + alternative_hits + primary_direct[1:])[:limit]
        else:
            # Preserve the established primary-query browse fallback. Alternatives
            # never turn recent, unrelated rows into matches.
            top = primary[:limit]
        if bump and not self.readonly:
            with self._lock:
                self._bump_access([row["id"] for row in top])
        return top

    def recent_memories(
        self,
        limit: int = 8,
        kind: str | None = None,
        since: str | None = None,
        until: str | None = None,
        scope: Any = _DEFAULT,
    ) -> list[dict[str, Any]]:
        with self._lock:
            sql = f"SELECT {self._mem_cols} FROM memories WHERE 1=1"
            params: list[Any] = []
            if kind:
                sql += " AND kind=?"
                params.append(kind)
            if since:
                sql += " AND created_at >= ?"
                params.append(since)
            if until:
                sql += " AND created_at <= ?"
                params.append(self._normalize_until(until))
            where, extra = self._scope_sql("project", self._scope(scope))
            sql += where + " ORDER BY updated_at DESC LIMIT ?"
            params.extend(extra)
            params.append(limit)
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def top_memories(
        self, limit: int = 8, prefer_facet: str | None = "personal", scope: Any = _DEFAULT,
    ) -> list[dict[str, Any]]:
        """The wake-up set: salience blended with recency decay, so memories that haven't
        been touched in a while fade and fresh/frequently-used ones surface. ``prefer_facet``
        (default 'personal') is a *tie-breaker only*: at equal salience-decay score, that
        facet sorts ahead of the rest, so waking up leads with who the user is — without
        disturbing the primary salience ordering. Pass ``None`` to disable the bias.
        ``scope``: the projects it draws from (DREAM-108), default this store's."""
        where, params = self._scope_sql("project", self._scope(scope))
        with self._lock:
            rows = [
                dict(r)
                for r in self._conn.execute(
                    f"SELECT {self._mem_cols} FROM memories WHERE 1=1" + where, params
                ).fetchall()
            ]
        if not rows:
            return []
        now = datetime.now(timezone.utc)
        hl = max(config.SALIENCE_HALFLIFE_DAYS, 0.1)

        def score(m: dict[str, Any]) -> float:
            ts = m.get("last_accessed") or m.get("updated_at") or m.get("created_at")
            age_days = 0.0
            try:
                dt = datetime.fromisoformat(ts)
                age_days = max((now - dt).total_seconds() / 86400.0, 0.0)
            except Exception:
                pass
            decay = 0.5 ** (age_days / hl)
            return m.get("salience", 1.0) * decay + 0.15 * math.log(1 + m.get("access_count", 0))

        if prefer_facet:
            # Facet rank leads with the preferred facet, then unknown, then the rest —
            # but only as the second element of the sort key, so score still dominates.
            def facet_rank(m: dict[str, Any]) -> int:
                f = m.get("facet") or ""
                if f == prefer_facet:
                    return 2
                return 1 if f == "" else 0

            rows.sort(key=lambda m: (score(m), facet_rank(m)), reverse=True)
        else:
            rows.sort(key=score, reverse=True)
        return rows[:limit]

    def set_facet(self, slug: str, facet: str) -> None:
        """Tag a memory's curation facet ('personal' | 'reference' | ''). Cheap; set by
        ``curation.curate`` during consolidation, not by the agent."""
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET facet=? WHERE slug=?", (facet, slug)
            )
            self._conn.commit()

    def merge_duplicates(self, threshold: float | None = None) -> "MergeResult":
        """Merge near-duplicate memories of the same kind (cosine ≥ threshold).
        Duplicates are grouped into connected-component clusters; each cluster collapses ONCE into its highest-salience
        survivor, combining every member's unique body and unioning tags, and the rest are
        deleted. Cluster-then-collapse avoids the stale-snapshot corruption of pairwise
        merging. Returns which survivors changed and which slugs were dropped so the caller
        can keep the markdown mirror in sync. Only memories of ONE project merge with each
        other (DREAM-108): a duplicate in another project is that project's."""
        empty = MergeResult(0, [], [])
        if not self._embed_available():
            return empty
        threshold = config.MERGE_THRESHOLD if threshold is None else threshold
        # Episodic memories are excluded: two sessions can read near-identically and
        # still be distinct events — time individuates them, similarity doesn't.
        with self._lock:
            rows = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT id, slug, kind, title, body, tags, salience, embedding, project "
                    "FROM memories WHERE embedding IS NOT NULL AND kind != 'episodic'"
                ).fetchall()
            ]
        if len(rows) < 2:
            return empty
        clusters = self._similarity_clusters(rows, threshold)
        if clusters is None:
            return empty

        updated: list[str] = []
        deleted: list[str] = []
        for members in clusters:
            group = [rows[i] for i in members]
            survivor = max(group, key=lambda m: m["salience"])
            # Combine bodies (survivor first, then any member text not already present).
            body = survivor["body"]
            for m in group:
                if m["id"] == survivor["id"]:
                    continue
                piece = m["body"].strip()
                if piece and piece not in body:
                    body = body.rstrip() + "\n\n" + piece
            # Union tags, order-preserving.
            tag_order: list[str] = []
            for m in group:
                for t in (m.get("tags") or "").split(","):
                    t = t.strip()
                    if t and t not in tag_order:
                        tag_order.append(t)
            self.upsert_memory(
                kind=survivor["kind"], title=survivor["title"], body=body,
                slug=survivor["slug"], tags=",".join(tag_order), salience=survivor["salience"],
                project=survivor["project"] or "",
            )
            updated.append(survivor["slug"])
            for m in group:
                if m["id"] != survivor["id"]:
                    self.delete_memory(m["slug"])
                    deleted.append(m["slug"])
        return MergeResult(len(deleted), updated, deleted)

    @staticmethod
    def _similarity_clusters(
        rows: list[dict[str, Any]],
        low: float,
        high: float | None = None,
        skip_pairs: set[tuple[str, str]] | None = None,
    ) -> list[list[int]] | None:
        """Union-find over the same-kind, same-project similarity graph: an edge where
        ``low <= cosine`` (``< high`` if given) and the slug pair isn't in
        ``skip_pairs``. Returns clusters of row indexes with ≥2 members, or None if
        the embeddings can't be stacked. Rows must carry kind/slug/embedding; rows
        with a ``project`` never join another project's (DREAM-108)."""
        import numpy as np

        try:
            mat = np.vstack([np.frombuffer(r["embedding"], dtype="float32") for r in rows])
        except Exception:
            return None
        sims = mat @ mat.T
        n = len(rows)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # Only pairs inside the band can form an edge, and at these thresholds there
        # are very few — let numpy find them instead of walking all N^2 pairs in
        # Python (this runs twice per consolidation). The mask is widened by an
        # epsilon well above float32 resolution so it stays a superset of the exact
        # float64 test below, which still decides every candidate.
        eps = 1e-6
        mask = sims >= low - eps
        if high is not None:
            mask &= sims < high + eps
        for pair in np.argwhere(np.triu(mask, k=1)):
            i, j = int(pair[0]), int(pair[1])
            if rows[i]["kind"] != rows[j]["kind"]:
                continue
            if (rows[i].get("project") or "") != (rows[j].get("project") or ""):
                continue
            s = float(sims[i][j])
            if s < low or (high is not None and s >= high):
                continue
            if skip_pairs and tuple(sorted((rows[i]["slug"], rows[j]["slug"]))) in skip_pairs:
                continue
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        groups: dict[int, list[int]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)
        return [g for g in groups.values() if len(g) >= 2]

    def find_conflicts(self, scope: Any = _DEFAULT) -> list[list[dict[str, Any]]]:
        """Same-kind memory clusters in the 'suspiciously similar' band
        [RECONCILE_THRESHOLD, MERGE_THRESHOLD) — similar enough to overlap or
        contradict, not similar enough to auto-merge. Pairs already adjudicated
        (see mark_reconciled) are skipped, so each pair is surfaced at most once.
        Episodic memories are exempt for the same reason they're exempt from merging.
        Only memories in ``scope`` (default this store's project and user-wide) are
        compared, and only within one project (DREAM-108)."""
        if not self._embed_available():
            return []
        where, params = self._scope_sql("project", self._scope(scope))
        with self._lock:
            rows = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT id, slug, kind, title, body, tags, salience, updated_at, "
                    "embedding, project FROM memories "
                    "WHERE embedding IS NOT NULL AND kind != 'episodic'" + where, params
                ).fetchall()
            ]
            seen = {
                (r[0], r[1])
                for r in self._conn.execute("SELECT slug_a, slug_b FROM reconciled_pairs")
            }
        if len(rows) < 2:
            return []
        clusters = self._similarity_clusters(
            rows, config.RECONCILE_THRESHOLD, config.MERGE_THRESHOLD, skip_pairs=seen
        )
        if not clusters:
            return []
        out: list[list[dict[str, Any]]] = []
        for members in clusters:
            group = [{k: v for k, v in rows[i].items() if k != "embedding"} for i in members]
            group.sort(key=lambda m: m["updated_at"], reverse=True)
            out.append(group)
        return out

    def mark_reconciled(self, slugs: list[str]) -> None:
        """Record that every pair among ``slugs`` has been adjudicated, so
        find_conflicts never surfaces those pairs again. Pairs are only recorded
        between memories that still exist — a slug deleted during adjudication must
        not leave a record that a future slug reuse would inherit."""
        now = _now()
        with self._lock:
            alive = [
                s
                for s in dict.fromkeys(slugs)
                if self._conn.execute(
                    "SELECT 1 FROM memories WHERE slug=?", (s,)
                ).fetchone()
            ]
            pairs = [(a, b) for i, a in enumerate(alive) for b in alive[i + 1 :]]
            self._conn.executemany(
                "INSERT OR IGNORE INTO reconciled_pairs(slug_a, slug_b, ts) VALUES (?,?,?)",
                [(*sorted(p), now) for p in pairs],
            )
            self._conn.commit()

    def set_created_at(self, slug: str, created_at: str) -> None:
        """Override a memory's creation time — used when importing markdown that
        carries its original timestamp, and by the eval harness to build fixtures."""
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET created_at=? WHERE slug=?", (created_at, slug)
            )
            self._conn.commit()

    def all_memories(self, kind: str | None = None, scope: Any = _DEFAULT) -> list[dict[str, Any]]:
        """Every memory in ``scope`` (default this store's project and user-wide; None for
        every project -- the file sync and curation pass that)."""
        where, params = self._scope_sql("project", self._scope(scope))
        with self._lock:
            if kind:
                rows = self._conn.execute(
                    f"SELECT {self._mem_cols} FROM memories WHERE kind=?" + where
                    + " ORDER BY updated_at DESC",
                    (kind, *params),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT {self._mem_cols} FROM memories WHERE 1=1" + where
                    + " ORDER BY updated_at DESC", params
                ).fetchall()
            return [dict(r) for r in rows]

    def _bump_access(self, ids: list[int]) -> None:
        if not ids:
            return
        now = _now()
        self._conn.executemany(
            "UPDATE memories SET access_count=access_count+1, last_accessed=? WHERE id=?",
            [(now, i) for i in ids],
        )
        self._conn.commit()

    # --- working memory (short-term) ----------------------------------------

    def add_note(self, session_id: str, note: str) -> int:
        """Returns the note's id: a compaction stub names it so the model can
        ask for exactly that note back. A note belongs to its session's project
        (DREAM-108); without a session row, to this store's."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO working_notes(session_id, note, ts, project) VALUES (?,?,?, "
                "COALESCE((SELECT NULLIF(project, '') FROM sessions WHERE id=?), ?))",
                (session_id, note, _now(), session_id, self.project or ""),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def project_notes(
        self, query: str, scope: tuple[str, ...] | None, limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Notes of every session in ``scope`` (None: all projects), newest first,
        with each note's session and project -- the explicit cross-session read."""
        q = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where, params = self._scope_sql("project", scope)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, note, ts, project FROM working_notes "
                "WHERE note LIKE ? ESCAPE '\\'" + where + " ORDER BY id DESC LIMIT ?",
                (f"%{q}%", *params, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def search_notes(self, session_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Case-insensitive substring match over this session's notes, newest
        first — the way an elided tool result is brought back."""
        # A query is model text: its % and _ are characters to find, not wildcards.
        q = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, note, ts, consolidated FROM working_notes WHERE session_id=? "
                "AND note LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?", (session_id, f"%{q}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def session_notes(
        self, session_id: str, only_unconsolidated: bool = False
    ) -> list[dict[str, Any]]:
        with self._lock:
            sql = "SELECT id, note, ts, consolidated FROM working_notes WHERE session_id=?"
            if only_unconsolidated:
                sql += " AND consolidated=0"
            sql += " ORDER BY id"
            rows = self._conn.execute(sql, (session_id,)).fetchall()
            return [dict(r) for r in rows]

    def mark_notes_consolidated(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE working_notes SET consolidated=1 WHERE session_id=?",
                (session_id,),
            )
            self._conn.commit()

    def stray_notes(
        self, exclude_session: str, limit: int = 20, scope: Any = _DEFAULT,
    ) -> list[dict[str, Any]]:
        """Unconsolidated notes left behind by *earlier* sessions — the residue of a
        consolidation that failed, or of a session that died before one ran. The next
        session's dreaming picks these up so durable info is never silently stranded.
        Excludes the current session (its own notes go through the normal read_notes
        path) and any session that still looks alive. Only sessions in ``scope``
        (default this store's project): another project's notes wait for its own
        next dream (DREAM-108).

        An open session (ended_at IS NULL) either crashed or belongs to a second Dream
        instance running right now; nothing on disk distinguishes them but time. A live
        instance touches the DB every turn, so a session whose whole activity trail —
        last turn, last note, else its start — is older than _CRASHED_SESSION_HOURS is
        treated as dead. The threshold is deliberately generous: being early would
        promote a live instance's notes behind its back, while being late costs
        nothing but a delay."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=_CRASHED_SESSION_HOURS)
        ).isoformat(timespec="seconds")
        where, params = self._scope_sql("s.project", self._scope(scope))
        with self._lock:
            rows = self._conn.execute(
                "SELECT wn.id, wn.note, wn.ts, wn.session_id "
                "FROM working_notes wn JOIN sessions s ON wn.session_id = s.id "
                "WHERE wn.consolidated = 0 AND wn.session_id != ? "
                "AND (s.ended_at IS NOT NULL OR MAX("
                "  COALESCE((SELECT MAX(ts) FROM turns WHERE session_id = s.id), ''), "
                "  COALESCE((SELECT MAX(ts) FROM working_notes WHERE session_id = s.id), ''), "
                "  s.started_at) < ?)" + where
                + " ORDER BY wn.ts LIMIT ?",
                (exclude_session, cutoff, *params, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_notes_consolidated_ids(self, ids: list[int]) -> None:
        """Mark exactly these note rows consolidated — used to retire only the
        stray notes actually shown to (and folded by) a completed consolidation."""
        if not ids:
            return
        with self._lock:
            placeholders = ",".join("?" * len(ids))
            self._conn.execute(
                f"UPDATE working_notes SET consolidated=1 WHERE id IN ({placeholders})",
                list(ids),
            )
            self._conn.commit()

    # --- self-built tool provenance -------------------------------------------

    def note_tool_seen(self, name: str, refreshed_at: str | None = None) -> None:
        """Register a self-built tool at boot. If the tool's file is newer than the
        recorded first_seen, it was rewritten — its age resets, because a refreshed
        tool embodies current judgment, not six-months-ago judgment."""
        with self._lock:
            row = self._conn.execute(
                "SELECT first_seen FROM tool_stats WHERE name=?", (name,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO tool_stats(name, first_seen) VALUES (?,?)",
                    (name, refreshed_at or _now()),
                )
            elif refreshed_at and refreshed_at > row[0]:
                self._conn.execute(
                    "UPDATE tool_stats SET first_seen=? WHERE name=?", (refreshed_at, name)
                )
            self._conn.commit()

    def bump_tool_use(self, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO tool_stats(name, first_seen) VALUES (?,?)",
                (name, _now()),
            )
            self._conn.execute(
                "UPDATE tool_stats SET use_count=use_count+1, last_used=? WHERE name=?",
                (_now(), name),
            )
            self._conn.commit()

    def tool_stat(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tool_stats WHERE name=?", (name,)
            ).fetchone()
            return dict(row) if row else None

    # --- stats ---------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        with self._lock:
            def one(sql: str) -> int:
                return int(self._conn.execute(sql).fetchone()[0])

            return {
                "sessions": one("SELECT COUNT(*) FROM sessions"),
                "memories": one("SELECT COUNT(*) FROM memories"),
                "semantic": one("SELECT COUNT(*) FROM memories WHERE kind='semantic'"),
                "procedural": one(
                    "SELECT COUNT(*) FROM memories WHERE kind='procedural'"
                ),
                "episodic": one("SELECT COUNT(*) FROM memories WHERE kind='episodic'"),
            }
