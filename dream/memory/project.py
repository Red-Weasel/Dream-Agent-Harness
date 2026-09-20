"""Project memory: one notebook per project, kept in Dream's own memory folder.

The owner's four tiers (2026-09-19): session notes, the plan (PLAN.md), this
per-project notebook, and global long-term memory. The notebook lives under
`memory/projects/<name>-<hash>/PROJECT.md` -- never inside the repo, so it can
never be committed and pushed with it -- and is loaded into the instructions of
every session that runs in that project.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from .. import config

PROJECTS_DIR_NAME = "projects"
NOTEBOOK = "PROJECT.md"
PROMPT_CHARS = 6000   # the notebook's share of the instructions


def projects_dir() -> Path:
    return config.MEMORY_DIR / PROJECTS_DIR_NAME


def project_dir(workspace: Path | str) -> Path:
    ws = Path(workspace).expanduser().resolve()
    tag = hashlib.sha1(str(ws).encode()).hexdigest()[:6]
    name = "".join(c if c.isalnum() or c in "-_." else "-" for c in ws.name).strip("-").lower() or "project"
    return projects_dir() / f"{name}-{tag}"


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
    (path.parent / ".workspace").write_text(str(Path(workspace).expanduser().resolve()), encoding="utf-8")
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
