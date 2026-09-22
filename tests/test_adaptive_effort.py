"""Adaptive reasoning effort (fix #40): 2026-09-21/22 sessions spent 3-18 minutes of
hidden reasoning per step to emit one read call. Orientation rounds (the previous
round was all read-only tools) now run at medium; the first call of a turn and any
call after a write/edit or a consequential shell command keep the configured base."""
from __future__ import annotations
import json
import pytest
from test_compaction import _FakeClient, _backend, _text_round, _tool, _tool_round

pytestmark = pytest.mark.asyncio


def _tools():
    async def ok(args):
        return {"content": [{"type": "text", "text": "ok"}]}
    return [_tool("read_file", ok), _tool("write_file", ok), _tool("run_bash", ok)]


def _b(monkeypatch, base="high"):
    monkeypatch.delenv("DREAM_ADAPTIVE_EFFORT", raising=False)
    b = _backend(_tools(), n_ctx=32768)
    b._local_options = {"reasoning_effort": base}
    b._local_options_model = b.model
    return b


def _efforts(b):
    return [p.get("reasoning_effort") for p in b._client.payloads]


async def test_read_only_rounds_drop_to_medium_and_a_write_restores_the_base(monkeypatch):
    b = _b(monkeypatch)
    b._client = _FakeClient([_tool_round("read_file"),
                             _tool_round("write_file", json.dumps({"path": "a.js", "content": "x"})),
                             _text_round()])
    [ev async for ev in b.ask("polish it")]
    assert _efforts(b) == ["high", "medium", "high"]


async def test_shell_reads_count_as_orientation_but_shell_edits_do_not(monkeypatch):
    b = _b(monkeypatch)
    b._client = _FakeClient([_tool_round("run_bash", json.dumps({"command": 'cd "/p" && ls -la src | head'})),
                             _tool_round("run_bash", json.dumps({"command": "sed -i 's/a/b/' src/a.js"})),
                             _text_round()])
    [ev async for ev in b.ask("polish it")]
    assert _efforts(b) == ["high", "medium", "high"]


async def test_an_explicit_effort_wins(monkeypatch):
    b = _b(monkeypatch)
    b.set_effort("med")
    b._client = _FakeClient([_tool_round("read_file"), _tool_round("write_file", '{"path":"a","content":"x"}'), _text_round()])
    [ev async for ev in b.ask("polish it")]
    assert _efforts(b) == ["medium", "medium", "medium"]


async def test_the_knob_turns_it_off(monkeypatch):
    b = _b(monkeypatch)
    monkeypatch.setenv("DREAM_ADAPTIVE_EFFORT", "0")
    b._client = _FakeClient([_tool_round("read_file"), _text_round()])
    [ev async for ev in b.ask("polish it")]
    assert _efforts(b) == ["high", "high"]
