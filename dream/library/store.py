"""The Library: durable files with stable identity and version history.

Dream's workspace is disposable — a path is whatever happened to be there when a
session ran. The Library is the opposite: a file put here keeps the same
``library_file_id`` for the rest of its life, across every rename, move, and edit, so
"update the plan I wrote last week" resolves to a thing rather than a guess at a path.

Three decisions worth stating, because they are what make the rest simple:

**Identity is the id, not the path.** Renaming, moving, or replacing contents never
mints a new id. An editor that writes its output to ``plan-v2.md`` is still producing
a new *version of the same item*; creating a second Library file there is how a
library turns into a junk drawer.

**Blobs are content-addressed.** Every version's bytes live at
``blobs/<sha[:2]>/<sha>``. Two versions with identical content cost one blob, and
``restore_version`` is a metadata write — it points a new version at an existing
blob rather than copying anything. It also means a version's bytes are immutable by
construction: you cannot edit v3 in place, only add v4.

**Deletes are soft.** ``delete`` clears the file from listings and search but keeps
its rows and blobs, because the whole promise of the Library is that putting
something in it is safe. ``purge`` is the separate, explicit, irreversible one.

This is a LOCAL store. The upstream spec this was modelled on describes a hosted
service — signed URLs, prepared uploads, byte transfers to a remote. None of that
applies to files already on this disk, so none of it is implemented: preparing an
upload to yourself is a round trip that can only add failure modes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

# Files above this are stored and served, but not indexed for content search —
# an FTS row per megabyte of a binary blob costs a lot and finds nothing.
FTS_MAX_BYTES = 2_000_000

# A `read` is meant to land in a context window, so it is bounded and says when it
# truncated. `materialize` is the route for bytes that do not fit.
READ_MAX_CHARS = 200_000

# Beyond this, `list` refuses rather than silently paginating something huge.
LIST_MAX = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_files (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    folder          TEXT NOT NULL DEFAULT '/',
    mime            TEXT NOT NULL DEFAULT '',
    current_version INTEGER NOT NULL DEFAULT 1,
    created         TEXT NOT NULL,
    updated         TEXT NOT NULL,
    deleted         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_library_folder ON library_files(folder, deleted);
CREATE INDEX IF NOT EXISTS idx_library_name   ON library_files(name);

CREATE TABLE IF NOT EXISTS library_versions (
    file_id    TEXT NOT NULL REFERENCES library_files(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created    TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (file_id, version)
);

CREATE VIRTUAL TABLE IF NOT EXISTS library_fts USING fts5(
    name, body, file_id UNINDEXED, tokenize='porter unicode61'
);

-- Which local path came out of which Library file. The xattr on the file says the
-- same thing and is checked first, but xattrs do not survive every copy, archive, or
-- filesystem — and losing the link is precisely the failure this table exists to
-- prevent. Keyed by resolved path: materializing the same item twice to one path is
-- one checkout, not two.
CREATE TABLE IF NOT EXISTS library_checkouts (
    local_path TEXT PRIMARY KEY,
    file_id    TEXT NOT NULL REFERENCES library_files(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL,
    created    TEXT NOT NULL,
    -- Whether the xattr write actually succeeded. On a filesystem that refuses
    -- xattrs the row is the ONLY signal there will ever be, and treating its
    -- absence as "something stripped the stamp" would make the path route
    -- permanently unusable there.
    stamped    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_checkout_file ON library_checkouts(file_id);

-- One row. Identifies THIS Library so a stamp from another one is refused rather
-- than resolved against a colliding local id.
CREATE TABLE IF NOT EXISTS library_meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""

# Where a materialized file carries its own identity. `user.` is the only namespace
# an unprivileged process may write on Linux.
#
# The value is JSON carrying THREE things, not just the id:
#   {"store": <uuid>, "file": <id>, "version": <n>}
# - store, because ids are only meaningful inside one Library. A file copied from
#   another machine would otherwise resolve against whatever local id collides.
# - version, because the checkout version is what makes the conflict guard honest, and
#   the path row does not travel when the file is moved. Carrying it on the file means
#   moving a checkout no longer silently upgrades the guard to "current".
XATTR_ID = "user.dream.library"
# The pre-JSON stamp, still read so files written by the first version resolve.
XATTR_ID_LEGACY = "user.dream.library_file_id"


class LibraryError(Exception):
    """A refusal the caller should see verbatim — not an internal fault."""


class VersionConflict(LibraryError):
    """The file moved under a caller holding an older version.

    Its own type because the recovery differs from every other error: re-read, merge,
    retry. Never resolved by dropping the guard.
    """


@dataclass(frozen=True)
class LibraryFile:
    id: str
    name: str
    folder: str
    mime: str
    version: int
    size_bytes: int
    created: str
    updated: str
    deleted: bool = False

    @property
    def path(self) -> str:
        """The canonical Library-relative path — folder + name, for display."""
        f = self.folder.rstrip("/")
        return f"{f}/{self.name}" if f else f"/{self.name}"

    def summary(self) -> str:
        return (f"{self.path}  (id {self.id} · v{self.version} · "
                f"{self.size_bytes:,} bytes · updated {self.updated[:19]})")


@dataclass
class SearchHit:
    file: LibraryFile
    snippet: str = ""


@dataclass
class FindMatch:
    file: LibraryFile
    line_no: int
    line: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_folder(folder: str | None) -> str:
    """Folders are absolute, slash-separated, and normalised to a single form.

    ``..`` is stripped rather than resolved: a folder is a label in this database, not
    a filesystem path, and letting one climb would let a caller address rows it
    shouldn't by spelling the label differently.
    """
    raw = (folder or "/").strip().replace("\\", "/")
    parts = [p for p in raw.split("/") if p and p not in (".", "..")]
    return "/" + "/".join(parts) if parts else "/"


def _safe_name(name: str) -> str:
    """The last segment of a name, so it can never carry a folder change.

    ``foo/bar.md`` becomes ``bar.md``. Note that this can collide with an existing
    ``bar.md`` — names are not unique in this store, ids are — so two files may share
    a display path. Resolution by name reports that as ambiguous rather than picking.
    """
    n = (name or "").strip().replace("\\", "/").split("/")[-1].strip()
    if not n or n in (".", ".."):
        raise LibraryError("a Library file needs a real name")
    return n


def _fts_query(text: str) -> str | None:
    """Free text → a safe FTS5 MATCH expression (prefix-OR of word tokens).

    Word characters, not ASCII. The index is built with unicode61, so an ASCII-only
    tokenizer here made every non-English name and body silently unfindable — and
    search is the one resolution route that does not need an id already in hand.

    Quotes are the only FTS metacharacter that can survive a word match; nothing else
    in a token can reach the MATCH parser.
    """
    tokens = re.findall(r"\w+", text or "", flags=re.UNICODE)
    return " OR ".join(f'"{t}"*' for t in tokens) if tokens else None


def _looks_textual(data: bytes) -> bool:
    """Whether these bytes can be handed to a model as text.

    The whole buffer is checked, not a prefix: a file with a plausible text header and
    a binary tail passed a 8 KiB sniff and was then decoded with ``errors="replace"``
    straight into a context window. Decoding is strict for the same reason — silently
    substituting U+FFFD turns "this is not text" into "here is some text".
    """
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


class Library:
    """A local, versioned, id-addressed file store."""

    def __init__(self, db_path: str | Path, blob_dir: str | Path):
        self.db_path = str(db_path)
        self.blob_dir = Path(blob_dir)
        self._lock = threading.RLock()
        self._lock_path = self.blob_dir.parent / ".library.lock"
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(library_checkouts)")}
            if "stamped" not in cols:
                self._conn.execute("ALTER TABLE library_checkouts "
                                   "ADD COLUMN stamped INTEGER NOT NULL DEFAULT 1")
            row = self._conn.execute(
                "SELECT v FROM library_meta WHERE k='store_id'").fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO library_meta (k,v) VALUES ('store_id',?)",
                    (uuid.uuid4().hex,))
                row = self._conn.execute(
                    "SELECT v FROM library_meta WHERE k='store_id'").fetchone()
            self.store_id = row["v"]
            self._conn.commit()

    @contextmanager
    def _blob_lock(self):
        """Serialise blob creation and removal across PROCESSES, not just threads.

        SQLite's WAL serialises database writers, and ``self._lock`` serialises threads
        in this interpreter. Neither covers the filesystem, and blob liveness is a
        filesystem fact: one process can decide a blob is unreferenced while another is
        committing the version that references it, and the unlink then orphans a live
        row. An advisory lock on a file next to the blobs is the only thing both
        processes can agree on.

        Held for the whole decide-and-act span — counting references and then unlinking
        outside the lock reintroduces exactly the gap it exists to close.
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self._lock_path, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()

    # --- blobs ---------------------------------------------------------------

    def _blob_path(self, sha: str) -> Path:
        return self.blob_dir / sha[:2] / sha

    def _put_blob(self, data: bytes) -> str:
        """Write bytes and return their hash.

        The CALLER must already hold the blob lock. Taking it here and releasing it
        before the version row is inserted was the whole bug: a writer observed the
        blob, dropped the lock, a purge decided that blob was unreferenced and
        unlinked it, and the writer then committed a row pointing at nothing. The lock
        has to span observing the bytes and making them live in SQL, which only the
        caller can do.
        """
        sha = hashlib.sha256(data).hexdigest()
        return self._put_blob_locked(data, sha, self._blob_path(sha))

    def _put_blob_locked(self, data: bytes, sha: str, p: Path) -> str:
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename: a crash mid-write must not leave a truncated blob
            # sitting at the name its own hash promises.
            # Unique per writer: a shared "<sha>.tmp" lets one process rename another
            # process's half-written file into place under a name that promises its
            # hash.
            tmp = p.with_name(f"{sha}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return sha

    def _get_blob(self, sha: str) -> bytes:
        """Bytes for a hash. Existence is not checked separately: between an
        ``is_file()`` and a read, a concurrent purge can remove the file, and the
        caller would then get a raw FileNotFoundError naming a blob path instead of
        this module's contract."""
        try:
            return self._blob_path(sha).read_bytes()
        except OSError as e:
            raise LibraryError(
                f"blob {sha[:12]} is missing from the Library store") from e

    # --- reading rows --------------------------------------------------------

    def _row_to_file(self, row: sqlite3.Row) -> LibraryFile:
        size = self._conn.execute(
            "SELECT size_bytes FROM library_versions WHERE file_id=? AND version=?",
            (row["id"], row["current_version"]),
        ).fetchone()
        return LibraryFile(
            id=row["id"], name=row["name"], folder=row["folder"], mime=row["mime"],
            version=row["current_version"], size_bytes=size["size_bytes"] if size else 0,
            created=row["created"], updated=row["updated"], deleted=bool(row["deleted"]),
        )

    def _file_at(self, file_id: str) -> LibraryFile:
        """Current row → LibraryFile, for use INSIDE an open transaction."""
        row = self._conn.execute(
            "SELECT * FROM library_files WHERE id=?", (file_id,)).fetchone()
        if row is None:
            raise LibraryError(f"no Library file with id '{file_id}'")
        return self._row_to_file(row)

    def get(self, file_id: str, *, include_deleted: bool = False) -> LibraryFile:
        row = self._conn.execute(
            "SELECT * FROM library_files WHERE id=?", (file_id,)
        ).fetchone()
        if row is None:
            raise LibraryError(f"no Library file with id '{file_id}'")
        if row["deleted"] and not include_deleted:
            raise LibraryError(f"Library file '{file_id}' is deleted (undelete to use it)")
        return self._row_to_file(row)

    def list(self, folder: str | None = None, limit: int = 50,
             offset: int = 0) -> list[LibraryFile]:
        if limit > LIST_MAX:
            raise LibraryError(f"limit is at most {LIST_MAX}")
        sql = "SELECT * FROM library_files WHERE deleted=0"
        args: list[Any] = []
        if folder is not None:
            sql += " AND folder=?"
            args.append(_norm_folder(folder))
        if limit <= 0:
            return []          # asking for none returns none, on every paginator
        # rowid is insertion order, so it breaks a same-second tie the right way.
        # Without it a model filing three artifacts in one tool round sees them
        # oldest-first under a label that says the opposite.
        sql += " ORDER BY updated DESC, rowid DESC LIMIT ? OFFSET ?"
        args += [limit, max(0, offset)]
        return [self._row_to_file(r) for r in self._conn.execute(sql, args)]

    def folders(self) -> list[str]:
        return [r["folder"] for r in self._conn.execute(
            "SELECT DISTINCT folder FROM library_files WHERE deleted=0 ORDER BY folder")]

    # --- search --------------------------------------------------------------

    def search(self, query: str, top_k: int = 5,
               title_only: bool = False) -> list[SearchHit]:
        """Rank files by an FTS match over name (+ body, unless title_only).

        A title-only search still goes through FTS rather than LIKE so that "quarterly
        revenue" matches "Quarterly Revenue.md" — stemming and tokenisation are the
        whole reason the index exists.
        """
        expr = _fts_query(query)
        if not expr or top_k <= 0:
            return []
        col = "name" if title_only else "library_fts"
        sql = (f"SELECT file_id, snippet(library_fts, 1, '', '', '…', 12) AS snip "
               f"FROM library_fts WHERE {col} MATCH ? ORDER BY rank LIMIT ?")
        out: list[SearchHit] = []
        for r in self._conn.execute(sql, (expr, min(top_k, 100))):
            try:
                f = self.get(r["file_id"])
            except LibraryError:
                continue  # deleted or vanished since indexing
            out.append(SearchHit(file=f, snippet=r["snip"] or ""))
        return out

    def find(self, file_ids: Sequence[str], pattern: str, *, regex: bool = False,
             max_matches: int = 100) -> list[FindMatch]:
        """Literal or regex matches inside specific files, with line numbers."""
        try:
            rx = re.compile(pattern if regex else re.escape(pattern), re.I)
        except re.error as e:
            raise LibraryError(f"bad pattern: {e}") from e
        out: list[FindMatch] = []
        if max_matches <= 0:
            return out
        for fid in file_ids:
            f = self.get(fid)
            text = self.read(fid)[0]
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    out.append(FindMatch(file=f, line_no=i, line=line.strip()[:300]))
                    if len(out) >= max_matches:
                        return out
        return out

    # --- content -------------------------------------------------------------

    def read(self, file_id: str, version: int | None = None) -> tuple[str, bool]:
        """(text, truncated). Binary content is refused rather than mangled."""
        f = self.get(file_id)
        v = version if version is not None else f.version
        row = self._conn.execute(
            "SELECT sha256 FROM library_versions WHERE file_id=? AND version=?",
            (file_id, v)).fetchone()
        if row is None:
            raise LibraryError(f"'{f.name}' has no version {v}")
        data = self._get_blob(row["sha256"])
        if not _looks_textual(data):
            raise LibraryError(
                f"'{f.name}' is binary — materialize it to a local path instead")
        text = data.decode("utf-8", errors="replace")
        if len(text) > READ_MAX_CHARS:
            return text[:READ_MAX_CHARS] + "\n…[truncated]", True
        return text, False

    def materialize(self, file_id: str, dest: str | Path,
                    version: int | None = None) -> Path:
        """Write a Library file's bytes to a local path and return it."""
        f = self.get(file_id)
        v = version if version is not None else f.version
        row = self._conn.execute(
            "SELECT sha256 FROM library_versions WHERE file_id=? AND version=?",
            (file_id, v)).fetchone()
        if row is None:
            raise LibraryError(f"'{f.name}' has no version {v}")
        dest = Path(dest).expanduser()
        if dest.is_dir():
            dest = dest / f.name
        # Writing through to the store's own files is not an edge case worth a stack
        # trace: `dest` comes from a model, and "materialize onto the database" is
        # store suicide reported as a copy. Resolved, so a symlink cannot alias in.
        try:
            resolved_dest = dest.resolve()
        except OSError:
            resolved_dest = dest
        blobs, db = self.blob_dir.resolve(), Path(self.db_path).resolve()
        if resolved_dest == db or str(resolved_dest).startswith(str(db) + "-") or \
                resolved_dest == blobs or blobs in resolved_dest.parents:
            raise LibraryError(
                f"refusing to materialize onto the Library's own storage ({dest}) — "
                f"choose a destination outside {blobs.parent}")
        dest = resolved_dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        checked_out = f if version is None else LibraryFile(**{**f.__dict__, "version": v})

        # Bytes and identity must land together. Copying to `dest` and then stamping it
        # is two operations on a shared name: two processes materializing different
        # files to one path can interleave and leave one file's bytes wearing the
        # other's stamp — which then resolves confidently to the wrong document.
        #
        # So build the whole thing beside the destination and move it into place. The
        # rename is atomic within a filesystem, and it carries the xattr with it, so
        # any observer sees either the old file or a fully-formed new one.
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.part")
        try:
            shutil.copyfile(self._blob_path(row["sha256"]), tmp)
            stamped = self._stamp_file_only(tmp, checked_out)
            os.replace(tmp, dest)
        except BaseException:
            tmp.unlink(missing_ok=True)   # never leave a half-materialized fragment
            raise
        # The path row is keyed to the FINAL name, so it is written after the move.
        self._record_checkout(dest, checked_out, stamped=stamped)
        return dest

    # --- identity that survives leaving the Library ---------------------------

    def _stamp(self, path: Path, f: LibraryFile) -> None:
        """Record that ``path`` is a checkout of ``f``.

        Written twice on purpose, with different failure modes:

        - the **xattr** travels with the file, so it survives a move or a rename, and
          a metadata-preserving copy carries it too (which is why it names the store
          and the version, not just the id);
        - the **row** is keyed to this exact path, so it survives an editor that
          strips xattrs — but NOT a move, and not a copy to a different path.

        Neither covers everything, and the honest statement is that some edits break
        both. An xattr failure is swallowed because a filesystem without xattr support
        must still be usable; a database failure is not, because losing the row
        silently is how a checkout becomes unresolvable later.
        """
        try:
            resolved = path.resolve()
        except OSError:
            return
        ok = self._stamp_file_only(resolved, f)
        self._record_checkout(resolved, f, stamped=ok)

    def _stamp_file_only(self, path: Path, f: LibraryFile) -> bool:
        """The half that lives on the file, so it can be applied before a rename.
        Returns whether it stuck."""
        stamp = json.dumps({"store": self.store_id, "file": f.id, "version": f.version})
        try:
            os.setxattr(path, XATTR_ID, stamp.encode("utf-8"))
            return True
        except OSError:
            return False   # no xattr support — the row is all there will be

    def _record_checkout(self, path: Path, f: LibraryFile, stamped: bool = True) -> None:
        """The half keyed to the path, written once the file is at its final name."""
        try:
            resolved = Path(path).resolve()
        except OSError:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO library_checkouts (local_path,file_id,version,created,stamped) "
                "VALUES (?,?,?,?,?) ON CONFLICT(local_path) DO UPDATE SET "
                "file_id=excluded.file_id, version=excluded.version, "
                "created=excluded.created, stamped=excluded.stamped",
                (str(resolved), f.id, f.version, _now(), 1 if stamped else 0))
            self._conn.commit()

    def _read_stamp(self, path: Path) -> tuple[str | None, int | None, bool]:
        """(file_id, checkout_version, is_from_another_library) from a file's xattr."""
        try:
            raw = os.getxattr(path, XATTR_ID).decode("utf-8", "replace")
        except OSError:
            try:  # a file stamped by the first version of this code
                legacy = os.getxattr(path, XATTR_ID_LEGACY).decode("ascii", "replace")
            except OSError:
                return None, None, False
            return (legacy or None), None, False
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            return None, None, False
        if data.get("store") and data["store"] != self.store_id:
            return None, None, True
        v = data.get("version")
        return (str(data.get("file")) or None,
                int(v) if isinstance(v, int) else None, False)

    def origin(self, path: str | Path) -> tuple[LibraryFile, int, str] | None:
        """Which Library file this path came from, at what version, and how sure we are.

        Confidence is returned because the two signals are not equally trustworthy, and
        the difference decides whether an automatic overwrite is safe:

        - ``"stamped"`` — the file itself carries the id in an extended attribute.
          Only the actual file object can answer this, so it cannot be faked by a
          different file arriving at the same path.
        - ``"conflicting"`` — the file's own stamp and the path record name *different*
          Library files. One of them is stale; there is no way to tell which from here.
        - ``"path-only"`` — nothing on the file says so; only a row keyed by its path
          does. A different file now sitting at that path looks identical to an editor
          that rewrote the original, so this cannot distinguish "your edit" from "an
          unrelated file someone dropped here". Measured on this machine: Dream's own
          ``write_file`` truncates in place and keeps the xattr, so the weak case is
          rare and worth stopping on rather than guessing through.

        Inode was tried as a tie-breaker and removed: inode numbers are recycled, and
        an unlink-then-create at the same path routinely reuses the number — so it
        reported a brand-new unrelated file as the one we wrote. A signal that is
        wrong in exactly the case it exists to catch is worse than no signal.

        None means the path genuinely has no history here.
        """
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return None
        fid, stamped_version, foreign = self._read_stamp(resolved)
        if foreign:
            # A stamp from a different Library. Its id means nothing here, and
            # resolving it against a local id that happens to collide would be the
            # worst possible answer: confident and wrong.
            return None
        confidence = "stamped" if fid else ""
        row = self._conn.execute(
            "SELECT file_id, version, stamped FROM library_checkouts WHERE local_path=?",
            (str(resolved),)).fetchone()
        if fid is None and row is not None:
            fid = row["file_id"]
            # A stamp that was never written is not a stamp that went missing. On a
            # filesystem without xattr support the row is the only signal there will
            # ever be, and refusing it there voids the guarantee on that whole mount.
            confidence = "unstampable" if not row["stamped"] else "path-only"
        if fid is None:
            return None
        try:
            f = self.get(fid, include_deleted=True)
        except LibraryError:
            # Points at something gone (purged, or copied from another machine's
            # Library). Stale, not usable — better than a confident wrong answer.
            return None
        # The checked-out version is only meaningful when the row is about the SAME
        # file the id names. When the two signals disagree — an xattr from one file on
        # a path checked out from another — taking the id from one and the version
        # from the other produces a guard number that belongs to neither, so the
        # conflict check silently compares against nonsense. Trust the file itself,
        # and stop calling the answer certain.
        if row is not None and row["file_id"] != fid:
            # Two signals naming different files. One is stale and there is no way to
            # tell which from here, so the answer stops claiming to be certain.
            confidence = "conflicting"
            version = stamped_version if stamped_version is not None else f.version
        elif stamped_version is not None:
            # The stamp travels with the file, so this survives a move; the row does
            # not. Prefer it.
            version = stamped_version
        elif row is not None:
            version = row["version"]
        else:
            version = f.version
        return f, version, confidence

    def checkouts(self, file_id: str) -> list[dict[str, Any]]:
        """Every local path known to have come from this Library file."""
        return [dict(r) for r in self._conn.execute(
            "SELECT local_path, version, created FROM library_checkouts "
            "WHERE file_id=? ORDER BY created DESC", (file_id,))]

    # --- writing -------------------------------------------------------------

    def _index(self, f: LibraryFile, data: bytes) -> None:
        self._conn.execute("DELETE FROM library_fts WHERE file_id=?", (f.id,))
        body = ""
        if len(data) <= FTS_MAX_BYTES and _looks_textual(data):
            body = data.decode("utf-8", errors="replace")
        self._conn.execute(
            "INSERT INTO library_fts (name, body, file_id) VALUES (?,?,?)",
            (f.name, body, f.id))

    def create(self, source: str | Path | bytes, *, name: str | None = None,
               folder: str = "/", mime: str = "", note: str = "") -> LibraryFile:
        """Add a new item. Returns it with the id it will keep forever."""
        src_path: Path | None = None
        if isinstance(source, (str, Path)) and not isinstance(source, bytes):
            src = Path(source).expanduser()
            if not src.is_file():
                raise LibraryError(f"no such local file: {src}")
            data = src.read_bytes()
            name = name or src.name
            src_path = src
        else:
            data = bytes(source)
            if not name:
                raise LibraryError("creating from bytes needs an explicit name")
        name = _safe_name(name)
        folder = _norm_folder(folder)
        fid = uuid.uuid4().hex
        ts = _now()
        with self._lock, self._blob_lock():
            sha = self._put_blob(data)
            self._conn.execute(
                "INSERT INTO library_files (id,name,folder,mime,current_version,"
                "created,updated,deleted) VALUES (?,?,?,?,1,?,?,0)",
                (fid, name, folder, mime, ts, ts))
            self._conn.execute(
                "INSERT INTO library_versions (file_id,version,sha256,size_bytes,"
                "created,note) VALUES (?,1,?,?,?,?)",
                (fid, sha, len(data), ts, note))
            f = self._file_at(fid)
            self._index(f, data)
            self._conn.commit()
        if src_path is not None:
            # Identity was only ever attached on the way OUT of the Library. A file
            # taken IN kept none, so once the id left the caller's context the same
            # local file resolved to nothing and a second create forked the document —
            # exactly the junk drawer this module exists to prevent.
            self._stamp(src_path, f)
        return f

    def replace(self, file_id: str, source: str | Path | bytes, *,
                expected_current_version: int | None = None,
                note: str = "", restamp: str | Path | None = None) -> LibraryFile:
        """New contents for an EXISTING item: same id, next version.

        ``expected_current_version`` is an optimistic guard. Passing one you actually
        read is how concurrent edits stay honest; the fix for a conflict is to re-read
        and merge, never to retry without the guard.

        ``restamp`` is the local file this content came from. Saving moves the Library
        forward, so a checkout that is not re-stamped now names a version that no
        longer exists — and the next save from that same file supplies the stale
        version as its guard and is refused, permanently. The path route worked exactly
        once before this existed, and the refusal it produced told the caller to
        re-read and try again, which could never succeed. An edit loop has to be a
        loop.
        """
        f = self.get(file_id)   # existence + a friendly early refusal; the real
                                # guard is the conditional UPDATE below
        if isinstance(source, (str, Path)) and not isinstance(source, bytes):
            src = Path(source).expanduser()
            if not src.is_file():
                raise LibraryError(f"no such local file: {src}")
            data = src.read_bytes()
        else:
            data = bytes(source)
        ts = _now()
        with self._lock, self._blob_lock():
            sha = self._put_blob(data)
            # Read-compare-write is not a guard if another writer can land between the
            # read and the write. The version bump is therefore CONDITIONAL on the row
            # still holding the version we checked, inside one immediate transaction —
            # so the losing writer is refused by the database rather than by luck.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "SELECT current_version FROM library_files WHERE id=? AND deleted=0",
                    (file_id,)).fetchone()
                if cur is None:
                    raise LibraryError(f"no Library file with id '{file_id}'")
                base = cur["current_version"]
                if expected_current_version is not None and expected_current_version != base:
                    raise VersionConflict(
                        f"'{f.name}' is at v{base}, not v{expected_current_version} — "
                        "re-read it, merge your change, and replace again")
                nv = base + 1
                self._conn.execute(
                    "INSERT INTO library_versions (file_id,version,sha256,size_bytes,"
                    "created,note) VALUES (?,?,?,?,?,?)",
                    (file_id, nv, sha, len(data), ts, note))
                changed = self._conn.execute(
                    "UPDATE library_files SET current_version=?, updated=? "
                    "WHERE id=? AND current_version=?", (nv, ts, file_id, base)).rowcount
                if changed != 1:
                    raise VersionConflict(
                        f"'{f.name}' changed while this write was in flight — "
                        "re-read it, merge your change, and replace again")
                # Index inside the same transaction. Committing the version and then
                # indexing leaves a window where search reports the old body, and two
                # writers interleaving their delete/insert pairs leave TWO fts rows for
                # one file — which makes library_resolve call a single document
                # "ambiguous" and refuse to write to it.
                out = self._file_at(file_id)
                self._index(out, data)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        if restamp is not None:
            # The saved file is now a checkout of the version we just created.
            self._stamp(Path(restamp), out)
        return out

    # --- organising ----------------------------------------------------------

    def rename(self, file_id: str, new_name: str) -> LibraryFile:
        f = self.get(file_id)
        name = _safe_name(new_name)
        with self._lock:
            self._conn.execute("UPDATE library_files SET name=?, updated=? WHERE id=?",
                               (name, _now(), file_id))
            self._conn.execute("UPDATE library_fts SET name=? WHERE file_id=?",
                               (name, file_id))
            self._conn.commit()
        return self.get(file_id)

    def move(self, file_id: str, new_folder: str) -> LibraryFile:
        self.get(file_id)
        with self._lock:
            self._conn.execute("UPDATE library_files SET folder=?, updated=? WHERE id=?",
                               (_norm_folder(new_folder), _now(), file_id))
            self._conn.commit()
        return self.get(file_id)

    def delete(self, file_id: str) -> LibraryFile:
        """Soft delete: out of listings and search, rows and blobs kept."""
        self.get(file_id)
        with self._lock:
            self._conn.execute("UPDATE library_files SET deleted=1, updated=? WHERE id=?",
                               (_now(), file_id))
            self._conn.execute("DELETE FROM library_fts WHERE file_id=?", (file_id,))
            self._conn.commit()
        return self.get(file_id, include_deleted=True)

    def undelete(self, file_id: str) -> LibraryFile:
        f = self.get(file_id, include_deleted=True)
        if not f.deleted:
            # "restored" for a file that was never gone is a false success — the
            # caller walks away believing something changed.
            raise LibraryError(f"'{f.name}' is not deleted — nothing to restore")
        with self._lock:
            self._conn.execute("UPDATE library_files SET deleted=0, updated=? WHERE id=?",
                               (_now(), file_id))
            self._conn.commit()
            out = self.get(file_id)
            row = self._conn.execute(
                "SELECT sha256 FROM library_versions WHERE file_id=? AND version=?",
                (file_id, out.version)).fetchone()
            if row:
                self._index(out, self._get_blob(row["sha256"]))
            self._conn.commit()
        return self.get(file_id)

    def deleted(self, limit: int = 50, offset: int = 0) -> list[LibraryFile]:
        """The trash: soft-deleted files, newest first.

        Without this, ``undelete`` is unreachable — it takes an id, and every other
        listing hides deleted rows, so "restore what I deleted yesterday" had no path
        once the id left the screen. A recoverable delete you cannot find is not
        recoverable.
        """
        if limit > LIST_MAX:
            raise LibraryError(f"limit is at most {LIST_MAX}")
        if limit <= 0:
            return []          # asking for none must return none, not one
        return [self._row_to_file(r) for r in self._conn.execute(
            "SELECT * FROM library_files WHERE deleted=1 ORDER BY updated DESC, rowid DESC "
            "LIMIT ? OFFSET ?", (limit, max(0, offset)))]

    def by_name(self, name: str, *, folder: str | None = None,
                include_deleted: bool = False) -> list[LibraryFile]:
        """Files whose name matches exactly, case-insensitively — no list cap.

        Resolving a typed name by scanning ``list(limit=200)`` had a cliff: the
        201st-oldest file could not be opened by name at all. A query has no cliff.
        Case-insensitive because ``Plan.md`` and ``plan.md`` are the same intent
        typed by a person, and names here are not unique anyway.
        """
        sql = "SELECT * FROM library_files WHERE LOWER(name)=LOWER(?)"
        args: list[Any] = [(name or "").strip()]
        if folder is not None:
            sql += " AND folder=?"; args.append(_norm_folder(folder))
        if not include_deleted:
            sql += " AND deleted=0"
        sql += " ORDER BY updated DESC, rowid DESC"
        return [self._row_to_file(r) for r in self._conn.execute(sql, args)]

    def find_by_id_prefix(self, prefix: str) -> list[str]:
        """Ids starting with ``prefix`` — the 8-character form every listing shows.

        Nobody retypes 32 hex characters. LIKE wildcards are escaped: a prefix is
        text, and letting ``%`` through would make ``undelete %`` restore whichever
        file sorted first.
        """
        prefix = (prefix or "").strip()
        if not prefix:
            return []
        pat = (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")) + "%"
        return [r["id"] for r in self._conn.execute(
            "SELECT id FROM library_files WHERE id LIKE ? ESCAPE '\\' ORDER BY rowid",
            (pat,))]

    def folder_counts(self) -> list[tuple[str, int]]:
        """Folders that hold at least one live file, with how many.

        ``folders()`` returned bare names and no tool ever called it, so a file could
        be filed into /plans and /plans could not then be discovered.
        """
        return [(r["folder"], r["n"]) for r in self._conn.execute(
            "SELECT folder, COUNT(*) AS n FROM library_files WHERE deleted=0 "
            "GROUP BY folder ORDER BY folder")]

    def purge(self, file_id: str) -> dict[str, Any]:
        """Permanently remove a file, its history, and any bytes nothing else needs.

        The irreversible counterpart to ``delete``, and the only operation here that
        destroys anything. Blobs are content-addressed and therefore shared: two files
        with identical content point at one blob, so a purge must reference-count
        before unlinking or it takes another file's bytes with it. Rows go first —
        an orphaned blob wastes disk, an orphaned row breaks reads.
        """
        f = self.get(file_id, include_deleted=True)
        shas = [r["sha256"] for r in self._conn.execute(
            "SELECT sha256 FROM library_versions WHERE file_id=?", (file_id,))]
        freed = 0
        failed: list[str] = []
        with self._lock, self._blob_lock():
            # Rows and blobs must be decided together. Committing the deletes first and
            # unlinking after leaves a window in which another writer creates a version
            # pointing at a blob this call is about to remove — and the reference count
            # that would have saved it was taken before that version existed. One
            # immediate transaction spans the count and the unlink, so a concurrent
            # writer either lands first (and its version is counted) or waits.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DELETE FROM library_versions WHERE file_id=?", (file_id,))
                self._conn.execute("DELETE FROM library_checkouts WHERE file_id=?", (file_id,))
                self._conn.execute("DELETE FROM library_fts WHERE file_id=?", (file_id,))
                self._conn.execute("DELETE FROM library_files WHERE id=?", (file_id,))
                orphans = []
                for sha in set(shas):
                    still = self._conn.execute(
                        "SELECT 1 FROM library_versions WHERE sha256=? LIMIT 1",
                        (sha,)).fetchone()
                    if not still:
                        orphans.append(sha)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            # Unlink AFTER the rows are committed, still holding the blob lock.
            #
            # Both orderings have a failure, and they are not equally bad. Unlinking
            # first means a crash before the commit rolls the rows back and leaves a
            # live file whose bytes are gone — silent data loss from an operation that
            # did not complete. Committing first means a crash leaves a blob nothing
            # references: wasted disk, nothing lost. The reason unlink-first looked
            # necessary was a concurrent writer claiming a condemned blob, and the
            # lock — held across both halves here, and across write-plus-insert in
            # create/replace — is what actually closes that, not the ordering.
            for sha in orphans:
                blob = self._blob_path(sha)
                try:
                    size = blob.stat().st_size
                    blob.unlink()
                    freed += size
                except FileNotFoundError:
                    pass          # already gone; nothing was lost
                except OSError:
                    # Reporting "bytes gone for good" when they are still on disk
                    # would be a false claim about a destructive operation.
                    failed.append(sha)
        return {"name": f.name, "path": f.path, "versions": len(shas),
                "bytes_freed": freed, "unremovable_blobs": failed}

    def versions(self, file_id: str) -> list[dict[str, Any]]:
        self.get(file_id, include_deleted=True)
        return [dict(r) for r in self._conn.execute(
            "SELECT version,size_bytes,created,note FROM library_versions "
            "WHERE file_id=? ORDER BY version DESC", (file_id,))]

    def restore_version(self, file_id: str, version: int) -> LibraryFile:
        """Bring an old version back as a NEW one.

        History is append-only: restoring v2 over v5 makes a v6 whose bytes are v2's,
        so the thing you undid is still there if the undo was itself a mistake.
        """
        f = self.get(file_id)
        row = self._conn.execute(
            "SELECT sha256, size_bytes FROM library_versions WHERE file_id=? AND version=?",
            (file_id, version)).fetchone()
        if row is None:
            raise LibraryError(f"'{f.name}' has no version {version}")
        ts = _now()
        with self._lock, self._blob_lock():
            # Same conditional bump as replace(): restoring is a write like any other,
            # and reading the version outside the transaction that uses it is the race
            # the guard exists to prevent.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "SELECT current_version FROM library_files WHERE id=? AND deleted=0",
                    (file_id,)).fetchone()
                if cur is None:
                    raise LibraryError(f"no Library file with id '{file_id}'")
                base = cur["current_version"]
                nv = base + 1
                self._conn.execute(
                    "INSERT INTO library_versions (file_id,version,sha256,size_bytes,"
                    "created,note) VALUES (?,?,?,?,?,?)",
                    (file_id, nv, row["sha256"], row["size_bytes"], ts,
                     f"restored from v{version}"))
                changed = self._conn.execute(
                    "UPDATE library_files SET current_version=?, updated=? "
                    "WHERE id=? AND current_version=?",
                    (nv, ts, file_id, base)).rowcount
                if changed != 1:
                    raise VersionConflict(
                        f"'{f.name}' changed while this restore was in flight — "
                        "re-read it and restore again")
                # Read the bytes BEFORE publishing. Fetching them afterwards meant a
                # missing blob raised after current_version had already moved: the
                # call reported failure while leaving the file pointing at an
                # unreadable version, and a retry stacked another ghost on top.
                blob = self._get_blob(row["sha256"])
                out = self._file_at(file_id)
                self._index(out, blob)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()
