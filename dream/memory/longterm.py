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

``memory/MEMORY.md`` is regenerated from the files on every write: one line per
memory, and nothing else. It is an index, never content.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .. import config
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
        "---",
        "",
        str(mem["body"]).rstrip(),
        "",
    ]
    return "\n".join(fm)


def parse_markdown(text: str) -> tuple[dict[str, str], str]:
    """Split a memory file into (frontmatter dict, body). Tolerant of files without
    frontmatter. Fences must be full ``---`` lines so a ``---`` inside the body (a
    horizontal rule, say) can't truncate content."""
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
    _atomic_write(p, _render(mem))
    if index:
        write_index()
    return p


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
        if f.is_file() and not is_reserved(f.name)
    )


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
        fm, body = parse_markdown(path.read_text(encoding="utf-8", errors="replace"))
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
    return {
        "name": name, "slug": name, "kind": kind, "mem_type": mem_type,
        "title": fm.get("title") or path.stem.replace("-", " ").title(),
        "description": fm.get("description", ""), "body": body,
        "tags": fm.get("tags", ""), "salience": salience,
        "created_at": fm.get("created_at", ""), "updated_at": fm.get("updated_at", ""),
    }


def write_index() -> Path:
    """Regenerate ``memory/MEMORY.md`` from the files. One line per memory, newest
    first — a pointer, never the content."""
    rows = []
    for f in memory_files():
        mem = read_file(f)
        if mem:
            rows.append((mem.get("updated_at") or mem.get("created_at") or "", mem, f.name))
    rows.sort(key=lambda r: r[0], reverse=True)
    lines = [
        "# Memory Index",
        "",
        "One line per memory, newest first. Regenerated on every write — edit the",
        "memory files themselves, not this index.",
        "",
    ]
    for _, mem, fname in rows:
        hook = describe(mem)
        title = str(mem["title"]).replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- [{title}]({fname})" + (f" — {hook}" if hook else ""))
    if not rows:
        lines.append("_(no memories yet)_")
    _atomic_write(config.MEMORY_INDEX_FILE, "\n".join(lines) + "\n")
    return config.MEMORY_INDEX_FILE


def index_lines(limit: int = 40) -> list[str]:
    """The index as the wake context loads it: the same one-liners, bounded."""
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
    return not mem["created_at"] or mem["created_at"] == row.get("created_at", "")


def sync(store: MemoryStore) -> dict[str, int]:
    """Make the database agree with the files. Files win, always.

    A file whose rendered row differs from what is on disk was edited by hand, so
    the row is rebuilt from the file. A row whose file is gone is dropped. A file
    with no row is imported. Nothing here needs a tool call, which is the point:
    the user edits a memory in their editor and the next boot knows it.
    """
    counts = {"moved": migrate_layout(), "imported": 0, "updated": 0, "dropped": 0}
    seen: set[str] = set()
    for f in memory_files():
        mem = read_file(f)
        if mem is None:
            continue
        seen.add(mem["name"])
        row = store.get_memory(mem["name"])
        if row is not None and _same(row, mem):
            continue  # the file says what the row says: nothing changed
        try:
            store.upsert_memory(
                kind=mem["kind"], title=mem["title"], body=mem["body"], slug=mem["name"],
                tags=mem["tags"], salience=mem["salience"],
                description=mem["description"],
                mem_type=mem["mem_type"] or default_type(mem["kind"], mem["title"], mem["body"]),
            )
            # The creation time is what time-scoped recall filters on; the file's
            # value is the true one.
            if mem["created_at"]:
                store.set_created_at(mem["name"], mem["created_at"])
        except Exception:
            continue
        counts["updated" if row is not None else "imported"] += 1

    for row in store.all_memories():
        if row["slug"] not in seen:
            store.delete_memory(row["slug"])
            counts["dropped"] += 1

    write_index()
    return counts


def import_markdown(store: MemoryStore) -> int:
    """Back-compatible entry point: sync, reporting how many files the database did
    not have. Kept because the engine and the tests have always called this."""
    counts = sync(store)
    return counts["imported"] + counts["moved"]
