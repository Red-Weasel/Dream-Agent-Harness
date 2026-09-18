"""Memory tools on the files: list, read, write, append, str_replace, delete.

Phase 9b. The memory IS the file (`dream/memory/longterm.py`), so these are
file tools with three things a plain file tool does not have: a `version` that
every write must present (a hand-edit changes it, so a stale model never
overwrites what the user just fixed), a size cap whose remedy is consolidation,
and a refusal for the one kind of content that is never filed — an instruction
to suppress disagreement, concern, or honest evaluation.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any

from claude_agent_sdk import tool

from .. import config
from ..memory import longterm
from ..memory.store import MEM_TYPES, MemoryIndexError, _now, default_type, slugify
from .context import ctx, err, in_thread, ok

# --- never filed -----------------------------------------------------------------------
# One place for the patterns; every write tool and `remember` check here. The
# test carries a dozen phrasings that must be refused and several that must pass.

_SUPPRESS_RE = re.compile(
    r"""
    \b(?:never|don'?t|do\s+not|stop|avoid|no\s+more|without|quit)\s+
        (?:\w+\s+){0,3}?
        (?:push(?:ing)?[\s-]*back|disagree(?:ing|ment)?|object(?:ing|ions?)?|
           challeng(?:e|ing)|question(?:ing)?\s+(?:the\s+user|him|her|them|me|decisions?|orders?)|
           rais(?:e|ing)\s+(?:concerns?|risks?|problems?|issues?|objections?|doubts?)|
           mention(?:ing)?\s+(?:risks?|concerns?|problems?|downsides?|caveats?|bugs?|doubts?)|
           flag(?:ging)?\s+(?:risks?|concerns?|problems?|issues?|bugs?)|
           warn(?:ing)?|critici[sz](?:e|ing|m)|critiqu(?:e|ing)|
           (?:be(?:ing)?\s+)?(?:honest|candid|critical|blunt|frank)|
           (?:report(?:ing)?|surfac(?:e|ing)|admit(?:ting)?|show(?:ing)?|disclos(?:e|ing)|reveal(?:ing)?)\s+
               (?:errors?|failures?|mistakes?|bugs?|defects?|problems?|uncertaint(?:y|ies)|doubts?|risks?))
    | \b(?:always|just|only|simply)\s+(?:\w+\s+){0,2}?(?:agree|comply|obey|say\s+yes|go\s+along|approve|defer)
    | \b(?:hide|suppress|swallow|bury|downplay|conceal|minimi[sz]e|gloss\s+over|cover\s+up|omit)\s+
        (?:\w+\s+){0,3}?
        (?:errors?|failures?|mistakes?|bugs?|defects?|flaws?|concerns?|risks?|problems?|doubts?|
           disagreements?|uncertaint(?:y|ies)|caveats?|downsides?|bad\s+news)
    | \b(?:pretend|claim|say|report)\s+(?:\w+\s+){0,3}?(?:works?|passed|succeeded|is\s+fine|is\s+done|is\s+ok)\s+
        (?:when|even\s+if|if|though|although)\s+(?:\w+\s+){0,3}?(?:not|n't|fail|didn't|isn't|broken)
    | \bno\s+(?:push[\s-]*back|disagreement|objections?|criticism|warnings?|caveats?|pushing\s+back|negativity)\b
    | \b(?:never|don'?t|do\s+not|stop|avoid|without)\s+(?:\w+\s+){0,3}?
        (?:contradict(?:ing)?|argu(?:e|ing)\s+with|second[\s-]*guess(?:ing)?|correct(?:ing)?\s+(?:the\s+user|him|her|them|me)|
           tell(?:ing)?\s+(?:the\s+user|him|her|them|me)\s+about\s+(?:bugs?|problems?|risks?|failures?|errors?))
    | \b(?:withhold(?:ing)?|sugarcoat(?:ing)?)\s+(?:\w+\s+){0,3}?(?:concerns?|failures?|problems?|bad\s+news|the\s+truth|risks?|errors?)
    | \bkeep\s+(?:\w+\s+){0,2}?(?:concerns?|doubts?|problems?|objections?|opinions?)\s+to\s+(?:yourself|myself)
    | \bstay\s+quiet\s+about | \bonly\s+tell\s+(?:the\s+user|him|her|them|me)\s+good\s+news | \byes[\s-]*man\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _plain(text: str) -> str:
    """The text as a rule can read it: compatibility-normalized (fullwidth letters
    fold to ASCII), markdown emphasis and fences removed, whitespace collapsed
    so a phrase broken across lines is still the phrase."""
    import html
    import unicodedata

    t = unicodedata.normalize("NFKC", html.unescape(str(text or "")))
    t = re.sub(r"[\u200b\u200c\u200d\u2060\u00ad\ufeff]", " ", t)  # zero-width, soft hyphen: a gap
    t = re.sub(r"<[^>]{1,80}>", " ", t)                             # markup between words
    t = re.sub(r"[*_`~]+", "", t)                                   # emphasis, fences
    t = re.sub(r"[.,;:—–…]+", " ", t)                               # punctuation inside a phrase
    return " ".join(t.split())


def suppression(*texts: Any) -> str | None:
    """Why a text is never filed, or None. Applied to EVERY field the model would
    write — body, title, description, an appended line, a replacement — never
    to what it merely read. Gate 9b: a title or a description that skipped the
    check reached the index, which is in every wake-up."""
    for text in texts:
        m = _SUPPRESS_RE.search(_plain(text))
        if m:
            return (f"never filed: {m.group(0).strip()!r} would instruct me to suppress "
                    "disagreement, concern, or honest evaluation — no memory may do that")
    return None


# --- versions ----------------------------------------------------------------------------


def version_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _name(raw: Any) -> str | None:
    # slugify has a fallback ("note") for input with no letters or digits; a
    # memory needs a real name, or two junk names would address one file.
    if not re.sub(r"[^a-z0-9]+", "", str(raw or "").lower()):
        return None
    name = slugify(str(raw or ""))
    if not name or longterm.is_reserved(f"{name}.md"):
        return None
    return name


def _path(name: str):
    return config.MEMORY_DIR / f"{name}.md"


def _read(name: str) -> tuple[str, str] | None:
    p = _path(name)
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    return text, version_of(text)


def _stale(name: str, given: Any, current: tuple[str, str]) -> dict[str, Any] | None:
    text, ver = current
    if given == ver:
        return None
    why = "no if_version given" if not given else f"if_version {given!r} is stale"
    return err(f"{name}: {why}; the memory was changed since you last read it (or you "
               f"never read it). Current version {ver}. Current content:\n\n{text}")


async def _finish_async(mem: dict[str, Any], verb: str) -> dict[str, Any]:
    """Write the row, the file, the index; report the version and any cap note.
    The cap is checked on the rendered file — frontmatter included — because
    that is what the cap is about: the file the user opens."""
    # Measured on what will be persisted: the store stamps both timestamps, and
    # a render without them is ~50 chars short of the file the user opens.
    probe = dict(mem)
    stamp = _now()
    probe["created_at"] = probe.get("created_at") or stamp
    probe["updated_at"] = stamp
    text = longterm._render(probe)
    if len(text) > config.MEMORY_FILE_MAX:
        return err(f"{mem['slug']}: refused — {longterm.near_cap(text)}. Nothing was written.")
    c = ctx()
    try:
        row = await in_thread(
            c.store.upsert_memory, kind=mem["kind"], title=mem["title"], body=mem["body"],
            slug=mem["slug"], tags=mem.get("tags", ""), salience=float(mem.get("salience", 1.0)),
            source_session=c.session_id, description=mem.get("description", ""),
            mem_type=mem.get("mem_type", ""), created_at=mem.get("created_at"),
            persist_markdown=True, embed=False,
        )
    except (ValueError, OSError, sqlite3.Error, MemoryIndexError) as e:
        return err(str(e))
    path = longterm.path_for(row)
    text = longterm._render(row)
    warnings = []
    try:
        await in_thread(longterm.write_index)
    except OSError as e:
        warnings.append(f"Memory saved, but MEMORY.md could not be refreshed: {e}. "
                        "Restart Dream to rebuild the index.")
    if c.store.embedder is not None:
        warnings.append("Optional semantic indexing is deferred until embedding backfill.")
    note = longterm.near_cap(text)
    return ok(f"{verb} {mem['slug']} ({len(text):,} chars, version {version_of(text)}) → {path}"
              + (f"\nNote: {note}." if note else "")
              + ("\n" + "\n".join(warnings) if warnings else ""))



# --- the tools -----------------------------------------------------------------------------


@tool(
    "memory_list",
    "List every memory: name, type, description, size, version. The names are what "
    "memory_read and the write tools take.",
    {"type": "object", "properties": {}, "required": []},
)
async def memory_list(args: dict[str, Any]) -> dict[str, Any]:
    files = await in_thread(longterm.memory_files)
    if not files:
        return ok("No memories yet.")
    lines = [f"{len(files)} memories (name · type · description · chars · version):"]
    for f in files:
        mem = longterm.read_file(f)
        if not mem:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        lines.append(f"- {mem['name']} · {mem['mem_type'] or default_type(mem['kind'], mem['title'], mem['body'])}"
                     f" · {longterm.describe(mem)[:100]} · {len(text):,} · {version_of(text)}")
    return ok("\n".join(lines))


@tool(
    "memory_read",
    "Read one memory whole, with its version. Every write to it must present that "
    "version, so read before you write.",
    {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
)
async def memory_read(args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args.get("name"))
    if not name:
        return err("memory_read: that is not a memory name.")
    cur = await in_thread(_read, name)
    if cur is None:
        return err(f"memory_read: no memory named {name!r}. memory_list shows the names.")
    text, ver = cur
    note = longterm.near_cap(text)
    return ok(f"version: {ver}\n\n{text}" + (f"\nNote: {note}." if note else ""))


_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The memory's name (its file stem)."},
        "content": {"type": "string", "description": "The body. Fact lines get a provenance tag."},
        "title": {"type": "string"},
        "type": {"type": "string", "description": "user | feedback | project | reference (derived if omitted)."},
        "description": {"type": "string", "description": "One line for the memory index."},
        "provenance": {"type": "string", "description": "stated | observed | inferred (default) for untagged lines."},
        "if_version": {"type": "string", "description": "From memory_read; required when the memory exists."},
    },
    "required": ["name", "content"],
}


@tool(
    "memory_write",
    "Create a memory, or replace the body of one that exists. Replacing needs if_version "
    "from memory_read. Refused: a body over the size cap (consolidate instead), or any "
    "instruction to suppress disagreement, concern, or honest evaluation.",
    _WRITE_SCHEMA,
)
async def memory_write(args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args.get("name"))
    if not name:
        return err("memory_write: that is not a usable memory name (reserved, empty, or a path).")
    prov = str(args.get("provenance") or "inferred")
    if prov not in longterm.PROVENANCE:
        return err("memory_write: provenance must be stated, observed, or inferred.")
    mtype = str(args.get("type") or "")
    if mtype and mtype not in MEM_TYPES:
        return err(f"memory_write: type must be one of {', '.join(MEM_TYPES)}.")
    content = str(args.get("content") or "")
    if not content.strip():
        return err("memory_write: the content is empty. memory_delete removes a memory.")
    why = suppression(content, args.get("title"), args.get("description"))
    if why:
        return err(f"memory_write: {why}.")
    body = longterm.tag_body(content, prov)
    cur = await in_thread(_read, name)
    if cur is None:
        mem = {"slug": name, "kind": "semantic", "title": str(args.get("title") or name.replace("-", " ")),
               "body": body, "description": str(args.get("description") or ""), "mem_type": mtype}
        return await _finish_async(mem, "Created")
    stale = _stale(name, args.get("if_version"), cur)
    if stale:
        return stale
    old = longterm.read_file(_path(name)) or {}
    mem = {"slug": name, "kind": old.get("kind", "semantic"),
           "title": str(args.get("title") or old.get("title") or name),
           "body": body, "tags": old.get("tags", ""), "salience": old.get("salience", 1.0),
           "created_at": old.get("created_at", ""),
           "description": str(args.get("description") or old.get("description") or ""),
           "mem_type": mtype or old.get("mem_type", "")}
    return await _finish_async(mem, "Replaced")


@tool(
    "memory_append",
    "Add lines to the end of a memory. Needs if_version from memory_read. Same refusals "
    "as memory_write.",
    {"type": "object", "properties": {
        "name": {"type": "string"}, "text": {"type": "string"},
        "provenance": {"type": "string", "description": "stated | observed | inferred (default)."},
        "if_version": {"type": "string"}}, "required": ["name", "text", "if_version"]},
)
async def memory_append(args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args.get("name"))
    if not name:
        return err("memory_append: that is not a memory name.")
    cur = await in_thread(_read, name)
    if cur is None:
        return err(f"memory_append: no memory named {name!r}; memory_write creates one.")
    stale = _stale(name, args.get("if_version"), cur)
    if stale:
        return stale
    prov = str(args.get("provenance") or "inferred")
    if prov not in longterm.PROVENANCE:
        return err("memory_append: provenance must be stated, observed, or inferred.")
    text = str(args.get("text") or "")
    if not text.strip():
        return err("memory_append: nothing to append.")
    why = suppression(text)
    if why:
        return err(f"memory_append: {why}.")
    old = longterm.read_file(_path(name))
    mem = dict(old)
    mem["body"] = (old["body"].rstrip() + "\n" + longterm.tag_body(text, prov)).strip()
    return await _finish_async(mem, "Appended to")


@tool(
    "memory_str_replace",
    "Replace one exact string in a memory's body with another. old_string must match "
    "exactly once — widen it with surrounding lines otherwise. Needs if_version.",
    {"type": "object", "properties": {
        "name": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
        "if_version": {"type": "string"}}, "required": ["name", "old_string", "new_string", "if_version"]},
)
async def memory_str_replace(args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args.get("name"))
    if not name:
        return err("memory_str_replace: that is not a memory name.")
    cur = await in_thread(_read, name)
    if cur is None:
        return err(f"memory_str_replace: no memory named {name!r}.")
    stale = _stale(name, args.get("if_version"), cur)
    if stale:
        return stale
    old_s, new_s = str(args.get("old_string") or ""), str(args.get("new_string") or "")
    if not old_s:
        return err("memory_str_replace: old_string is empty.")
    why = suppression(new_s)
    if why:
        return err(f"memory_str_replace: {why}.")
    old = longterm.read_file(_path(name))
    n = old["body"].count(old_s)
    if n != 1:
        return err(f"memory_str_replace: old_string matches {n} time(s) in {name}; it must match "
                   "exactly once. Widen it with surrounding lines. Nothing was changed.")
    mem = dict(old)
    mem["body"] = old["body"].replace(old_s, new_s, 1)
    if mem["body"].strip() == "":
        return err("memory_str_replace: that would empty the memory; memory_delete removes one.")
    return await _finish_async(mem, "Edited")


@tool(
    "memory_delete",
    "Delete a memory file and its index line. Needs if_version from memory_read.",
    {"type": "object", "properties": {"name": {"type": "string"}, "if_version": {"type": "string"}},
     "required": ["name", "if_version"]},
)
async def memory_delete(args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args.get("name"))
    if not name:
        return err("memory_delete: that is not a memory name.")
    cur = await in_thread(_read, name)
    if cur is None:
        return err(f"memory_delete: no memory named {name!r}.")
    stale = _stale(name, args.get("if_version"), cur)
    if stale:
        return stale
    c = ctx()
    await in_thread(c.store.delete_memory, name)
    await in_thread(longterm.delete_markdown, name)
    return ok(f"Deleted {name}.")


MEMORY_FILE_TOOLS = [memory_list, memory_read, memory_write, memory_append, memory_str_replace, memory_delete]
