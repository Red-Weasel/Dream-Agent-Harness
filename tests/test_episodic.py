"""Episodic memory: the third kind — events, time-scoped recall, merge exemption."""

import tempfile
from pathlib import Path

import pytest

from dream.memory.store import MemoryStore


def _store(**kw) -> MemoryStore:
    return MemoryStore(Path(tempfile.mkdtemp()) / "t.db", **kw)


def test_episodic_kind_accepted():
    s = _store()
    m = s.upsert_memory("episodic", "An event", "something happened", slug="e1")
    assert m["kind"] == "episodic"
    with pytest.raises(ValueError):
        s.upsert_memory("nonsense", "x", "y")


def test_time_filtered_search():
    s = _store()
    s.upsert_memory("episodic", "May event", "moved the server into the rack", slug="may")
    s.upsert_memory("episodic", "June event", "installed the graphics card upgrade", slug="june")
    s.set_created_at("may", "2026-05-12T10:00:00+00:00")
    s.set_created_at("june", "2026-06-20T10:00:00+00:00")

    hits = s.search_memories(
        "server rack graphics upgrade", since="2026-06-01", until="2026-06-30"
    )
    slugs = {h["slug"] for h in hits}
    assert "june" in slugs and "may" not in slugs

    # A bare-date `until` includes that whole day.
    hits = s.search_memories("server rack moved", until="2026-05-12")
    assert {h["slug"] for h in hits} == {"may"}


def test_recent_memories_time_window():
    s = _store()
    s.upsert_memory("episodic", "Old", "ancient history", slug="old")
    s.upsert_memory("episodic", "New", "current events", slug="new")
    s.set_created_at("old", "2025-01-01T00:00:00+00:00")
    recent = s.recent_memories(limit=10, since="2026-01-01")
    assert {m["slug"] for m in recent} == {"new"}


def test_merge_never_touches_episodic(session_embedder):
    s = _store(embedder=session_embedder)
    body = "Deployed the new dashboard and fixed the login redirect bug."
    s.upsert_memory("episodic", "Tuesday deploy", body, slug="ep-tue")
    s.upsert_memory("episodic", "Friday deploy", body + " Again.", slug="ep-fri")
    result = s.merge_duplicates(threshold=0.8)
    assert result.count == 0
    assert s.get_memory("ep-tue") and s.get_memory("ep-fri")


def test_merge_never_crosses_kinds(session_embedder):
    s = _store(embedder=session_embedder)
    body = "Always run the checksum manifest before pruning backup snapshots."
    s.upsert_memory("semantic", "Backup fact", body, slug="fact")
    s.upsert_memory("procedural", "Backup playbook", body, slug="playbook")
    result = s.merge_duplicates(threshold=0.8)
    assert result.count == 0
    assert s.get_memory("fact") and s.get_memory("playbook")


def test_episodic_markdown_roundtrip(tmp_path, monkeypatch):
    import dream.config as config
    from dream.memory import longterm

    s = _store()
    s.upsert_memory("episodic", "The launch", "we shipped it", slug="launch")
    s.set_created_at("launch", "2026-06-15T12:00:00+00:00")
    longterm.write_markdown(s.get_memory("launch"))
    # Phase 9: one flat file per memory; the kind lives in its frontmatter
    assert (config.MEMORY_DIR / "launch.md").exists()

    s2 = _store()
    assert longterm.import_markdown(s2) == 1
    mem = s2.get_memory("launch")
    assert mem is not None and mem["kind"] == "episodic"
    # The original creation time survives the roundtrip — time-scoped recall
    # depends on it.
    assert mem["created_at"] == "2026-06-15T12:00:00+00:00"


def test_stats_counts_episodic():
    s = _store()
    s.upsert_memory("episodic", "E", "x", slug="e")
    assert s.stats()["episodic"] == 1
