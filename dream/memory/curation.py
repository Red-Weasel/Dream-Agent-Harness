"""Self-curating memory: split facts *about the user* (always in the room) from
*reference facts* (looked up on demand), so waking up leads with the person.

Pure heuristics — no model calls. ``classify_facet`` reads cues off a memory's
text; ``curate`` sweeps the still-unlabeled memories during consolidation;
``wake_ordering`` reorders the wake-up top-of-mind so personal facets lead.
"""

from __future__ import annotations

import re
from typing import Any

PERSONAL, REFERENCE = "personal", "reference"

# Cues that a memory is *about the user* — their life, people, preferences,
# decisions, the things they are working on. Word-bounded so "he" doesn't fire
# inside "the".
_PERSONAL_RE = re.compile(
    r"""\b(?:
        user(?:'s)? | he | he's | his | him | she | she's | her | they | their |
        prefer(?:s|red|ence)? | like(?:s|d)? | dislike(?:s|d)? | love(?:s|d)? |
        want(?:s|ed)? | hate(?:s|d)? | enjoy(?:s|ed)? |
        family | wife | husband | daughter | son | kids | children | friend |
        decide(?:s|d)? | rebuild(?:s|ing)? | building
    )\b
    | working\son | plans?\sto | grew\sup | lives\sin | married\sto""",
    re.IGNORECASE | re.VERBOSE,
)

# Cues that a memory is world/tech reference — product names, "how to", specs.
_REFERENCE_RE = re.compile(
    r"""\b(?:
        how\sto | api | cli | sdk | url | http | install(?:s|ed|ing)? |
        command | syntax | spec(?:s|ification)? | version | release |
        documentation | docs | library | framework | protocol | algorithm |
        parameter | endpoint | config(?:uration)? | schema | flag
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# Hardware/measurement units and version numbers — strong reference signals.
_UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:gb|tb|mb|kb|ghz|mhz|khz|hz|ms|fps|px|tokens?|bit)\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")


def classify_facet(title: str, body: str, tags: str = "") -> str:
    """Heuristic facet for a memory: PERSONAL when it's about the user, their life,
    preferences, decisions; REFERENCE when it's world/tech trivia (product names,
    specs, versions, units, "how to"). ``""`` when the signals are absent or tied."""
    text = f"{title}\n{body}\n{tags}"
    personal = len(_PERSONAL_RE.findall(text))
    reference = (
        len(_REFERENCE_RE.findall(text))
        + len(_UNIT_RE.findall(text))
        + len(_VERSION_RE.findall(text))
    )
    if personal > reference:
        return PERSONAL
    if reference > personal:
        return REFERENCE
    return ""


def curate(store: Any) -> dict[str, int]:
    """Classify every memory whose facet is still ``''`` and persist it via
    ``set_facet``. Cheap pure-heuristic pass, called from consolidation. Returns
    ``{"classified": n, "personal": p, "reference": r}``. Memories that stay unsure
    keep an empty facet and are retried on the next pass."""
    classified = personal = reference = 0
    for m in store.all_memories():
        if (m.get("facet") or "") != "":
            continue
        facet = classify_facet(
            m.get("title", ""), m.get("body", ""), m.get("tags", "") or ""
        )
        if not facet:
            continue
        store.set_facet(m["slug"], facet)
        classified += 1
        if facet == PERSONAL:
            personal += 1
        else:
            reference += 1
    return {"classified": classified, "personal": personal, "reference": reference}


# Sort rank: personal leads, unknown sits in the middle, reference trails.
_WAKE_RANK = {PERSONAL: 0, "": 1, REFERENCE: 2}


def wake_ordering(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable reorder of the wake-up top-of-mind so personal facets lead, reference
    trails, and unknown sits between — preserving the incoming (salience) order
    within each group. Pure; used by the system prompt's wake context."""
    return sorted(memories, key=lambda m: _WAKE_RANK.get(m.get("facet") or "", 1))
