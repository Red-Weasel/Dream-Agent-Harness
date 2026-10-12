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
    # Every project's memories: a facet is a label on a memory, not a move between
    # projects, so the sweep stays whole (DREAM-108).
    for m in store.all_memories(scope=None):
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


# Explicit metadata only: heuristics never decide that one fact invalidates another.
LIFECYCLE_VERSION = 1
MAX_ACTIVE_ROWS = 64
MAX_ACTIVE_BYTES = 262144
MAX_MEMORY_BYTES = 65536


def lifecycle_json(value: Any) -> str:
    """Validate the versioned file/index contract; absent legacy metadata is eligible.

    Timestamps require an explicit timezone. Unknown versions/keys fail closed
    and remain visible as metadata errors on file import, never silently reset.
    """
    import json
    from datetime import datetime

    if value is None or value == '':
        value = {}
    if isinstance(value, str):
        if len(value.encode('utf-8')) > 4096:
            raise ValueError('lifecycle metadata exceeds 4096 bytes')
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError('malformed lifecycle JSON') from exc
    if not isinstance(value, dict):
        raise ValueError('lifecycle must be an object')
    if value:
        if type(value.get('version')) is not int or value['version'] != LIFECYCLE_VERSION:
            raise ValueError('unsupported lifecycle version')
        if set(value) - {'version', 'expires_at', 'invalidated_at', 'superseded_by', 'reason'}:
            raise ValueError('unknown lifecycle field')
        for key in ('expires_at', 'invalidated_at'):
            if key in value:
                try:
                    dt = datetime.fromisoformat(value[key])
                    if dt.utcoffset() is None:
                        raise ValueError('timezone required')
                except (ValueError, TypeError) as exc:
                    raise ValueError(f'{key} requires an ISO timestamp with timezone') from exc
        if 'superseded_by' in value:
            target = value['superseded_by']
            if not isinstance(target, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', target) or len(target) > 80:
                raise ValueError('superseded_by requires an exact memory slug')
        if 'reason' in value and (not isinstance(value['reason'], str) or len(value['reason']) > 1024):
            raise ValueError('lifecycle reason must be a string of at most 1024 characters')
    text = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    if len(text) > 4096:
        raise ValueError('lifecycle metadata exceeds 4096 bytes')
    return text


def active_status(memory: dict, *, now=None, target=None) -> str:
    """Return eligible, expired, invalidated, superseded, conflict or metadata_error.

    A dangling/cross-project/self supersession is ambiguous: keep the fact
    eligible and surface conflict. Explicit invalidation is immediate; its time
    is provenance, not a scheduler. Expiry takes effect at the inclusive boundary.
    """
    import json
    from datetime import datetime, timezone

    if memory.get('lifecycle_error'):
        return 'metadata_error'
    try:
        lifecycle = json.loads(lifecycle_json(memory.get('lifecycle')))
    except ValueError:
        return 'metadata_error'
    if 'invalidated_at' in lifecycle:
        return 'invalidated'
    if 'expires_at' in lifecycle:
        if datetime.fromisoformat(lifecycle['expires_at']) <= (now or datetime.now(timezone.utc)):
            return 'expired'
    if 'superseded_by' in lifecycle:
        if (target and target.get('slug') != memory.get('slug')
                and target.get('project') == memory.get('project')
                and target.get('slug') == lifecycle['superseded_by']
                and not target.get('lifecycle_error')
                and active_status(dict(target, lifecycle=json.loads(lifecycle_json(target.get('lifecycle')))), now=now) == 'eligible'):
            return 'superseded'
        return 'conflict'
    return 'eligible'


def curate_active(store: Any, *, scope=None, limit: int = MAX_ACTIVE_ROWS,
                  max_bytes: int = MAX_ACTIVE_BYTES, after_id: int = 0) -> dict:
    """Bounded advisory inspection, safe on every task/write; no models or file edits.

    Exact duplicates are reported within their own scope, never collapsed.
    Pass next_after_id on a later call to inspect the next page. Zero means wrap.
    File ingestion is still explicit sync; this pass does not rewrite user files.
    """
    import hashlib
    import json

    rows = store.active_memory_page(scope=scope if scope is not None else store.default_scope(),
                                    limit=limit, max_bytes=max_bytes, after_id=after_id)
    groups: dict[tuple, list[str]] = {}
    report = {'examined': len(rows), 'bytes': 0, 'eligible': 0, 'expired': 0,
              'invalidated': 0, 'superseded': 0, 'conflict': 0, 'metadata_error': 0,
              'duplicates': [], 'next_after_id': rows[-1]['id'] if rows else 0}
    for row in rows:
        report['bytes'] += len(json.dumps(row, ensure_ascii=False).encode('utf-8'))
        try:
            target_slug = json.loads(lifecycle_json(row.get('lifecycle'))).get('superseded_by')
        except ValueError:
            target_slug = None
        target = store.lifecycle_target(target_slug) if target_slug else None
        report[active_status(row, target=target)] += 1
        key = (row.get('project'), row['kind'], hashlib.sha256(
            (row['title'] + '\0' + row['body']).encode('utf-8')).hexdigest())
        groups.setdefault(key, []).append(row['slug'])
    report['duplicates'] = [slugs for slugs in groups.values() if len(slugs) > 1]
    return report
