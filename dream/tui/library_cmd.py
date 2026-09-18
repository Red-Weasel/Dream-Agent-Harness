"""`/library` — the Library from where the user actually sits.

The store had nine tools and had never been used once, because nothing reached it
from the TUI: the model could file things and the user could not see them. A library
nobody can open is a folder. This is the human side.

Subcommands mirror Claude Code's shape (`/thing verb args`), never `@`-mentions —
the user's call. All logic lives here rather than in ``app.py`` so the tests drive the
exact code path the TUI does, with a captured printer and a scratch store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from rich.markup import escape as _rich_escape
from rich.text import Text

from ..library.store import Library, LibraryError

Print = Callable[[object], None]   # a str with markup, or a rich Text

USAGE = (
    "[dim]/library                 list files, newest first\n"
    "/library search <words>  find by name or contents\n"
    "/library open <id|name>  print a file\n"
    "/library folders         where things are filed\n"
    "/library trash           what was deleted\n"
    "/library undelete <id>   bring one back[/dim]"
)


def _esc(s: str) -> str:
    """Rich markup treats [brackets] as tags; file contents are not markup.

    ``rich.markup.escape``, not a hand-rolled replace: a body containing ``\\[red]``
    (any regex, LaTeX, or Python source) was being turned into an escaped backslash
    followed by a LIVE tag, which dropped text and painted what followed.
    """
    return _rich_escape(s)


def _resolve_one(lib: Library, ref: str):
    """One file, or a list of candidates, from what a person would actually type.

    Order matters and each step was a gate finding:
    - a full or 8-character id (every listing shows the short form — an ``open``
      that only took the long form was unreachable from the TUI);
    - an EXACT name, which wins even when siblings share a token (FTS is an OR of
      prefixes, so ``plan.md`` was "ambiguous" next to ``plan-alpha.md``);
    - then the search, which may be ambiguous and says so.
    A deleted file is reported as deleted, not as "nothing matches".
    """
    ids = lib.find_by_id_prefix(ref) if _looks_like_id(ref) else []
    if len(ids) == 1:
        return lib.get(ids[0], include_deleted=True), []
    if len(ids) > 1:
        return None, [lib.get(i, include_deleted=True) for i in ids]
    # A query, not a scan of list(limit=200): the scan had a cliff where the
    # 201st-oldest file could not be opened by name. Deleted rows are included so
    # a deleted file resolves and is REPORTED as deleted, not as "nothing matches".
    name = ref.rsplit("/", 1)[-1]
    exact = lib.by_name(name, include_deleted=True)
    if "/" in ref:
        exact = [f for f in exact if f.path.lower() == ref.lower()]
    live = [f for f in exact if not f.deleted]
    pick = live or exact
    if len(pick) == 1:
        return pick[0], []
    if len(pick) > 1:
        return None, pick
    hits = lib.search(ref, 5, True) or lib.search(ref, 5, False)
    if len(hits) == 1:
        return hits[0].file, []
    return None, [h.file for h in hits]


def _looks_like_id(ref: str) -> bool:
    """Eight hex chars is what every listing shows; four let a file NAMED `cafe`
    lose to whichever id happened to start with it."""
    r = ref.strip().lower()
    return 8 <= len(r) <= 32 and all(c in "0123456789abcdef" for c in r)


async def run(arg: str, out: Print, lib: Library) -> None:
    """Dispatch one `/library ...` line. Prints through ``out``; never raises."""
    parts = arg.split(maxsplit=1)
    verb = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    try:
        if verb == "":
            files = lib.list(limit=25)
            if not files:
                out("[dim]Library is empty — nothing filed yet.[/dim]")
                out(USAGE)
                return
            out(f"[bold]{len(files)} file(s)[/bold] (newest first):")
            for f in files:
                out(f"  [cyan]{f.id[:8]}[/cyan]  {_esc(f.path)}  [dim]v{f.version} · "
                    f"{f.size_bytes:,} B · {f.updated[:16]}[/dim]")
            return

        if verb == "search":
            if not rest:
                out("[red]/library search <words>[/red]")
                return
            hits = lib.search(rest, 10)
            if not hits:
                out(f"[dim]nothing matches '{_esc(rest)}'[/dim]")
                return
            for h in hits:
                out(f"  [cyan]{h.file.id[:8]}[/cyan]  {_esc(h.file.path)}  [dim]v{h.file.version}[/dim]")
                if h.snippet:
                    out(f"      [dim]…{_esc(h.snippet)}…[/dim]")
            return

        if verb == "open":
            if not rest:
                out("[red]/library open <id|name>[/red]")
                return
            f, candidates = _resolve_one(lib, rest)
            if f is None:
                if not candidates:
                    out(f"[dim]nothing matches '{_esc(rest)}'[/dim]")
                    return
                out(f"[yellow]'{_esc(rest)}' is ambiguous — {len(candidates)} candidates:[/yellow]")
                for c in candidates:
                    out(f"  [cyan]{c.id[:8]}[/cyan]  {_esc(c.path)}")
                out("[dim]/library open <id> to pick one[/dim]")
                return
            if f.deleted:
                out(f"[yellow]{_esc(f.path)} is deleted[/yellow] — /library undelete {f.id[:8]}")
                return
            text, truncated = lib.read(f.id)
            out(f"[bold]{_esc(f.path)}[/bold]  [dim]id {f.id} · v{f.version}[/dim]")
            # A Text renderable, not an escaped string: Rich's plain-text pass still
            # rewrites `\[` to `[` after markup escaping, so any body with a
            # backslash before a bracket (regex, LaTeX) lost characters. Handing
            # the console a Text skips the markup parser entirely — lossless.
            out(Text(text))
            if truncated:
                out("[dim]…truncated — library_materialize for the whole file[/dim]")
            return

        if verb == "folders":
            rows = lib.folder_counts()
            if not rows:
                out("[dim]no folders — everything is at the root[/dim]")
                return
            for name, n in rows:
                out(f"  {_esc(name)}  [dim]({n} file{'s' if n != 1 else ''})[/dim]")
            return

        if verb == "trash":
            gone = lib.deleted(limit=25)
            if not gone:
                out("[dim]trash is empty[/dim]")
                return
            out(f"[bold]{len(gone)} deleted[/bold] — /library undelete <id> restores one:")
            for f in gone:
                out(f"  [cyan]{f.id[:8]}[/cyan]  {_esc(f.path)}  [dim]v{f.version} · {f.updated[:16]}[/dim]")
            return

        if verb == "undelete":
            if not rest:
                out("[red]/library undelete <id>[/red]")
                return
            fid = _expand_id(lib, rest)
            f = lib.undelete(fid)
            out(f"[green]restored[/green] {_esc(f.path)}  [dim]v{f.version}[/dim]")
            return

        out(f"[red]unknown: /library {_esc(verb)}[/red]")
        out(USAGE)
    except LibraryError as e:
        out(f"[red]{_esc(str(e))}[/red]")


def _expand_id(lib: Library, prefix: str) -> str:
    """Accept the 8-char prefix the listings show, not only the full id.

    Nobody retypes 32 hex characters. A prefix that matches more than one file is
    refused rather than guessed — an undelete aimed at the wrong file is exactly
    the kind of mistake a short id must not enable.
    """
    if len(prefix) >= 32:
        return prefix
    ids = lib.find_by_id_prefix(prefix)
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise LibraryError(f"no Library file with id starting '{prefix}'")
    raise LibraryError(f"'{prefix}' matches {len(ids)} files — use more of the id")


async def file_session(log_path: Path, session_id: str, lib: Library, out: Print,
                       *, tools: bool = True) -> None:
    """`/export library` — put this session into the Library instead of a loose file.

    A session transcript is the one deliverable Dream produces on every run, and
    `/export` was writing it to a path in $HOME where it is found by accident. Filed
    under `/sessions` with a stable id, it can be searched and read like anything
    else the user kept.
    """
    import asyncio
    import tempfile

    from ..tools.project import project_title
    from .export import export_session

    if not log_path.exists():
        out(f"[red]no session log at {log_path}[/red]")
        return
    name = f"session-{session_id}.md"
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / f"dream-{session_id}.md"
        # Off the event loop: a long session is a real amount of work, and the
        # sibling /export path already does this.
        title = project_title() or None
        _, n = await asyncio.to_thread(export_session, log_path, tmp, fmt="md", tools=tools,
                                       title=title)
        # Exporting the same session twice is one document with two versions, not
        # two documents — the Library's own rule, applied to the Library's own writes.
        prior = lib.by_name(name, folder="/sessions")
        if prior:
            f = lib.replace(prior[0].id, tmp,
                            note=f"re-exported ({n} events)" + (f": {title}" if title else ""))
            verb = f"updated to v{f.version}"
        else:
            f = lib.create(tmp, name=name, folder="/sessions",
                           note=f"exported from /export library ({n} events)"
                                + (f": {title}" if title else ""))
            verb = "filed"
    out(f"[green]{verb} {n} events → Library[/green] {_esc(f.path)}  [dim]id {f.id}[/dim]")
