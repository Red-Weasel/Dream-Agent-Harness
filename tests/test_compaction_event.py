"""Compaction is destructive and expensive (45 s + 73 s of re-prefill on 2026-09-21),
so it is recorded on the runtime meter, and it triggers later on a big window."""
from __future__ import annotations
import pytest
from dream.core.backends import openai_compat
from test_compaction import _FakeClient, _backend, _stuff_history, _text_round

pytestmark = pytest.mark.asyncio


class Meter:
    """The backend calls check/usage/before_tool/record on the engine's meter."""
    def __init__(self):
        self.records = []

    def record(self, event, **fields):
        self.records.append((event, fields))

    def check(self):
        return None

    def usage(self, usage, phase="lead"):
        return None

    def before_tool(self, *a, **k):
        return None


async def test_compaction_is_recorded_on_the_runtime_meter():
    b = _backend(n_ctx=8192)
    _stuff_history(b)
    b.runtime_meter = Meter()
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("WHAT DID YOU FIND?")]
    recs = [f for e, f in b.runtime_meter.records if e == "compaction"]
    assert len(recs) == 1
    r = recs[0]
    assert r["before"] > r["after"] > 0 and r["elided"] > 0
    assert r["window"] == 8192 and r["threshold"] == 0.75


def test_compact_at_is_later_on_a_large_window(monkeypatch):
    monkeypatch.setattr(openai_compat, "_COMPACT_AT", None)
    assert openai_compat.compact_at(8192) == 0.75
    assert openai_compat.compact_at(32768) == 0.75
    assert openai_compat.compact_at(65536) == 0.85
    assert openai_compat.compact_at(75000) == 0.85


def test_the_env_knob_still_wins_everywhere(monkeypatch):
    monkeypatch.setattr(openai_compat, "_COMPACT_AT", 0.5)
    assert openai_compat.compact_at(8192) == 0.5 and openai_compat.compact_at(75000) == 0.5
