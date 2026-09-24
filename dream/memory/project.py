"""Project memory: one notebook per project, kept in Dream's own memory folder.

The owner's four tiers (2026-09-19): session notes, the plan (PLAN.md), this
per-project notebook, and global long-term memory. The notebook lives under
`memory/projects/<name>-<hash>/PROJECT.md` -- never inside the repo, so it can
never be committed and pushed with it -- and is loaded into the instructions of
every session that runs in that project.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from .. import config

PROJECTS_DIR_NAME = "projects"
NOTEBOOK = "PROJECT.md"
PROMPT_CHARS = 6000   # the notebook's share of the instructions
WORKSPACE_MARKER = ".workspace"

# DREAM-108: every memory item belongs to the project of the workspace it was written in
# (its key, below). Two values are not workspaces: USER is a deliberate user-wide item,
# visible in every project; UNASSIGNED is legacy data the startup migration could not
# place from evidence, shown only when asked for (all_projects / project="unassigned").
USER = "user"
UNASSIGNED = "unassigned"
_KEY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,120}")


def projects_dir() -> Path:
    return config.MEMORY_DIR / PROJECTS_DIR_NAME


def project_dir(workspace: Path | str) -> Path:
    ws = Path(workspace).expanduser().resolve()
    tag = hashlib.sha1(str(ws).encode()).hexdigest()[:6]
    name = "".join(c if c.isalnum() or c in "-_." else "-" for c in ws.name).strip("-").lower() or "project"
    return projects_dir() / f"{name}-{tag}"


def project_key(workspace: Path | str) -> str:
    """The one key a workspace has: the notebook folder's name, e.g. `deepseek---testing-a2a75a`."""
    return project_dir(workspace).name


def valid_owner(value: object) -> str:
    """A stored project value as read back from a file or a row: a key, USER, UNASSIGNED, or ''
    (legacy, not yet migrated) for anything else."""
    text = str(value or "").strip()
    return text if _KEY_RE.fullmatch(text) else ""


def register(workspace: Path | str) -> str:
    """Record which folder a key stands for (memory/projects/<key>/.workspace), so a project can
    be named by its folder and the migration knows its workspaces. Returns the key."""
    ws = Path(workspace).expanduser().resolve()
    marker = project_dir(ws) / WORKSPACE_MARKER
    try:
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != str(ws):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(ws), encoding="utf-8")
    except OSError:
        pass  # the key still works; only naming the project by its folder needs the marker
    return project_key(ws)


def known() -> dict[str, str]:
    """Every project Dream has recorded a workspace for: {key: workspace path}."""
    out: dict[str, str] = {}
    root = projects_dir()
    if not root.is_dir():
        return out
    for marker in sorted(root.glob(f"*/{WORKSPACE_MARKER}")):
        try:
            path = marker.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if path and valid_owner(marker.parent.name):
            out[marker.parent.name] = path
    return out


def resolve(name: object, extra: object = ()) -> str:
    """The key the model means by `name`: a key, a folder name ('Orbital'), a workspace path,
    'user' or 'unassigned'. `extra` adds keys only the database knows. Raises ValueError naming
    the known projects when the name matches none, or more than one."""
    text = " ".join(str(name or "").split())
    if not text:
        raise ValueError("a project name is empty")
    low = text.lower()
    if low in (USER, "user-wide", "global"):
        return USER
    if low == UNASSIGNED:
        return UNASSIGNED
    recorded = known()
    keys = set(recorded) | {k for k in extra if valid_owner(k) and k not in (USER, UNASSIGNED)}
    if low in keys:
        return low
    path_key = None
    if "/" in text or text.startswith("~"):
        try:
            path_key = project_key(text)
        except (RuntimeError, OSError, ValueError):   # ~user naming no user, a symlink loop: not a path
            path_key = None
    slug = "".join(c if c.isalnum() or c in "-_." else "-" for c in text).strip("-").lower()
    hits = sorted(k for k in keys if k == path_key or k.rsplit("-", 1)[0] == slug
                  or Path(recorded.get(k, "")).name.lower() == low)
    if len(hits) == 1:
        return hits[0]
    listing = ", ".join(f"{k} ({Path(recorded[k]).name})" if k in recorded else k for k in sorted(keys))
    if hits:
        raise ValueError(f"{text!r} matches several projects: {', '.join(hits)}; pass one key")
    raise ValueError(f"no project {text!r}. Known: {listing or 'none yet'}; or 'user' / 'unassigned'")


def tag(owner: str, current: str | None, *, cross: bool = False) -> str:
    """The label an item carries when it is not this project's own. In a cross-project listing
    the current project's items are marked too, so every line says whose it is."""
    owner = owner or UNASSIGNED
    if owner == current:
        return " [this project]" if cross else ""
    if owner == USER:
        return " [user-wide]"
    if owner == UNASSIGNED:
        return " [unassigned]"
    return f" [project: {owner}]"


def notebook(workspace: Path | str) -> Path:
    return project_dir(workspace) / NOTEBOOK


def is_project(workspace: Path | str | None) -> bool:
    """A real project folder: not home, not Dream's own directory."""
    if not workspace:
        return False
    ws = Path(workspace).expanduser().resolve()
    return ws not in (Path.home().resolve(), config.ROOT.resolve(), Path("/"))


def add_note(workspace: Path | str, text: str, *, replace: bool = False) -> Path:
    path = notebook(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / WORKSPACE_MARKER).write_text(str(Path(workspace).expanduser().resolve()), encoding="utf-8")
    if replace or not path.exists():
        head = f"# Project memory: {Path(workspace).expanduser().resolve().name}\n\n"
        body = text.strip() + "\n" if replace else f"- {datetime.now():%Y-%m-%d}: {text.strip()}\n"
        path.write_text(head + body, encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- {datetime.now():%Y-%m-%d}: {text.strip()}\n")
    return path


def prompt_section(workspace: Path | str | None) -> str:
    if not is_project(workspace):
        return ""
    path = notebook(workspace)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    if len(text) > PROMPT_CHARS:
        text = text[:PROMPT_CHARS] + f"\n[… the rest is in {path}]"
    return (f"\n**Project memory** (`{path}`, kept across sessions in this project; add with "
            f"`project_note`):\n{text}")
