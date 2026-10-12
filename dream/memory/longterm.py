"""Long-term memory on disk: markdown is the source of truth.

Phase 9 turned the relationship around. The database used to own memory and the
files under ``memory/semantic`` etc. were a mirror; now one flat
``memory/<name>.md`` per fact IS the memory, and SQLite is a derived index that
``sync`` rebuilds from the files at every boot. Hand-edit a file and the next
boot picks it up with no tool call; delete a file and the row goes with it.

A memory carries in its frontmatter:

- ``name``        the file's identity (its stem), stable across edits
- ``description`` the one line the MEMORY.md index shows
- ``type``        user | feedback | project | reference — the user's taxonomy
- ``kind``        semantic | procedural | episodic — kept beside ``type`` because
                  time-scoped recall filters on it; dropping it would lose meaning
- ``tags`` / ``salience`` / ``created_at`` / ``updated_at``
- ``project``     whose memory it is (DREAM-108): a workspace key, ``user`` for a
                  user-wide memory, ``unassigned`` for legacy data nobody could place.
                  A file without the line is legacy until the one-time migration has
                  run; after it, a file the owner writes by hand without the line
                  joins the project of the session that starts next (``adopt_orphans``),
                  and a file an older Dream rewrote without it keeps its row's project.

``memory/MEMORY.md`` is regenerated from the files on every write: one line per
memory, and nothing else. It is an index, never content.
"""

from __future__ import annotations

import os
import json
import hashlib
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import config
from .project import UNASSIGNED, USER, known as known_projects, resolve as resolve_project, valid_owner
from .store import MEM_TYPES, MemoryStore, default_type, slugify

# --- provenance ---------------------------------------------------------------------

PROVENANCE = ("stated", "observed", "inferred")
_PROV_RE = re.compile(r"^\s*\[(?:" + "|".join(PROVENANCE) + r")\]", re.IGNORECASE)
# A line that is structure, not a fact: blank, a heading, a fence, a table rule.
_STRUCTURE_RE = re.compile(r"^\s*(?:#{1,6}\s|```|\||-{3,}\s*$|\*{3,}\s*$)")


def tag_body(body: str, default: str = "inferred") -> str:
    """Give every fact line a provenance tag. ``[stated]`` is what the user said in
    their own words, ``[observed]`` is what happened where I could see it, ``[inferred]``
    is my conclusion. A line that already carries one is left alone, and structure
    (headings, fences, tables, rules) is not a fact and is never tagged."""
    if default not in PROVENANCE:
        default = "inferred"
    out = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced or not line.strip() or _STRUCTURE_RE.match(line) or _PROV_RE.match(line):
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip())]
        rest = line[len(indent):]
        # A bullet keeps its marker in front of the tag, so the list still reads.
        m = re.match(r"([-*+]\s+|\d+[.)]\s+)(.*)$", rest)
        if m:
            out.append(f"{indent}{m.group(1)}[{default}] {m.group(2)}")
        else:
            out.append(f"{indent}[{default}] {rest}")
    return "\n".join(out)


# --- one memory on disk --------------------------------------------------------------


def path_for(mem: dict[str, Any]) -> Path:
    """The file for a memory. The name is re-slugified HERE, at the one place a
    name becomes a filesystem path: it travels from tool arguments straight into
    this join, so '../../x' would otherwise write arbitrary .md files."""
    name = slugify(str(mem.get("name") or mem.get("slug") or ""))
    return config.MEMORY_DIR / f"{name}.md"


def describe(mem: dict[str, Any]) -> str:
    """The index line's hook. The memory's own description when it has one, else
    the first sentence of the body with its provenance tag stripped."""
    d = str(mem.get("description") or "").strip()
    if d:
        return d.replace("\n", " ")
    for line in str(mem.get("body") or "").splitlines():
        line = _PROV_RE.sub("", line).strip(" -*+\t")
        if line and not _STRUCTURE_RE.match(line):
            first = re.split(r"(?<=[.!?])\s", line)[0]
            return first[:160].strip()
    return ""


def _one_line(v: Any) -> str:
    """A frontmatter field is one line: a newline inside a title or a description
    would end the frontmatter early and turn the rest into body (Gate 9b)."""
    return " ".join(str(v if v is not None else "").split())


def _render(mem: dict[str, Any]) -> str:
    name = slugify(str(mem.get("name") or mem.get("slug") or ""))
    from .curation import lifecycle_json
    if mem.get("lifecycle_error"):
        raise ValueError("repair lifecycle metadata before rewriting this memory")
    lifecycle = lifecycle_json(mem.get("lifecycle"))
    fm = [
        "---",
        f"name: {name}",
        # Only an explicit description is written; a derived one is computed by
        # the index from the body each time, so a body replace never leaves a
        # stale hook frozen in the frontmatter.
        f"description: {_one_line(mem.get('description'))}",
        f"type: {mem.get('mem_type') or default_type(mem['kind'], mem.get('title', ''), mem.get('body', ''))}",
        f"kind: {mem['kind']}",
        f"title: {_one_line(mem['title'])}",
        f"tags: {_one_line(mem.get('tags', ''))}",
        f"salience: {mem.get('salience', 1.0)}",
        f"created_at: {mem.get('created_at', '')}",
        f"updated_at: {mem.get('updated_at', '')}",
        *([f"source_session: {_one_line(mem['source_session'])}"] if mem.get('source_session') else []),
        *([f"lifecycle: {lifecycle}"] if lifecycle != '{}' else []),
        *([f"project: {owner}"] if (owner := valid_owner(mem.get("project"))) else []),
        "---",
        "",
        str(mem["body"]).rstrip(),
        "",
    ]
    return "\n".join(fm)


def parse_markdown(text: str) -> tuple[dict[str, str], str]:
    """Split a memory file into (frontmatter dict, body). Tolerant of files without
    frontmatter. Fences must be full ``---`` lines so a ``---`` inside the body (a
    horizontal rule, say) can't truncate content. A leading byte-order mark is not content."""
    text = text.removeprefix("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    end = None
    for i, ln in enumerate(lines[1:], start=1):
        if ln.strip() == "---":
            end = i
            break
    if end is None:
        return {}, text.strip()
    fm: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm, "\n".join(lines[end + 1:]).strip()


class MemoryDurabilityError(OSError):
    """The file was replaced, but its directory durability was not confirmed."""

    def __init__(self, path: Path, cause: OSError):
        self.path = path
        super().__init__(
            f"Memory file replaced at {path}, but directory durability is unconfirmed: "
            f"{cause}. Read the current file before retrying an append or replacement."
        )


def _sync_directory(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _atomic_write(path: Path, text: str) -> None:
    """Sync contents and directory entries; distinguish failure after replacement.

    Synchronize every ancestor even on retries: an existing directory may have
    been created by a previous attempt whose directory synchronization failed.
    Directory-open/sync support is required; unsupported filesystems fail visibly.
    This contract covers writes, not deletion or migration source unlinks.
    """
    temporary = None
    directory = None
    replaced = False
    try:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            parent = path.parent.resolve()
            directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            os.fsync(directory)
            for ancestor in parent.parents:
                _sync_directory(ancestor)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            replaced = True
            os.fsync(directory)
        finally:
            try:
                if directory is not None:
                    os.close(directory)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    except OSError as exc:
        if replaced:
            raise MemoryDurabilityError(path, exc) from exc
        raise


def write_markdown(mem: dict[str, Any], *, index: bool = True) -> Path:
    p = path_for(mem)
    if is_reserved(p.name):
        # The store suffixes reserved stems before a row exists; a caller that
        # bypassed it must not write a file the index will never see.
        raise ValueError(f"{p.name} is a reserved file, not a memory name — use "
                         f"{config.free_memory_name(p.stem)!r}")
    with memory_edit_lock():
        _atomic_write(p, _render(mem))
    if index:
        write_index()
    return p


def set_project_line(path: Path, owner: str) -> None:
    """Give a memory file its project (DREAM-108) by editing that one frontmatter line
    and nothing else, so a hand-added field or a hand-formatted body survives a move
    or the migration byte for byte. A file without frontmatter gets a minimal one,
    which reads back as the same memory."""
    owner = valid_owner(owner)
    if not owner:
        raise ValueError("a memory's project must be a project key, 'user' or 'unassigned'")
    with open(path, encoding="utf-8", newline="") as fh:   # newline="": every line keeps its own ending
        text = fh.read()
    # The lines exactly as read_file sees them (universal newlines, then splitlines), each
    # with its ending, so this edit and parse_markdown can never disagree on the fences.
    # A leading byte-order mark stays where it is; parse_markdown skips it too.
    bom = "﻿" if text.startswith("﻿") else ""
    lines = text[len(bom):].splitlines(keepends=True)

    def ending(line: str) -> str:
        return line[len(line.splitlines()[0]):] if line.splitlines() else line

    new = None
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            fence = [i for i in range(1, end)
                     if ":" in lines[i] and lines[i].partition(":")[0].strip() == "project"]
            for i in fence:                       # every project line, so the last one agrees too
                lines[i] = f"project: {owner}{ending(lines[i]) or ending(lines[end - 1]) or chr(10)}"
            if not fence:
                lines.insert(end, f"project: {owner}{ending(lines[end - 1]) or chr(10)}")
            new = bom + "".join(lines)
    if new is None:
        nl = ending(lines[0]) if lines else "\n"
        nl = nl or "\n"
        new = f"{bom}---{nl}project: {owner}{nl}---{nl}{nl}{text[len(bom):]}"
    # Checked before it lands: as Dream reads the file back, only the project may differ.
    fm, body = parse_markdown(_as_read(text))
    if parse_markdown(_as_read(new)) != ({**fm, "project": owner}, body):
        raise ValueError(f"{path.name}: its project line cannot be written without changing the rest "
                         "of the memory as Dream reads it; left unchanged")
    _atomic_write(path, new)


def _as_read(raw: str) -> str:
    """A file's text as read_file sees it: universal newlines."""
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def delete_markdown(slug: str, *, index: bool = True) -> None:
    name = slugify(str(slug or ""))  # same reason as path_for: never unlink outside
    if not name or is_reserved(f"{name}.md"):
        return
    for d in (config.MEMORY_DIR, config.SEMANTIC_DIR, config.PROCEDURAL_DIR, config.EPISODIC_DIR):
        f = d / f"{name}.md"
        if f.exists():
            f.unlink()
    if index:
        write_index()


def near_cap(text: str) -> str:
    """The note a memory carries when it is running out of room. The fix is
    consolidation — fewer, better facts — not shaving words off the ones there."""
    n = len(text)
    cap = config.MEMORY_FILE_MAX
    if n > cap:
        return (f"this memory is {n:,} characters, over the {cap:,} cap — consolidate it "
                f"into fewer, sharper facts (or split it in two), don't shave words")
    if n > cap * 0.8:
        return (f"this memory is {n:,} characters, near the {cap:,} cap — consolidate "
                f"soon rather than trimming later")
    return ""


# --- the files as a set ---------------------------------------------------------------


def memory_files() -> list[Path]:
    """Every file under memory/ that is a memory: flat, ``.md``, not reserved."""
    if not config.MEMORY_DIR.exists():
        return []
    return sorted(
        f for f in config.MEMORY_DIR.glob("*.md")
        if f.is_file() and not is_reserved(f.name) and _utf8_name(f)
    )


_bad_names: set[str] = set()


def _utf8_name(f: Path) -> bool:
    """A name that is not valid UTF-8 cannot go into MEMORY.md or a row: such a file is left
    alone and named once in the boot log (fix list #73: it used to stop every boot)."""
    try:
        f.name.encode("utf-8")
        return True
    except UnicodeEncodeError:
        if f.name not in _bad_names:
            _bad_names.add(f.name)
            boot_log(f"{f.name.encode('utf-8', 'backslashreplace').decode()}: its file name is not valid "
                     "UTF-8, so it is not read as a memory; rename it to use it")
        return False


def memory_file_map() -> dict[str, Path]:
    """Each memory's file by the memory's name, the slug of the file's stem: a hand-written
    file's name need not be a slug ('My Notes.md' is my-notes). When two files share a slug,
    the one named exactly wins, else the first by name."""
    files: dict[str, Path] = {}
    for f in memory_files():
        slug = slugify(f.stem)
        if slug not in files or f.stem == slug:
            files[slug] = f
    return files


def _why_not(exc: Exception) -> str:
    if isinstance(exc, UnicodeDecodeError):
        return "the file is not UTF-8 (save it as UTF-8 so the memory tools see its project)"
    return str(exc)


def is_reserved(fname: str) -> bool:
    """A file name that is one of the reserved uppercase files, case-insensitive
    (see config.reserved_memory_name)."""
    stem = fname[:-3] if fname.lower().endswith(".md") else fname
    return config.reserved_memory_name(stem)


def read_file(path: Path, *, allow_reserved: bool = False) -> dict[str, Any] | None:
    """One file as a memory dict, or None when it cannot be read at all. Forgiving
    on purpose: this is the hand-edit path, and a typo must not cost the boot.
    A reserved stem is not a memory in the flat tree; the migration reads one
    from the old directories with ``allow_reserved`` and suffixes it."""
    try:
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        lines = raw_text.removeprefix("\ufeff").splitlines()
        if (lines and lines[0].strip() == "---"
                and not any(line.strip() == "---" for line in lines[1:])):
            # A partially saved header is not legacy body-only text. Keep the
            # prior indexed fact quarantined until the authoritative file repairs.
            return None
        fm, body = parse_markdown(raw_text)
    except OSError:
        return None
    # The file's identity is its stem, always: a `name:` line that disagrees is
    # informational and is rewritten on the next write. Two files can therefore
    # never claim one name, and a delete by name always finds the file.
    name = slugify(path.stem)
    if not name or (is_reserved(f"{name}.md") and not allow_reserved):
        return None
    if not body.strip():
        return None  # a file with no body is not a memory, frontmatter or not
    kind = fm.get("kind", "semantic")
    if kind not in ("semantic", "procedural", "episodic"):
        kind = "semantic"
    mem_type = fm.get("type", "")
    if mem_type not in MEM_TYPES:
        mem_type = ""
    try:
        salience = float(fm.get("salience", "1.0"))
    except ValueError:
        salience = 1.0
    from .curation import lifecycle_json
    lifecycle_error = ""
    try:
        lifecycle = lifecycle_json(fm.get("lifecycle"))
        header_lines = raw_text.removeprefix("\ufeff").splitlines()
        if header_lines and header_lines[0].strip() == "---":
            end = next((i for i in range(1, len(header_lines)) if header_lines[i].strip() == "---"), 0)
            if sum(ln.partition(':')[0].strip() == 'lifecycle' for ln in header_lines[1:end]) > 1:
                raise ValueError("duplicate lifecycle metadata field")
    except ValueError as exc:
        lifecycle, lifecycle_error = "{}", str(exc)
    return {
        "lifecycle": lifecycle, "lifecycle_error": lifecycle_error,
        "source_session": fm.get("source_session") or None,
        "source_session_present": "source_session" in fm,
        "name": name, "slug": name, "kind": kind, "mem_type": mem_type,
        "title": fm.get("title") or path.stem.replace("-", " ").title(),
        "description": fm.get("description", ""), "body": body,
        "tags": fm.get("tags", ""), "salience": salience,
        "created_at": fm.get("created_at", ""), "updated_at": fm.get("updated_at", ""),
        "project": valid_owner(fm.get("project")),
        # The line as written, None when the file has none: "no line" (legacy, or a file
        # written by hand) is not the same as a line naming something unusable.
        "project_line": fm["project"].strip() if "project" in fm else None,
    }


def _indexed() -> list[tuple[dict[str, Any], str]]:
    """Every memory file with its name, newest first -- the index's order."""
    rows = []
    for f in memory_files():
        mem = read_file(f)
        if mem:
            rows.append((mem.get("updated_at") or mem.get("created_at") or "", mem, f.name))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(mem, fname) for _, mem, fname in rows]


def _index_line(mem: dict[str, Any], fname: str) -> str:
    hook = describe(mem)
    title = str(mem["title"]).replace("[", "\\[").replace("]", "\\]")
    line = f"- [{title}]({fname})" + (f" — {hook}" if hook else "")
    if mem.get("lifecycle_error"):
        line += " [lifecycle metadata error; excluded from active recall]"
    elif mem.get("lifecycle") not in (None, "", "{}"):
        line += " [lifecycle: " + mem["lifecycle"] + "]"
    return line


def _owner_label(owner: str) -> str:
    return {USER: " [user-wide]", UNASSIGNED: " [unassigned]"}.get(owner, f" [project: {owner}]")


def write_index() -> Path:
    """Regenerate ``memory/MEMORY.md`` from the files. One line per memory, newest
    first — a pointer, never the content. The file covers every project, so each
    line carries its memory's project (DREAM-108); the wake-up loads only its own
    project's lines (``index_lines(scope=...)``)."""
    rows = _indexed()
    lines = [
        "# Memory Index",
        "",
        "One line per memory, newest first. Regenerated on every write — edit the",
        "memory files themselves, not this index.",
        "",
    ]
    for mem, fname in rows:
        owner = mem.get("project") or ""
        lines.append(_index_line(mem, fname) + (_owner_label(owner) if owner else ""))
    if not rows:
        lines.append("_(no memories yet)_")
    _atomic_write(config.MEMORY_INDEX_FILE, "\n".join(lines) + "\n")
    return config.MEMORY_INDEX_FILE


def index_lines(limit: int = 40, scope: tuple[str, ...] | None = None) -> list[str]:
    """The index as the wake context loads it: the same one-liners, bounded. With a
    ``scope`` (DREAM-108) only the memories of those projects, read from the files,
    user-wide ones labelled; without one, MEMORY.md as it stands."""
    if scope is not None:
        out = []
        for mem, fname in _indexed():
            owner = mem.get("project") or ""
            if owner in scope:
                out.append(_index_line(mem, fname) + (" [user-wide]" if owner == USER else ""))
        return out[:limit]
    if not config.MEMORY_INDEX_FILE.exists():
        return []
    out = [
        ln for ln in config.MEMORY_INDEX_FILE.read_text(encoding="utf-8").splitlines()
        if ln.startswith("- [")
    ]
    return out[:limit]


# --- migration and sync ----------------------------------------------------------------


def migrate_layout() -> int:
    """Move memories out of the old semantic/ procedural/ episodic/ directories into
    one flat memory/<name>.md, giving each the frontmatter the new shape needs.
    Idempotent: a second run finds nothing to move. The old directories are left in
    place, empty — they are the user's, not mine to delete."""
    moved = 0
    for kind, d in (
        ("semantic", config.SEMANTIC_DIR),
        ("procedural", config.PROCEDURAL_DIR),
        ("episodic", config.EPISODIC_DIR),
    ):
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                mem = read_file(f, allow_reserved=True)
                if mem is None:
                    continue
                # A file with no `kind:` line takes the kind of the directory it
                # was filed under — that directory WAS the kind.
                fm, _ = parse_markdown(f.read_text(encoding="utf-8", errors="replace"))
                if fm.get("kind") not in ("semantic", "procedural", "episodic"):
                    mem["kind"] = kind
                if not mem["mem_type"]:
                    mem["mem_type"] = default_type(mem["kind"], mem["title"], mem["body"])
                # A name that would land on a reserved file, or on a different
                # memory already there, is suffixed rather than overwriting either.
                base, n = mem["name"], 1
                while True:
                    target = config.MEMORY_DIR / f"{mem['name']}.md"
                    if not is_reserved(target.name):
                        if not target.exists():
                            break
                        there = read_file(target)
                        if there and there["body"] == mem["body"] and there["title"] == mem["title"]:
                            break  # already migrated: this run is a no-op for it
                    n += 1
                    mem["name"] = mem["slug"] = f"{base}-{n}"
                write_markdown(mem, index=False)
                f.unlink()
                moved += 1
            except Exception:
                continue
    if moved:
        write_index()
    return moved


def _same(row: dict[str, Any], mem: dict[str, Any]) -> bool:
    """Whether a file and its row agree on everything that matters. Compared by
    field, not by bytes: a hand-written file that lacks canonical lines (no
    `updated_at`, no `salience`) is still the same memory, and byte comparison
    re-imported it on every boot, bumping `updated_at` each time (Gate 9a)."""
    if (row["kind"], row["title"], row["body"], row.get("tags", "")) != (
            mem["kind"], mem["title"], mem["body"], mem["tags"]):
        return False
    if float(row.get("salience", 1.0)) != float(mem["salience"]):
        return False
    if mem["mem_type"] and mem["mem_type"] != (row.get("mem_type") or ""):
        return False
    if describe(row) != describe(mem):
        return False
    if (mem.get("project") or "") != (row.get("project") or ""):
        return False  # the file names the project; a hand edit of that line moves it
    for key in ("lifecycle", "lifecycle_error"):
        if mem.get(key, "{}" if key == "lifecycle" else "") != row.get(key, "{}" if key == "lifecycle" else ""):
            return False
    if mem.get("source_session_present") and not row.get("source_from_file"):
        return False
    if mem.get("source_session") != row.get("source_session"):
        return False  # the authoritative file can explicitly remove provenance
    for key in ("created_at", "updated_at"):
        if mem.get(key) and mem[key] != row.get(key):
            return False
    return True


def boot_log(line: str) -> None:
    """A line in Dream's boot log (var/logs/cli.log, where the Engine writes its own), so
    what the file sync decides about a memory's project is never silent (DREAM-108)."""
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (config.LOG_DIR / "cli.log").open("a", encoding="utf-8") as fh:
            fh.write(f"memory: {line}\n")
    except OSError:
        pass


def known_keys(store: MemoryStore) -> set[str]:
    """Projects Dream knows: a recorded workspace (memory/projects/<key>/.workspace) or a
    session that ran in it, plus 'user' and 'unassigned'."""
    keys = {USER, UNASSIGNED, *known_projects()}
    with store._lock:
        keys.update(r[0] for r in store._conn.execute("SELECT DISTINCT project FROM sessions") if r[0])
    return keys


def sync(store: MemoryStore) -> dict[str, int]:
    """Make the database agree with the files. Files win, always.

    A file whose rendered row differs from what is on disk was edited by hand, so
    the row is rebuilt from the file. A row whose file is gone is dropped. A file
    with no row is imported. Nothing here needs a tool call, which is the point:
    the user edits a memory in their editor and the next boot knows it.

    The project line (DREAM-108) wins too, with one exception: a file WITHOUT the line
    whose row names a project was rewritten by an older Dream, so the row keeps its
    project and the line is written back. A file without the line and without a
    project row is legacy before the one-time migration (it files it by evidence) and,
    after it, one the owner wrote by hand: the next session to start adopts it
    (``adopt_orphans``). A line that is not a known key is resolved the way the memory
    tools resolve a project name (a folder name, a path, 'global'): one match is written
    back as the project's key. A line that matches nothing, or several, is kept when it
    is shaped like a key and otherwise leaves the memory hidden; either way it is named
    in the boot log, and it never stops the boot.
    """
    # Same file-lock -> store-lock order as persisted upserts and lifecycle CAS.
    # Hold across file reads, row reconciliation and index publication so an old
    # snapshot cannot overwrite an intervening explicit invalidation.
    with memory_edit_lock():
        return _sync_locked(store)


def _sync_locked(store: MemoryStore) -> dict[str, int]:
    counts = {"moved": migrate_layout(), "imported": 0, "updated": 0, "dropped": 0}
    seen: set[str] = set()
    known = None
    files = memory_file_map()
    shadowed = len(memory_files()) - len(files)
    if shadowed:
        counts["shadowed"] = shadowed  # duplicate file identities retained on disk
    for f in files.values():
        # Unreadable/partial files remain recoverable; never drop an existing row
        # merely because parsing failed during this pass.
        seen.add(slugify(f.stem))
        mem = read_file(f)
        if mem is None:
            counts["unreadable"] = counts.get("unreadable", 0) + 1
            with store._lock, store._conn:
                store._conn.execute("UPDATE memories SET lifecycle_error=? WHERE slug=?",
                                    ("unreadable authoritative file; repair then sync", slugify(f.stem)))
            continue
        if mem.get("lifecycle_error"):
            counts["quarantined"] = counts.get("quarantined", 0) + 1
            boot_log(f"{f.name}: {mem['lifecycle_error']}; excluded from active recall, file retained")
        row = store.get_memory(mem["name"])
        if row and not mem["source_session_present"] and not row.get("source_from_file"):
            # Old files never contained this field; their SQLite-only provenance
            # still supplies evidence for project migration. Once a source field
            # is observed/authored in a file, later removal authoritatively clears.
            mem["source_session"] = row.get("source_session")
        heal = False
        if mem["project_line"] is None:
            if row is not None and row.get("project"):
                mem["project"], heal = row["project"], True
        else:
            known = known if known is not None else known_keys(store)
            if mem["project"] not in known:
                _resolve_line(f, mem, known)
        if row is None or not _same(row, mem):
            try:
                store.upsert_memory(
                    kind=mem["kind"], title=mem["title"], body=mem["body"], slug=mem["name"],
                    tags=mem["tags"], salience=mem["salience"],
                    description=mem["description"],
                    mem_type=mem["mem_type"] or default_type(mem["kind"], mem["title"], mem["body"]),
                    # Files win, the project line too (DREAM-108): never the session's
                    # project, which would claim every legacy file for this workspace.
                    project=mem.get("project") or "", reassign=True, embed=False,
                    lifecycle=mem["lifecycle"], lifecycle_error=mem["lifecycle_error"],
                    source_session=mem.get("source_session"),
                    source_from_file=mem["source_session_present"],
                    created_at=mem.get("created_at") or None,
                    updated_at=mem.get("updated_at") or None,
                )
                # The creation time is what time-scoped recall filters on; the file's
                # value is the true one.
                if mem["created_at"]:
                    store.set_created_at(mem["name"], mem["created_at"])
            except Exception as exc:
                counts["failed"] = counts.get("failed", 0) + 1
                boot_log(f"{f.name}: index refresh failed: {exc}; file retained for retry")
                continue
            counts["updated" if row is not None else "imported"] += 1
        if heal:
            try:
                set_project_line(f, mem["project"])
                boot_log(f"{f.name} had lost its project line (an older Dream rewrote it); "
                         f"restored {mem['project']!r} from the database")
            except (OSError, ValueError):
                pass

    for row in store.all_memories(scope=None):
        if row["slug"] not in seen:
            store.delete_memory(row["slug"])
            counts["dropped"] += 1

    write_index()
    return counts


def _resolve_line(f: Path, mem: dict[str, Any], known: set[str]) -> None:
    """A project line naming no known key (DREAM-108): a folder name, a path, 'global' ... One
    match: the memory takes that key and the line is rewritten to it. No match, or several: a
    line shaped like a key is kept as written, anything else leaves the memory hidden. The boot
    log says which; a hand-written line never costs the boot."""
    line = mem["project_line"]
    try:
        key = resolve_project(line, extra=known)
    except (ValueError, RuntimeError, OSError) as exc:
        if mem["project"]:
            boot_log(f"{f.name} names project {mem['project']!r}, which is not a project Dream knows "
                     f"({exc}); kept there (memory_list(all_projects=true) shows it)")
        else:
            boot_log(f"{f.name}: its project line {line!r} is not a project key ({exc}); "
                     "the memory stays hidden until the line names one (all_projects=true lists it)")
        return
    mem["project"] = key
    try:
        set_project_line(f, key)
        note = "the line now says so"
    except (OSError, ValueError) as exc:
        note = f"the line was not rewritten: {_why_not(exc)}"
    boot_log(f"{f.name}: its project line {line!r} names project {key!r}; {note}")


def adopt_orphans(store: MemoryStore, owner: str) -> list[str]:
    """File the memories with no project line and no project in their row: one an older
    Dream wrote joins the project of the session that wrote it; one the owner wrote by
    hand joins ``owner``, the project of the session that is starting. The line is
    written into each file (DREAM-108). Only after the one-time migration (before it,
    such a file is legacy and the migration files it by evidence). Two starts at once:
    the row update is conditional, so one of them adopts each file."""
    if not valid_owner(owner) or owner in (USER, UNASSIGNED) or not store.scope_migrated():
        return []
    with store._lock:
        rows = store._conn.execute(
            "SELECT m.slug, COALESCE(s.project, '') FROM memories m "
            "LEFT JOIN sessions s ON s.id = m.source_session WHERE m.project=''").fetchall()
    files = memory_file_map()
    adopted = []
    for slug, source in rows:
        target = source if valid_owner(source) and source not in (USER, UNASSIGNED) else owner
        path = files.get(slug)
        mem = read_file(path) if path is not None else None
        if mem is None or mem["project_line"] is not None:
            continue
        with store._lock:
            cur = store._conn.execute(
                "UPDATE memories SET project=? WHERE slug=? AND project=''", (target, slug))
            store._conn.commit()
        if cur.rowcount != 1:
            continue
        try:
            set_project_line(path, target)
        except (OSError, ValueError) as exc:     # ValueError: a file that is not UTF-8
            boot_log(f"{path.name}: filed under {target!r}, but its project line was not written: "
                     f"{_why_not(exc)}")
        adopted.append((slug, target))
    if adopted:
        try:
            write_index()
        except OSError:
            pass
        boot_log(f"{len(adopted)} memory file(s) without a project line filed under the project of the "
                 "session that wrote them, or else of the session that started: "
                 + ", ".join(f"{slug} -> {target}" for slug, target in adopted))
    return [slug for slug, _ in adopted]


def import_markdown(store: MemoryStore) -> int:
    """Back-compatible entry point: sync, reporting how many files the database did
    not have. Kept because the engine and the tests have always called this."""
    counts = sync(store)
    return counts["imported"] + counts["moved"]


_EDIT_LOCK = threading.RLock()
_EDIT_LOCAL = threading.local()


@contextmanager
def memory_edit_lock():
    """Serialize cooperating memory writes across threads, stores and processes."""
    import fcntl
    with _EDIT_LOCK:
        if getattr(_EDIT_LOCAL, "depth", 0):
            _EDIT_LOCAL.depth += 1
            try:
                yield
            finally:
                _EDIT_LOCAL.depth -= 1
            return
        config.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(config.MEMORY_DIR / '.lifecycle.lock',
                     os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _EDIT_LOCAL.depth = 1
            yield
        finally:
            _EDIT_LOCAL.depth = 0
            os.close(fd)


class MemoryVersionError(ValueError):
    """The authoritative file changed since the caller inspected it."""


def update_lifecycle(store: MemoryStore, slug: str, lifecycle: dict, *,
                     expected_version: str, scope: tuple[str, ...]) -> dict:
    """Explicit lifecycle edit, serialized with other lifecycle edits by a file lock.

    expected_version is SHA256 of the exact file bytes, not second-resolution
    updated_at. Only the lifecycle line changes; custom metadata/body are retained.
    External editors do not participate in the advisory lock: a final digest
    check detects observed edits, but cannot make arbitrary editors transactional.
    On a post-replace index/durability error, inspect the file then sync to recover.
    """
    from .curation import lifecycle_json, active_status, MAX_MEMORY_BYTES

    encoded = lifecycle_json(lifecycle)
    if not isinstance(expected_version, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_version):
        raise ValueError('expected_version must be a SHA256 file digest')
    if not isinstance(scope, tuple) or not scope or any(not valid_owner(p) for p in scope):
        raise ValueError('an explicit authorized scope is required')
    if slugify(slug) != slug:
        raise ValueError('an exact memory slug is required')
    files = memory_file_map()
    path = files.get(slug)
    if path is None or path.is_symlink():
        raise ValueError('an existing regular memory file is required')
    with memory_edit_lock():
        with store._lock:
            if path.stat().st_size > MAX_MEMORY_BYTES:
                raise ValueError('memory exceeds bounded lifecycle edit size')
            original = path.read_bytes()
            if hashlib.sha256(original).hexdigest() != expected_version:
                raise MemoryVersionError('memory changed; inspect it and retry with its current version')
            mem = read_file(path)
            if mem is None or mem.get('project') not in scope:
                raise ValueError('memory is outside the authorized scope')
            indexed = store.get_memory(slug)
            if indexed and not mem["source_session_present"] and not indexed.get("source_from_file"):
                mem["source_session"] = indexed.get("source_session")
            target_slug = json.loads(encoded).get('superseded_by')
            if target_slug:
                target_path = files.get(target_slug)
                if target_path and (target_path.is_symlink() or target_path.stat().st_size > MAX_MEMORY_BYTES):
                    raise ValueError('supersession target must be a bounded regular file')
                target = read_file(target_path) if target_path else None
                if (target_slug == slug or not target or target.get('project') != mem['project']
                        or active_status(target) != 'eligible'):
                    raise ValueError('supersession requires a different valid memory in the same scope')
                # No cycles: a target already explicitly superseded is ambiguous.
                if json.loads(target['lifecycle']).get('superseded_by'):
                    raise ValueError('supersession target is already superseded')
            text = original.decode('utf-8')
            bom = '\ufeff' if text.startswith('\ufeff') else ''
            lines = text[len(bom):].splitlines(keepends=True)
            end = next((i for i in range(1,len(lines)) if lines[i].strip() == '---'), None) if lines and lines[0].strip() == '---' else None
            if end is None:
                new = f'---\nlifecycle: {encoded}\n---\n' + text
            else:
                newline = '\r\n' if lines[0].endswith('\r\n') else '\n'
                header = [ln for ln in lines[1:end] if ln.partition(':')[0].strip() != 'lifecycle']
                new = bom + lines[0] + ''.join(header) + f'lifecycle: {encoded}{newline}' + ''.join(lines[end:])
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_version:
                raise MemoryVersionError('memory changed during lifecycle update')
            durability_error = None
            try:
                _atomic_write(path, new)
            except MemoryDurabilityError as exc:
                durability_error = exc
            try:
                row = store.upsert_memory(
                    kind=mem['kind'], title=mem['title'], body=mem['body'], slug=slug,
                    tags=mem['tags'], salience=mem['salience'], description=mem['description'],
                    mem_type=mem['mem_type'], project=mem['project'], embed=False,
                    source_session=mem.get('source_session'), source_from_file=mem['source_session_present'],
                    created_at=mem.get('created_at') or None,
                    updated_at=mem.get('updated_at') or None, lifecycle=encoded,
                )
            except Exception as exc:
                from .store import MemoryIndexError
                raise MemoryIndexError('lifecycle saved to file; index failed; inspect file then sync') from exc
            if durability_error:
                raise durability_error
            return row
