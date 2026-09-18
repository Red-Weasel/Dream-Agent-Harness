"""Private state snapshots and restore into an empty root.

SQLite databases use the online backup API; they are never copied with a live WAL
missing. Stop sessions for a cross-store snapshot: separate databases/files do
not share a transaction. A changing ordinary file aborts the snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATE_TREES = ("memory", "data", "var/loops", "var/checkpoints")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISREG(source.lstat().st_mode):
        raise ValueError(f"Snapshot requires regular files: {source}")
    with source.open("rb") as f:
        sqlite = f.read(16) == b"SQLite format 3\0"
    if sqlite:
        src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        dest = sqlite3.connect(target)
        try:
            src.backup(dest)
            if dest.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Database snapshot failed integrity check")
        finally:
            dest.close()
            src.close()
    else:
        before = source.stat()
        shutil.copyfile(source, target)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError(f"State changed during snapshot: {source}; stop active sessions and retry")
    target.chmod(0o600)


def create(source_root: Path, destination: Path) -> dict:
    source_root, destination = source_root.resolve(), destination.expanduser().absolute()
    if destination.exists():
        raise ValueError("Choose a new snapshot directory; existing snapshots are never overwritten")
    for tree in STATE_TREES:
        if destination.resolve().is_relative_to((source_root / tree).resolve()):
            raise ValueError("Snapshot destination must be outside the state trees")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dream-backup-", dir=destination.parent))
    try:
        files = []
        for tree in STATE_TREES:
            origin = source_root / tree
            if not origin.exists():
                continue
            if origin.is_symlink():
                raise ValueError(f"State tree is a symlink: {tree}")
            for folder, dirs, names in os.walk(origin, followlinks=False):
                for name in dirs:
                    if (Path(folder) / name).is_symlink():
                        raise ValueError("State contains a directory symlink; choose an explicit export instead")
                for name in sorted(names):
                    if name.endswith(("-wal", "-shm", ".lock")) or name == ".lock":
                        continue
                    source = Path(folder) / name
                    relative = source.relative_to(source_root)
                    target = staging / relative
                    _copy(source, target)
                    files.append({"path": relative.as_posix(), "bytes": target.stat().st_size, "sha256": _hash(target)})
        manifest = {"format": "dream-backup/v1", "created": datetime.now(timezone.utc).isoformat(),
                    "consistency": "SQLite snapshots; stop all sessions for consistency across stores",
                    "files": files}
        (staging / "backup.json").write_text(json.dumps(manifest, indent=2) + "\n")
        staging.rename(destination)
        return {"path": str(destination), "files": len(files), "bytes": sum(f["bytes"] for f in files)}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def restore(snapshot: Path, destination: Path) -> dict:
    snapshot, destination = snapshot.resolve(), destination.expanduser().absolute()
    if destination.exists():
        raise ValueError("Restore requires a new empty root; existing state is never overwritten")
    manifest_path = snapshot / "backup.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 50_000_000:
        raise ValueError("Invalid backup manifest")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("format") != "dream-backup/v1" or not isinstance(manifest.get("files"), list):
        raise ValueError("Unsupported backup format")
    seen = set()
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or entry["path"] in seen:
            raise ValueError("Invalid or duplicate backup path")
        if not any(relative.is_relative_to(Path(t)) for t in STATE_TREES):
            raise ValueError("Backup path is outside Dream state trees")
        source = snapshot / relative
        if source.is_symlink() or not source.resolve().is_relative_to(snapshot) or not source.is_file():
            raise ValueError("Invalid backup file")
        if source.stat().st_size != entry["bytes"] or _hash(source) != entry["sha256"]:
            raise ValueError(f"Backup integrity mismatch: {relative}")
        seen.add(entry["path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".dream-restore-", dir=destination.parent))
    try:
        for entry in manifest["files"]:
            target = staging / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snapshot / entry["path"], target)
            if _hash(target) != entry["sha256"]:
                raise ValueError("Backup changed during restore")
            target.chmod(0o600)
        staging.rename(destination)
        return {"path": str(destination), "files": len(seen)}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
