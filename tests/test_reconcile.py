"""Contradiction resolution: find_conflicts surfaces suspiciously-similar same-kind
pairs exactly once; mark_reconciled retires them."""

import tempfile
from pathlib import Path

import dream.config as config
from dream.memory.store import MemoryStore


def _store(embedder) -> MemoryStore:
    return MemoryStore(Path(tempfile.mkdtemp()) / "t.db", embedder=embedder)


def test_find_conflicts_and_mark_reconciled(session_embedder, monkeypatch):
    # Widen the band so the test doesn't depend on exact cosine values.
    monkeypatch.setattr(config, "RECONCILE_THRESHOLD", 0.70)
    monkeypatch.setattr(config, "MERGE_THRESHOLD", 0.995)
    s = _store(session_embedder)
    s.upsert_memory(
        "semantic", "Theme pref", "Alex prefers dark mode in every application.", slug="dark"
    )
    s.upsert_memory(
        "semantic", "Theme pref now", "Alex now prefers light mode in every application.",
        slug="light",
    )
    s.upsert_memory("semantic", "Unrelated", "The NAS lives at 192.168.1.20.", slug="nas")

    conflicts = s.find_conflicts()
    assert len(conflicts) == 1
    slugs = {m["slug"] for m in conflicts[0]}
    assert slugs == {"dark", "light"}
    # Newest first, so the adjudicator sees the current belief on top.
    assert conflicts[0][0]["updated_at"] >= conflicts[0][1]["updated_at"]

    # Once adjudicated, the pair never comes back.
    s.mark_reconciled(["dark", "light"])
    assert s.find_conflicts() == []


def test_conflicts_ignore_cross_kind(session_embedder, monkeypatch):
    monkeypatch.setattr(config, "RECONCILE_THRESHOLD", 0.70)
    monkeypatch.setattr(config, "MERGE_THRESHOLD", 0.995)
    s = _store(session_embedder)
    body = "Cap the GPU power limit at 350 watts in summer."
    s.upsert_memory("semantic", "GPU fact", body, slug="fact")
    s.upsert_memory("procedural", "GPU playbook", body, slug="playbook")
    assert s.find_conflicts() == []


def test_conflicts_ignore_episodic(session_embedder, monkeypatch):
    monkeypatch.setattr(config, "RECONCILE_THRESHOLD", 0.70)
    monkeypatch.setattr(config, "MERGE_THRESHOLD", 0.995)
    s = _store(session_embedder)
    s.upsert_memory("episodic", "Mon", "Deployed the dashboard fix.", slug="mon")
    s.upsert_memory("episodic", "Tue", "Deployed the dashboard fix again.", slug="tue")
    assert s.find_conflicts() == []
