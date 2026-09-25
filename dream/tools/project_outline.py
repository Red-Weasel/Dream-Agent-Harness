"""`project_outline` (DREAM-113, fix list #47): the workspace in one call.

Live 2026-09-24, on a project with a handoff doc, the model's first write came at +608 s, after
about twenty read_file calls in 80-160-line chunks. This tool answers "what is here" in one call of
at most OUTLINE_MAX_CHARS (about 2,000 tokens):

- With an Understand-Anything map (the owner ran the `understand` skill: `.ua/knowledge-graph.json`,
  and `.ua/domain-graph.json` when present; formats in skills/understand/references/schema.md): the
  project, its layers with their key files and top symbols, the domains with their flows, and the
  files changed or added since the map was built (its summaries describe the code as it was then).
- Without one: a quick scan -- the folders, and notable files with their line counts and their
  first docstring or heading line.

The walk is the Studio mirror's (DREAM-111, mirror.py): it never follows a symlink, skips hidden,
installed and vendored trees, and stops at WALK_MAX_ENTRIES entries or WALK_MAX_S seconds, and then
says the outline is partial. The text is cached per workspace until a file's mtime or size changes
(or the map's). It only reads: free in every mode (core/policy.py).
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import tool

from .context import ctx, err, in_thread, ok
# The Studio mirror's skip list (DREAM-111): installed and vendored trees, by name and by marker.
from .mirror import _INSTALL_MARKERS, _SKIP_DIRS

OUTLINE_MAX_CHARS = 8_000          # about 2,000 tokens
WALK_MAX_ENTRIES = 20_000
WALK_MAX_S = 1.0
_READ_MAX_S = 1.0                  # first lines and line counts, after the walk
_HEAD_BYTES = 4_096
_COUNT_MAX_BYTES = 4 * 1024 * 1024
_MAP_MAX_BYTES = 32 * 1024 * 1024
_LINE_CHARS = 110                  # a first line or a summary, clipped
_PATH_CHARS = 100                  # a path, clipped in the middle
_LIST_SHOWN, _LIST_CHARS = 8, 30   # a map's languages or frameworks: how many, and each one's length
_MISSES = 40                       # candidates in a row that no longer fit: stop reading
_CACHE_KEEP = 8
_CACHE: dict[str, tuple[str, str]] = {}   # workspace -> (digest of its files, outline)
_LOCK = threading.Lock()

_LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".html": "HTML", ".htm": "HTML", ".css": "CSS",
    ".md": "Markdown", ".json": "JSON", ".toml": "TOML", ".yaml": "YAML", ".yml": "YAML", ".rs": "Rust",
    ".go": "Go", ".c": "C", ".h": "C/C++ header", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
    ".sh": "Shell", ".bash": "Shell", ".lua": "Lua", ".sql": "SQL", ".vue": "Vue", ".svelte": "Svelte",
    ".glsl": "GLSL", ".wgsl": "WGSL", ".cu": "CUDA", ".r": "R", ".scala": "Scala", ".dart": "Dart",
}
_HASH_COMMENTS = {".sh", ".bash", ".zsh", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".rb", ".pl", ".r",
                  ".cmake", ".conf", ".env"}
_SLASH_COMMENTS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp",
                   ".rs", ".go", ".java", ".kt", ".swift", ".cs", ".css", ".scss", ".glsl", ".wgsl", ".cu",
                   ".php", ".scala", ".dart", ".vue", ".svelte"}
_TEXT_DOCS = {".md", ".markdown", ".rst", ".txt", ".adoc"}
_BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".glb", ".bin", ".zip", ".gz", ".tgz",
           ".xz", ".pdf", ".mp4", ".webm", ".mov", ".mp3", ".wav", ".ogg", ".woff", ".woff2", ".ttf", ".otf",
           ".so", ".o", ".a", ".dll", ".exe", ".pyc", ".db", ".sqlite", ".npy", ".pt", ".gguf", ".safetensors"}
_START = ("start_here", "readme", "claude", "agents", "plan")   # in reading order; a handoff goes after START_HERE
_TESTS = {"test", "tests", "spec", "specs", "__tests__", "fixtures", "testdata"}
_ENTRY = {"main.py", "__main__.py", "app.py", "server.py", "cli.py", "manage.py", "index.html", "index.js",
          "index.ts", "main.js", "main.ts", "main.rs", "lib.rs", "main.go", "main.cpp", "main.c"}
_MANIFESTS = {"pyproject.toml", "package.json", "cargo.toml", "go.mod", "cmakelists.txt", "makefile",
              "setup.py", "requirements.txt", "dockerfile", "docker-compose.yml", "compose.yaml"}
_FILE_NODES = {"file", "config", "document", "service", "pipeline", "schema", "resource"}
_SYMBOL_NODES = {"function", "class"}
_PY_DOC = re.compile(r"\A(?:[ \t]*(?:#[^\n]*)?\n)*[ \t]*[rRuU]?(\"\"\"|''')(.*?)(?:\1|\Z)", re.S)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>|<h1[^>]*>(.*?)</h1>", re.I | re.S)


def _clock() -> float:
    return time.monotonic()


# --- the tool --------------------------------------------------------------------------------------


@tool(
    "project_outline",
    "Outline this workspace in one call: its layers, key files and top symbols from the Understand "
    "map (.ua/) when one was made, else its folders and notable files with each file's line count "
    "and first docstring or heading line. Call it first in an unfamiliar project, before reading "
    "files one by one. At most about 2,000 tokens; cached until a file changes; reads only (free).",
    {"type": "object", "properties": {}, "required": []},
)
async def project_outline(args: dict[str, Any]) -> dict[str, Any]:
    root = Path(ctx().workspace)
    try:
        return ok(await in_thread(outline, root))
    except OSError as e:
        return err(f"Could not outline {root}: {type(e).__name__}: {e}")


def outline(root: Path) -> str:
    """The outline of `root`: the cached text while no file's mtime or size changed, else a new one."""
    root = root.resolve()
    walk = _walk(root)
    graph, domains, why = _find_map(root)
    digest = _digest(walk, graph, domains, why)
    with _LOCK:
        cached = _CACHE.get(str(root))
    if cached is not None and cached[0] == digest:
        return cached[1]
    text = _render(root, walk, graph, domains, why)
    with _LOCK:
        _CACHE.pop(str(root), None)
        _CACHE[str(root)] = (digest, text)
        while len(_CACHE) > _CACHE_KEEP:
            _CACHE.pop(next(iter(_CACHE)))
    return text


# --- the walk: mirror.py's rules, breadth first ---------------------------------------------------------


@dataclass
class _Walk:
    files: list[tuple[str, int, int]] = field(default_factory=list)   # (relative path, mtime_ns, size)
    dirs: list[str] = field(default_factory=list)                      # folders scanned
    unscanned: list[str] = field(default_factory=list)                 # folders found after a cap
    capped: str = ""                                                   # "entries" | "time" | ""
    seen: int = 0


def _walk(root: Path) -> _Walk:
    """Every regular file under `root`, shallow first. Never follows a symlink (a linked file or folder
    may lead out of the workspace), skips hidden, installed and vendored trees, and stops at
    WALK_MAX_ENTRIES entries or WALK_MAX_S seconds with what it found so far."""
    walk, start = _Walk(), _clock()
    queue: deque[str] = deque([""])
    while queue and not walk.capped:
        rel = queue.popleft()
        files: list[tuple[str, int, int]] = []
        subdirs: list[str] = []
        installed = False
        try:
            with os.scandir(root / rel if rel else root) as entries:
                for entry in entries:
                    walk.seen += 1
                    if walk.seen > WALK_MAX_ENTRIES:
                        walk.capped = "entries"
                    elif _clock() - start > WALK_MAX_S:
                        walk.capped = "time"
                    if walk.capped:
                        walk.seen -= 1
                        break
                    name = entry.name
                    if name in _INSTALL_MARKERS:
                        installed = True
                    if name.startswith(".") or entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if name not in _SKIP_DIRS:
                            subdirs.append(name)
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        files.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
        if installed:
            continue           # a virtualenv, conda or browser install: not the project's own files
        if rel:
            walk.dirs.append(rel)
        walk.files.extend((f"{rel}/{name}" if rel else name, m, s) for name, m, s in sorted(files))
        queue.extend(f"{rel}/{name}" if rel else name for name in sorted(subdirs))
    walk.unscanned = sorted(queue)
    return walk


def _digest(walk: _Walk, graph: Path | None, domains: Path | None, why: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    for rel, mtime, size in walk.files:
        h.update(f"f\0{rel}\0{mtime}\0{size}\n".encode("utf-8", "surrogateescape"))
    for rel in (*walk.dirs, "\0", *walk.unscanned):
        h.update(f"d\0{rel}\n".encode("utf-8", "surrogateescape"))
    for p in (graph, domains):
        try:
            st = p.stat() if p is not None else None
        except OSError:
            st = None
        h.update(f"m\0{p}\0{st.st_mtime_ns if st else 0}\0{st.st_size if st else 0}\n".encode("utf-8", "surrogateescape"))
    h.update(f"{walk.capped}\0{why}".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def _shown(rel: str) -> str:
    """A path as text: a name that is not UTF-8 shows its bytes as replacement characters."""
    return rel.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


# --- the Understand map ---------------------------------------------------------------------------------


def _find_map(root: Path) -> tuple[Path | None, Path | None, str]:
    """(knowledge graph, domain graph, why there is none). Only files that stay inside the workspace."""
    from ..gui.understand_routes import data_dir

    folder = data_dir(root)
    graph = folder / "knowledge-graph.json"
    if not graph.is_file():
        return None, None, "no Understand map (.ua/knowledge-graph.json)"

    def inside(p: Path) -> bool:
        try:
            return p.resolve().is_relative_to(root) and p.resolve().is_file()
        except OSError:
            return False

    if not inside(graph):
        return None, None, (f"no Understand map in the workspace ({_clip_path(graph.relative_to(root).as_posix())} "
                            "leads outside it and is not read)")
    domains = folder / "domain-graph.json"
    return graph, domains if domains.is_file() and inside(domains) else None, ""


def _load(root: Path, p: Path) -> dict[str, Any]:
    """A map file, read through page_server's pinned open like every scan read (_read): one component at a time,
    never through a link, so a map or its folder swapped for a link after _find_map's check is refused, not read.
    A ValueError's text is the reason the outline gives."""
    from ..gui.page_server import _open_pinned

    try:
        fd, info = _open_pinned(root, p.relative_to(root))
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):     # the file (ELOOP) or a folder (ENOTDIR) on the way
            raise ValueError("a link or a non-folder on its path, not followed") from e
        raise
    with os.fdopen(fd, "rb") as f:
        raw = f.read(_MAP_MAX_BYTES + 1) if info.st_size <= _MAP_MAX_BYTES else b""
    if info.st_size > _MAP_MAX_BYTES or len(raw) > _MAP_MAX_BYTES:
        raise ValueError(f"over {_MAP_MAX_BYTES // (1024 * 1024)} MB")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError("no node list")
    # The outline counts and looks up edges by their endpoints: a list or an object there raised TypeError.
    for i, e in enumerate(data.get("edges") or ()):
        if isinstance(e, dict) and not (isinstance(e.get("source"), str) and isinstance(e.get("target"), str)):
            raise ValueError(f"map edge {i} has a non-string endpoint")
    return data


def _epoch(when: Any) -> float | None:
    """An ISO time WITH a timezone (the skill writes `...Z`); a naive one is not trusted."""
    try:
        moment = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment.timestamp() if moment.tzinfo is not None else None


# --- rendering ------------------------------------------------------------------------------------------


@dataclass(eq=False)
class _Item:
    priority: float
    order: tuple
    text: str | Callable[[], str | None]
    kind: str = "line"                 # "file" | "folder" | "line": what the left-out note counts
    rank: int = 0                      # among equals: the first file of every folder before any second one
    parent: "_Item | None" = None      # shown only when this one is (a file's symbols line)


def _fit(items: list[_Item], head: list[str], limit: int, deadline: float, reserve: int) -> tuple[list[_Item], Counter]:
    """The most important items that fit in `limit` characters after `head` and `reserve` (section titles
    and the note on what was left out), in priority order. An item whose text is a callable is read only
    when its turn comes; reading stops at `deadline` or after _MISSES items in a row that did not fit."""
    used = sum(len(line) + 1 for line in head) + reserve
    kept: list[_Item] = []
    left: Counter = Counter()
    misses = 0
    shown: set[int] = set()
    for item in sorted(items, key=lambda i: (i.priority, i.rank, i.order)):
        if (misses >= _MISSES or (callable(item.text) and _clock() > deadline)
                or (item.parent is not None and id(item.parent) not in shown)):
            left[item.kind] += 1
            continue
        text = item.text() if callable(item.text) else item.text
        if text is None:
            continue
        if used + len(text) + 1 > limit:
            misses += 1
            left[item.kind] += 1
            continue
        misses = 0
        item.text = text
        used += len(text) + 1
        kept.append(item)
        shown.add(id(item))
    return kept, left


def _reserve(what: str, titles: tuple[str, ...] = ()) -> int:
    """The most the section titles and the left-out note can take (the note's counts at their widest)."""
    widest = Counter(file=10**9, folder=10**9, line=10**9)
    return len(_left_note(widest, what)) + 1 + sum(len(t) + 1 for t in titles)


def _left_note(left: Counter, what: str) -> str:
    parts = [f"{n:,} {kind}{'' if n == 1 else 's'}" for kind, n in
             (("file", left["file"]), ("folder", left["folder"]), ("other line", left["line"])) if n]
    return (f"[{' and '.join(parts)} not listed: the outline keeps to about 2,000 tokens. {what}]"
            if parts else "")


def _partial(walk: _Walk) -> str:
    if walk.capped == "entries":
        return (f"This outline is partial: the scan stopped at its limit of {WALK_MAX_ENTRIES:,} entries; "
                "list_dir or grep a folder for the rest.")
    if walk.capped == "time":
        return (f"This outline is partial: the scan stopped at its time limit of {WALK_MAX_S:g} s "
                f"({walk.seen:,} entries seen); list_dir or grep a folder for the rest.")
    return ""


def _clip(text: Any, n: int = _LINE_CHARS) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def _clip_path(path: str, n: int = _PATH_CHARS) -> str:
    """A path of at most n characters: its start and its end (the file's name), "…" between."""
    if len(path) <= n:
        return path
    head = (n - 1) // 3
    return path[:head] + "…" + path[len(path) - (n - 1 - head):]


def _listed(values: Any, shown: int = _LIST_SHOWN) -> str:
    items = [_clip(v, _LIST_CHARS) for v in values or ()] if isinstance(values, (list, tuple)) else []
    more = f" (+{len(items) - shown} more)" if len(items) > shown else ""
    return ", ".join(items[:shown]) + more


def _render(root: Path, walk: _Walk, graph: Path | None, domains: Path | None, why: str) -> str:
    if graph is not None:
        try:
            data = _load(root, graph)
        except (OSError, ValueError) as e:
            # _load's own reasons are plain ValueErrors; a decoding or an OS error is named by its type.
            why = (f"could not read the Understand map ({graph.relative_to(root).as_posix()}: "
                   f"{e if type(e) is ValueError else type(e).__name__})")
        else:
            try:
                extra = _load(root, domains) if domains is not None else None
            except (OSError, ValueError):
                extra = None
            return _render_map(root, walk, graph, data, extra)
    return _render_scan(root, walk, why)


def _render_map(root: Path, walk: _Walk, graph: Path, data: dict[str, Any], extra: dict[str, Any] | None) -> str:
    from ..gui.understand_routes import relative_path

    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    nodes = [n for n in data["nodes"] if isinstance(n, dict) and isinstance(n.get("id"), str)]
    edges = [e for e in data.get("edges") or () if isinstance(e, dict)]
    degree: Counter = Counter()
    for e in edges:
        degree[e.get("source")] += 1
        degree[e.get("target")] += 1

    def path_of(n: dict[str, Any]) -> str | None:
        raw = n.get("filePath")
        return relative_path(raw, root) if isinstance(raw, str) and raw else None

    files = {n["id"]: n for n in nodes if n.get("type") in _FILE_NODES and path_of(n)}
    symbols: dict[str, list[dict[str, Any]]] = {}
    for n in nodes:
        if n.get("type") in _SYMBOL_NODES and path_of(n):
            symbols.setdefault(path_of(n), []).append(n)

    def span(n: dict[str, Any]) -> int:
        r = n.get("lineRange")
        return (r[1] - r[0]) if isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) for x in r) else 0

    built = project.get("analyzedAt")
    when = _epoch(built)
    mapped = {path_of(n) for n in files.values()}
    head = [f"Workspace outline of {_clip(project.get('name') or root.name, 60)}, from its Understand map "
            f"({graph.relative_to(root).as_posix()}"
            + (f", built {datetime.fromtimestamp(when).astimezone():%Y-%m-%d %H:%M %Z}" if when is not None else "")
            + f"; {len(files):,} files and {sum(map(len, symbols.values())):,} functions and classes mapped). "
              "Its summaries describe the code as it was then."]
    if project.get("description"):
        head.append(_clip(project["description"], 300))
    stack = [_listed(project.get(k)) for k in ("languages", "frameworks")]
    if any(stack):
        head.append(f"Languages: {stack[0] or '?'} · frameworks: {stack[1] or 'none named'}")
    on_disk = {rel: mtime for rel, mtime, _ in walk.files}
    if when is not None:
        changed = sorted((rel for rel in mapped if rel in on_disk and on_disk[rel] / 1e9 > when),
                         key=lambda r: -on_disk[r])
        if changed:
            head.append(f"Changed since the map (modified after it was built): {_names(changed)}")
    new = sorted((rel for rel in on_disk if rel not in mapped), key=lambda r: -on_disk[r])
    if new and mapped:
        head.append(f"Not in the map: {_names(new)}")
    if walk.capped:
        head.append(_partial(walk))

    items: list[_Item] = []
    layers = [l for l in data.get("layers") or () if isinstance(l, dict)] or [
        {"name": "Files", "description": "", "nodeIds": list(files)}]
    for li, layer in enumerate(layers):
        members = sorted((files[i] for i in layer.get("nodeIds") or () if i in files),
                         key=lambda n: (-degree[n["id"]], path_of(n)))
        desc = _clip(layer.get("description"), 140)
        items.append(_Item(1, (1, li, -1, 0), f"## {_clip(layer.get('name'), 60)}" + (f" — {desc}" if desc else "")
                           + f" ({len(members)} file{'' if len(members) == 1 else 's'})"))
        for rank, n in enumerate(members):
            summary = _clip(n.get("summary"))
            line = _Item(2 + rank, (1, li, rank, 0),
                         f"- {_clip_path(_shown(path_of(n)))}" + (f" — {summary}" if summary else ""), "file")
            items.append(line)
            top = sorted(symbols.get(path_of(n), ()), key=lambda s: (-degree[s["id"]], -span(s), str(s.get("name"))))
            if top:
                items.append(_Item(2.5 + rank, (1, li, rank, 1),
                                   "  symbols: " + ", ".join(_clip(s.get("name"), 40) for s in top[:4]), parent=line))
    if extra is not None:
        dnodes = {n["id"]: n for n in extra.get("nodes") or () if isinstance(n, dict) and isinstance(n.get("id"), str)}
        flows: dict[str, list[str]] = {}
        for e in extra.get("edges") or ():
            if isinstance(e, dict) and e.get("type") == "contains_flow" and e.get("target") in dnodes:
                flows.setdefault(e.get("source"), []).append(_clip(dnodes[e["target"]].get("name"), 50))
        for rank, d in enumerate(n for n in dnodes.values() if n.get("type") == "domain"):
            summary = _clip(d.get("summary"), 100)
            line = f"- {_clip(d.get('name'), 50)}" + (f" — {summary}" if summary else "")
            if flows.get(d["id"]):
                line += " Flows: " + "; ".join(flows[d["id"]][:4])
            items.append(_Item(2 + rank, (2, rank), line))
    titles = {1: "\nLayers (key files by connections; symbols: the most connected functions and classes):",
              2: "\nDomains (.ua/domain-graph.json):"}
    what = "read_file a file for its detail, or list_dir a folder."
    kept, left = _fit(items, head, OUTLINE_MAX_CHARS, _clock() + _READ_MAX_S, _reserve(what, tuple(titles.values())))
    body = _sections(kept, titles)
    note = _left_note(left, what)
    return "\n".join([*head, *body, *([note] if note else [])])


def _names(paths: list[str], shown: int = 6) -> str:
    more = f" (+{len(paths) - shown} more)" if len(paths) > shown else ""
    return (f"{len(paths)} file{'' if len(paths) == 1 else 's'} — "
            + ", ".join(_clip_path(_shown(p)) for p in paths[:shown]) + more)


def _sections(kept: list[_Item], titles: dict[int, str]) -> list[str]:
    out: list[str] = []
    section = None
    for item in sorted(kept, key=lambda i: i.order):
        if item.order[0] != section:
            section = item.order[0]
            if section in titles:
                out.append(titles[section])
        out.append(item.text)  # type: ignore[arg-type]
    return out


def _render_scan(root: Path, walk: _Walk, why: str) -> str:
    counts: Counter = Counter()
    for rel, _, _ in walk.files:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            counts["/".join(parts[:i])] += 1
    languages = Counter(_LANGUAGES[Path(rel).suffix.lower()] for rel, _, _ in walk.files
                        if Path(rel).suffix.lower() in _LANGUAGES)
    folders = [d for d in walk.dirs if counts[d]]
    head = [f"Workspace outline of {_clip(root.name, 60)}: {why}, so a quick scan — {len(walk.files):,} "
            f"file{'' if len(walk.files) == 1 else 's'} in {len(folders):,} folder{'' if len(folders) == 1 else 's'}."]
    if walk.capped:
        head.append(_partial(walk))
    if languages:
        head.append("Languages: " + ", ".join(f"{name} {n}" for name, n in languages.most_common(8)))
    by_rel = {rel: (mtime, size) for rel, mtime, size in walk.files}
    # The newest handoff docs, then README, CLAUDE.md, AGENTS.md and PLAN.md at the top.
    handoffs = sorted((rel for rel in by_rel if rel.count("/") <= 3 and _is_handoff(rel)), key=lambda r: -by_rel[r][0])[:2]
    start = sorted((rel for rel in by_rel if "/" not in rel and _is_start(rel)),
                   key=lambda r: (_START.index(Path(r).stem.lower()), r))
    start[1 if start and Path(start[0]).stem.lower() == "start_here" else 0:0] = handoffs
    if start:
        head.append("Start with: " + ", ".join(_clip_path(_shown(r)) for r in start[:6]))

    def order(rel: str, is_dir: bool) -> tuple:
        parts = rel.split("/")
        return (1, *(("1", p.lower(), p) for p in parts[:-1]), ("1" if is_dir else "0", parts[-1].lower(), parts[-1]))

    # Priorities: each level of folders before the files one level up (the shape before the detail); within
    # a level, the first file of every folder before any folder's second (docs newest first, code largest
    # first); start docs, handoffs, manifests and entry points ahead, tests, __init__ and binaries behind.
    items: list[_Item] = []
    for d in folders + walk.unscanned:
        depth = d.count("/")
        indent = "  " * depth
        if d in walk.unscanned:
            items.append(_Item(2 * depth + 1, order(d, True), f"{indent}- {_clip_path(_shown(d))}/ (not scanned)",
                               "folder"))
            continue
        items.append(_Item(2 * depth + 1, order(d, True),
                           lambda d=d, indent=indent: _folder_line(root, d, indent, counts[d], by_rel), "folder"))
    siblings: dict[str, list[tuple[int, int, str]]] = {}
    for rel, mtime, size in walk.files:
        doc = Path(rel).suffix.lower() in _TEXT_DOCS
        siblings.setdefault(rel.rpartition("/")[0], []).append((-(mtime if doc else size), 0, rel))
    rank = {rel: i for group in siblings.values() for i, (_, _, rel) in enumerate(sorted(group))}
    for rel, _, size in walk.files:
        name = rel.rpartition("/")[2]
        depth = rel.count("/")
        low = name.lower()
        priority = 2 * depth + 2
        if (depth == 0 and _is_start(name)) or _is_handoff(rel) or low in _ENTRY or low in _MANIFESTS:
            priority -= 1
        if _TESTS & set(rel.lower().split("/")[:-1]):
            priority += 2
        if low == "__init__.py" or Path(low).suffix in _BINARY:
            priority += 1 if low == "__init__.py" else 3
        items.append(_Item(priority, order(rel, False),
                           lambda rel=rel, size=size, depth=depth: _file_line(root, rel, size, "  " * depth), "file",
                           rank=rank[rel]))
    what = "list_dir a folder, grep, or read_file for the rest."
    kept, left = _fit(items, head, OUTLINE_MAX_CHARS, _clock() + _READ_MAX_S, _reserve(what, ("",)))
    note = _left_note(left, what)
    return "\n".join([*head, "", *(i.text for i in sorted(kept, key=lambda i: i.order)),  # type: ignore[misc]
                      *([note] if note else [])])


def _is_start(name: str) -> bool:
    """START_HERE, README, CLAUDE, AGENTS or PLAN, as Markdown or plain text."""
    p = Path(name)
    return p.suffix.lower() in (".md", ".txt", ".rst", "") and p.stem.lower() in _START


def _is_handoff(rel: str) -> bool:
    """A handoff document: a text doc whose name starts with "handoff" (HANDOFF.md, HANDOFF_2026-09-16b.md)."""
    name = rel.rpartition("/")[2]
    return Path(name).suffix.lower() in _TEXT_DOCS and name.lower().startswith("handoff")


def _folder_line(root: Path, rel: str, indent: str, n: int, by_rel: dict[str, tuple[int, int]]) -> str:
    about = ""
    for name in ("README.md", "readme.md", "README", "__init__.py"):
        if f"{rel}/{name}" in by_rel:
            about = _first_line(name, _head(root, f"{rel}/{name}"))
            if about:
                break
    return f"{indent}- {_clip_path(_shown(rel))}/ ({n:,} file{'' if n == 1 else 's'})" + (f" — {about}" if about else "")


def _file_line(root: Path, rel: str, size: int, indent: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    shown = _clip_path(_shown(rel))
    if Path(name).suffix.lower() in _BINARY:
        return f"{indent}- {shown} (binary, {_size(size)})"
    data = _read(root, rel, _COUNT_MAX_BYTES + 1 if size <= _COUNT_MAX_BYTES else _HEAD_BYTES)
    if data is None:
        return f"{indent}- {shown} (unreadable)"
    if b"\0" in data[:8192]:
        return f"{indent}- {shown} (binary, {_size(size)})"
    if size > _COUNT_MAX_BYTES or len(data) > _COUNT_MAX_BYTES:
        what = _size(size)
    else:
        lines = data.count(b"\n") + (0 if not data or data.endswith(b"\n") else 1)
        what = f"{lines:,} line{'' if lines == 1 else 's'}"
    first = _first_line(name, data[:_HEAD_BYTES].decode("utf-8", "replace"))
    return f"{indent}- {shown} ({what})" + (f" — {first}" if first else "")


def _size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} KB"


def _read(root: Path, rel: str, n: int) -> bytes | None:
    """Up to n bytes of the regular file `rel` under `root`, opened one component at a time without following
    a link (page_server's pinned open, DREAM-111): a file or folder the walk saw and that has since become a
    link out is refused, not read."""
    from ..gui.page_server import _open_pinned

    try:
        fd, _ = _open_pinned(root, Path(rel))
    except (OSError, ValueError):
        return None
    try:
        with os.fdopen(fd, "rb") as f:
            return f.read(n)
    except OSError:
        return None


def _head(root: Path, rel: str) -> str:
    data = _read(root, rel, _HEAD_BYTES)
    return data.decode("utf-8", "replace") if data and b"\0" not in data else ""


def _first_line(name: str, head: str) -> str:
    """A file's own one-line description: a Markdown heading, a Python docstring, the first comment,
    an HTML title. Empty when it has none."""
    suffix = Path(name).suffix.lower()
    low = name.lower()
    lines = head.splitlines()
    if suffix in _TEXT_DOCS:
        for line in lines:
            text = line.strip().lstrip("#").strip()
            if text and not set(text) <= set("=-~*_`"):
                return _clip(text)
        return ""
    if suffix == ".py":
        m = _PY_DOC.match(head)
        if m:
            text = next((l.strip() for l in m.group(2).splitlines() if l.strip()), "")
            if text:
                return _clip(text)
        return _comment(lines, "#")
    if suffix in (".html", ".htm", ".svg"):
        m = _TITLE.search(head)
        return _clip(re.sub(r"<[^>]+>", "", m.group(1) or m.group(2))) if m else ""
    if suffix in _SLASH_COMMENTS:
        return _comment(lines, "//", "/*", "*")
    if suffix in _HASH_COMMENTS or low in ("makefile", "dockerfile", "cmakelists.txt"):
        return _comment(lines, "#")
    return ""


def _comment(lines: list[str], *marks: str) -> str:
    """The file's header comment: its first comment line with words in it, before any code -- not a
    shebang, an encoding or a linter pragma."""
    for line in lines[:40]:
        s = line.strip()
        if not s or s.startswith("#!"):
            continue
        mark = next((m for m in marks if s.startswith(m)), None)
        if mark is None:
            return ""            # code before any comment: no header comment
        text = s[len(mark):].strip().lstrip("/!*").strip().rstrip("*/").strip()
        if not text or "coding" in text[:20] or text.startswith(("noqa", "type:", "pylint", "eslint", "@ts-",
                                                                 "SPDX-")):
            continue
        return _clip(text)
    return ""
