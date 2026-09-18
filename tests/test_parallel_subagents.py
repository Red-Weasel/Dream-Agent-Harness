"""Phase 13: several `task` calls in one round run together on openai/xai and one
at a time on machx; history and events stay paired and ordered either way."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_schema_deferral import _FakeClient, _sse, _text_round  # noqa: E402
from dream.core.backends.openai_compat import OpenAICompatBackend  # noqa: E402
from dream.tools.native import NATIVE_TOOLS  # noqa: E402

pytestmark = pytest.mark.asyncio


def _backend_for(key: str) -> OpenAICompatBackend:
    prov = SimpleNamespace(key=key, label=key, base_url="https://api.example.com/v1",
                           multimodal=False, api_key=lambda: "n")
    subs = {"worker": SimpleNamespace(description="does work", prompt="p", tool_names=["read_file"])}
    b = OpenAICompatBackend(provider=prov, model="m", system_prompt="S", tools=list(NATIVE_TOOLS),
                            permission_cb=None, subagents=subs)
    b.n_ctx = 16384
    return b


def _round(calls):
    """One assistant round with several tool calls: [(id, name, args_dict), ...]."""
    deltas = [{"index": i, "id": cid, "function": {"name": name, "arguments": json.dumps(args)}}
              for i, (cid, name, args) in enumerate(calls)]
    return [_sse({"choices": [{"delta": {"tool_calls": deltas}, "finish_reason": None}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}), "data: [DONE]"]


def _task(cid, prompt):
    return (cid, "task", {"subagent_type": "worker", "prompt": prompt})


class _Meter:
    def __init__(self):
        self.inflight = 0
        self.peak = 0
        self.order: list[str] = []

    async def run(self, sub, prompt):
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        self.order.append(f"start {prompt}")
        try:
            await asyncio.sleep(0.15)
            if prompt == "boom":
                raise RuntimeError("worker fell over")
            return f"did {prompt}", False
        finally:
            self.inflight -= 1
            self.order.append(f"end {prompt}")


def _tool_msgs(b):
    return [m for m in b.messages if m.get("role") == "tool"]


def _assistant_ids(b):
    m = next(m for m in b.messages if m.get("role") == "assistant" and m.get("tool_calls"))
    return [tc["id"] for tc in m["tool_calls"]]


async def test_three_tasks_overlap_on_openai_and_stay_ordered(monkeypatch):
    b = _backend_for("openai")
    meter = _Meter()
    monkeypatch.setattr(b, "_run_subagent", meter.run)
    b._client = _FakeClient([_round([_task("c1", "one"), _task("c2", "two"), _task("c3", "three")]), _text_round("ok")])
    t0 = time.monotonic()
    events = [ev async for ev in b.ask("go")]
    assert time.monotonic() - t0 < 0.4, "three 0.15 s subagents ran together"
    assert meter.peak == 3
    assert [m["tool_call_id"] for m in _tool_msgs(b)] == ["c1", "c2", "c3"] == _assistant_ids(b)
    assert [m["content"] for m in _tool_msgs(b)] == ["did one", "did two", "did three"]
    uses = [e.data["id"] for e in events if e.kind == "tool_use"]
    results = [e.data["id"] for e in events if e.kind == "tool_result"]
    assert uses == results == ["c1", "c2", "c3"]


async def test_the_same_round_runs_one_at_a_time_on_machx(monkeypatch):
    b = _backend_for("machx")
    meter = _Meter()
    monkeypatch.setattr(b, "_run_subagent", meter.run)
    b._client = _FakeClient([_round([_task("c1", "one"), _task("c2", "two"), _task("c3", "three")]), _text_round("ok")])
    t0 = time.monotonic()
    [ev async for ev in b.ask("go")]
    assert time.monotonic() - t0 >= 0.44, "serial: the sleeps add up"
    assert meter.peak == 1
    assert meter.order == ["start one", "end one", "start two", "end two", "start three", "end three"]
    assert [m["tool_call_id"] for m in _tool_msgs(b)] == ["c1", "c2", "c3"]


async def test_a_failing_subagent_is_its_own_error_and_other_tools_keep_their_slot(monkeypatch, tmp_path):
    # read_file runs for real, so it needs the tool context an engine would set
    from dream.memory.store import MemoryStore
    from dream.tools import context as tool_context
    from dream.tools.context import ToolContext, set_context

    store = MemoryStore(tmp_path / "c.db")
    monkeypatch.setattr(tool_context, "_CTX", None)   # restored to None at teardown
    set_context(ToolContext(store=store, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="s", workspace=tmp_path, emit=None))
    b = _backend_for("xai")
    meter = _Meter()
    monkeypatch.setattr(b, "_run_subagent", meter.run)
    seen: list[str] = []
    real = b._guarded_exec

    async def spy(name, args, hist, allowed=None):
        seen.append(name)
        return await real(name, args, hist, allowed=allowed)

    monkeypatch.setattr(b, "_guarded_exec", spy)
    (tmp_path / "f.txt").write_text("hello")
    # a tool BETWEEN two tasks splits them: neither run has two tasks in a row,
    # so nothing batches and nothing races the file read (Gate 13)
    b._client = _FakeClient([_round([_task("c1", "one"), ("c2", "read_file", {"path": str(tmp_path / "f.txt")}),
                                     _task("c3", "boom")]), _text_round("ok")])
    events = [ev async for ev in b.ask("go")]
    msgs = _tool_msgs(b)
    assert [m["tool_call_id"] for m in msgs] == ["c1", "c2", "c3"]
    assert msgs[0]["content"] == "did one" and "hello" in msgs[1]["content"]
    assert "worker fell over" in msgs[2]["content"] or "failed" in msgs[2]["content"]
    res = {e.data["id"]: e.data for e in events if e.kind == "tool_result"}
    assert res["c3"]["is_error"] and not res["c1"]["is_error"] and not res["c2"]["is_error"]
    assert meter.peak == 1, "a tool between two tasks splits the run"
    assert meter.order == ["start one", "end one", "start boom", "end boom"]
    assert seen == ["task", "read_file", "task"]
    tool_context._CTX = None
    store.close()


async def test_gate13_a_tool_written_before_the_tasks_finishes_before_they_start(monkeypatch, tmp_path):
    """Gate 13 blocking finding: the batch started at the top of the round, so a
    tool the model wrote BEFORE its tasks ran concurrently with them — it asked
    to write a file and summarize it, and the summary read the file mid-write."""
    from dream.memory.store import MemoryStore
    from dream.tools import context as tool_context
    from dream.tools.context import ToolContext, set_context

    store = MemoryStore(tmp_path / "r.db")
    monkeypatch.setattr(tool_context, "_CTX", None)
    set_context(ToolContext(store=store, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="s", workspace=tmp_path, emit=None))
    b = _backend_for("openai")
    out = tmp_path / "out.txt"
    seen_by_subagent: list[str] = []

    async def run(sub, prompt):
        seen_by_subagent.append(out.read_text().strip() if out.exists() else "MISSING")
        await asyncio.sleep(0.05)
        return "read it", False

    monkeypatch.setattr(b, "_run_subagent", run)
    b._client = _FakeClient([_round([
        ("c0", "run_bash", {"command": f"sleep 0.3; echo v1 > {out}"}),
        _task("c1", "summarize"), _task("c2", "also")]), _text_round("ok")])
    t0 = time.monotonic()
    [ev async for ev in b.ask("write it then summarize it")]
    assert seen_by_subagent == ["v1", "v1"], "both subagents saw the finished file"
    assert time.monotonic() - t0 >= 0.3
    assert [m["tool_call_id"] for m in _tool_msgs(b)] == ["c0", "c1", "c2"]
    tool_context._CTX = None
    store.close()


async def test_a_single_task_or_a_serial_provider_does_not_batch(monkeypatch):
    b = _backend_for("openai")
    one = [{"name": "task", "id": "c1", "args": '{"subagent_type": "worker", "prompt": "x"}'}]
    assert b._task_batches(one) == {}, "one task is not a run"
    b2 = _backend_for("machx")
    calls = [{"name": "task", "id": f"c{i}", "args": '{"subagent_type": "worker", "prompt": "x"}'} for i in range(3)]
    assert b2._task_batches(calls) == {}, "a single-flight provider never batches"
    assert b.concurrent_tasks() and not b2.concurrent_tasks()
    # an `openai` key pointed at a single-flight server on this machine stays serial
    local = _backend_for("openai")
    for url in ("http://127.0.0.1:8080/v1", "http://localhost/v1", "http://[::1]:8080/v1",
                "http://user:pw@localhost:8080/v1", "http://127.0.0.2/v1",
                "http://box.localhost/v1", "http://0.0.0.0/v1", ""):
        local.provider.base_url = url
        assert not local.concurrent_tasks(), url
    for url in ("https://api.openai.com/v1", "http://localhost.example.com/v1",
                "http://10.0.0.14:8080/v1"):
        local.provider.base_url = url
        assert local.concurrent_tasks(), url
    # the runs a round splits into: contiguous, two or more
    mixed = [{"name": n, "id": f"c{i}", "args": '{"subagent_type": "worker", "prompt": "x"}'}
             for i, n in enumerate(["read_file", "task", "task", "write_file", "task", "task", "task"])]
    assert {k: sorted(v) for k, v in b._task_batches(mixed).items()} == {1: [1, 2], 4: [4, 5, 6]}
    assert b._task_batches([mixed[0], mixed[1]]) == {}, "one task is not a run"
