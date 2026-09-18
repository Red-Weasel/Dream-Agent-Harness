"""Offline unit tests for the memory store — no model, no network."""

import tempfile
from pathlib import Path

from dream.memory.store import MemoryStore, slugify


def _store() -> MemoryStore:
    return MemoryStore(Path(tempfile.mkdtemp()) / "t.db")


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("") == "note"


def test_sessions_and_turns():
    s = _store()
    s.start_session("a", "first")
    s.add_turn("a", "user", "hi")
    s.add_turn("a", "assistant", "hello")
    sess = s.get_session("a")
    assert sess["turn_count"] == 2
    s.end_session("a", "we met")
    assert s.previous_session("b")["summary"] == "we met"


def test_memory_upsert_and_search():
    s = _store()
    s.upsert_memory("semantic", "The user likes concision", "They prefer short answers.", tags="pref")
    s.upsert_memory("procedural", "Search well", "web_search then browse top results.")
    hits = s.search_memories("concision answers")
    assert hits and hits[0]["slug"] == "the-user-likes-concision"
    # kind filter
    proc = s.search_memories("search browse", kind="procedural")
    assert proc and proc[0]["kind"] == "procedural"


def test_memory_update_in_place():
    s = _store()
    m1 = s.upsert_memory("semantic", "Fact", "v1", slug="fact")
    m2 = s.upsert_memory("semantic", "Fact", "v2 updated", slug="fact")
    assert m1["slug"] == m2["slug"] == "fact"
    assert s.get_memory("fact")["body"] == "v2 updated"
    assert len(s.all_memories()) == 1


def test_search_stems_query_and_body():
    # Porter stemming in the FTS tokenizer: "planning" must find "plans" and
    # "concentrating" must find "concentration" — the lexical gap that cost
    # the golden set its top-1 hits before.
    s = _store()
    s.upsert_memory("semantic", "Focus sound", "Brown noise helps concentration.", slug="focus")
    hits = s.search_memories("concentrating")
    # A REAL match, not the via_recency browse fallback a no-hit query gets.
    assert hits and hits[0]["slug"] == "focus" and not hits[0].get("via_recency")


def test_fts_porter_migration_rebuilds_a_legacy_index():
    db = Path(tempfile.mkdtemp()) / "t.db"
    s = MemoryStore(db)
    s.upsert_memory("semantic", "Focus sound", "Brown noise helps concentration.", slug="focus")
    # Regress the index to the pre-porter tokenizer a v0 store had.
    s._conn.executescript(
        "DROP TABLE memories_fts;"
        "CREATE VIRTUAL TABLE memories_fts USING fts5("
        "  title, body, tags, content='memories', content_rowid='id');"
        "INSERT INTO memories_fts(memories_fts) VALUES('rebuild');"
    )
    s._conn.commit()
    # Old tokenizer: no stemming, so no real hit — only the recency fallback.
    assert all(h.get("via_recency") for h in s.search_memories("concentrating"))
    s.close()

    s2 = MemoryStore(db)  # reopen: the migration must detect and rebuild
    sql = s2._conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='memories_fts'"
    ).fetchone()[0]
    assert "porter" in sql
    hits = s2.search_memories("concentrating")
    assert hits and hits[0]["slug"] == "focus" and not hits[0].get("via_recency")
    s2.close()


def test_search_handles_special_chars():
    s = _store()
    s.upsert_memory("semantic", "Edge", "some body text")
    # Should not raise on FTS-hostile input.
    assert s.search_memories('"()* AND OR') == s.search_memories('"()* AND OR')


def test_forget_and_notes():
    s = _store()
    s.upsert_memory("semantic", "Temp", "delete me", slug="temp")
    assert s.delete_memory("temp") is True
    assert s.delete_memory("temp") is False
    s.add_note("sess", "a working note")
    assert s.session_notes("sess")[0]["note"] == "a working note"
    s.mark_notes_consolidated("sess")
    assert s.session_notes("sess", only_unconsolidated=True) == []


def test_stats():
    s = _store()
    s.upsert_memory("semantic", "A", "x")
    s.upsert_memory("procedural", "B", "y")
    st = s.stats()
    assert st["memories"] == 2 and st["semantic"] == 1 and st["procedural"] == 1


def test_salience_boosts_rank_not_buries_it():
    # Regression: FTS ordering divided by (0.5+salience) — since bm25() is
    # negative, that pushed HIGH-salience matches toward zero → last. With
    # vectors sidelined this is the live recall order, so a high-salience memory
    # about the same terms must rank FIRST.
    s = _store()
    s.upsert_memory("semantic", "Low one", "attorney profile dallas texas", slug="low", salience=1.0)
    s.upsert_memory("semantic", "High one", "attorney profile dallas texas", slug="high", salience=5.0)
    hits = s.search_memories("attorney profile dallas")
    assert [h["slug"] for h in hits][:2] == ["high", "low"]
