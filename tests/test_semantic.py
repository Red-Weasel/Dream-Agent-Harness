"""Semantic (vector) recall test. Skips gracefully if the local embedder isn't
available (fastembed missing or model not downloadable)."""

import tempfile
from pathlib import Path

import pytest

from dream.memory.embeddings import Embedder
from dream.memory.store import MemoryStore


@pytest.fixture(scope="module")
def embedder():
    e = Embedder()
    if not e.available():
        pytest.skip("local embedder unavailable")
    return e


def test_hybrid_recall_by_meaning(embedder):
    store = MemoryStore(Path(tempfile.mkdtemp()) / "t.db", embedder=embedder)
    store.upsert_memory("semantic", "Reply style", "The user likes short, to-the-point answers.")
    store.upsert_memory("semantic", "Weekend", "We hiked a cold mountain trail on Saturday.")

    # Paraphrase with NO shared keywords must still surface the reply-style memory.
    hits = store.search_memories("keep responses brief and terse", limit=2)
    assert hits and hits[0]["slug"] == "reply-style"


def test_keyword_still_works(embedder):
    store = MemoryStore(Path(tempfile.mkdtemp()) / "t.db", embedder=embedder)
    store.upsert_memory("procedural", "Web research", "Use web_search then browse results.")
    store.upsert_memory("semantic", "Pet", "The user has a dog named Rex.")
    hits = store.search_memories("web_search", limit=1)
    assert hits and hits[0]["slug"] == "web-research"


def test_backfill_embeddings(embedder):
    # A store opened without an embedder writes no vectors; opening with one backfills.
    p = Path(tempfile.mkdtemp()) / "t.db"
    MemoryStore(p).upsert_memory("semantic", "Fact", "The sky is blue.")
    store = MemoryStore(p, embedder=embedder)
    assert store.backfill_embeddings() == 1
    assert store.backfill_embeddings() == 0  # nothing left to do
