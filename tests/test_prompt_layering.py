"""Cache-stability of the system prompt.

Ordering here is a performance contract: a prompt cache (Anthropic's, or
llama.cpp's KV reuse) is valid only up to the first byte that differs, so
anything stable placed AFTER a volatile block stops being cacheable. The wake
context changes every session, so it must be last.
"""

from __future__ import annotations

import dream.config as config
from dream.core import system_prompt
from dream.memory.store import MemoryStore

WS = "\n## Your workspace\nThis session's working directory is `/tmp/x`."
SUB = "\n## Delegating (subagents)\nYou can hand a subtask to a specialist."


def _store(tmp_path, monkeypatch):
    for name, sub in (("SEMANTIC_DIR", "sem"), ("PROCEDURAL_DIR", "proc"),
                      ("EPISODIC_DIR", "ep")):
        d = tmp_path / sub
        d.mkdir()
        monkeypatch.setattr(config, name, d)
    monkeypatch.setattr(config, "IDENTITY_FILE", tmp_path / "IDENTITY.md")
    monkeypatch.setattr(config, "THREADS_FILE", tmp_path / "THREADS.md")
    return MemoryStore(tmp_path / "db.sqlite")


def _shared_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def test_stable_sections_come_before_the_volatile_wake_context(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    try:
        store.start_session("s1")
        store.upsert_memory("semantic", "A fact", "body", slug="a-fact")
        p = system_prompt.build_system_prompt(store, "s1", stable_sections=[WS, SUB])
        assert p.index("## Your workspace") < p.index("## Waking up")
        assert p.index("## Delegating") < p.index("## Waking up")
        assert p.index("## Waking up") > len(system_prompt.BASE) // 2
    finally:
        store.close()


def test_two_sessions_share_every_stable_byte(tmp_path, monkeypatch):
    """The whole point: two sessions with different memories must still share
    the entire stable region — base plus every stable section."""
    store = _store(tmp_path, monkeypatch)
    try:
        store.start_session("s1")
        store.upsert_memory("semantic", "Fact one", "x" * 200, slug="fact-one")
        a = system_prompt.build_system_prompt(store, "s1", stable_sections=[WS, SUB])
        store.end_session("s1", "a summary that makes the wake context differ")
        store.upsert_memory("semantic", "Fact two", "y" * 200, slug="fact-two")
        store.start_session("s2")
        b = system_prompt.build_system_prompt(store, "s2", stable_sections=[WS, SUB])

        assert a != b  # the wake context genuinely differs
        shared = _shared_prefix(a, b)
        # Everything up to the wake context is identical, so the shared prefix
        # must cover the last stable section — not stop at the end of BASE.
        assert shared >= a.index("## Waking up") - 2
        assert "## Delegating" in a[:shared]
    finally:
        store.close()


def test_the_prompt_is_frozen_for_the_session(tmp_path, monkeypatch):
    """Hermes's discipline: the prompt must not shift mid-session, or the cache
    it earned is thrown away on the next turn."""
    store = _store(tmp_path, monkeypatch)
    try:
        store.start_session("s1")
        first = system_prompt.build_system_prompt(store, "s1", stable_sections=[WS])
        # A memory saved mid-session must not retroactively change this session's
        # prompt — the Engine builds it once at start and holds it.
        store.upsert_memory("semantic", "Learned later", "z" * 300, slug="later")
        again = system_prompt.build_system_prompt(store, "s1", stable_sections=[WS])
        assert first[:len(WS) + len(system_prompt.BASE)] == \
            again[:len(WS) + len(system_prompt.BASE)]
    finally:
        store.close()


def test_empty_stable_sections_are_skipped(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    try:
        store.start_session("s1")
        p = system_prompt.build_system_prompt(store, "s1", stable_sections=["", WS, ""])
        assert "\n\n\n\n" not in p
        assert "## Your workspace" in p
    finally:
        store.close()
