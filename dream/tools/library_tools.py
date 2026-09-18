"""The Library, as the model reaches for it.

Eight tools over ``library.store``. The shape they enforce is the store's contract
restated where the model will actually read it: resolve the target first, pick ONE
access route, and preserve identity when writing.

The one rule worth repeating everywhere it applies: **an edit is a replace, not a
create.** A model that writes ``plan-v2.md`` and creates it fresh has silently forked
the user's document and orphaned its history. Every write tool's description says so,
because the description is the only part that is guaranteed to be read.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from .. import config
from ..library.store import Library, LibraryError, VersionConflict
from .context import err, in_thread, ok

_LIB: Library | None = None


def library() -> Library:
    """The process-wide Library, opened on first use."""
    global _LIB
    if _LIB is None:
        _LIB = Library(config.LIBRARY_DB, config.LIBRARY_BLOBS)
    return _LIB


def _fail(e: Exception) -> dict[str, Any]:
    """Store refusals are for the model to act on; anything else is a real fault."""
    if isinstance(e, VersionConflict):
        return err(f"{e}  (this is a conflict, not a permissions problem — do NOT "
                   f"retry without the version guard)")
    if isinstance(e, LibraryError):
        return err(str(e))
    return err(f"{type(e).__name__}: {e}")


# --- reading -----------------------------------------------------------------


@tool(
    "library_list",
    "List Library files, newest first — the inventory route. Use it for 'what's in "
    "my Library', 'what did I work on recently', or to see a folder's contents. To "
    "find something by name or subject, use library_search instead: listing is not "
    "searching, and paging through everything to find one file wastes the turn.",
    {
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "Restrict to one folder, e.g. '/plans'. Omit for all."},
            "limit": {"type": "integer", "description": "Max files (default 25, cap 200)."},
            "offset": {"type": "integer", "description": "Skip this many, to page."},
        },
        "required": [],
    },
)
async def library_list(args: dict[str, Any]) -> dict[str, Any]:
    try:
        files = await in_thread(
            library().list, args.get("folder"), int(args.get("limit", 25)),
            int(args.get("offset", 0)))
    except Exception as e:
        return _fail(e)
    if not files:
        return ok("The Library is empty." if not args.get("folder")
                  else f"Nothing in folder '{args['folder']}'.")
    return ok(f"{len(files)} file(s):\n" + "\n".join(f"• {f.summary()}" for f in files))


@tool(
    "library_search",
    "Find Library files by name or by what's in them. This is the default way to "
    "resolve 'the plan I wrote', 'my notes on X', or an exact filename. Set "
    "title_only when you know the actual name and want to avoid matching bodies. "
    "Search returns snippets — read the file with library_read before making claims "
    "about its contents; a snippet is a pointer, not evidence.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words to match. Names are matched with stemming, so 'revenue' finds 'Revenues'."},
            "top_k": {"type": "integer", "description": "How many hits (default 5, cap 100)."},
            "title_only": {"type": "boolean", "description": "Match names only, ignoring file contents."},
        },
        "required": ["query"],
    },
)
async def library_search(args: dict[str, Any]) -> dict[str, Any]:
    try:
        hits = await in_thread(library().search, str(args.get("query", "")),
                               int(args.get("top_k", 5)),
                               bool(args.get("title_only", False)))
    except Exception as e:
        return _fail(e)
    if not hits:
        return ok(f"No Library file matches '{args.get('query')}'.")
    lines = []
    for h in hits:
        lines.append(f"• {h.file.summary()}")
        if h.snippet:
            lines.append(f"    …{h.snippet}…")
    return ok(f"{len(hits)} hit(s):\n" + "\n".join(lines))


@tool(
    "library_read",
    "Read a Library file's text, by id. Do this before answering any question about "
    "what a file says — search snippets are not enough to make a claim on. Pass a "
    "version to read an older one. Binary files are refused here; use "
    "library_materialize to get their bytes onto disk instead.",
    {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "The library_file_id, from list or search."},
            "version": {"type": "integer", "description": "Read a specific version (default: current)."},
        },
        "required": ["file_id"],
    },
)
async def library_read(args: dict[str, Any]) -> dict[str, Any]:
    fid = str(args.get("file_id", ""))
    try:
        f = await in_thread(library().get, fid)
        version = args.get("version")
        text, truncated = await in_thread(
            library().read, fid, int(version) if version is not None else None)
    except Exception as e:
        return _fail(e)
    head = f"{f.path} (id {f.id} · v{args.get('version', f.version)})"
    tail = "\n\n[truncated — materialize it if you need the whole file]" if truncated else ""
    return ok(f"{head}:\n\n{text}{tail}")


@tool(
    "library_find",
    "Search INSIDE specific Library files for a literal string or regex, with line "
    "numbers. Use it after you have resolved which files matter — to locate a clause, "
    "a value, or every mention of a term. It is not a substitute for library_search: "
    "this one needs the ids up front.",
    {
        "type": "object",
        "properties": {
            "file_ids": {"type": "array", "items": {"type": "string"},
                         "description": "The files to look inside."},
            "pattern": {"type": "string", "description": "Literal text, or a regex if regex=true."},
            "regex": {"type": "boolean", "description": "Treat the pattern as a regex (default false)."},
        },
        "required": ["file_ids", "pattern"],
    },
)
async def library_find(args: dict[str, Any]) -> dict[str, Any]:
    ids = args.get("file_ids") or []
    if isinstance(ids, str):
        ids = [ids]
    try:
        hits = await in_thread(library().find, [str(i) for i in ids],
                               str(args.get("pattern", "")),
                               regex=bool(args.get("regex", False)))
    except Exception as e:
        return _fail(e)
    if not hits:
        return ok(f"No match for '{args.get('pattern')}' in those files.")
    return ok(f"{len(hits)} match(es):\n" + "\n".join(
        f"• {h.file.name}:{h.line_no}  {h.line}" for h in hits))


@tool(
    "library_resolve",
    "Turn a name, a local path, or an id into ONE Library file you can act on. Use it "
    "before any write when you are not already certain which file is meant — it either "
    "commits to a single match or hands you the candidates and tells you to ask the user. "
    "It also answers 'where did this local file come from?', so a file you materialized "
    "earlier resolves back to its Library identity without you remembering an id.",
    {
        "type": "object",
        "properties": {
            "reference": {
                "type": "string",
                "description": "A filename, a phrase describing the document, an "
                "absolute local path, or a library_file_id.",
            }
        },
        "required": ["reference"],
    },
)
async def library_resolve(args: dict[str, Any]) -> dict[str, Any]:
    ref = str(args.get("reference") or "").strip()
    if not ref:
        return err("library_resolve needs a 'reference'.")
    lib = library()

    # 1. An id, used verbatim. Cheapest and unambiguous.
    try:
        f = await in_thread(lib.get, ref)
        return ok(f"Resolved to {f.summary()}")
    except Exception:
        pass

    # 2. A local path Dream materialized. This is the case that makes an edit safe
    #    after the id has fallen out of context.
    if "/" in ref or ref.startswith("~"):
        found = await in_thread(lib.origin, ref)
        if found is not None:
            f, seen, confidence = found
            note = "" if seen == f.version else (
                f"\n  You checked out v{seen}; the Library is now at v{f.version} — "
                f"re-read before replacing.")
            if confidence not in ("stamped", "unstampable"):
                note += (
                    f"\n  WEAK MATCH: the file no longer carries its Library stamp, so "
                    f"this is only a path record. Confirm it is really '{f.name}' before "
                    f"writing — replace by file_id, not by path.")
            return ok(f"'{ref}' came from the Library: {f.summary()}{note}")
        return err(
            f"'{ref}' has no Library history. Dream has no record of materializing it, "
            f"so it is either a new local file (use library_create) or it came from "
            f"somewhere else. Do not guess an id.")

    # 3. A name or a description. Commit only when the answer is unambiguous.
    try:
        exact = await in_thread(lib.search, ref, 5, True)
        hits = exact or await in_thread(lib.search, ref, 5, False)
    except Exception as e:
        return _fail(e)
    if not hits:
        return ok(f"Nothing in the Library matches '{ref}'. It may not be there — say "
                  f"so rather than creating a duplicate on the assumption it is.")
    if len(hits) == 1:
        return ok(f"Resolved to {hits[0].file.summary()}")
    listing = "\n".join(f"  {i}. {h.file.summary()}" for i, h in enumerate(hits, 1))
    return ok(f"'{ref}' is ambiguous — {len(hits)} candidates:\n{listing}\n"
              f"Ask the user which one before writing to any of them. Reading one to "
              f"decide is fine; guessing on a write is not.")


# --- writing -----------------------------------------------------------------


@tool(
    "library_create",
    "Put a NEW file into the Library — a deliverable worth keeping, not scratch work. "
    "Only for something with no Library identity yet. If you are producing a new "
    "version of a file that is already there, use library_replace instead: creating a "
    "second copy forks the user's document and orphans its history, which is the one "
    "mistake this store exists to prevent. Search first if you are unsure it is new.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path of the local file to take in."},
            "content": {"type": "string", "description": "Text content, as an alternative to path."},
            "name": {"type": "string", "description": "Library filename. Required with content; defaults to the local name with path."},
            "folder": {"type": "string", "description": "Library folder, e.g. '/plans' (default '/')."},
            "note": {"type": "string", "description": "Why this version exists — shown in history."},
        },
        "required": [],
    },
)
async def library_create(args: dict[str, Any]) -> dict[str, Any]:
    path, content = args.get("path"), args.get("content")
    if not path and content is None:
        return err("Pass either 'path' (a local file) or 'content' (text) to create.")
    source: Any = path if path else str(content).encode("utf-8")
    try:
        f = await in_thread(library().create, source, name=args.get("name"),
                            folder=args.get("folder", "/"), note=args.get("note", ""))
    except Exception as e:
        return _fail(e)
    return ok(f"Created in Library: {f.summary()}\nKeep this id — replacing it later "
              f"is what keeps its history intact.")


@tool(
    "library_replace",
    "Give an EXISTING Library file new contents: same id, next version, history "
    "preserved. This is the right tool whenever you have edited something that came "
    "out of the Library, even if your editor wrote to a different local filename. "
    "If you materialized the file, you can pass just its 'path' and omit file_id — "
    "Dream remembers which Library item that path came from, so the id does not have "
    "to survive in your context. Pass expected_current_version with the version you "
    "actually read — if someone else changed it meanwhile you get a conflict instead "
    "of silently overwriting them. On a conflict, re-read and merge; never retry "
    "without the guard.",
    {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "The library_file_id to update. Optional if 'path' is a file you materialized."},
            "path": {"type": "string", "description": "Absolute path of the local file holding the new contents. If it came from the Library, its identity is looked up automatically."},
            "content": {"type": "string", "description": "New text content, as an alternative to path."},
            "expected_current_version": {"type": "integer", "description": "The version you read. Omit only if you are the sole writer."},
            "note": {"type": "string", "description": "What changed — shown in history."},
        },
        # Neither field is unconditionally required: an update is identified by
        # file_id OR by a path Dream stamped. The handler enforces "one of", which a
        # JSON Schema `required` list cannot express.
        "required": [],
    },
)
async def library_replace(args: dict[str, Any]) -> dict[str, Any]:
    path, content = args.get("path"), args.get("content")
    if not path and content is None:
        return err("Pass either 'path' or 'content' with the new version.")
    file_id = str(args.get("file_id") or "").strip()
    exp = args.get("expected_current_version")

    # No id, but a path that came out of the Library: look up what it is. This is the
    # whole point of stamping — the id no longer has to survive in context for an edit
    # to land on the right document.
    if not file_id and path:
        found = await in_thread(library().origin, str(path))
        if found is None:
            return err(
                f"No file_id given, and '{path}' has no Library history — Dream has no "
                f"record of materializing it. If this is a NEW document use "
                f"library_create; if it IS a Library file, find it with library_search "
                f"and pass its file_id.")
        f0, seen_version, confidence = found
        if confidence not in ("stamped", "unstampable"):
            # Only a path-keyed row says these are the same file. A different file
            # dropped at that path looks exactly like an editor that rewrote the
            # original — and guessing wrong here overwrites a document that was never
            # part of this task. Make the caller commit to an id instead.
            return err(
                f"'{path}' is recorded as a checkout of '{f0.name}' (id {f0.id}), but "
                f"the file no longer carries its Library stamp — it was replaced or "
                f"rewritten by something that dropped it. Dream cannot tell your edit "
                f"apart from an unrelated file now sitting at that path, and will not "
                f"guess. If this really is '{f0.name}', re-send with "
                f"file_id={f0.id}. If it is new work, use library_create.")
        file_id = f0.id
        # The version they checked out is the honest guard when none was supplied.
        if exp is None:
            exp = seen_version
    if not file_id:
        return err("library_replace needs a 'file_id', or a 'path' that came from the "
                   "Library. To create something new, use library_create.")

    # An explicit id and a stamped path that name different documents is not a
    # preference to resolve — it is a sign the caller is working from a stale belief.
    # Writing either one would be a guess about which.
    if path and args.get("file_id"):
        found = await in_thread(library().origin, str(path))
        if found is not None and found[2] in ("stamped", "unstampable") \
                and found[0].id != file_id:
            return err(
                f"Refusing: you asked to update {file_id}, but '{path}' carries the "
                f"stamp of '{found[0].name}' (id {found[0].id}). One of those is "
                f"wrong. Check which document you mean with library_resolve before "
                f"writing to either.")

    source: Any = path if path else str(content).encode("utf-8")
    try:
        f = await in_thread(library().replace, file_id, source,
                            expected_current_version=int(exp) if exp is not None else None,
                            note=args.get("note", ""),
                            # Re-stamp the file we saved FROM, so the next edit of it
                            # carries the version it now holds. Without this the path
                            # route is single-use.
                            restamp=path if path else None)
    except Exception as e:
        return _fail(e)
    return ok(f"Replaced: {f.summary()}")


@tool(
    "library_materialize",
    "Copy a Library file's bytes to a local path so ordinary tools can work on it — "
    "editing, running, diffing, or anything that needs real bytes rather than text in "
    "context. Remember where it came from: when you are done editing, the result goes "
    "back with library_replace on the SAME id, not library_create.",
    {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "The library_file_id to materialize."},
            "dest": {"type": "string", "description": "Local destination path, or a directory to use the Library name."},
            "version": {"type": "integer", "description": "Materialize an older version (default: current)."},
        },
        "required": ["file_id", "dest"],
    },
)
async def library_materialize(args: dict[str, Any]) -> dict[str, Any]:
    v = args.get("version")
    try:
        out = await in_thread(library().materialize, str(args.get("file_id", "")),
                              str(args.get("dest", "")),
                              int(v) if v is not None else None)
    except Exception as e:
        return _fail(e)
    return ok(f"Wrote {out} — replace the same library_file_id when you're done editing.")


_ACTIONS = ("rename", "move", "delete", "undelete", "versions", "restore_version",
            "folders", "trash", "purge")


@tool(
    "library_manage",
    "Organise the Library: rename, move, delete, undelete, list a file's versions, or "
    "restore an old one; 'folders' lists where things are filed and 'trash' lists what "
    "was deleted so you can undelete it. Deletes are recoverable — the file leaves "
    "listings and search "
    "but its bytes and history stay, so undelete brings it back whole. Restoring is "
    "also non-destructive: it appends the old contents as a NEW version, so the thing "
    "you undid is still there if the undo was the mistake. 'purge' is the ONE "
    "exception: it destroys the file, its history, and its bytes for good, so only "
    "run it when the user has asked for exactly that. Resolve which file you mean before "
    "calling this; if several candidates fit, ask rather than guess.",
    {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS),
                       "description": "What to do."},
            "file_id": {"type": "string", "description": "The file to act on. Not needed for 'folders' or 'trash'."},
            "name": {"type": "string", "description": "New name, for rename."},
            "folder": {"type": "string", "description": "New folder, for move."},
            "version": {"type": "integer", "description": "Which version, for restore_version."},
            "limit": {"type": "integer", "description": "For 'trash': how many (default 50, cap 200)."},
            "offset": {"type": "integer", "description": "For 'trash': skip this many, to page back further."},
        },
        "required": ["action"],
    },
)
async def library_manage(args: dict[str, Any]) -> dict[str, Any]:
    action, fid = str(args.get("action", "")), str(args.get("file_id", ""))
    if action not in _ACTIONS:
        return err(f"Unknown action '{action}'. One of: {', '.join(_ACTIONS)}.")
    lib = library()
    try:
        if action == "folders":
            rows = await in_thread(lib.folder_counts)
            if not rows:
                return ok("No folders — everything is at the root.")
            return ok("Folders:\n" + "\n".join(
                f"• {name}  ({n} file{'s' if n != 1 else ''})" for name, n in rows))
        if action == "trash":
            gone = await in_thread(lib.deleted, int(args.get("limit", 50)),
                                   int(args.get("offset", 0)))
            if not gone:
                return ok("Trash is empty — nothing deleted.")
            return ok("Deleted files (undelete by id):\n" + "\n".join(
                f"• {g.summary()}" for g in gone))
        if not fid:
            return err(f"'{action}' needs a 'file_id'.")
        if action == "purge":
            rep = await in_thread(lib.purge, fid)
            msg = (f"Purged '{rep['name']}' permanently — {rep['versions']} version(s) "
                   f"and {rep['bytes_freed']:,} bytes gone. This cannot be undone.")
            if rep.get("unremovable_blobs"):
                # The store goes to the trouble of reporting these; swallowing them
                # here would tell the user bytes are gone while they sit on disk.
                msg += (f"\n{len(rep['unremovable_blobs'])} blob(s) could NOT be "
                        f"deleted from disk — the records are gone but those bytes "
                        f"remain. Check permissions on the Library's blob directory.")
            return ok(msg)
        if action == "rename":
            if not args.get("name"):
                return err("rename needs a 'name'.")
            return ok(f"Renamed: {(await in_thread(lib.rename, fid, str(args['name']))).summary()}")
        if action == "move":
            if not args.get("folder"):
                return err("move needs a 'folder'.")
            return ok(f"Moved: {(await in_thread(lib.move, fid, str(args['folder']))).summary()}")
        if action == "delete":
            f = await in_thread(lib.delete, fid)
            return ok(f"Deleted '{f.name}' — recoverable with undelete; nothing was erased.")
        if action == "undelete":
            return ok(f"Restored: {(await in_thread(lib.undelete, fid)).summary()}")
        if action == "versions":
            hist = await in_thread(lib.versions, fid)
            if not hist:
                return ok("No versions recorded.")
            return ok("Versions (newest first):\n" + "\n".join(
                f"• v{h['version']} · {h['size_bytes']:,} bytes · {h['created'][:19]}"
                + (f" · {h['note']}" if h["note"] else "") for h in hist))
        v = args.get("version")
        if v is None:
            return err("restore_version needs a 'version'.")
        f = await in_thread(lib.restore_version, fid, int(v))
        return ok(f"Restored v{v} as the new v{f.version}: {f.summary()}")
    except Exception as e:
        return _fail(e)


LIBRARY_TOOLS = [
    library_list, library_search, library_read, library_find, library_resolve,
    library_create, library_replace, library_materialize, library_manage,
]
