"""Memory tools: the agent's hands on its own long-term memory."""

from __future__ import annotations

import sqlite3
from typing import Any

from claude_agent_sdk import tool

from .. import config
from ..memory import longterm
from ..memory import project as projects
from ..memory.store import (
    MAX_ALTERNATIVE_QUERIES,
    MAX_RECALL_QUERY_CHARS,
    MAX_RECALL_RESULTS,
    MemoryIndexError,
    ScopeError,
    slugify,
)
from .context import ctx, err, in_thread, ok

# --- project scope (DREAM-108) ----------------------------------------------------------------
# Memory belongs to the project of the workspace it was written in. Reads see this
# project's items and user-wide ones; another project's come only when named, labelled.

PROJECT_ARG = {"type": "string", "description": "Another project (folder name or key): its items, labelled."}
ALL_PROJECTS_ARG = {"type": "boolean", "description": "Every project's items, unassigned included, labelled."}
SCOPE_ARG = {"type": "string", "description": "'user' only for a general fact or preference the user "
             "stated about themselves (shown in every project). Default: this project."}
FROM_PROJECT_ARG = {"type": "string", "description": "Where it is now; required when that is another project."}


def here() -> str:
    """This session's project key: its workspace's."""
    return projects.project_key(ctx().workspace)


def resolve_project(name: Any) -> str:
    """The key the model means (folder name, key, 'user', 'unassigned'); ValueError otherwise."""
    store = ctx().store
    return projects.resolve(name, store.known_owners() if store is not None else ())


def flag(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value or "").strip().lower() in ("true", "yes", "1")


def read_scope(args: dict[str, Any]) -> tuple[tuple[str, ...] | None, str, bool]:
    """(scope, this project, cross) for a read: this project and user-wide by default,
    one named project, or every project (None). ValueError names an unknown project."""
    current = here()
    if flag(args.get("all_projects")):
        return None, current, True
    if args.get("project"):
        key = resolve_project(args["project"])
        return (key,), current, key != current
    return (current, projects.USER), current, False


def owned(owner: str, current: str) -> bool:
    """Whether this project may use an item without naming its owner."""
    return (owner or "") in (current, projects.USER)


def not_here(what: str, owner: str, hint: str) -> str:
    owner = owner or projects.UNASSIGNED
    where = "is unassigned" if owner == projects.UNASSIGNED else f"belongs to project {owner}"
    return f"{what} {where}, not this project. {hint}"


def move_refusal(what: str, owner: str, args: dict[str, Any]) -> str | None:
    """Why a move of an item owned by ``owner`` must not go ahead, or None. This project's,
    user-wide and unassigned items move on a destination alone; another project's item only
    when the call names it as the source (``from_project``), so a model that confuses two
    projects cannot pull an item across by naming where it wants it (DREAM-108 gate)."""
    owner = owner or projects.UNASSIGNED
    if args.get("from_project"):
        try:
            source = resolve_project(args["from_project"])
        except ValueError as e:
            return f"{e}."
        return None if source == owner else f"{what} is in {owner}, not {source}; nothing moved."
    if owner in (here(), projects.USER, projects.UNASSIGNED):
        return None
    return (f"{what} belongs to project {owner}, not this project; name where it is now "
            f"(from_project={owner!r}) to move it.")


_REMEMBER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short title for the memory."},
        "body": {"type": "string", "description": "The fact or playbook to remember."},
        "kind": {
            "type": "string",
            "description": "semantic (facts, default) | procedural (playbook) | episodic (event).",
        },
        "tags": {"type": "string", "description": "Comma-separated tags (optional)."},
        "salience": {
            "type": "number",
            "description": "0-5, default 1.0; higher surfaces sooner on wake-up.",
        },
        "slug": {
            "type": "string",
            "description": "Pass an existing slug to update that memory in place.",
        },
        "type": {
            "type": "string",
            "description": "user | feedback | project | reference (derived if omitted).",
        },
        "description": {"type": "string", "description": "One line for the memory index."},
        "provenance": {
            "type": "string",
            "description": "stated (default) | observed | inferred — tags each fact line.",
        },
        "scope": SCOPE_ARG,
    },
    "required": ["title", "body"],
}


@tool(
    "remember",
    "Save something to long-term memory so it survives across sessions. Recall first; "
    "pass an existing slug to update a memory in place.",
    _REMEMBER_SCHEMA,
)
async def remember(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    kind = args.get("kind", "semantic")
    if kind not in ("semantic", "procedural", "episodic"):
        return err("kind must be 'semantic', 'procedural', or 'episodic'.")
    prov = str(args.get("provenance") or "stated")
    if prov not in longterm.PROVENANCE:
        return err("provenance must be 'stated', 'observed', or 'inferred'.")
    from .memory_file_tools import suppression  # one place for the never-filed rule

    why = suppression(args["body"], args.get("title"), args.get("description"), args.get("tags"))
    if why:
        return err(f"remember: {why}.")
    scope = str(args.get("scope") or "project").strip().lower()
    if scope not in ("project", "user"):
        return err("remember: scope must be 'project' (default) or 'user'.")
    current = here()
    owner = projects.USER if scope == "user" else current
    # scope='user' on this project's own memory promotes it -- deliberately, by name.
    reassign = False
    if scope == "user" and args.get("slug"):
        row = c.store.get_memory(config.free_memory_name(slugify(str(args["slug"]))))
        reassign = row is not None and (row.get("project") or "") == current
    # A playbook's steps are not facts; provenance tags fact memories.
    body = args["body"] if kind == "procedural" else longterm.tag_body(args["body"], prov)
    try:
        mem = await in_thread(
            c.store.upsert_memory,
            kind=kind,
            title=args["title"],
            body=body,
            slug=args.get("slug"),
            tags=args.get("tags", ""),
            salience=float(args.get("salience", 1.0)),
            source_session=c.session_id,
            description=str(args.get("description") or ""),
            mem_type=str(args.get("type") or ""),
            persist_markdown=True,
            embed=False,
            project=owner,
            reassign=reassign,
        )
    except ScopeError as e:
        hint = ("Leave out slug to save a new memory here; memory_append(name, text, "
                "if_version, project=...) changes it there.")
        return err("remember: " + not_here(f"memory {args.get('slug')!r}", e.owner, hint))
    except (ValueError, OSError, sqlite3.Error, MemoryIndexError) as e:
        return err(str(e))
    path = longterm.path_for(mem)
    warnings = []
    try:
        await in_thread(longterm.write_index)
    except OSError as e:
        warnings.append(f"Memory saved, but MEMORY.md could not be refreshed: {e}. "
                        "Restart Dream to rebuild the index.")
    note = longterm.near_cap(longterm._render(mem))
    if c.store.embedder is not None:
        warnings.append("Optional semantic indexing is deferred until embedding backfill.")
    return ok(f"Remembered [{kind}/{mem.get('mem_type') or '?'}] '{mem['title']}' "
              f"(slug: {mem['slug']}){projects.tag(mem.get('project') or '', current)} → {path}"
              + (f"\nNote: {note}." if note else "")
              + ("\n" + "\n".join(warnings) if warnings else ""))


_RECALL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_RECALL_QUERY_CHARS,
            "description": "What to recall (full-text search).",
        },
        "alternative_queries": {
            "type": "array",
            "maxItems": MAX_ALTERNATIVE_QUERIES,
            "items": {"type": "string", "minLength": 1, "maxLength": MAX_RECALL_QUERY_CHARS},
            "description": "Up to three caller-written lexical rephrasings. Dream does not generate them.",
        },
        "kind": {
            "type": "string",
            "description": "Restrict to 'semantic', 'procedural', or 'episodic' (optional).",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_RECALL_RESULTS,
            "description": "Max results (default 6).",
        },
        "since": {
            "type": "string",
            "description": "Only memories created on/after this ISO date, e.g. "
            "'2026-06-01' (optional; compute the date yourself from phrases like "
            "'last week').",
        },
        "until": {
            "type": "string",
            "description": "Only memories created on/before this ISO date (optional).",
        },
        "project": PROJECT_ARG,
        "all_projects": ALL_PROJECTS_ARG,
    },
    "required": ["query"],
}

_RECALL_FIELDS = frozenset(_RECALL_SCHEMA["properties"])


def _alternative_queries(args: dict[str, Any]) -> list[str] | None:
    """Validate caller-written alternatives without adding a rewriting layer."""
    values = args.get("alternative_queries")
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_ALTERNATIVE_QUERIES:
        return None
    primary = str(args["query"]).strip().casefold()
    alternatives: list[str] = []
    seen = {primary}
    for value in values:
        if not isinstance(value, str):
            return None
        alternative = value.strip()
        if not alternative or len(alternative) > MAX_RECALL_QUERY_CHARS:
            return None
        key = alternative.casefold()
        if key not in seen:
            alternatives.append(alternative)
            seen.add(key)
    return alternatives


def _recall_request(args: dict[str, Any]) -> tuple[str, int, list[str]] | None:
    if set(args) - _RECALL_FIELDS:
        return None
    query = args.get("query")
    if type(query) is not str or not query.strip() or len(query) > MAX_RECALL_QUERY_CHARS:
        return None
    limit = args.get("limit", 6)
    if type(limit) is not int or not 1 <= limit <= MAX_RECALL_RESULTS:
        return None
    alternatives = _alternative_queries(args)
    if alternatives is None:
        return None
    return query, limit, alternatives


@tool(
    "recall",
    "Search long-term memory for relevant facts, playbooks, and past events. Do this "
    "whenever a task touches something you might already know. For 'when did X "
    "happen' questions, filter with since/until and kind='episodic'.",
    _RECALL_SCHEMA,
)
async def recall(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    request = _recall_request(args)
    if request is None:
        return err(
            "recall accepts only query, kind, limit, since, until, alternative_queries, project "
            "and all_projects; "
            "query must be a non-empty string up to 256 characters, limit an integer from 1 to 20, "
            "and alternative_queries a list of up to three non-empty strings."
        )
    query, limit, alternatives = request
    try:
        scope, current, cross = read_scope(args)
    except ValueError as e:
        return err(f"recall: {e}.")
    search = (
        c.store.search_memories_with_alternatives
        if alternatives else c.store.search_memories
    )
    search_args = (query, alternatives) if alternatives else (query,)
    hits = await in_thread(
        search,
        *search_args,
        kind=args.get("kind"),
        limit=limit,
        since=args.get("since"),
        until=args.get("until"),
        scope=scope,
    )
    if not hits:
        return ok(f"No memories found for '{args['query']}'.")
    if hits[0].get("via_recency"):
        lines = [
            f"Nothing matched '{args['query']}' directly; the most recent memories "
            "in scope instead:"
        ]
    else:
        lines = [f"Recalled {len(hits)} memory(ies) for '{args['query']}':"]
    for h in hits:
        tag = " (linked)" if h.get("via_link") else ""
        matched = f" (matched alternative: {h['matched_query']!r})" if h.get("matched_query") else ""
        owner = projects.tag(h.get("project") or "", current, cross=cross)
        lines.append(
            f"\n• [{h['kind']}] {h['title']}{tag}{matched} (slug: {h['slug']}, salience {h['salience']}){owner}"
        )
        if h.get("tags"):
            lines.append(f"  tags: {h['tags']}")
        lines.append(f"  {h['body']}")
    return ok("\n".join(lines))


_FORGET_SCHEMA = {
    "type": "object",
    "properties": {"slug": {"type": "string", "description": "Slug of the memory to delete."}},
    "required": ["slug"],
}


@tool("forget", "Delete a long-term memory by its slug.", _FORGET_SCHEMA)
async def forget(args: dict[str, Any]) -> dict[str, Any]:
    c = ctx()
    row = await in_thread(c.store.get_memory, args["slug"])
    if row is not None and not owned(row.get("project") or "", here()):
        return err("forget: " + not_here(f"memory {args['slug']!r}", row.get("project") or "",
                                        "memory_delete(name, if_version, project=...) deletes it there."))
    deleted = await in_thread(c.store.delete_memory, args["slug"])
    if deleted:
        await in_thread(longterm.delete_markdown, args["slug"])
        return ok(f"Forgot '{args['slug']}'.")
    return err(f"No memory with slug '{args['slug']}'.")


_SESSIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "description": "How many (default 8)."},
        "query": {"type": "string", "description": "Topic words; with this, a search over "
                  "every past session's turns instead of the recent list."},
        "project": PROJECT_ARG,
        "all_projects": ALL_PROJECTS_ARG,
    },
    "required": [],
}


@tool(
    "recall_sessions",
    "Past sessions: the recent list with summaries and exact read_session arguments, "
    "or with `query` a topic search over "
    "what was said in other sessions — each hit names the session, the time, and a turn "
    "id that read_session opens. The current session is excluded.",
    _SESSIONS_SCHEMA,
)
async def recall_sessions(args: dict[str, Any]) -> dict[str, Any]:
    import json

    c = ctx()
    try:
        scope, current, cross = read_scope(args)
    except ValueError as e:
        return err(f"recall_sessions: {e}.")
    # Opening another project's session needs the same explicit argument again.
    reopen = ({"all_projects": True} if scope is None
              else {"project": scope[0]} if cross else {})
    query = str(args.get("query") or "").strip()
    if query:
        hits = await in_thread(c.store.search_turns, query, int(args.get("limit", 8)),
                               exclude_session_id=c.session_id, scope=scope)
        if not hits:
            return ok(f"No past session mentions '{query}'.")
        lines = [f"{len(hits)} hit(s) for '{query}' (read_session(id, at=turn) opens one):"]
        for h in hits:
            title = h.get("session_title") or "(untitled)"
            owner = projects.tag(h.get("project") or "", current, cross=cross)
            lines.append(f"\n• session {h['session_id']} — {title} — turn {h['id']} "
                         f"({h['role']}, {h['ts']}){owner}\n  {h['excerpt']}")
        return ok("\n".join(lines))
    sessions = await in_thread(c.store.recent_sessions, int(args.get("limit", 8)),
                               exclude_session_id=c.session_id, scope=scope)
    if not sessions:
        return ok("No past sessions recorded yet.")
    lines = ["Recent sessions:"]
    for s in sessions:
        when = s.get("started_at", "?")
        title = s.get("title") or "(untitled)"
        owner = projects.tag(s.get("project") or "", current, cross=cross)
        lines.append(f"\n• {when} — {title} ({s.get('turn_count', 0)} turns){owner}")
        lines.append("  Open: read_session(" + json.dumps(
            {"id": s["id"], **reopen}, ensure_ascii=False) + ")")
        if s.get("summary"):
            lines.append(f"  {s['summary']}")
    return ok("\n".join(lines))



@tool(
    "read_session",
    "Open a past session at a point: the turns around `at` (a turn id from "
    "recall_sessions), or its start. `window` turns each side, default 6. "
    "To page one exact turn, supply `at` and `offset` or `chars`; returns JSON "
    "with raw content and next_offset (null at end). Paging ignores window. "
    "Current-session recovery stops at the latest user request; its live tool log is excluded. "
    "Read additional pages only for missing detail relevant to the task.",
    {"type": "object", "properties": {
        "id": {"type": "string", "description": "The session id."},
        "at": {"type": "integer", "description": "A stored global turn id, not a session-local "
               "position; required and "
               "exact in page mode (1–9223372036854775807)."},
        "window": {"type": "integer", "description": "Turns each side (1–20)."},
        "offset": {"type": "integer", "minimum": 0, "maximum": 9223372036854775807,
                   "description": "Page start in raw Unicode code points before stripping "
                   "or indentation (default 0). Requires at. next_offset locates more detail."},
        "chars": {"type": "integer", "minimum": 1, "maximum": 12000,
                  "description": "Maximum raw code points per page (default 12000). "
                  "Pages may be shorter to fit the serialized result limit. Requires at."},
        "project": PROJECT_ARG, "all_projects": ALL_PROJECTS_ARG},
     "required": ["id"]},
)
async def read_session(args: dict[str, Any]) -> dict[str, Any]:
    import json

    paging = "offset" in args or "chars" in args
    if paging:
        at, offset, chars = args.get("at"), args.get("offset", 0), args.get("chars", 12000)
        for name, value, low, high in (("at", at, 1, 9223372036854775807),
                                       ("offset", offset, 0, 9223372036854775807),
                                       ("chars", chars, 1, 12000)):
            if type(value) is not int or not low <= value <= high:
                return err(f"read_session: page mode requires {name} to be an integer "
                           f"from {low} to {high}.")
    c = ctx()
    try:
        scope, current, cross = read_scope(args)
    except ValueError as e:
        return err(f"read_session: {e}.")
    # Session IDs are opaque. Trimming can select a different stored session,
    # including when following the exact locator from recall_sessions.
    sid = str(args.get("id") or "")
    sess = await in_thread(c.store.get_session, sid)
    owner = (sess.get("project") or "") if sess else ""
    if sess and scope is not None and owner not in scope and owner != current:
        # An exact id is no way around the scope (DREAM-108): another project's session opens
        # only when asked for by name, and then says whose it is.
        hint = (f"Pass project={owner!r} or all_projects=true to open it anyway."
                if owner else "Pass all_projects=true to open it anyway.")
        return err("read_session: " + not_here(f"session {sid!r}", owner, hint))
    if not sess:
        # Name the stored id the guess contains (a "session-" prefix, a ".md" suffix) WITHOUT opening it:
        # the exact id stays the model's to send (2026-09-23, three misspelt guesses in a row, live session).
        recent = await in_thread(c.store.recent_sessions, 200, scope=scope)
        near = [s["id"] for s in recent if s.get("id") and len(s["id"]) >= 8 and s["id"] in sid][:3]
        hint = (" Did you mean " + " or ".join(f"id={json.dumps(s, ensure_ascii=False)}" for s in near)
                + "? Send the id exactly as recall_sessions prints it after the word 'session'.") if near else ""
        return err(f"read_session: no session {sid!r}. recall_sessions lists them.{hint}")
    ceiling = (await in_thread(c.store.latest_user_turn_id, sid)
               if sid == c.session_id else None)
    bounds = {"through_turn": ceiling} if ceiling is not None else {}
    live_error = (f"read_session: turns after {ceiling} belong to the current request's "
                  "live log, not prior work. Use the current conversation or read_notes; "
                  "recover earlier work at or before that turn. Do not scan newer turn IDs.")
    if paging:
        if ceiling is not None and at > ceiling:
            return err(live_error)
        # This existing API materializes one record; only the returned page is bounded.
        turns = await in_thread(c.store.turns_window, sid, at, 0, **bounds)
        if not turns or turns[0]["id"] != at:
            return err(f"read_session: no turn {at} in session {sid!r}. "
                       "Use read_session with id only to find its turn IDs.")
        raw = str(turns[0]["content"])
        total = len(raw)
        if offset > total:
            return err(f"read_session: offset {offset} exceeds turn {at} length {total}. "
                       f"Use an offset from 0 to {total}.")
        end = min(offset + chars, total)
        def encode_page(stop: int) -> str:
            return json.dumps({"session_id": sid, "turn_id": at, "start": offset,
                               "end": stop, "total": total, "content": raw[offset:stop],
                               "next_offset": stop if stop < total else None}, ensure_ascii=False)

        encoded = encode_page(end)
        if len(encoded) <= config.TOOL_RESULT_CAP:
            return ok(encoded)
        # Escaped control characters and opaque IDs consume transport space too.
        # Check the complete page first: the final null cursor can be shorter than
        # a numeric cursor. All remaining candidates have numeric cursors, making
        # their encoded sizes monotonic for binary search.
        low, high = offset + 1, end - 1
        fitted = None
        while low <= high:
            middle = (low + high) // 2
            candidate = encode_page(middle)
            if len(candidate) <= config.TOOL_RESULT_CAP:
                fitted = candidate
                low = middle + 1
            else:
                high = middle - 1
        if fitted is None:
            return err("read_session: page cannot fit safely. Increase DREAM_TOOL_RESULT_CAP.")
        return ok(fitted)
    at = args.get("at")
    try:
        at = int(at) if at is not None else None
        window = 6 if args.get("window") is None else max(1, min(20, int(args["window"])))
    except (TypeError, ValueError):
        return err("read_session: at and window must be integers.")
    if ceiling is not None and at is not None and at > ceiling:
        return err(live_error)
    turns = await in_thread(c.store.turns_window, sid, at, window, **bounds)
    if not turns:
        return ok(f"Session {sid} ({sess.get('title') or 'untitled'}) has no turns"
                  + (f" at {at}" if at is not None else "") + ".")
    head = (f"Session {sid} — {sess.get('title') or '(untitled)'} — started {sess.get('started_at', '?')}"
            + projects.tag(owner, current, cross=cross))
    lines = [head, f"Stored turn IDs {turns[0]['id']}–{turns[-1]['id']}; "
             f"session has {sess.get('turn_count', '?')} records:"]
    if ceiling is not None:
        lines.append(f"Current-session recovery ends at user request turn {ceiling}; "
                     "later live records are excluded.")
    for t in turns:
        mark = " ◀" if at is not None and t["id"] == at else ""
        who = t["role"] + (f"/{t['tool_name']}" if t.get("tool_name") else "")
        body = str(t["content"]).strip().replace("\n", "\n    ")
        if len(body) > 1200:
            body = body[:1200] + "…"
            body += "\n    Read raw turn pages: read_session(" + json.dumps(
                {"id": sid, "at": t["id"], "offset": 0, "chars": 12000},
                ensure_ascii=False) + "). Read more only for relevant missing detail."
        lines.append(f"\n[{t['id']}] {who} {t['ts']}{mark}\n    {body}")
    return ok("\n".join(lines))
