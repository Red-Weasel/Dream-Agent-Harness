"""Path traversal: a model-supplied slug must never escape the memory tree."""
import dream.config as config
from dream.memory import longterm
from dream.memory.store import MemoryStore


def test_traversal_slug_cannot_escape_the_memory_tree(tmp_path, monkeypatch):
    for name, sub in (("SEMANTIC_DIR", "sem"), ("PROCEDURAL_DIR", "proc"),
                      ("EPISODIC_DIR", "ep")):
        d = tmp_path / sub
        d.mkdir()
        monkeypatch.setattr(config, name, d)
    outside = tmp_path / "OUTSIDE.md"

    store = MemoryStore(tmp_path / "db.sqlite")
    try:
        mem = store.upsert_memory("semantic", "t", "b", slug="../../OUTSIDE")
        p = longterm.write_markdown(mem)
    finally:
        store.close()

    assert not outside.exists(), "wrote a file outside the memory tree"
    assert p.parent == config.MEMORY_DIR, f"escaped to {p}"  # Phase 9: flat
    assert ".." not in mem["slug"] and "/" not in mem["slug"]


def test_delete_markdown_cannot_escape(tmp_path, monkeypatch):
    for name, sub in (("SEMANTIC_DIR", "sem"), ("PROCEDURAL_DIR", "proc"),
                      ("EPISODIC_DIR", "ep")):
        d = tmp_path / sub
        d.mkdir()
        monkeypatch.setattr(config, name, d)
    victim = tmp_path / "victim.md"
    victim.write_text("precious")
    longterm.delete_markdown("../victim")
    assert victim.exists(), "unlinked a file outside the memory tree"


def test_ordinary_slugs_still_update_in_place(tmp_path):
    store = MemoryStore(tmp_path / "db.sqlite")
    try:
        a = store.upsert_memory("semantic", "T", "one", slug="my-fact")
        b = store.upsert_memory("semantic", "T", "two", slug="my-fact")
        assert a["slug"] == b["slug"] == "my-fact"
        assert store.get_memory("my-fact")["body"] == "two"
        s = store.upsert_memory("episodic", "S", "x", slug="session-20260801-2315-ab12")
        assert s["slug"] == "session-20260801-2315-ab12"  # consolidation's shape
    finally:
        store.close()
