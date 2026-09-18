#!/usr/bin/env python3
"""Validate Dream's portable project handoff, optionally against a source diff.

Standard library only. Does not import Dream, mutate Git, or load runtime data.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


HUB = "docs/project/"
CURRENT = HUB + "CURRENT.md"
MASTER = HUB + "MASTER_PLAN.md"
UPDATES = HUB + "updates/"
STATUSES = {
    "proposed", "planned", "active", "blocked", "deferred", "implemented",
    "verified", "accepted", "released", "cancelled",
}
EXCLUDED = {
    "data", "memory", "var", "artifacts", ".dream", ".remember",
    ".codebase-memory", ".venv", "dist", "build", "__pycache__",
}
SECTIONS = ("Request", "Changes", "Validation", "Unfinished work", "Next steps", "Files changed")


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def source_path(name: str) -> bool:
    p = PurePosixPath(name)
    return (bool(p.parts) and p.parts[0] not in EXCLUDED
            and "__pycache__" not in p.parts and not name.endswith((".pyc", ".log")))


def names(raw: bytes) -> set[str]:
    return {os.fsdecode(n) for n in raw.split(b"\0") if n and source_path(os.fsdecode(n))}


def snapshot(root: Path) -> dict:
    paths = names(git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"))
    files = {}
    for name in sorted(paths):
        p = root / name
        if p.is_symlink():
            files[name] = hashlib.sha256(("symlink:" + str(p.readlink())).encode()).hexdigest()
        elif p.is_file():
            with p.open("rb") as stream:
                files[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"version": 1, "root": str(root), "created_at": datetime.now(timezone.utc).isoformat(),
            "head": git(root, "rev-parse", "HEAD").decode().strip(), "files": files}


def metadata(body: str, key: str) -> str:
    found = re.findall(rf"^{re.escape(key)}: `([^`]+)`\s*$", body, re.M)
    if len(found) != 1:
        raise ValueError(f"expected one {key}: `value` field")
    return found[0]


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp needs an explicit timezone")
    if result > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("timestamp is in the future")
    return result


def work_ids(value: str) -> set[str]:
    values = {v.strip() for v in value.split(",")}
    if not values or any(not re.fullmatch(r"DREAM-\d{3,}", v) for v in values):
        raise ValueError("expected comma-separated DREAM-NNN work IDs")
    return values


def local_links(root: Path, document: Path, body: str) -> list[str]:
    errors = []
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    for raw in re.findall(r"\[[^\]\n]*\]\((<[^>]+>|[^)\n]+)\)", body):
        link = raw[1:-1] if raw.startswith("<") else raw.split(' "', 1)[0]
        url = urlsplit(link)
        if url.scheme or url.netloc or not url.path:
            continue
        target = (document.parent / unquote(url.path)).resolve()
        if not target.is_relative_to(root) or not target.exists():
            errors.append(f"{document.relative_to(root)}: broken/nonportable local link {link}")
    return errors


def validate(root: Path) -> tuple[list[str], dict[str, set[str]]]:
    errors: list[str] = []
    records: dict[str, set[str]] = {}
    required = ["START_HERE.md", "AGENTS.md", "README.md", "CLAUDE.md"] + [
        HUB + n for n in ("README.md", "CURRENT.md", "MASTER_PLAN.md", "WORKFLOW.md",
                         "DECISIONS.md", "HISTORY.md", "templates/update.md")]
    missing = [n for n in required if not (root / n).is_file()]
    if missing:
        return ["missing required file: " + n for n in missing], records
    plan = (root / MASTER).read_text()
    rows = re.findall(r"^\| (DREAM-\d{3,}) \| [^|]+ \| ([^|]+) \|", plan, re.M)
    statuses = {key: state.strip() for key, state in rows}
    if not rows or len(rows) != len(statuses):
        errors.append(f"{MASTER}: missing or duplicate work IDs")
    for key, state in statuses.items():
        if state not in STATUSES:
            errors.append(f"{MASTER}: invalid status {state!r} for {key}")
    dated = []
    for path in sorted((root / UPDATES).glob("*.md")):
        name = path.relative_to(root).as_posix()
        body = path.read_text()
        try:
            recorded = timestamp(metadata(body, "Recorded"))
            if not path.name.startswith(recorded.date().isoformat() + "-"):
                raise ValueError("filename must begin with the recorded local date and a hyphen")
            ids = work_ids(metadata(body, "Work items"))
            if ids - statuses.keys():
                raise ValueError("unknown work IDs: " + ", ".join(sorted(ids - statuses.keys())))
            if metadata(body, "Outcome") not in STATUSES:
                raise ValueError("invalid Outcome status")
            if not re.search(r"^Actor: \S.+$", body, re.M):
                raise ValueError("missing Actor attribution")
            sections = {}
            for section in SECTIONS:
                matches = re.findall(rf"^## {re.escape(section)}\n(.*?)(?=^## |\Z)", body, re.M | re.S)
                if len(matches) != 1 or not matches[0].strip():
                    raise ValueError(f"missing/empty/duplicate section: {section}")
                sections[section] = matches[0]
            paths = set(re.findall(r"^- `([^`]+)`\s*$", sections["Files changed"], re.M))
            if not paths:
                raise ValueError("Files changed needs exact backtick-wrapped path bullets")
            for entry in paths:
                p = PurePosixPath(entry)
                if p.is_absolute() or ".." in p.parts or p.as_posix() != entry or any(c in entry for c in "*?["):
                    raise ValueError(f"noncanonical/wildcard changed path: {entry}")
            records[name] = paths
            dated.append((recorded, name))
        except (ValueError, KeyError) as exc:
            errors.append(f"{name}: {exc}")
    if not dated:
        errors.append("no valid dated update records")
    current = (root / CURRENT).read_text()
    try:
        updated = timestamp(metadata(current, "Updated"))
        active = metadata(current, "Current work")
        ids = set() if active == "none" else work_ids(active)
        if ids != {key for key, state in statuses.items() if state == "active"}:
            raise ValueError("Current work must exactly match active items in MASTER_PLAN.md")
        handoffs = re.findall(r"^Latest handoff: \[[^\]]+\]\(([^)]+)\)\s*$", current, re.M)
        if len(handoffs) != 1:
            raise ValueError("expected one Latest handoff Markdown link")
        if dated:
            latest_date, latest_name = max(dated)
            if (root / CURRENT).parent.joinpath(handoffs[0]).resolve() != (root / latest_name).resolve():
                raise ValueError("Latest handoff must link to the newest recorded update")
            if updated < latest_date:
                raise ValueError("Updated timestamp predates the latest handoff")
    except ValueError as exc:
        errors.append(f"{CURRENT}: {exc}")
    documents = {root / name for name in required} | set((root / HUB).rglob("*.md"))
    for path in sorted(documents):
        errors.extend(local_links(root, path, path.read_text()))
    return errors, records


def check_diff(changed: set[str], previous: set[str], records: dict[str, set[str]]) -> list[str]:
    if not changed:
        return []
    errors = []
    old_records = {p for p in previous if p.startswith(UPDATES) and p.endswith(".md")}
    for path in sorted(changed & old_records):
        errors.append(f"{path}: previous handoffs are append-only; add a correction record")
    added = (set(records) - previous) & changed
    if CURRENT not in changed:
        errors.append(f"{CURRENT}: must be updated for this change")
    if not added:
        errors.append("this change needs a NEW dated update record")
    covered = set().union(*(records[p] for p in added))
    for path in sorted(changed - covered):
        errors.append(f"undocumented changed path: {path}")
    return errors


def ci_base() -> str | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    event = json.loads(Path(event_path).read_text())
    base = (event.get("pull_request") or {}).get("base", {}).get("sha") or event.get("before")
    if not base or set(base) == {"0"}:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base):
        raise ValueError("invalid CI comparison SHA")
    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("snapshot", help="save a pre-session source hash baseline outside the repo")
    start.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("check", help="validate tracking, optionally including a change comparison")
    group = check.add_mutually_exclusive_group()
    group.add_argument("--snapshot", type=Path)
    group.add_argument("--base")
    group.add_argument("--ci", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "snapshot":
            output = args.output.resolve()
            if output.is_relative_to(root):
                raise ValueError("save the session snapshot outside the repository")
            state = snapshot(root)
            with output.open("x") as stream:
                json.dump(state, stream, indent=2)
                stream.write("\n")
            print(f"Saved {len(state['files'])} source hashes to {output}. This is not a backup.")
            return 0
        errors, records = validate(root)
        changed = previous = None
        mode = "Structure only; no source-diff coverage checked."
        if args.snapshot:
            before = json.loads(args.snapshot.read_text())
            if before.get("version") != 1 or before.get("root") != str(root):
                raise ValueError("snapshot version or repository does not match")
            prior_files = before["files"]
            now = snapshot(root)["files"]
            previous = set(prior_files)
            changed = {n for n in previous | now.keys() if prior_files.get(n) != now.get(n)}
            mode = f"Session snapshot: {len(changed)} changed source paths checked."
        else:
            base = ci_base() if args.ci else args.base
            if base:
                # Resolve to a commit before using user input in further Git arguments.
                revision = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
                previous = names(git(root, "ls-tree", "-r", "--name-only", "-z", revision))
                changed = names(git(root, "diff", "--name-only", "--no-renames", "-z", revision, "--"))
                changed |= names(git(root, "ls-files", "--others", "--exclude-standard", "-z"))
                mode = f"Commit comparison: {len(changed)} changed source paths checked."
                if CURRENT not in previous:
                    changed = previous = None
                    mode = "Bootstrap: base predates tracking; structure only. Check session changes with --snapshot."
        if changed is not None and previous is not None:
            errors.extend(check_diff(changed, previous, records))
        if errors:
            for error in errors:
                print("ERROR: " + error, file=sys.stderr)
            print(f"Tracking failed ({len(errors)} findings). {mode}", file=sys.stderr)
            return 1
        print(f"Tracking passed ({len(records)} dated records). {mode}")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f"Tracking could not complete: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
