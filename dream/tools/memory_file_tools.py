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
from ..memory import project as projects
from ..memory.store import MEM_TYPES, MemoryIndexError, ScopeError, _now, default_type, slugify
from .context import ctx, err, in_thread, ok
from .memory_tools import (
    ALL_PROJECTS_ARG, FROM_PROJECT_ARG, PROJECT_ARG, SCOPE_ARG, here, move_refusal, not_here, owned,
    read_scope, resolve_project,
)

# Writes to a memory another project owns name that project (DREAM-108).
_OWNER_ARG = {"type": "string", "description": "Only to change a memory another project owns: that project."}

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


def _owner_of(name: str) -> str:
    mem = longterm.read_file(_path(name)) or {}
    return mem.get("project") or ""


def _write_target(tool: str, name: str, args: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """(the project a write to an EXISTING memory acts in, or an error). This project's
    and user-wide memories need nothing; another project's needs that project named."""
    owner, current = _owner_of(name), here()
    if not args.get("project"):
        if owned(owner, current):
            return owner or current, None
        hint = (f"Pass project={owner or projects.UNASSIGNED!r} to change it there."
                if tool != "memory_write" or (owner and owner != projects.UNASSIGNED)
                else "memory_move it to a project first, then write it there.")
        return None, err(f"{tool}: " + not_here(f"memory {name!r}", owner, hint))
    try:
        named = resolve_project(args["project"])
    except ValueError as e:
        return None, err(f"{tool}: {e}.")
    if named != (owner or projects.UNASSIGNED):
        return None, err(f"{tool}: memory {name!r} belongs to {owner or projects.UNASSIGNED}, "
                         f"not {named}; memory_move moves a memory.")
    return owner, None


async def _finish_async(mem: dict[str, Any], verb: str) -> dict[str, Any]:
    """Write the row, the file, the index; report the version and any cap note.
    The cap is checked on the rendered file — frontmatter included — because
    that is what the cap is about: the file the user opens. ``mem['project']`` is
    the project it is written in (DREAM-108); ``mem['reassign']`` moves it there."""
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
            project=mem["project"] if mem.get("project") is not None else here(),
            reassign=bool(mem.get("reassign")),
        )
    except ScopeError as e:
        return err(not_here(f"memory {mem['slug']!r}", e.owner, "Name its project to change it there."))
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
    owner = projects.tag(row.get("project") or "", here())
    return ok(f"{verb} {mem['slug']}{owner} ({len(text):,} chars, version {version_of(text)}) → {path}"
              + (f"\nNote: {note}." if note else "")
              + ("\n" + "\n".join(warnings) if warnings else ""))



# --- the tools -----------------------------------------------------------------------------


@tool(
    "memory_list",
    "List this project's and user-wide memories: name, type, description, size, version. "
    "The names are what memory_read and the write tools take.",
    {"type": "object", "properties": {"project": PROJECT_ARG, "all_projects": ALL_PROJECTS_ARG},
     "required": []},
)
async def memory_list(args: dict[str, Any]) -> dict[str, Any]:
    try:
        scope, current, cross = read_scope(args)
    except ValueError as e:
        return err(f"memory_list: {e}.")
    files = await in_thread(longterm.memory_files)
    rows = []
    for f in files:
        mem = longterm.read_file(f)
        if not mem or (scope is not None and (mem.get("project") or "") not in scope):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        rows.append(f"- {mem['name']} · {mem['mem_type'] or default_type(mem['kind'], mem['title'], mem['body'])}"
                    f" · {longterm.describe(mem)[:100]} · {len(text):,} · {version_of(text)}"
                    + projects.tag(mem.get("project") or "", current, cross=cross))
    if not rows:
        return ok("No memories yet." if scope is None else
                  "No memories in this scope. memory_list(all_projects=true) lists every project's.")
    return ok("\n".join([f"{len(rows)} memories (name · type · description · chars · version):", *rows]))


@tool(
    "memory_read",
    "Read one memory whole, with its version. Every write to it must present that "
    "version, so read before you write.",
    {"type": "object", "properties": {"name": {"type": "string"}, "project": PROJECT_ARG,
                                      "all_projects": ALL_PROJECTS_ARG}, "required": ["name"]},
)
async def memory_read(args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args.get("name"))
    if not name:
        return err("memory_read: that is not a memory name.")
    try:
        scope, current, cross = read_scope(args)
    except ValueError as e:
        return err(f"memory_read: {e}.")
    cur = await in_thread(_read, name)
    if cur is None:
        return err(f"memory_read: no memory named {name!r}. memory_list shows the names.")
    owner = _owner_of(name)
    if scope is not None and owner not in scope and not owned(owner, current):
        # An exact name is no way around the scope (DREAM-108).
        return err("memory_read: " + not_here(f"memory {name!r}", owner,
                                              "Pass project= with its project, or all_projects=true, "
                                              "to read another project's memory."))
    text, ver = cur
    note = longterm.near_cap(text)
    return ok(f"version: {ver}{projects.tag(owner, current, cross=cross)}\n\n{text}"
              + (f"\nNote: {note}." if note else ""))


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
        "scope": SCOPE_ARG,
        "project": _OWNER_ARG,
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
    scope = str(args.get("scope") or "project").strip().lower()
    if scope not in ("project", "user"):
        return err("memory_write: scope must be 'project' (default) or 'user'.")
    if args.get("project"):
        try:
            named = resolve_project(args["project"])
        except ValueError as e:
            return err(f"memory_write: {e}.")
        if named == projects.UNASSIGNED:
            # Unassigned is where the migration leaves what it could not place, never a
            # destination (DREAM-108 gate): not for a new memory, not for a rewrite.
            return err("memory_write: unassigned is not a destination; leave project out to write "
                       "here, or memory_move an unassigned memory to a project first.")
    body = longterm.tag_body(content, prov)
    cur = await in_thread(_read, name)
    if cur is None:
        owner = projects.USER if scope == "user" else here()
        if args.get("project") and scope != "user":
            try:
                owner = resolve_project(args["project"])
            except ValueError as e:
                return err(f"memory_write: {e}.")
        mem = {"slug": name, "kind": "semantic", "title": str(args.get("title") or name.replace("-", " ")),
               "body": body, "description": str(args.get("description") or ""), "mem_type": mtype,
               "project": owner}
        return await _finish_async(mem, "Created")
    owner, refused = _write_target("memory_write", name, args)
    if refused:
        return refused
    stale = _stale(name, args.get("if_version"), cur)
    if stale:
        return stale
    old = longterm.read_file(_path(name)) or {}
    # scope='user' on this project's own memory promotes it, deliberately.
    promote = scope == "user" and owner == here()
    mem = {"slug": name, "kind": old.get("kind", "semantic"),
           "title": str(args.get("title") or old.get("title") or name),
           "body": body, "tags": old.get("tags", ""), "salience": old.get("salience", 1.0),
           "created_at": old.get("created_at", ""),
           "description": str(args.get("description") or old.get("description") or ""),
           "mem_type": mtype or old.get("mem_type", ""),
           "project": projects.USER if promote else owner, "reassign": promote}
    return await _finish_async(mem, "Replaced")


@tool(
    "memory_append",
    "Add lines to the end of a memory. Needs if_version from memory_read. Same refusals "
    "as memory_write.",
    {"type": "object", "properties": {
        "name": {"type": "string"}, "text": {"type": "string"},
        "provenance": {"type": "string", "description": "stated | observed | inferred (default)."},
        "if_version": {"type": "string"}, "project": _OWNER_ARG}, "required": ["name", "text", "if_version"]},
)
async def memory_append(args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args.get("name"))
    if not name:
        return err("memory_append: that is not a memory name.")
    cur = await in_thread(_read, name)
    if cur is None:
        return err(f"memory_append: no memory named {name!r}; memory_write creates one.")
    owner, refused = _write_target("memory_append", name, args)
    if refused:
        return refused
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
    mem["project"] = owner
    return await _finish_async(mem, "Appended to")


@tool(
    "memory_str_replace",
    "Replace one exact string in a memory's body with another. old_string must match "
    "exactly once — widen it with surrounding lines otherwise. Needs if_version.",
    {"type": "object", "properties": {
        "name": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"},
        "if_version": {"type": "string"}, "project": _OWNER_ARG},
     "required": ["name", "old_string", "new_string", "if_version"]},
)
async def memory_str_replace(args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args.get("name"))
    if not name:
        return err("memory_str_replace: that is not a memory name.")
    cur = await in_thread(_read, name)
    if cur is None:
        return err(f"memory_str_replace: no memory named {name!r}.")
    owner, refused = _write_target("memory_str_replace", name, args)
    if refused:
        return refused
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
    mem["project"] = owner
    return await _finish_async(mem, "Edited")


@tool(
    "memory_delete",
    "Delete a memory file and its index line. Needs if_version from memory_read.",
    {"type": "object", "properties": {"name": {"type": "string"}, "if_version": {"type": "string"},
                                      "project": _OWNER_ARG},
     "required": ["name", "if_version"]},
)
async def memory_delete(args: dict[str, Any]) -> dict[str, Any]:
    name = _name(args.get("name"))
    if not name:
        return err("memory_delete: that is not a memory name.")
    cur = await in_thread(_read, name)
    if cur is None:
        return err(f"memory_delete: no memory named {name!r}.")
    _owner, refused = _write_target("memory_delete", name, args)
    if refused:
        return refused
    stale = _stale(name, args.get("if_version"), cur)
    if stale:
        return stale
    c = ctx()
    await in_thread(c.store.delete_memory, name)
    await in_thread(longterm.delete_markdown, name)
    return ok(f"Deleted {name}.")


@tool(
    "memory_move",
    "Give a memory (name) or a past session and its notes (session) to another project, "
    "or a memory to 'user' (every project). For misfiled or unassigned items.",
    {"type": "object", "properties": {
        "name": {"type": "string"}, "session": {"type": "string"},
        "project": {"type": "string", "description": "Folder name or key, or 'user'."},
        "from_project": FROM_PROJECT_ARG},
     "required": ["project"]},
)
async def memory_move(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    try:
        target = resolve_project(args.get("project"))
    except ValueError as e:
        return err(f"memory_move: {e}.")
    if target == projects.UNASSIGNED:
        return err("memory_move: unassigned is where the migration leaves what it could not place, "
                   "not a destination; name a project.")
    if bool(args.get("name")) == bool(args.get("session")):
        return err("memory_move: pass name (a memory) or session (a session id), one of them.")
    if args.get("session"):
        if target == projects.USER:
            return err("memory_move: a session belongs to one project; 'user' is for memories.")
        sid = str(args["session"])
        sess = await in_thread(c.store.get_session, sid)
        if sess is None:
            return err(f"memory_move: no session {sid!r}. recall_sessions(all_projects=true) lists them.")
        before = sess.get("project") or projects.UNASSIGNED
        refused = move_refusal(f"session {sid!r}", before, args)
        if refused:
            return err(f"memory_move: {refused}")
        if before == target:
            return ok(f"Session {sid} is already in {target}; nothing moved.")
        moved = await in_thread(c.store.move_session, sid, target)
        return ok(f"Moved session {sid} and its {moved} note(s) from {before} to {target}.")
    name = _name(args.get("name"))
    if not name or await in_thread(_read, name) is None:
        return err(f"memory_move: no memory named {args.get('name')!r}. "
                   "memory_list(all_projects=true) shows the names.")
    before = _owner_of(name) or projects.UNASSIGNED
    refused = move_refusal(f"memory {name!r}", before, args)
    if refused:
        return err(f"memory_move: {refused}")
    if before == target:
        return ok(f"Memory {name} is already in {target}; nothing moved.")
    try:
        await in_thread(longterm.set_project_line, _path(name), target)
    except (OSError, ValueError) as e:
        return err(f"memory_move: the file was not changed: {e}")
    await in_thread(c.store.move_memory, name, target)
    try:
        await in_thread(longterm.write_index)
    except OSError as e:
        return ok(f"Moved memory {name} from {before} to {target}; MEMORY.md was not refreshed: {e}.")
    return ok(f"Moved memory {name} from {before} to {target}.")


MEMORY_FILE_TOOLS = [memory_list, memory_read, memory_write, memory_append, memory_str_replace,
                     memory_delete, memory_move]
