"""The OpenAI-compat backend surviving the real world: interrupted rounds, a
flaky HTTP hop, and a subagent that spirals.

Three field failures this pins:

- The assistant message carries ALL of a round's tool_calls at once, but their
  results are appended one at a time. Anything that walks away in between (the
  engine's tool-budget stop, a Ctrl-C landing while a handler runs) leaves a
  tool_call with no matching tool message — and a strict server 400s on that
  history forever after, while a local model hallucinates the missing result.
- One transient blip (MachX swapping a model, a 429, a reset mid-handshake)
  threw away a whole in-progress agentic turn. Retrying is only safe BEFORE any
  stream bytes are consumed; after that a retry would duplicate output.
- `_subagent_loop` never had the main loop's repetition guard, so a subagent
  could grind out identical calls for its entire round budget.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import _SUB_MAX_ROUNDS, OpenAICompatBackend
from dream.core.subagents import LocalSubagentSpec


@pytest.fixture(autouse=True)
def _no_backoff_sleeps(monkeypatch):
    """Retry timing is asserted directly on _retry_delay; the end-to-end tests
    only care that the retry happened."""
    monkeypatch.setattr(openai_compat, "_RETRY_BACKOFF_S", (0.0, 0.0, 0.0))


def _tool(name, handler):
    return SimpleNamespace(
        name=name, description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def _backend(tools=(), subs=None) -> OpenAICompatBackend:
    p = SimpleNamespace(
        key="machx", label="MachX", base_url="http://x/v1",
        multimodal=False, api_key=lambda: "n",
    )
    return OpenAICompatBackend(
        provider=p, model="m", system_prompt="s",
        tools=list(tools), permission_cb=None, subagents=subs,
    )


def _researcher():
    return {"researcher": LocalSubagentSpec(
        name="researcher", description="web research",
        prompt="You are the researcher.", tool_names=("web_search", "recall"),
    )}


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


def _text_round(text="done"):
    return [
        _sse({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]


def _multi_call_round(n=2, name="read_file"):
    deltas = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": i, "id": f"c{i}",
             "function": {"name": name, "arguments": "{}"}}
        ]}, "finish_reason": None}]})
        for i in range(n)
    ]
    return deltas + [_sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
                     "data: [DONE]"]


# --- fake streaming transport ------------------------------------------------

class _Resp:
    def __init__(self, lines, status=200, body=b"", headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._lines = lines
        self._body = body

    async def aiter_lines(self):
        for ln in self._lines:
            if isinstance(ln, Exception):
                raise ln
            yield ln

    async def aread(self):
        return self._body


class _Ctx:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        if self._exc is not None:
            raise self._exc
        return self._resp

    async def __aexit__(self, *a):
        return False


class _ScriptedClient:
    """Each entry is either an Exception (raised on open), an (status, headers)
    tuple (a non-200), or a list of SSE lines (a good stream). The last entry
    repeats."""

    def __init__(self, script):
        self._script = list(script)
        self.attempts = 0
        self.payloads = []

    def stream(self, method, url, json=None):
        step = self._script[min(self.attempts, len(self._script) - 1)]
        self.attempts += 1
        self.payloads.append(json)
        if isinstance(step, Exception):
            return _Ctx(exc=step)
        if isinstance(step, tuple):
            status, headers = step
            return _Ctx(resp=_Resp([], status=status, body=b"server busy",
                                   headers=headers))
        return _Ctx(resp=_Resp(step))


# --- fake non-streaming transport (subagent path) ----------------------------

class _PostResp:
    def __init__(self, payload=None, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._p = payload or {}
        self.text = json.dumps(self._p)

    def json(self):
        return self._p


class _PostClient:
    def __init__(self, script):
        self._script = list(script)
        self.attempts = 0
        self.posted = []

    async def post(self, url, json=None):
        step = self._script[min(self.attempts, len(self._script) - 1)]
        self.attempts += 1
        self.posted.append(json)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return _PostResp(status=step)
        return _PostResp(step)


def _sub_toolcall(name, args):
    return {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "s1", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}}]}


def _sub_final(text):
    return {"choices": [{"message": {"content": text, "tool_calls": []}}]}


def _pairs_balanced(messages) -> bool:
    want = sorted(tc["id"] for m in messages if m.get("role") == "assistant"
                  for tc in (m.get("tool_calls") or []))
    got = sorted(m["tool_call_id"] for m in messages if m.get("role") == "tool")
    return want == got


# --- defect 2: dangling tool_call / tool pairs -------------------------------

async def test_dangling_tool_call_is_repaired_before_the_next_request():
    b = _backend()
    # A round the consumer abandoned after the first result: 'c1' never landed.
    b.messages.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": "c0", "type": "function",
         "function": {"name": "read_file", "arguments": "{}"}},
        {"id": "c1", "type": "function",
         "function": {"name": "read_file", "arguments": "{}"}},
    ]})
    b.messages.append({"role": "tool", "tool_call_id": "c0", "content": "first"})

    b._client = _ScriptedClient([_text_round("ok")])
    [ev async for ev in b.ask("continue")]

    sent = b._client.payloads[0]["messages"]
    assert _pairs_balanced(sent)
    stub = next(m for m in sent if m.get("tool_call_id") == "c1")
    assert "interrupted" in stub["content"]
    # The stub belongs to ITS round — it must precede the new user message.
    assert sent.index(stub) < next(
        i for i, m in enumerate(sent)
        if m.get("role") == "user" and str(m.get("content")).startswith("continue"))


async def test_repair_leaves_a_complete_history_alone():
    b = _backend()
    b.messages.append({"role": "assistant", "content": None, "tool_calls": [
        {"id": "c0", "type": "function",
         "function": {"name": "read_file", "arguments": "{}"}},
    ]})
    b.messages.append({"role": "tool", "tool_call_id": "c0", "content": "first"})
    b._client = _ScriptedClient([_text_round("ok")])
    [ev async for ev in b.ask("continue")]
    sent = b._client.payloads[0]["messages"]
    assert [m.get("content") for m in sent if m.get("role") == "tool"] == ["first"]


async def test_cancel_inside_a_tool_handler_still_closes_the_round():
    ran = []

    async def handler(args):
        ran.append(1)
        if len(ran) == 2:
            raise asyncio.CancelledError()  # Ctrl-C landing mid-handler
        return {"content": [{"type": "text", "text": "ok"}]}

    b = _backend([_tool("read_file", handler)])
    b._client = _ScriptedClient([_multi_call_round(n=3)])

    with pytest.raises(asyncio.CancelledError):
        async for _ in b.ask("go"):
            pass

    assert _pairs_balanced(b.messages)  # nothing dangling despite the abort
    stubs = [m for m in b.messages
             if m.get("role") == "tool" and "interrupted" in m["content"]]
    assert len(stubs) == 2  # the cancelled call and the one after it


# --- defect 3: bounded retry on the streaming path ---------------------------

async def test_transient_5xx_is_retried_and_the_turn_survives():
    b = _backend()
    b._client = _ScriptedClient([(503, {}), _text_round("recovered")])
    events = [ev async for ev in b.ask("hi")]

    assert not [e for e in events if e.kind == "error"]
    assert next(e.data for e in events if e.kind == "assistant_done") == "recovered"
    assert b._client.attempts == 2
    # A retry must not re-append anything: exactly one user + one assistant turn.
    assert [m["role"] for m in b.messages] == ["system", "user", "assistant"]


async def test_connect_error_is_retried():
    b = _backend()
    b._client = _ScriptedClient([httpx.ConnectError("connection refused"),
                                 _text_round("up again")])
    events = [ev async for ev in b.ask("hi")]
    assert not [e for e in events if e.kind == "error"]
    assert b._client.attempts == 2


async def test_429_is_retried():
    b = _backend()
    b._client = _ScriptedClient([(429, {"retry-after": "0"}), _text_round("ok")])
    events = [ev async for ev in b.ask("hi")]
    assert not [e for e in events if e.kind == "error"]
    assert b._client.attempts == 2


async def test_retry_gives_up_cleanly_after_the_bounded_attempts():
    b = _backend()
    b._client = _ScriptedClient([(503, {})])  # never recovers
    events = [ev async for ev in b.ask("hi")]

    errs = [e.data for e in events if e.kind == "error"]
    assert len(errs) == 1 and "503" in errs[0] and "MachX" in errs[0]
    assert b._client.attempts == openai_compat._RETRY_ATTEMPTS
    assert [m["role"] for m in b.messages] == ["system", "user"]


async def test_a_4xx_is_not_retried():
    b = _backend()
    b._client = _ScriptedClient([(400, {}), _text_round("never reached")])
    events = [ev async for ev in b.ask("hi")]
    errs = [e.data for e in events if e.kind == "error"]
    assert len(errs) == 1 and "400" in errs[0]
    assert b._client.attempts == 1  # a bad request will never become a good one


async def test_a_failure_after_bytes_are_consumed_is_never_retried():
    # Retrying here would replay text the consumer already saw.
    b = _backend()
    dying = [_sse({"choices": [{"delta": {"content": "half an ans"}}]}),
             httpx.ReadError("connection reset")]
    b._client = _ScriptedClient([dying, _text_round("would duplicate")])
    events = [ev async for ev in b.ask("hi")]

    assert [e.data for e in events if e.kind == "text_delta"] == ["half an ans"]
    assert any(e.kind == 'error' for e in events)
    assert events[-1].kind == 'result'
    assert events[-1].data['is_error'] is True
    assert events[-1].data['subtype'] == 'stream_error'
    assert b._client.attempts == 1


def test_retry_delay_uses_the_table_and_honors_retry_after():
    assert openai_compat._retry_delay(0, None) == openai_compat._RETRY_BACKOFF_S[0]
    assert openai_compat._retry_delay(1, None) == openai_compat._RETRY_BACKOFF_S[1]
    assert openai_compat._retry_delay(9, None) == openai_compat._RETRY_BACKOFF_S[-1]
    assert openai_compat._retry_delay(0, 3.0) == 3.0
    # A bogus Retry-After must not freeze the session for an hour.
    assert openai_compat._retry_delay(0, 3600.0) == openai_compat._RETRY_MAX_WAIT_S


def test_retry_after_header_parsing():
    assert openai_compat._retry_after(SimpleNamespace(headers={"retry-after": "2"})) == 2.0
    assert openai_compat._retry_after(SimpleNamespace(headers={})) is None
    assert openai_compat._retry_after(SimpleNamespace(headers={"retry-after": "Wed, 21 Oct"})) is None
    assert openai_compat._retry_after(SimpleNamespace()) is None  # header-less fakes


# --- defect 3 (cont.): the subagent's non-streaming post ---------------------

async def test_subagent_post_retries_a_transient_failure():
    async def h(a):
        return {"content": [{"type": "text", "text": "x"}]}

    b = _backend([_tool("web_search", h), _tool("recall", h)], _researcher())
    b._client = _PostClient([500, _sub_final("done")])
    out, err = await b._run_subagent("researcher", "t")
    assert not err and out == "done"
    assert b._client.attempts == 2


async def test_subagent_reports_a_persistent_failure_without_killing_the_turn():
    async def h(a):
        return {"content": [{"type": "text", "text": "x"}]}

    b = _backend([_tool("web_search", h), _tool("recall", h)], _researcher())
    b._client = _PostClient([httpx.ConnectError("refused")])
    out, err = await b._run_subagent("researcher", "t")
    assert err and "researcher" in out
    assert b._client.attempts == openai_compat._RETRY_ATTEMPTS


# --- defect 4: the subagent gets the main loop's repetition guard ------------

async def test_subagent_loop_guard_stops_an_identical_call_spiral():
    runs = 0

    async def h(a):
        nonlocal runs
        runs += 1
        return {"content": [{"type": "text", "text": "the same dead end"}]}

    b = _backend([_tool("web_search", h), _tool("recall", h)], _researcher())
    b._client = _PostClient([_sub_toolcall("web_search", {"q": "x"})])  # forever
    out, err = await b._run_subagent("researcher", "spiral")

    assert runs == 2  # round 3 is blocked, not executed
    assert err and "loop" in out.lower()
    assert b._client.attempts < _SUB_MAX_ROUNDS  # cut long before the budget


async def test_subagent_with_changing_results_is_never_guarded():
    runs = 0

    async def h(a):
        nonlocal runs
        runs += 1
        return {"content": [{"type": "text", "text": f"state #{runs}"}]}

    b = _backend([_tool("web_search", h), _tool("recall", h)], _researcher())
    b._client = _PostClient([_sub_toolcall("web_search", {"q": "x"}),
                             _sub_toolcall("web_search", {"q": "x"}),
                             _sub_toolcall("web_search", {"q": "x"}),
                             _sub_final("summary")])
    out, err = await b._run_subagent("researcher", "t")
    assert not err and out == "summary"
    assert runs == 3
