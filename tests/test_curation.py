"""Offline unit tests for self-curating memory — pure heuristics, no model."""

import tempfile
from pathlib import Path

from dream.memory import curation
from dream.memory.curation import PERSONAL, REFERENCE, classify_facet
from dream.memory.store import MemoryStore


def _store() -> MemoryStore:
    return MemoryStore(Path(tempfile.mkdtemp()) / "t.db")


# --- classify_facet -------------------------------------------------------


def test_classify_personal():
    assert classify_facet("The user likes concision", "They prefer short answers.", "pref") == PERSONAL
    assert classify_facet("Family", "The user's wife and kids live nearby.") == PERSONAL
    assert classify_facet("Current project", "The user is rebuilding the harness they decided to keep.") == PERSONAL


def test_classify_reference():
    assert classify_facet("SQLite FTS5", "Use MATCH syntax; version 3.12 adds bm25().") == REFERENCE
    assert classify_facet("GPU", "The card has 24 GB of VRAM running at 2.5 GHz.") == REFERENCE
    assert classify_facet("Install", "How to install the CLI via the package command.") == REFERENCE


def test_classify_unsure_returns_empty():
    assert classify_facet("Note", "A reminder to look at later.") == ""
    assert classify_facet("", "") == ""


# --- curate ---------------------------------------------------------------


def test_curate_persists_and_counts():
    s = _store()
    s.upsert_memory("semantic", "The user likes concision", "They prefer short answers.", slug="p1")
    s.upsert_memory("semantic", "Family", "The user's daughter loves hiking.", slug="p2")
    s.upsert_memory("semantic", "FTS5", "Use MATCH syntax; version 3.12 adds bm25().", slug="r1")
    s.upsert_memory("semantic", "Blank", "A reminder to look at later.", slug="u1")

    result = curation.curate(s)
    assert result == {"classified": 3, "personal": 2, "reference": 1}

    assert s.get_memory("p1")["facet"] == PERSONAL
    assert s.get_memory("p2")["facet"] == PERSONAL
    assert s.get_memory("r1")["facet"] == REFERENCE
    assert s.get_memory("u1")["facet"] == ""  # unsure stays unlabeled

    # Idempotent: a second pass reclassifies nothing already labeled.
    again = curation.curate(s)
    assert again["classified"] == 0


# --- wake_ordering --------------------------------------------------------


def test_wake_ordering_leads_with_personal():
    mems = [
        {"slug": "ref", "facet": REFERENCE},
        {"slug": "unknown", "facet": ""},
        {"slug": "me", "facet": PERSONAL},
    ]
    ordered = [m["slug"] for m in curation.wake_ordering(mems)]
    assert ordered == ["me", "unknown", "ref"]


def test_wake_ordering_is_stable_within_group():
    mems = [
        {"slug": "a", "facet": PERSONAL},
        {"slug": "b", "facet": REFERENCE},
        {"slug": "c", "facet": PERSONAL},
    ]
    ordered = [m["slug"] for m in curation.wake_ordering(mems)]
    assert ordered == ["a", "c", "b"]  # personals keep incoming order, reference trails


# --- store facet + migration ----------------------------------------------


def test_facet_column_and_set_facet_persist_across_reopen():
    path = Path(tempfile.mkdtemp()) / "t.db"
    s = MemoryStore(path)
    s.upsert_memory("semantic", "A", "x", slug="a")
    assert s.get_memory("a")["facet"] == ""  # default
    s.set_facet("a", PERSONAL)
    s.close()

    # Reopening runs the migration again — must be idempotent and preserve data.
    s2 = MemoryStore(path)
    assert s2.get_memory("a")["facet"] == PERSONAL
    facet_cols = [
        r for r in s2._conn.execute("PRAGMA table_info(memories)") if r[1] == "facet"
    ]
    assert len(facet_cols) == 1  # not duplicated by the second migration


def test_top_memories_bias_undisturbed_when_facets_empty():
    s = _store()
    s.upsert_memory("semantic", "One", "x", salience=3.0)
    s.upsert_memory("semantic", "Two", "y", salience=1.0)
    # Default prefer_facet is a tie-breaker only; with empty facets salience dominates.
    top = s.top_memories(2)
    assert [m["slug"] for m in top] == ["one", "two"]
    # Explicitly disabling the bias yields the same pure-salience order.
    assert [m["slug"] for m in s.top_memories(2, prefer_facet=None)] == ["one", "two"]


def test_top_memories_salience_beats_facet():
    s = _store()
    s.upsert_memory("semantic", "Ref high", "x", slug="ref", salience=3.0)
    s.upsert_memory("semantic", "Personal low", "y", slug="me", salience=1.0)
    s.set_facet("ref", REFERENCE)
    s.set_facet("me", PERSONAL)
    # Facet is only a tie-breaker: higher salience still leads.
    assert [m["slug"] for m in s.top_memories(2)] == ["ref", "me"]


def test_top_memories_personal_leads_at_equal_salience():
    s = _store()
    s.upsert_memory("semantic", "Ref", "x", slug="ref", salience=2.0)
    s.upsert_memory("semantic", "Me", "y", slug="me", salience=2.0)
    s.set_facet("ref", REFERENCE)
    s.set_facet("me", PERSONAL)
    # Equal salience + freshness → facet breaks the tie, personal ahead.
    assert s.top_memories(2)[0]["slug"] == "me"
