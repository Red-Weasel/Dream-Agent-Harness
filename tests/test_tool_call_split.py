"""Fix list #104, DREAM-128: one MiMo reply at 13:04:38 UTC on 2026-09-25 (session
20260925-025648-88d2) carried two blender__execute_blender_code calls -- `{"code": "import bpy, o"}`
and then the full 728-character script it was the start of. The fragment ran first and failed
(ModuleNotFoundError: No module named 'o').

Dream did not split the call: `ie serve` parses MiMo's <tool_call> XML itself and sends every call
of a reply in ONE chunk with distinct indexes (openai_proto.cpp chat_chunk_sse_tool_calls_json), and
the engine logged no repaired or unparseable call block for that request, so its raw completion held
two complete call blocks. The guard is Dream's and deliberately narrow (gate round 1 listed the
legitimate pairs a looser rule skipped; every one is pinned below as a call that must run): a code
tool's `code` only, one short line, not a prefix of the later program, which is several times longer.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from test_build_turn_thinking import _b, _thinking
from test_schema_deferral import _FakeClient, _backend, _sse, _text_round

TOOL = "blender__execute_blender_code"
FRAGMENT = "import bpy, o"
SCRIPT = ("import bpy\nfrom mathutils import Vector\n\ndef bbox(o):\n"
          "    ws = [o.matrix_world @ Vector(c) for c in o.bound_box]\n"
          "    return ws\n\nprint(bbox(bpy.data.objects['Greenhouse']))\n")
SAVE = "bpy.ops.wm.save_mainfile()"


def _engine_round(calls):
    """The frame `ie serve` sends for a MiMo reply: prose, then every parsed call in ONE chunk
    (role, index 0..n-1, the engine's call ids, arguments as the engine dumps them)."""
    tcs = [{"id": f"call_5f0c1a2b_{i}", "type": "function", "index": i,
            "function": {"name": name, "arguments": json.dumps(args, separators=(",", ":"))}}
           for i, (name, args) in enumerate(calls)]
    return [
        _sse({"choices": [{"index": 0, "delta": {"content": "The greenhouse is one mesh."},
                           "finish_reason": None}]}),
        _sse({"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": tcs},
                           "finish_reason": None}]}),
        _sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


def _recording_tools(names, ran):
    def make(name):
        async def handler(args):
            ran.append((name, args))
            return {"content": [{"type": "text", "text": "ok"}]}
        return SimpleNamespace(name=name, description=name, input_schema={"type": "object"}, handler=handler)
    return [make(n) for n in names]


async def _run(calls):
    ran: list = []
    b = _backend(_recording_tools(sorted({n for n, _ in calls}), ran), n_ctx=65536)
    b._client = _FakeClient([_engine_round(calls), _text_round("done")])
    events = [ev async for ev in b.ask("trace the trim")]
    return b, ran, events


async def test_the_observed_fragment_is_not_run_and_the_script_is():
    b, ran, events = await _run([(TOOL, {"code": FRAGMENT}), (TOOL, {"code": SCRIPT})])
    assert ran == [(TOOL, {"code": SCRIPT})], "only the complete script may run"
    results = [e.data for e in events if e.kind == "tool_result"]
    assert len(results) == 2
    note = results[0]["content"]
    assert "not run" in note and "a later call in the same reply (call 2" in note
    assert "instead" not in note
    assert results[1]["is_error"] is False
    # Every call in the history keeps its paired tool message (a dangling id 400s).
    reply = next(m for m in b.messages if m.get("tool_calls"))
    ids = [tc["id"] for tc in reply["tool_calls"]]
    assert [m["tool_call_id"] for m in b.messages if m.get("role") == "tool"] == ids


async def test_a_chain_of_starts_points_each_note_at_the_call_that_runs():
    b, ran, events = await _run([(TOOL, {"code": FRAGMENT}), (TOOL, {"code": "import bpy, os"}),
                                 (TOOL, {"code": SCRIPT})])
    assert ran == [(TOOL, {"code": SCRIPT})]
    notes = [e.data["content"] for e in events if e.kind == "tool_result"][:2]
    assert all("(call 3," in n for n in notes)


# Gate round 1's false positives: each pair is two real calls and both must run.
LEGITIMATE = [
    [("write_file", {"path": "a.py", "content": "x = 1\n"}), ("write_file", {"path": "a.py.bak", "content": "x = 1\n"})],
    [("write_file", {"path": "src/app", "content": "x"}), ("write_file", {"path": "src/app.py", "content": "x"})],
    [("write_file", {"path": "a.txt", "content": "part one ", "append": True}),
     ("write_file", {"path": "a.txt", "content": "part one and a much longer second part of it", "append": True})],
    [("run_bash", {"command": "git status"}), ("run_bash", {"command": "git status --short"})],
    [("run_bash", {"command": "git diff"}), ("run_bash", {"command": "git diff --stat"})],
    [("run_bash", {"command": "ls /tmp"}), ("run_bash", {"command": "ls /tmp/foo"})],
    [("run_bash", {"command": "cd proj && make"}), ("run_bash", {"command": "cd proj && make test"})],
    [("run_bash", {"command": "python script.py"}), ("run_bash", {"command": "python script.py --verbose"})],
    [("run_bash", {"command": "pytest x"}), ("run_bash", {"command": "pytest x -x -q"})],
    [("run_bash", {"command": "rm -rf build"}), ("run_bash", {"command": "rm -rf build2"})],
    [("run_bash", {"command": "cat a"}), ("run_bash", {"command": "cat a b"})],
    [("edit_file", {"path": "a.py", "old_string": "x = 1", "new_string": "y"}),
     ("edit_file", {"path": "a.py", "old_string": "x = 10", "new_string": "y"})],
    [("read_file", {"path": "src/app"}), ("read_file", {"path": "src/app/main.py"})],
    [("web_search", {"query": "camaro 1969"}), ("web_search", {"query": "camaro 1969 drip rail chrome trim"})],
    [("grep", {"pattern": "def bbox"}), ("grep", {"pattern": "def bbox(o):"})],
    [(TOOL, {"code": SAVE}), (TOOL, {"code": SAVE + "\nprint(1)"})],
    [(TOOL, {"code": "print(1)"}), (TOOL, {"code": "print(12)"})],
    [(TOOL, {"code": "print(1)"}), (TOOL, {"code": "print(2)"})],
    [(TOOL, {"code": "x = 1"}), (TOOL, {"code": "x = 10"})],
    [("run_script", {"code": "log(1)"}), ("run_script", {"code": "log(1); log(2); log(3); log(4)"})],
]


@pytest.mark.parametrize("calls", LEGITIMATE, ids=lambda c: f"{c[0][0]}:{json.dumps(c[0][1])[:30]}")
async def test_legitimate_pairs_both_run(calls):
    _, ran, _ = await _run(calls)
    assert ran == calls


async def test_one_call_is_untouched():
    _, ran, _ = await _run([(TOOL, {"code": SCRIPT})])
    assert ran == [(TOOL, {"code": SCRIPT})]


async def test_a_skipped_start_does_not_count_as_a_failed_round(monkeypatch):
    """DREAM-125 keeps thinking on after a failed round; a skipped start is not a failure, so the
    request after the (successful) script still iterates without thinking."""
    ran: list = []
    b = _b(monkeypatch, thinking=True, tools=_recording_tools(["write_file", TOOL], ran))
    b._client = _FakeClient([_engine_round([("write_file", {"path": "a.py", "content": "x"})]),
                             _engine_round([(TOOL, {"code": FRAGMENT}), (TOOL, {"code": SCRIPT})]),
                             _text_round("done")])
    [ev async for ev in b.ask("build it")]
    assert _thinking(b) == [True, False, False]


def _call(name, args):
    return {"id": "", "name": name, "args": json.dumps(args)}


def test_the_rule():
    from dream.core.backends.openai_compat import _abandoned_starts
    assert _abandoned_starts([_call(TOOL, {"code": FRAGMENT}), _call(TOOL, {"code": SCRIPT})]) == {0: 1}
    for name in ("run_script", "eval_js", "eval_js_user_view"):
        assert _abandoned_starts([_call(name, {"code": "const a = 1, b"}),
                                  _call(name, {"code": "const a = 1\n" + "log(a);\n" * 10})]) == {0: 1}
    # Not a start: a plain prefix (two real steps), another tool, a longer or multi-line earlier call,
    # a later call not several times longer, a later (not earlier) fragment, bad JSON, a one-word call.
    assert _abandoned_starts([_call(TOOL, {"code": "import bpy\nfrom math"}), _call(TOOL, {"code": SCRIPT})]) == {}
    assert _abandoned_starts([_call(TOOL, {"code": "import bpy"}), _call(TOOL, {"code": SCRIPT})]) == {}
    assert _abandoned_starts([_call("run_bash", {"code": FRAGMENT}), _call("run_bash", {"code": SCRIPT})]) == {}
    assert _abandoned_starts([_call(TOOL, {"code": "import bpy, math, mathutils, bmesh, random, o"}),
                              _call(TOOL, {"code": "import bpy, math, mathutils, bmesh, random\n" + SCRIPT * 2})]) == {}
    assert _abandoned_starts([_call(TOOL, {"code": "import bpy, os"}),
                              _call(TOOL, {"code": "import bpy\nprint(bpy.data)"})]) == {}
    assert _abandoned_starts([_call(TOOL, {"code": SCRIPT}), _call(TOOL, {"code": FRAGMENT})]) == {}
    # `see` is Dream's own vision tool (not run by a fake handler above): its path refinement is pinned here.
    assert _abandoned_starts([_call("see", {"paths": ["renders/a"]}),
                              _call("see", {"paths": ["renders/a_side.png"]})]) == {}
    assert _abandoned_starts([_call("see", {"path": "renders/a"}),
                              _call("see", {"path": "renders/a_side_view_of_the_car.png"})]) == {}
    assert _abandoned_starts([{"id": "", "name": TOOL, "args": "{bad"}, _call(TOOL, {"code": SCRIPT})]) == {}
    assert _abandoned_starts([_call(TOOL, {"code": "o"}), _call(TOOL, {"code": SCRIPT})]) == {}
    assert len(json.loads(json.dumps(SCRIPT))) >= 4 * len(FRAGMENT)
