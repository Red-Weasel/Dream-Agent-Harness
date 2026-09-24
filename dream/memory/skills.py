"""Skills — procedural memory with a shape.

A skill is what's left when a finished piece of work is distilled into something
reusable: when to reach for it, the steps, what bites, and how to check it still
works. Skills are not a second store — they *are* procedural memories, written
through ``upsert_memory`` so FTS recall, the markdown mirror, and consolidation
keep working on them unchanged.

Two things make them worth their own layer:

- **Patch, don't rewrite.** ``patch_skill`` amends one section and leaves the rest
  alone, so correcting a wrong step doesn't cost the accumulated detail around it.
- **Progressive disclosure.** ``index_lines`` yields one bounded line per skill
  ("slug — when to use"), cheap enough to carry in the system prompt; the full body
  is fetched with ``load_skill`` only when a skill is actually needed.

The body format is deliberately small and forgiving. A procedural memory written
before skills existed (or hand-edited into a different shape) still parses: its
prose becomes the when-to-use line rather than being discarded, so nothing is lost
by patching one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .store import MemoryStore, ScopeError

# The progressive-disclosure budget: N skills cost N short lines, nothing more.
INDEX_LIMIT = 30
INDEX_LINE_CHARS = 110

_HEADING_RE = re.compile(r"^##\s+(when to use|steps|pitfalls|verification)\s*$", re.I)
_ITEM_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_VERIFIED_RE = re.compile(r"^Last verified:\s*(.*)$", re.I)


@dataclass
class Skill:
    slug: str
    title: str
    when_to_use: str = ""
    steps: list[str] = field(default_factory=list)
    pitfalls: list[str] = field(default_factory=list)
    verification: str = ""
    # The day the procedure was last written or amended by someone who had just run
    # it. Not proof it still works — a marker for how stale the claim is.
    last_verified: str = ""


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _in_scope(store: MemoryStore, mem: dict[str, Any]) -> bool:
    """A skill is a procedural memory, so it belongs to a project too (DREAM-108): this
    store's project and user-wide skills are its own; another project's are not."""
    scope = store.default_scope()
    return scope is None or (mem.get("project") or "") in scope


def _clean(items: Iterable[str] | None) -> list[str]:
    return [str(i).strip() for i in (items or []) if str(i).strip()]


_CONT_INDENT = "   "  # continuation lines of a list item, so they round-trip


def _render_item(marker: str, text: str) -> str:
    head, *rest = str(text).split("\n")
    return "\n".join([marker + head] + [_CONT_INDENT + ln for ln in rest])


def _dedent_cont(line: str) -> str:
    return line[len(_CONT_INDENT):] if line.startswith(_CONT_INDENT) else line.strip()


def render_body(skill: Skill) -> str:
    """The canonical markdown body. Empty sections are omitted rather than left as
    bare headings, so a half-written skill reads as what it is."""
    parts: list[str] = []
    if skill.when_to_use.strip():
        parts.append("## When to use\n" + skill.when_to_use.strip())
    if skill.steps:
        parts.append("## Steps\n" + "\n".join(
            _render_item(f"{i}. ", s) for i, s in enumerate(skill.steps, 1)))
    if skill.pitfalls:
        parts.append("## Pitfalls\n" + "\n".join(
            _render_item("- ", p) for p in skill.pitfalls))
    if skill.verification.strip():
        parts.append("## Verification\n" + skill.verification.strip())
    if skill.last_verified:
        parts.append(f"Last verified: {skill.last_verified}")
    return "\n\n".join(parts)


def parse_skill(slug: str, title: str, body: str) -> Skill:
    """Read a procedural body back into a Skill. Only the four known headings split
    the text, so an unrecognized heading stays inside the section it was written in
    instead of vanishing on the next patch. Anything before the first known heading
    joins the when-to-use line — that's what makes a legacy procedural memory
    (all prose, no headings) survive being patched."""
    lines = (body or "").splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    last_verified = ""
    if lines:
        m = _VERIFIED_RE.match(lines[-1].strip())
        if m:
            last_verified = m.group(1).strip()
            lines.pop()

    blocks: dict[str, list[str]] = {"": []}
    current = ""
    for ln in lines:
        heading = _HEADING_RE.match(ln.strip())
        if heading:
            current = heading.group(1).lower()
            blocks.setdefault(current, [])
            continue
        blocks[current].append(ln)

    def text(key: str) -> str:
        return "\n".join(blocks.get(key, [])).strip()

    def items(key: str) -> list[str]:
        """One entry per marker, not per LINE. A step worth writing down carries
        the exact command, so it often spans lines (a fenced block, a pasted
        path); splitting per line renumbered those continuation lines as steps
        of their own and garbled the procedure."""
        out: list[str] = []
        for ln in blocks.get(key, []):
            if _ITEM_RE.match(ln):
                out.append(_ITEM_RE.sub("", ln).rstrip())
            elif out:
                out[-1] += "\n" + _dedent_cont(ln)
            elif ln.strip():
                out.append(ln.strip())  # an unmarked first line is still an item
        return [s for s in (s.strip() for s in out) if s]

    return Skill(
        slug=slug,
        title=title,
        when_to_use="\n\n".join(t for t in (text(""), text("when to use")) if t),
        steps=items("steps"),
        pitfalls=items("pitfalls"),
        verification=text("verification"),
        last_verified=last_verified,
    )


def save_skill(
    store: MemoryStore,
    title: str,
    when_to_use: str,
    steps: Iterable[str],
    pitfalls: Iterable[str] = (),
    verification: str = "",
    slug: str | None = None,
    source_session: str | None = None,
) -> dict[str, Any]:
    """Write a skill as a procedural memory. Returns the memory row, so the caller
    can mirror it to markdown the same way ``remember`` does."""
    skill = Skill(
        slug=slug or "",
        title=title,
        when_to_use=(when_to_use or "").strip(),
        steps=_clean(steps),
        pitfalls=_clean(pitfalls),
        verification=(verification or "").strip(),
        last_verified=_today(),
    )
    return store.upsert_memory(
        kind="procedural",
        title=title,
        body=render_body(skill),
        slug=slug,
        source_session=source_session,
    )


def patch_skill(
    store: MemoryStore,
    slug: str,
    *,
    title: str | None = None,
    when_to_use: str | None = None,
    steps: Iterable[str] | None = None,
    pitfalls: Iterable[str] | None = None,
    verification: str | None = None,
) -> dict[str, Any] | None:
    """Amend the named sections in place; everything else is preserved, tags and
    salience included. ``None`` means "leave this section alone" — passing nothing
    but the slug is a re-verification, which just moves the last-verified marker.
    Returns None if there's no such memory."""
    mem = store.get_memory(slug)
    if mem is None:
        return None
    if not _in_scope(store, mem):
        raise ScopeError(f"skill {slug!r}", mem.get("project") or "")
    if mem["kind"] != "procedural":
        raise ValueError(f"'{slug}' is a {mem['kind']} memory, not a skill.")
    skill = parse_skill(slug, title or mem["title"], mem["body"])
    if when_to_use is not None:
        skill.when_to_use = when_to_use.strip()
    if steps is not None:
        skill.steps = _clean(steps)
    if pitfalls is not None:
        skill.pitfalls = _clean(pitfalls)
    if verification is not None:
        skill.verification = verification.strip()
    # Amending a skill means someone just met it against reality again.
    skill.last_verified = _today()
    return store.upsert_memory(
        kind="procedural",
        title=skill.title,
        body=render_body(skill),
        slug=slug,
        tags=mem.get("tags", ""),
        salience=mem.get("salience", 1.0),
    )


def _one_line(text: str, width: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= width else line[: width - 1].rstrip() + "…"


def index_lines(store: MemoryStore, limit: int = INDEX_LIMIT, scope: Any = None) -> list[str]:
    """One bounded line per skill — the tier that sits in context. Most recently
    updated first, so the cut at ``limit`` drops the coldest skills. A skill with no
    when-to-use falls back to its title rather than showing a stretch of its body.
    Only this store's project's skills and user-wide ones, or ``scope``'s (DREAM-108)."""
    lines: list[str] = []
    mems = (store.all_memories(kind="procedural") if scope is None
            else store.all_memories(kind="procedural", scope=scope))
    for mem in mems[:limit]:
        skill = parse_skill(mem["slug"], mem["title"], mem["body"])
        desc = skill.when_to_use.strip() or mem["title"]
        lines.append(_one_line(f"{mem['slug']} — {desc}", INDEX_LINE_CHARS))
    return lines


def load_skill(store: MemoryStore, slug: str) -> str | None:
    """The full body, on demand. Raw, not re-rendered: whatever a hand-edit added
    is part of the skill and should reach the reader."""
    mem = store.get_memory(slug)
    if mem is None or mem["kind"] != "procedural" or not _in_scope(store, mem):
        return None
    return mem["body"]
