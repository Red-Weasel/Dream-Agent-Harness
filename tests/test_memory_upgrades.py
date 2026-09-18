"""Tests for the memory upgrades: links, chunking, reranker, merge, decay.

Vector-dependent tests skip gracefully if the local embedder isn't available."""

import tempfile
from pathlib import Path

import pytest

from dream.memory.embeddings import Embedder, Reranker
from dream.memory.store import MemoryStore, _chunk_text, parse_links


def test_parse_links():
    assert parse_links("see [[Machx Arc]] and [[reply-style]]") == ["machx-arc", "reply-style"]
    assert parse_links("no links here") == []


def test_chunk_text():
    chunks = _chunk_text("word " * 500, size=200)
    assert len(chunks) > 1
    assert all(chunks)


def test_links_stored_and_traversed():
    s = MemoryStore(Path(tempfile.mkdtemp()) / "t.db")  # no embedder needed for links
    s.upsert_memory("semantic", "A", "points at [[b-note]]", slug="a-note")
    s.upsert_memory("semantic", "B", "the target", slug="b-note")
    assert s.linked_slugs("a-note") == ["b-note"]
    assert s.linked_slugs("b-note") == ["a-note"]  # both directions
    # deleting removes links
    s.delete_memory("a-note")
    assert s.linked_slugs("b-note") == []


def test_top_memories_decay_runs():
    s = MemoryStore(Path(tempfile.mkdtemp()) / "t.db")
    s.upsert_memory("semantic", "One", "x", salience=3.0)
    s.upsert_memory("semantic", "Two", "y", salience=1.0)
    top = s.top_memories(2)
    assert len(top) == 2 and top[0]["slug"] == "one"  # higher salience, both fresh


@pytest.fixture(scope="module")
def embedder():
    e = Embedder()
    if not e.available():
        pytest.skip("embedder unavailable")
    return e


def test_merge_duplicates(embedder):
    s = MemoryStore(Path(tempfile.mkdtemp()) / "t.db", embedder=embedder)
    s.upsert_memory("semantic", "Dup A", "The user prefers concise minimal replies.", slug="da", salience=2.0)
    s.upsert_memory("semantic", "Dup B", "The user prefers concise, minimal replies.", slug="db", salience=1.0)
    s.upsert_memory("semantic", "Other", "The sky is blue today.", slug="other")
    result = s.merge_duplicates(threshold=0.9)
    assert result.count == 1
    assert result.updated == ["da"] and result.deleted == ["db"]
    assert s.get_memory("db") is None and s.get_memory("da") is not None
    assert s.get_memory("other") is not None  # distinct memory untouched


def test_merge_three_member_cluster_preserves_content_and_tags(embedder):
    # Regression for the stale-snapshot bug: 3 near-identical memories must collapse to
    # ONE survivor whose body keeps every member's content and whose tags are the union.
    s = MemoryStore(Path(tempfile.mkdtemp()) / "t.db", embedder=embedder)
    base = "The user prefers concise minimal replies."
    s.upsert_memory("semantic", "A", base + " Detail one.", slug="a", tags="alpha", salience=3.0)
    s.upsert_memory("semantic", "B", base + " Detail two.", slug="b", tags="beta", salience=2.0)
    s.upsert_memory("semantic", "C", base + " Detail three.", slug="c", tags="gamma", salience=1.0)
    result = s.merge_duplicates(threshold=0.85)
    assert result.count == 2  # two dropped, one survives
    survivors = [sl for sl in ("a", "b", "c") if s.get_memory(sl)]
    assert survivors == ["a"]  # highest salience wins
    survivor = s.get_memory("a")
    for piece in ("Detail one", "Detail two", "Detail three"):
        assert piece in survivor["body"]  # no content lost
    tags = set(survivor["tags"].split(","))
    assert {"alpha", "beta", "gamma"} <= tags  # tags unioned, not wiped


def test_linked_recall_respects_kind_and_limit():
    # A procedural-only recall must not return a linked semantic memory, nor exceed limit.
    s = MemoryStore(Path(tempfile.mkdtemp()) / "t.db")  # FTS path; no embedder needed
    s.upsert_memory("procedural", "Deploy", "run the deploy script; see [[coffee]]", slug="deploy")
    s.upsert_memory("semantic", "Coffee", "The user likes coffee", slug="coffee")
    hits = s.search_memories("deploy script", kind="procedural", limit=3)
    assert all(h["kind"] == "procedural" for h in hits)  # no semantic injected
    assert len(hits) <= 3 and "coffee" not in {h["slug"] for h in hits}


def test_merge_markdown_mirror_no_resurrection(embedder, tmp_path, monkeypatch):
    # Regression for the mirror-inconsistency finding: after a merge, a fresh boot's
    # import_markdown must NOT resurrect the dropped memory.
    import dream.config as config
    from dream.memory import longterm

    monkeypatch.setattr(config, "SEMANTIC_DIR", tmp_path / "sem")
    monkeypatch.setattr(config, "PROCEDURAL_DIR", tmp_path / "proc")
    config.SEMANTIC_DIR.mkdir()
    config.PROCEDURAL_DIR.mkdir()

    s = MemoryStore(tmp_path / "m.db", embedder=embedder)
    a = s.upsert_memory("semantic", "A", "The user likes concise replies. one.", slug="a", salience=2.0)
    b = s.upsert_memory("semantic", "B", "The user likes concise replies. two.", slug="b", salience=1.0)
    longterm.write_markdown(a)
    longterm.write_markdown(b)

    result = s.merge_duplicates(threshold=0.85)
    assert result.deleted == ["b"]
    # Reconcile the markdown mirror exactly the way consolidation does.
    for slug in result.updated:
        longterm.write_markdown(s.get_memory(slug))
    for slug in result.deleted:
        longterm.delete_markdown(slug)
    assert not (config.SEMANTIC_DIR / "b.md").exists()

    # Simulate a restart: fresh DB, import the markdown mirror.
    s2 = MemoryStore(tmp_path / "m2.db", embedder=embedder)
    longterm.import_markdown(s2)
    assert s2.get_memory("b") is None  # not resurrected
    assert s2.get_memory("a") is not None
    assert "two" in s2.get_memory("a")["body"]  # merged content preserved


def test_chunk_recall(embedder):
    s = MemoryStore(Path(tempfile.mkdtemp()) / "t.db", embedder=embedder)
    body = ("Small talk about weather. " * 20) + ("Detailed notes on surface code quantum error correction. " * 20)
    s.upsert_memory("semantic", "Mixed note", body, slug="mixed")
    hits = s.search_memories("surface code quantum error correction", limit=2)
    assert any(h["slug"] == "mixed" for h in hits)
