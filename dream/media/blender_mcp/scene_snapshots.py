"""Numbered snapshots of the live Blender scene (DREAM-129), kept in the workspace.

Before every live-Blender call that can change the scene (execute_blender_code,
export_scene) the bridge saves a copy of the scene into .dream/blender-snapshots/ as
NNNN-HHMMSS.blend, with bpy.ops.wm.save_as_mainfile(copy=True), so the file the session
has open (and its one .blend1 backup) is untouched. The last KEEP snapshots stay;
older ones are deleted. index.json beside them lists each one's number, time and the
first line of the call it preceded. restore_scene_snapshot reopens one.

Runs inside the sandbox, in the bridge: standard library only. Blender itself writes
and reads the .blend files (addon.save_snapshot, addon.open_snapshot).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

FOLDER = Path(".dream") / "blender-snapshots"  # relative to the workspace (the bridge's working directory)
KEEP = 20  # snapshots kept; each is a compressed .blend (0.84 MB for the owner's 5.3 MB car scene)
_NAME = re.compile(r"^(\d{4,})-\d{6}\.blend$")


def folder(workspace: Path | None = None) -> Path:
    return (workspace or Path.cwd()) / FOLDER


def first_line(text: str, limit: int = 100) -> str:
    line = next((s.strip() for s in (text or "").splitlines() if s.strip()), "")
    return line if len(line) <= limit else line[:limit - 3] + "..."


def _numbers(where: Path) -> dict[int, Path]:
    found = {}
    if where.is_dir():
        for path in where.iterdir():
            if (match := _NAME.match(path.name)) and path.is_file():
                found[int(match.group(1))] = path
    return found


def read_index(where: Path) -> list[dict]:
    """The index entries whose file still exists, oldest first."""
    try:
        entries = json.loads((where / "index.json").read_text())
    except (OSError, ValueError):
        entries = []
    files = _numbers(where)
    if not isinstance(entries, list):
        entries = []
    kept = [e for e in entries if isinstance(e, dict) and files.get(e.get("number")) is not None
            and files[e["number"]].name == e.get("file")]
    known = {e["number"] for e in kept}
    kept += [{"number": n, "file": p.name, "time": "", "call": ""} for n, p in files.items() if n not in known]
    return sorted(kept, key=lambda e: e["number"])


def _write_index(where: Path, entries: list[dict]) -> None:
    temporary = where / "index.json.tmp"
    temporary.write_text(json.dumps(entries, indent=1))
    os.replace(temporary, where / "index.json")


def allocate(where: Path, now: float | None = None, after: int = 0) -> tuple[int, Path]:
    """The next snapshot's number (above every file, index entry and ``after``, the bridge's last number)
    and its path (the folder is created)."""
    where.mkdir(parents=True, exist_ok=True)
    try:  # numbers in the index count even when their files were deleted
        indexed = [e["number"] for e in json.loads((where / "index.json").read_text()) if isinstance(e["number"], int)]
    except (OSError, ValueError, TypeError, KeyError):
        indexed = []
    number = max([*_numbers(where), *indexed, after], default=0) + 1
    stamp = time.strftime("%H%M%S", time.localtime(now))
    return number, where / f"{number:04d}-{stamp}.blend"


def record(where: Path, number: int, path: Path, call: str, now: float | None = None,
           protect: int | None = None) -> list[dict]:
    """Add the saved snapshot to the index, delete all but the last KEEP (never ``protect``, the one
    being restored), and return the index."""
    entries = [e for e in read_index(where) if e["number"] != number]
    entries.append({"number": number, "file": path.name,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "call": first_line(call)})
    entries.sort(key=lambda e: e["number"])
    dropped = [e for e in entries[:-KEEP] if e["number"] != protect]
    for old in dropped:
        (where / old["file"]).unlink(missing_ok=True)
    entries = [e for e in entries if e not in dropped]
    _write_index(where, entries)
    return entries


def find(where: Path, number: int) -> Path | None:
    return _numbers(where).get(number)


def listing(where: Path) -> str:
    entries = read_index(where)
    if not entries:
        return "No scene snapshots yet. One is saved before every execute_blender_code or export_scene call."
    lines = [f"Scene snapshots in {FOLDER}/ (newest last; the last {KEEP} are kept). "
             "Each is the scene as it was BEFORE the call shown. restore_scene_snapshot(number) reopens one."]
    lines += [f"{e['number']:4d}  {e['time'] or '?'}  before: {e['call'] or '?'}" for e in entries]
    return "\n".join(lines)
