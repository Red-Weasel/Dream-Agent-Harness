"""Field finding: a small local model that hits a dead-end tool result (e.g. a
search-form page with no listings) retries the *identical* tool call, gets the
identical result, and each round stuffs another identical (assistant, tool
result) pair into the context — in-context repetition is self-reinforcing, so
within a few rounds the model locks into a verbatim loop ("I understand you're
looking to create a professional…" × 15, 11 identical read_url calls in one
session). Sampling penalties can't reach it: they never see the prompt history.

The backend's own loop must break the spiral:
- a repeated identical call whose result is byte-identical gets a warning
  appended to the result (novel context breaks the lock-in),
- the next identical repeat is not executed at all — a guard error comes back,
- if the model *still* repeats, the turn ends with subtype "loop_detected"
  (consolidation already distrusts any non-"success" subtype).

Stateful tools are exempt by construction: `git status` twice with different
output never trips the guard, because the guard keys on identical *results*,
not just identical calls.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from dream.core.backends.openai_compat import OpenAICompatBackend


def _tool(handler):
    return SimpleNamespace(
        name="read_url",
        description="fetch a page",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}},
        handler=handler,
    )


def _backend(tool) -> OpenAICompatBackend:
    p = SimpleNamespace(
        key="machx", label="MachX", base_url="http://x/v1",
        multimodal=False, api_key=lambda: "n",
    )
    return OpenAICompatBackend(
        provider=p, model="m", system_prompt="s", tools=[tool], permission_cb=None
    )


class _FakeResponse:
    def __init__(self, lines, status=200):
        self.status_code = status
        self._lines = lines

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _FakeStream:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResponse(self._lines)

    async def __aexit__(self, *exc):
        return False


class _SequenceClient:
    """Serves one scripted SSE response per request; the last script repeats."""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.requests = 0

    def stream(self, method, url, json=None):
        i = min(self.requests, len(self._scripts) - 1)
        self.requests += 1
        return _FakeStream(self._scripts[i])


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


def _tool_call_round(url="https://bar.example/find") -> list[str]:
    args = json.dumps({"url": url})
    return [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1",
             "function": {"name": "read_url", "arguments": args}},
        ]}, "finish_reason": None}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


def _text_round(text="done") -> list[str]:
    return [
        _sse({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]


async def _run(backend):
    events = []
    async for ev in backend.ask("find the attorneys"):
        events.append(ev)
    return events


async def test_identical_call_identical_result_is_guarded_then_broken():
    executions = 0

    async def handler(args):
        nonlocal executions
        executions += 1
        return {"content": [{"type": "text", "text": "the same search form page"}]}

    b = _backend(_tool(handler))
    client = _SequenceClient([_tool_call_round()])  # the model repeats forever
    b._client = client

    events = await _run(b)
    result = next(e.data for e in events if e.kind == "result")

    # Round 1 and 2 execute (state could have changed); the identical result on
    # round 2 marks the spiral. Round 3 must NOT execute. Round 4 ends the turn.
    assert executions == 2
    assert result["subtype"] == "loop_detected"
    assert result["is_error"] is True  # An intentional stop still leaves the task incomplete.
    assert client.requests <= 4  # spiral cut long before _MAX_TOOL_ROUNDS

    tool_msgs = [m for m in b.messages if m.get("role") == "tool"]
    assert "loop guard" in tool_msgs[1]["content"].lower()  # warning appended
    assert "loop guard" in tool_msgs[2]["content"].lower()  # blocked, not executed
    # Every assistant tool_call still has a matching tool message (strict servers
    # reject a dangling tool_call on the next request).
    n_calls = sum(len(m.get("tool_calls") or []) for m in b.messages
                  if m.get("role") == "assistant")
    assert n_calls == len(tool_msgs)


async def test_stateful_tool_with_changing_results_never_trips_the_guard():
    executions = 0

    async def handler(args):
        nonlocal executions
        executions += 1
        return {"content": [{"type": "text", "text": f"working tree state #{executions}"}]}

    b = _backend(_tool(handler))
    # Four identical calls whose results all differ (git-status-like), then done.
    b._client = _SequenceClient([
        _tool_call_round(), _tool_call_round(), _tool_call_round(),
        _tool_call_round(), _text_round(),
    ])

    events = await _run(b)
    result = next(e.data for e in events if e.kind == "result")

    assert executions == 4  # every call ran — no guard, no block
    assert result["subtype"] == "success"
    assert all("loop guard" not in (m.get("content") or "").lower()
               for m in b.messages if m.get("role") == "tool")


async def test_different_args_are_different_calls():
    executions = 0

    async def handler(args):
        nonlocal executions
        executions += 1
        return {"content": [{"type": "text", "text": "the same page"}]}

    b = _backend(_tool(handler))
    b._client = _SequenceClient([
        _tool_call_round("https://a.example"),
        _tool_call_round("https://b.example"),
        _tool_call_round("https://c.example"),
        _text_round(),
    ])

    events = await _run(b)
    result = next(e.data for e in events if e.kind == "result")

    assert executions == 3  # distinct args: identical results alone are fine
    assert result["subtype"] == "success"


async def test_abandoning_generator_after_tool_result_leaves_balanced_messages():
    # Regression: engine.ask abandons this generator on the tool-budget cap /
    # Ctrl-C right after a tool_result. The tool message must already be recorded
    # so the assistant `tool_calls` isn't left dangling (which 400s the next turn).
    async def handler(args):
        return {"content": [{"type": "text", "text": "some page"}]}

    b = _backend(_tool(handler))
    b._client = _SequenceClient([_tool_call_round()])
    async for ev in b.ask("go"):
        if ev.kind == "tool_result":
            break  # consumer walks away here, mid-turn

    asst_calls = sum(len(m.get("tool_calls") or []) for m in b.messages
                     if m.get("role") == "assistant")
    tool_msgs = [m for m in b.messages if m.get("role") == "tool"]
    assert asst_calls == len(tool_msgs) == 1  # balanced, not dangling
