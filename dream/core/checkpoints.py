"""Undo for Dream's own edits — content-addressed file snapshots under ``var/``.

In auto mode Dream writes real files, and until now git was the only way back —
except that git is *the user's*, holding their work alongside Dream's. So the snapshots
live in Dream's own ``var/``: no commit, no stash, no index, no HEAD, nothing
inside a ``.git`` directory ever read or written. A workspace that isn't a repo
at all behaves identically, because none of this knows what a repo is.

What gets snapshotted is only what Dream is about to touch — the paths the
permission layer already names per write (``policy.classify_file_activity``) —
not the tree. A turn touches a handful of files, so a snapshot is a handful of
hashes; walking the whole tree before every prompt is not affordable, and most of
it would be bytes Dream never had any intention of changing.

Two observations per file are what make the rollback safe:

    take()  — the state BEFORE the write, which is what restore puts back
    seal()  — the state Dream LEFT, which is what restore expects to find

Without the second one a rollback cannot tell Dream's edit from an edit the user made
in their own editor afterwards, and "undo" quietly eats their work. A file in neither
state is reported and left alone; ``force`` is the only way past that, and it
says what it overwrote.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config

# Bounds. These follow the DREAM_* convention of config.py and belong there once
# something else needs them; they live here so the module owns its own limits.
LIMIT = int(os.environ.get("DREAM_CHECKPOINT_LIMIT", "100"))
# A snapshot has to stay cheap: above this a file is recorded as skipped rather
# than copied, because an undo that fills the disk is worse than no undo.
MAX_BYTES = int(os.environ.get("DREAM_CHECKPOINT_MAX_MB", "16")) * 1024 * 1024

# A current state that is deliberately not a sha, so it compares equal only to
# itself: a file that grew past the limit between take() and seal().
OVERSIZE = "oversize"


@dataclass
class RestoreReport:
    """Exactly what a restore did — and, just as importantly, didn't."""

    checkpoint_id: str
    label: str = ""
    restored: list[str] = field(default_factory=list)  # content written back
    deleted: list[str] = field(default_factory=list)  # created by the turn, removed
    unchanged: list[str] = field(default_factory=list)  # already at the snapshot state
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (path, why)

    def summary(self) -> str:
        lines = [f"Checkpoint {self.checkpoint_id}" + (f" — {self.label}" if self.label else "")]
        for label, paths in (("restored", self.restored), ("deleted", self.deleted)):
            for p in paths:
                lines.append(f"• {label}: {p}")
        for p in self.unchanged:
            lines.append(f"• unchanged (nothing to undo): {p}")
        for p, why in self.skipped:
            lines.append(f"• SKIPPED {p} — {why}")
        if not (self.restored or self.deleted or self.unchanged or self.skipped):
            lines.append("• nothing recorded in this checkpoint")
        return "\n".join(lines)


def _protected(p: Path) -> bool:
    """Anything inside a VCS directory. Dream's undo must not be able to rewrite
    the user's git state, so those paths are neither snapshotted nor restored."""
    return ".git" in p.parts


def _abs(raw: str | Path) -> Path:
    """Absolute path with the PARENT resolved — the leaf is left alone so a symlink
    stays visible as one instead of silently becoming its target."""
    p = Path(raw).expanduser()
    return p.parent.resolve() / p.name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CheckpointStore:
    """Snapshots of the files Dream touches, kept in Dream's own directory.

    ``session`` is recorded on each checkpoint so a listing can say which run made
    it; the store itself is shared, so a rollback still works after a restart.
    """

    def __init__(
        self,
        root: str | Path,
        session: str = "",
        limit: int = LIMIT,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.session = session
        self.limit = limit
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects.mkdir(parents=True, exist_ok=True)

    # --- taking ---------------------------------------------------------------

    def take(self, label: str, paths: Iterable[str | Path], into: str | None = None) -> str:
        """Snapshot what's on disk NOW for ``paths`` — call before the write lands.

        ``into`` adds to an existing checkpoint instead of opening a new one, so a
        turn that writes five files across five tool calls is still one undo step.
        A path already recorded keeps its first observation, which is the state
        before the turn began. Returns the checkpoint id to pass back as ``into``.
        """
        cp = None
        if into:
            try:
                cp = self._load(into)
            except KeyError:  # pruned or never existed — open a fresh one
                cp = None
        fresh = cp is None
        if cp is None:
            cp = {
                "id": self._next_id(),
                "label": label,
                "session": self.session,
                "created_at": _now(),
                "entries": [],
            }
        known = {e["path"] for e in cp["entries"]}
        for raw in paths:
            p = _abs(raw)
            if str(p) not in known:
                known.add(str(p))
                cp["entries"].append(self._observe(p))
        self._save(cp)
        if fresh:  # the bound holds once the new one is in, not before
            self.prune()
        return cp["id"]

    def seal(self, checkpoint_id: str) -> None:
        """Record what Dream LEFT — call once the write (or the turn) is finished.
        Restore compares against this to tell Dream's change from a later one of
        the user's. Idempotent: the newest observation wins."""
        cp = self._load(checkpoint_id)
        for e in cp["entries"]:
            if "skip" not in e:
                try:
                    e["after"] = self._current(Path(e["path"]))
                except OSError:
                    # A stale "after" would misattribute the user's later edit to
                    # Dream; no record at all makes restore refuse the file
                    # (with force as the stated way past) — the safe failure.
                    e.pop("after", None)
        self._save(cp)

    # --- reading --------------------------------------------------------------

    def list(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Checkpoints newest first, as display rows."""
        rows = []
        for cid in reversed(self._ids()):
            cp = self._load(cid)
            entries = cp["entries"]
            rows.append(
                {
                    "id": cp["id"],
                    "label": cp.get("label", ""),
                    "session": cp.get("session", ""),
                    "created_at": cp.get("created_at", ""),
                    "files": len(entries),
                    "paths": [e["path"] for e in entries],
                    "sealed": bool(entries) and all("after" in e for e in entries if "skip" not in e),
                }
            )
            if limit and len(rows) >= limit:
                break
        return rows

    def diff(self, checkpoint_id: str) -> str:
        """Unified diff from the snapshot to what's on disk now — what changed since
        this checkpoint was taken."""
        cp = self._load(checkpoint_id)
        out: list[str] = []
        for e in cp["entries"]:
            path = e["path"]
            if "skip" in e:
                out.append(f"# {path}: not snapshotted ({e['skip']})")
                continue
            old = self._text(self._blob_bytes(e.get("before")))
            new = self._text(self._read_now(Path(path)))
            if old is None or new is None:
                out.append(f"# {path}: binary content, not shown")
                continue
            if old == new:
                continue
            out.extend(
                difflib.unified_diff(
                    old.splitlines(),
                    new.splitlines(),
                    fromfile=f"{path} (checkpoint {cp['id']})",
                    tofile=f"{path} (now)",
                    lineterm="",
                )
            )
        return "\n".join(out) if out else f"# checkpoint {cp['id']}: nothing has changed since"

    # --- restoring ------------------------------------------------------------

    def restore(self, checkpoint_id: str, force: bool = False) -> RestoreReport:
        """Put the snapshotted files back. Never touches a file whose current
        contents the store can't account for — that content is the user's, not Dream's,
        and it is reported instead of overwritten unless ``force`` says otherwise."""
        cp = self._load(checkpoint_id)
        rep = RestoreReport(checkpoint_id=cp["id"], label=cp.get("label", ""))
        for e in cp["entries"]:
            path = e["path"]
            p = Path(path)
            if "skip" in e:
                rep.skipped.append((path, f"not snapshotted: {e['skip']}"))
                continue
            if _protected(p):
                rep.skipped.append((path, "inside .git — never restored"))
                continue
            if p.is_symlink() or (p.exists() and not p.is_file()):
                rep.skipped.append((path, "a symlink or directory sits there now"))
                continue
            before = e.get("before")
            # Per-file fault isolation: an unreadable or unwritable file is
            # reported and the rest of the checkpoint still restores.
            try:
                now = self._current(p)
                if now == before:
                    rep.unchanged.append(path)
                    continue
                if not force:
                    if "after" not in e:
                        rep.skipped.append(
                            (path, "no post-turn record — can't tell Dream's write from a "
                                   "later edit of yours; restore with force to override")
                        )
                        continue
                    if now != e["after"]:
                        rep.skipped.append(
                            (path, "changed since the snapshot — your edit would be lost; "
                                   "restore with force to override")
                        )
                        continue
                if before is None:
                    p.unlink()
                    rep.deleted.append(path)
                    continue
                data = self._blob_bytes(before)
                if data is None:
                    rep.skipped.append((path, "snapshot content missing from the store"))
                    continue
                self._put_back(p, data, e.get("mode"))
                rep.restored.append(path)
            except OSError as exc:
                rep.skipped.append(
                    (path, f"restore failed: {type(exc).__name__}: {exc}")
                )
        return rep

    # --- bounding -------------------------------------------------------------

    def prune(self, keep: int | None = None) -> int:
        """Drop the oldest checkpoints beyond ``keep``, then delete every blob
        nothing references any more. Returns how many checkpoints were dropped."""
        keep = self.limit if keep is None else keep
        ids = self._ids()
        if len(ids) <= keep:
            return 0
        for cid in ids[: len(ids) - keep]:
            (self.root / f"{cid}.json").unlink()
        live = {
            e["before"]
            for cid in self._ids()
            for e in self._load(cid)["entries"]
            if e.get("before")
        }
        for blob in self.objects.rglob("*"):
            if blob.is_file() and blob.name not in live:
                blob.unlink()
        return len(ids) - keep

    # --- storage --------------------------------------------------------------

    def _ids(self) -> list[str]:
        """Every checkpoint id, oldest first. Ids are zero-padded counters, so
        length-then-lexical ordering is numeric ordering without reading a file."""
        stems = [p.stem for p in self.root.glob("*.json")]
        return sorted(stems, key=lambda s: (len(s), s))

    def _next_id(self) -> str:
        # Assumes one writer per store (one session at a time). Two Dream processes
        # sharing var/ could claim the same id in the same instant and one of the
        # two checkpoints would be lost — an undo record, not any of the user's data.
        ids = self._ids()
        return f"{(int(ids[-1]) + 1) if ids else 1:04d}"

    def _path_for(self, checkpoint_id: str) -> Path:
        cid = str(checkpoint_id).strip()
        if cid.isdigit():  # "7" and "0007" name the same checkpoint
            cid = f"{int(cid):04d}"
        return self.root / f"{cid}.json"

    def _load(self, checkpoint_id: str) -> dict[str, Any]:
        f = self._path_for(checkpoint_id)
        if not f.exists():
            raise KeyError(f"no checkpoint '{checkpoint_id}'")
        return json.loads(f.read_text(encoding="utf-8"))

    def _save(self, cp: dict[str, Any]) -> None:
        f = self.root / f"{cp['id']}.json"
        tmp = f.with_name(f"{cp['id']}.json.tmp")
        tmp.write_text(json.dumps(cp), encoding="utf-8")
        os.replace(tmp, f)

    def _blob(self, sha: str) -> Path:
        return self.objects / sha[:2] / sha

    def _store_bytes(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        blob = self._blob(sha)
        if not blob.exists():  # content-addressed: an unchanged file costs nothing
            blob.parent.mkdir(parents=True, exist_ok=True)
            tmp = blob.with_name(f"{sha}.{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, blob)
        return sha

    def _blob_bytes(self, sha: str | None) -> bytes | None:
        if not sha or sha == OVERSIZE:
            return b"" if sha is None else None
        blob = self._blob(sha)
        return blob.read_bytes() if blob.exists() else None

    # --- observing ------------------------------------------------------------

    def _observe(self, p: Path) -> dict[str, Any]:
        """One entry: the file's current content stored, or why it couldn't be."""
        e: dict[str, Any] = {"path": str(p)}
        if _protected(p):
            e["skip"] = "inside .git"
            return e
        if p.is_symlink():
            e["skip"] = "symlink"
            return e
        if not p.exists():
            e["before"] = None  # created by the turn → restore removes it again
            return e
        if not p.is_file():
            e["skip"] = "not a regular file"
            return e
        # One unreadable file must cost only ITSELF its snapshot, never the
        # other files of the turn — take() saves what it could observe.
        try:
            st = p.stat()
            if st.st_size > self.max_bytes:
                e["skip"] = f"{st.st_size} bytes, over the {self.max_bytes} byte snapshot limit"
                return e
            e["before"] = self._store_bytes(p.read_bytes())
            e["mode"] = st.st_mode & 0o777
        except OSError as exc:
            e.pop("before", None)
            e["skip"] = f"unreadable: {type(exc).__name__}: {exc}"
        return e

    def _read_now(self, p: Path) -> bytes | None:
        """Current contents of the regular file at ``p``; None if there isn't one."""
        if p.is_symlink() or not p.is_file():
            return None
        return p.read_bytes()

    def _current(self, p: Path) -> str | None:
        """State of the regular file at ``p`` as a sha, or None for nothing there.
        A file that has grown past the snapshot limit reports OVERSIZE rather than
        being read into memory just to be compared."""
        if p.is_symlink() or not p.is_file():
            return None
        if p.stat().st_size > self.max_bytes:
            return OVERSIZE
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def _put_back(self, p: Path, data: bytes, mode: int | None) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.dream-restore")
        tmp.write_bytes(data)
        if mode is not None:
            tmp.chmod(mode)
        os.replace(tmp, p)

    @staticmethod
    def _text(data: bytes | None) -> str | None:
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None


def default_store(session: str = "") -> CheckpointStore:
    """The live store under Dream's ``var/``. Config is read at call time so a
    relocated DREAM_ROOT — or a test — lands in the right place."""
    return CheckpointStore(config.VAR_DIR / "checkpoints", session=session)
