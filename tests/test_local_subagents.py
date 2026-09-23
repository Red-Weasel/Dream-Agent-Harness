"""Local subagents for the OpenAI-compat backend.

The Claude backend delegates via the SDK's Task tool; the local backend never
had an equivalent, so a local model asked to "launch subagents" correctly said
it couldn't (observed live on Qwen 35B). These tests pin the new `task` tool:
the lead dispatches a scoped subagent that runs an isolated, non-streaming loop
on the same engine and hands back a summary — serial, context-isolated, no
recursion, no reaching outside its scope.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import _SUB_MAX_ROUNDS, OpenAICompatBackend
from dream.core.subagents import LocalSubagentSpec, local_subagents, subagents


def _tool(name, handler):
    return SimpleNamespace(
        name=name, description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _researcher():
    return {"researcher": LocalSubagentSpec(
        name="researcher", description="web research",
        prompt="You are the researcher.", tool_names=("web_search", "recall"),
    )}


def _backend(tools, subs):
    p = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1",
                        multimodal=False, api_key=lambda: "n")
    return OpenAICompatBackend(
        provider=p, model="m", system_prompt="s",
        tools=tools, permission_cb=None, subagents=subs,
    )


# --- fake transport: both .stream() (lead) and .post() (subagent) ------------

class _StreamResp:
    def __init__(self, lines): self.status_code = 200; self._lines = lines
    async def aiter_lines(self):
        for ln in self._lines:
            yield ln
    async def aread(self): return b""


class _StreamCtx:
    def __init__(self, lines): self._lines = lines
    async def __aenter__(self): return _StreamResp(self._lines)
    async def __aexit__(self, *a): return False


class _PostResp:
    def __init__(self, payload): self.status_code = 200; self._p = payload; self.text = json.dumps(payload)
    def json(self): return self._p


class _FakeClient:
    def __init__(self, stream_scripts=(), post_scripts=()):
        self._s = list(stream_scripts); self.si = 0
        self._p = list(post_scripts); self.pi = 0
        self.posted = []

    def stream(self, method, url, json=None):
        i = min(self.si, len(self._s) - 1); self.si += 1
        return _StreamCtx(self._s[i])

    async def post(self, url, json=None):
        # SNAPSHOT the payload — it holds the subagent's live message list,
        # which keeps mutating (appends, compaction) after the call returns.
        self.posted.append(copy.deepcopy(json))
        i = min(self.pi, len(self._p) - 1); self.pi += 1
        return _PostResp(self._p[i])


def _sse(obj): return "data: " + json.dumps(obj)


def _lead_task_call(subagent_type, prompt):
    args = json.dumps({"subagent_type": subagent_type, "prompt": prompt})
    return [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "t1", "function": {"name": "task", "arguments": args}}
        ]}, "finish_reason": None}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


def _lead_final(text="all done"):
    return [_sse({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}),
            "data: [DONE]"]


def _sub_toolcall(name, args):
    return {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "s1", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}}]}


def _sub_final(text):
    return {"choices": [{"message": {"content": text, "tool_calls": []}}]}


# --- the task schema is advertised only when subagents are wired -------------

def test_task_tool_advertised_only_with_subagents():
    calls = []

    async def h(a): return {"content": [{"type": "text", "text": "ok"}]}
    with_subs = _backend([_tool("web_search", h)], _researcher())
    without = _backend([_tool("web_search", h)], None)
    names_with = [s["function"]["name"] for s in with_subs.tool_schemas]
    names_without = [s["function"]["name"] for s in without.tool_schemas]
    assert "task" in names_with
    assert "task" not in names_without
    # The task schema enumerates the real subagents so the model can only pick valid ones.
    task = next(s for s in with_subs.tool_schemas if s["function"]["name"] == "task")
    assert task["function"]["parameters"]["properties"]["subagent_type"]["enum"] == ["researcher"]
    assert not calls


# --- end to end: lead → task → scoped subagent loop → summary ----------------

async def test_lead_dispatches_subagent_and_gets_summary():
    seen = {"web_search": 0}

    async def web_search(a):
        seen["web_search"] += 1
        return {"content": [{"type": "text", "text": "raw results"}]}

    async def recall(a):
        return {"content": [{"type": "text", "text": "mem"}]}

    b = _backend([_tool("web_search", web_search), _tool("recall", recall)], _researcher())
    b._client = _FakeClient(
        stream_scripts=[_lead_task_call("researcher", "find X"), _lead_final()],
        post_scripts=[_sub_toolcall("web_search", {"query": "X"}), _sub_final("RESEARCH RESULT")],
    )
    events = [ev async for ev in b.ask("go find X")]

    # The subagent actually ran its scoped tool...
    assert seen["web_search"] == 1
    # ...and its summary came back to the lead as the task tool result.
    task_results = [e.data for e in events
                    if e.kind == "tool_result" and e.data["name"] == "task"]
    assert task_results and task_results[0]["content"] == "RESEARCH RESULT"
    result = next(e.data for e in events if e.kind == "result")
    assert result["subtype"] == "success"


# --- the subagent is offered only its scoped tools, never `task` -------------

async def test_subagent_scope_excludes_task_and_out_of_scope_tools():
    async def h(a): return {"content": [{"type": "text", "text": "x"}]}
    # 'write_file' exists on the backend but is NOT in the researcher's scope.
    b = _backend([_tool("web_search", h), _tool("recall", h), _tool("write_file", h)],
                 _researcher())
    b._client = _FakeClient(post_scripts=[_sub_final("done")])
    await b._run_subagent("researcher", "task text")

    offered = {t["function"]["name"] for t in b._client.posted[0]["tools"]}
    assert offered == {"web_search", "recall"}   # scoped, and no 'task', no 'write_file'


async def test_subagent_cannot_call_out_of_scope_or_recurse():
    async def h(a): return {"content": [{"type": "text", "text": "x"}]}
    b = _backend([_tool("web_search", h), _tool("write_file", h)], _researcher())
    # allowed set is non-None → this is the subagent path.
    out, err = await b._exec_tool("write_file", {}, allowed={"web_search"})
    assert err and "not available to this subagent" in out
    task_out, task_err = await b._exec_tool("task", {"subagent_type": "researcher"},
                                            allowed={"web_search"})
    assert task_err and "not available to this subagent" in task_out  # no recursion


# --- robustness ---------------------------------------------------------------

async def test_unknown_subagent_is_a_clean_error():
    async def h(a): return {"content": [{"type": "text", "text": "x"}]}
    b = _backend([_tool("web_search", h)], _researcher())
    out, err = await b._run_subagent("nope", "x")
    assert err and "unknown subagent 'nope'" in out and "researcher" in out


async def test_subagent_loop_guard_stops_a_repeating_spiral():
    # Identical call AND identical result every round: the loop guard now catches
    # this long before the round limit would.
    async def h(a): return {"content": [{"type": "text", "text": "again"}]}
    b = _backend([_tool("web_search", h), _tool("recall", h)], _researcher())
    b._client = _FakeClient(post_scripts=[_sub_toolcall("web_search", {"q": "x"})])
    out, err = await b._run_subagent("researcher", "loop forever")
    assert err and "loop guard" in out
    assert b._client.pi < _SUB_MAX_ROUNDS  # stopped early, not at the backstop


async def test_subagent_round_limit_is_bounded():
    # Results DIFFER every round, so the guard (which keys on identical results,
    # not identical calls) never fires — the round limit is the only backstop.
    seen = 0

    async def h(a):
        nonlocal seen
        seen += 1
        return {"content": [{"type": "text", "text": f"result {seen}"}]}

    b = _backend([_tool("web_search", h), _tool("recall", h)], _researcher())
    # Always returns another tool call → never terminates on its own.
    b._client = _FakeClient(post_scripts=[_sub_toolcall("web_search", {"q": "x"})])
    out, err = await b._run_subagent("researcher", "loop forever")
    assert err and "round limit" in out
    assert b._client.pi == _SUB_MAX_ROUNDS   # bounded, didn't run away


async def test_missing_tool_names_in_spec_are_skipped_not_fatal():
    async def h(a): return {"content": [{"type": "text", "text": "x"}]}
    # spec names a tool the backend doesn't have; runner must skip it, not crash.
    subs = {"ghost": LocalSubagentSpec("ghost", "d", "p", ("web_search", "does_not_exist"))}
    b = _backend([_tool("web_search", h)], subs)
    b._client = _FakeClient(post_scripts=[_sub_final("ok")])
    out, err = await b._run_subagent("ghost", "t")
    assert not err and out == "ok"
    assert {t["function"]["name"] for t in b._client.posted[0]["tools"]} == {"web_search"}


# --- review findings (2026-07-08): hardening against a live server ------------

class _GarbageResp:
    """A 200 whose body isn't JSON — a hiccuping local server mid-restart."""
    status_code = 200
    text = "<html>proxy error</html>"
    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


class _GarbageClient:
    async def post(self, url, json=None):
        return _GarbageResp()


class _ListResp:
    """A 200 whose body parses to a non-dict (some non-compliant servers)."""
    status_code = 200
    text = "[]"
    def json(self):
        return []


class _ListClient:
    async def post(self, url, json=None):
        return _ListResp()


async def test_garbled_200_body_fails_the_task_not_the_session():
    async def h(a): return {"content": [{"type": "text", "text": "x"}]}
    b = _backend([_tool("web_search", h)], _researcher())
    for client in (_GarbageClient(), _ListClient()):
        b._client = client
        out, err = await b._run_subagent("researcher", "t")  # must NOT raise
        assert err and "failed" in out


async def test_missing_tool_call_id_stays_consistent_between_messages():
    async def h(a): return {"content": [{"type": "text", "text": "x"}]}
    b = _backend([_tool("web_search", h), _tool("recall", h)], _researcher())
    # The model returns a tool call with NO id (observed on local models).
    no_id_call = {"choices": [{"message": {"content": None, "tool_calls": [
        {"function": {"name": "web_search", "arguments": "{}"}}
    ]}}]}
    b._client = _FakeClient(post_scripts=[no_id_call, _sub_final("done")])
    out, err = await b._run_subagent("researcher", "t")
    assert not err and out == "done"
    # The id the assistant message carries must equal the tool message's id —
    # a strict server 400s the next round otherwise.
    history = b._client.posted[1]["messages"]
    asst = next(m for m in history if m["role"] == "assistant" and m.get("tool_calls"))
    toolmsg = next(m for m in history if m["role"] == "tool")
    assert asst["tool_calls"][0]["id"] == toolmsg["tool_call_id"]
    assert asst["tool_calls"][0]["id"]  # and it's non-empty


async def test_length_truncated_final_answer_is_flagged_not_clean():
    async def h(a): return {"content": [{"type": "text", "text": "x"}]}
    b = _backend([_tool("web_search", h)], _researcher())
    truncated = {"choices": [{"message": {"content": "half an ans", "tool_calls": []},
                              "finish_reason": "length"}]}
    b._client = _FakeClient(post_scripts=[truncated])
    out, err = await b._run_subagent("researcher", "t")
    assert err  # a fragment must not look like a finished summary
    assert "half an ans" in out and "truncat" in out.lower()


async def test_capitalized_Task_still_dispatches():
    # Observed live: Qwen called `Task` (SDK-style) and got "unknown tool".
    async def h(a): return {"content": [{"type": "text", "text": "x"}]}
    b = _backend([_tool("web_search", h)], _researcher())
    b._client = _FakeClient(post_scripts=[_sub_final("done")])
    out, err = await b._exec_tool("Task", {"subagent_type": "researcher", "prompt": "t"})
    assert not err and out == "done"


async def test_miscased_regular_tool_falls_back_to_lowercase():
    ran = []
    async def h(a):
        ran.append(1)
        return {"content": [{"type": "text", "text": "ok"}]}
    b = _backend([_tool("web_search", h)], _researcher())
    out, err = await b._exec_tool("Web_Search", {})
    assert not err and ran  # Web_Search -> web_search


# --- context: a subagent's history must stay inside the window ----------------

async def test_subagent_history_is_compacted_inside_the_window():
    """A research subagent can run _SUB_MAX_ROUNDS rounds of capped tool
    results — far more than a local window holds. The lead loop compacts its
    own history (_maybe_compact); the subagent loop had nothing, so a long run
    outgrew the window and a strict server 400'd away all its work."""
    calls = {"n": 0}

    async def h(a):
        # Distinct result per round, so the loop guard (identical results)
        # never fires and the round count is what drives the growth.
        calls["n"] += 1
        return {"content": [{"type": "text", "text": f"R{calls['n']} " + "y" * 20_000}]}

    b = _backend([_tool("web_search", h), _tool("recall", h)], _researcher())
    b.n_ctx = 8192
    b._client = _FakeClient(
        post_scripts=[_sub_toolcall("web_search", {"q": "x"})] * 6 + [_sub_final("DONE")]
    )
    out, err = await b._run_subagent("researcher", "find X")

    # The run still completes and hands back its real summary...
    assert not err and out == "DONE"
    # ...and no request was ever sent over the window.
    for p in b._client.posted:
        assert openai_compat._est_tokens(p["messages"]) <= 8192, (
            f"request {b._client.posted.index(p)} carried "
            f"{openai_compat._est_tokens(p['messages'])} tokens against 8192"
        )
    # Old results were stubbed, and the task prompt itself survived whole.
    last = b._client.posted[-1]["messages"]
    assert any((m.get("content") or "").startswith("[elided:") for m in last)
    assert last[1] == {"role": "user", "content": "find X"}
    # Round 1 had a roomy window — nothing was elided prematurely.
    first = b._client.posted[0]["messages"]
    assert not any((m.get("content") or "").startswith("[elided:") for m in first)


# --- specs stay in sync with both backends -----------------------------------

def test_local_subagent_tool_names_all_resolve():
    from dream.tools import registry
    from dream.tools.native import NATIVE_TOOLS

    known = {t.name for t in registry.build()["tools"]}
    known |= {t.name for t in NATIVE_TOOLS}
    for spec in local_subagents().values():
        missing = [n for n in spec.tool_names if n not in known]
        assert not missing, f"{spec.name} names unknown local tools: {missing}"


def test_claude_subagents_still_intact():
    subs = subagents()
    # The original three must never quietly disappear; the roster may grow.
    assert {"researcher", "coder", "explorer"} <= set(subs)
    assert {"reviewer", "debugger", "librarian"} <= set(subs)
    for a in subs.values():
        assert a.description and a.prompt and a.tools and a.model == "inherit"


def test_both_backends_offer_the_same_roster():
    """One canonical spec, two renderings — a subagent that exists for Claude but not
    for the local backend (or the reverse) is a spec that drifted."""
    assert set(subagents()) == set(local_subagents())


def test_each_test_starts_with_the_built_in_roster_only():
    """plugins.load() (an Engine boot, extensions reviewing a module) fills a module-global
    roster from the checkout's plugins/ directory and nothing unloads it; the conftest hands
    it back after every test (DREAM-099), so a plugin agent -- local tools `*`, no SDK tool
    list -- loaded by an earlier file cannot fail the roster checks above."""
    from dream.core.subagents import builtin_names
    assert set(local_subagents()) == set(builtin_names())
