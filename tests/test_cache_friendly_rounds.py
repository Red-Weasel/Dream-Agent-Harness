"""DREAM-112, fix list #17: turns died at the 100-round tool limit while the model ran cheap read-only
probes (live 2026-09-19: ~100 rounds of eval_js / see / screenshot on a CPU-rendered preview, 3 h 46 min).
On a local engine a round of nothing but looks now costs half a round; the rounds-left note keeps working.
"""
from __future__ import annotations

import pytest

from dream import config
from dream.core.backends import openai_compat
from test_cache_friendly_head import FakeEngine, backend, tool, turn

LOOKS = ("read_file", "grep", "list_dir", "eval_js", "see", "save_screenshot", "run_bash")


def looks(n):
    """n rounds of one look each, every call different (the loop guard stays out of it)."""
    script = []
    for i in range(n):
        name = LOOKS[i % len(LOOKS)]
        args = {"command": f"ls -la src{i} && git status"} if name == "run_bash" else {"path": f"p{i}"}
        script.append({"calls": [(name, args)]})
    return script


def looks_backend(engine, **kw):
    tools = [tool(name, (lambda n: lambda a: f"{n} saw {a}")(name), images=1 if name == "see" else 0)
             for name in (*LOOKS, "write_file")]
    return backend(engine, tools=tools, multimodal=True, **kw)


def result(events):
    return next(e.data for e in events if e.kind == "result")


async def test_rounds_of_looks_cost_half_on_a_local_engine(monkeypatch):
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 4)
    engine = FakeEngine(lead=looks(6) + [{"text": "Found it."}])
    b = looks_backend(engine)
    events = await turn(b)
    assert result(events)["subtype"] == "success"             # 6 looks (3) and the answer (1): 4 of 4
    assert len(engine.requests) == 7


async def test_the_limit_is_never_passed_by_half_a_round(monkeypatch):
    """DREAM-112 gate: at 3.5 of 4 a round could start and end at 4.5. A round starts only while a whole
    one fits, so the turn stops there, half a round unused."""
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 4)
    engine = FakeEngine(lead=looks(7) + [{"text": "Found it."}], side=[{"text": "partial"}])
    b = looks_backend(engine)
    events = await turn(b)
    assert result(events)["subtype"] == "tool_round_limit"
    assert [r["kind"] for r in engine.requests] == ["lead"] * 7 + ["side"]


async def test_a_round_that_changes_something_costs_a_whole_one(monkeypatch):
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 2)
    mixed = [{"calls": [("read_file", {"path": f"p{i}"}), ("write_file", {"path": f"w{i}"})]} for i in range(3)]
    engine = FakeEngine(lead=mixed, side=[{"text": "partial"}])
    b = looks_backend(engine)
    events = await turn(b)
    assert result(events)["subtype"] == "tool_round_limit"
    assert [r["kind"] for r in engine.requests] == ["lead", "lead", "side"]
    said = [str(e.data) for e in events if e.kind == "system" and "tool-round limit (2)" in str(e.data)]
    assert said and "a round of only reads counted half" in said[0]


async def test_a_remote_backend_still_counts_every_round(monkeypatch):
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 4)
    engine = FakeEngine(lead=looks(7) + [{"text": "Found it."}], side=[{"text": "partial"}])
    b = looks_backend(engine, key="openai", base_url="https://api.example.com/v1")
    events = await turn(b)
    assert result(events)["subtype"] == "tool_round_limit"
    assert [r["kind"] for r in engine.requests].count("lead") == 4


async def test_the_rounds_left_note_counts_the_halves(monkeypatch):
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 5)
    engine = FakeEngine(lead=looks(8) + [{"text": "Found it."}])
    b = looks_backend(engine)
    events = await turn(b)
    assert result(events)["subtype"] == "success"
    notes = [m["content"] for m in b.messages if m.get("role") == "tool" and "tool rounds left" in m["content"]]
    assert len(notes) == 1                                     # the 3-rounds mark, crossed once
    assert "[Dream: 3 tool rounds left in this turn (a round of only reads costs half)." in notes[0]


async def test_a_mark_stepped_over_by_a_whole_round_still_warns(monkeypatch):
    """A half round moves the count off the whole numbers: 4.5 left, then a round that writes takes it
    to 3.5 and the next to 2.5, stepping over the 3 mark. The note fires as the mark is crossed."""
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 5)
    writes = [{"calls": [("write_file", {"path": f"w{i}"})]} for i in range(2)]
    engine = FakeEngine(lead=looks(1) + writes + [{"text": "Done."}])
    b = looks_backend(engine)
    await turn(b)
    notes = [m["content"] for m in b.messages if m.get("role") == "tool" and "tool rounds left" in m["content"]]
    assert len(notes) == 1 and "[Dream: 2.5 tool rounds left in this turn" in notes[0]


@pytest.mark.parametrize("name,arguments,look", [
    ("read_file", '{"path": "a"}', True), ("grep", "{}", True), ("list_dir", "{}", True),
    ("eval_js", "{}", True), ("see", "{}", True), ("save_screenshot", "{}", True),
    ("run_bash", '{"command": "ls -la && git status"}', True),
    ("bash", '{"command": "cat notes.md"}', True),                 # an alias of run_bash
    (f"mcp__{config.MCP_SERVER_NAME}__read_file", "{}", True),
    ("run_bash", '{"command": "rm -rf build"}', False),
    ("run_bash", '{"command": "echo hi > out.txt"}', False),
    ("run_bash", '{"command": "python3 build.py"}', False),
    ("run_bash", '{"command": ', False),                           # unparseable: not known to be a read
    ("write_file", "{}", False), ("note", "{}", False), ("task", "{}", False),
])
def test_what_counts_as_a_look(name, arguments, look):
    assert openai_compat._read_only_call(name, arguments) is look
