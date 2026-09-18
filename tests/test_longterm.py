"""Tests for the markdown mirror and its resilience to hand-edited files."""

import tempfile
from pathlib import Path

import dream.config as config
from dream.memory import longterm
from dream.memory.store import MemoryStore


def test_parse_markdown_fenced():
    fm, body = longterm.parse_markdown("---\nslug: x\ntitle: T\n---\nhello body\n")
    assert fm["slug"] == "x" and fm["title"] == "T"
    assert body == "hello body"


def test_parse_markdown_body_with_horizontal_rule():
    # A '---' inside the body must NOT truncate content.
    text = "---\nslug: x\n---\nfirst para\n\n---\n\nsecond para"
    fm, body = longterm.parse_markdown(text)
    assert fm["slug"] == "x"
    assert "first para" in body and "second para" in body


def test_parse_markdown_no_frontmatter():
    fm, body = longterm.parse_markdown("just text, no fence")
    assert fm == {} and body == "just text, no fence"


def test_import_markdown_skips_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEMANTIC_DIR", tmp_path / "sem")
    monkeypatch.setattr(config, "PROCEDURAL_DIR", tmp_path / "proc")
    config.SEMANTIC_DIR.mkdir()
    config.PROCEDURAL_DIR.mkdir()
    # good file
    (config.SEMANTIC_DIR / "good.md").write_text(
        "---\nslug: good\nkind: semantic\ntitle: Good\n---\nbody", encoding="utf-8"
    )
    # bad kind — must not crash, should fall back to directory kind
    (config.SEMANTIC_DIR / "badkind.md").write_text(
        "---\nslug: badkind\nkind: nonsense\ntitle: Bad\n---\nbody", encoding="utf-8"
    )
    # non-UTF8 bytes — must not crash the import
    (config.SEMANTIC_DIR / "binary.md").write_bytes(b"\xff\xfe not utf8")

    store = MemoryStore(tmp_path / "t.db")
    n = longterm.import_markdown(store)
    assert n >= 2  # good + badkind both imported, binary tolerated
    assert store.get_memory("good")["kind"] == "semantic"
    assert store.get_memory("badkind")["kind"] == "semantic"  # fell back


def test_custom_tool_dedup():
    from dream.tools import registry

    names = {t.name for t in registry._BASE_TOOLS}
    # No duplicate names among built-ins.
    assert len(names) == len(registry._BASE_TOOLS)
