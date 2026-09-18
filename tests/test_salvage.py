"""A turn that ends badly must still report what it found.

The failure this is built against, observed on 2026-08-24 with a Q4_K_M local model:
asked to review the system for vulnerabilities, it ran eleven useful commands — open
ports, running containers, network interfaces, sudo state, SSH host keys — then locked
onto a twelfth that returned nothing and repeated it verbatim. The loop guard did its
job and stopped the turn. Dream then printed "loop guard: ended the turn" and threw
all eleven results away. The user got a diagnostic about Dream's internals instead of
the security review they asked for.

The work had already been done. Ending the turn is correct; ending it silently is not.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend


def _tool(name, handler):
    return SimpleNamespace(
        name=name, description=f"{name} tool",
        input_schema={"type": "object", "properties": {}}, handler=handler,
    )


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
    def __init__(self, payload):
        self.status_code = 200; self._p = payload; self.text = json.dumps(payload)
    def json(self): return self._p


class _FakeClient:
    def __init__(self, stream_scripts=(), post_scripts=(), post_fails=False):
        self._s = list(stream_scripts); self.si = 0
        self._p = list(post_scripts); self.pi = 0
        self.posted = []
        self.post_fails = post_fails

    def stream(self, method, url, json=None):
        i = min(self.si, len(self._s) - 1); self.si += 1
        return _StreamCtx(self._s[i])

    async def post(self, url, json=None):
        self.posted.append(copy.deepcopy(json))
        if self.post_fails:
            raise RuntimeError("engine died")
        i = min(self.pi, len(self._p) - 1); self.pi += 1
        return _PostResp(self._p[i])


def _sse(obj): return "data: " + json.dumps(obj)


def _call(name, args, cid="c1"):
    """A stream that emits one tool call and stops."""
    return [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": cid, "function": {"name": name,
                                                 "arguments": json.dumps(args)}}
        ]}, "finish_reason": None}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


def _backend(tools):
    p = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1",
                        multimodal=False, api_key=lambda: "n")
    return OpenAICompatBackend(provider=p, model="m", system_prompt="s",
                               tools=tools, permission_cb=None)


async def _drain(backend, prompt="review my system"):
    return [ev async for ev in backend.ask(prompt)]


def _kinds(events):
    return [e.kind for e in events]


def _texts(events, kind):
    return [e.data for e in events if e.kind == kind]


# A model that asks the same dead-end question forever — the RavenX shape.
def _stuck_scripts():
    return [_call("run_bash", {"command": "cat /etc/firmware"})]


SALVAGE = {"choices": [{"message": {
    "content": "Found: 3 listening ports, one container, no firewall configured.",
    "tool_calls": []}}]}


@pytest.mark.asyncio
async def test_the_loop_guard_still_ends_the_turn():
    """The guard is not being weakened — the spiral must still stop."""
    async def h(args):
        return {"content": [{"type": "text", "text": "(exit 0)\n"}]}

    b = _backend([_tool("run_bash", h)])
    b._client = _FakeClient(_stuck_scripts(), [SALVAGE])
    events = await _drain(b)
    assert any("Loop guard" in t for t in _texts(events, "system"))
    assert _kinds(events)[-1] == "result"


@pytest.mark.asyncio
async def test_findings_are_salvaged_instead_of_discarded():
    """The whole point: the turn ends, but the user still gets the answer."""
    async def h(args):
        return {"content": [{"type": "text", "text": "(exit 0)\n"}]}

    b = _backend([_tool("run_bash", h)])
    b._client = _FakeClient(_stuck_scripts(), [SALVAGE])
    events = await _drain(b)
    done = _texts(events, "assistant_done")
    assert done, "a loop-broken turn produced no answer at all"
    assert "3 listening ports" in done[-1]


@pytest.mark.asyncio
async def test_the_salvage_call_has_no_tools():
    """Safety property. A model that cannot emit a tool call cannot resume the
    spiral that got us here, so salvage can never re-enter the loop."""
    async def h(args):
        return {"content": [{"type": "text", "text": "(exit 0)\n"}]}

    b = _backend([_tool("run_bash", h)])
    fake = _FakeClient(_stuck_scripts(), [SALVAGE])
    b._client = fake
    await _drain(b)
    assert fake.posted, "salvage never made its call"
    payload = fake.posted[-1]
    assert "tools" not in payload
    assert payload.get("stream") is False


@pytest.mark.asyncio
async def test_the_salvage_prompt_forbids_more_tools_and_demands_partials():
    async def h(args):
        return {"content": [{"type": "text", "text": "(exit 0)\n"}]}

    b = _backend([_tool("run_bash", h)])
    fake = _FakeClient(_stuck_scripts(), [SALVAGE])
    b._client = fake
    await _drain(b)
    ask = fake.posted[-1]["messages"][-1]["content"]
    assert "Do not call any more tools" in ask
    assert "Partial findings" in ask


@pytest.mark.asyncio
async def test_the_scaffold_instruction_is_not_kept_in_history():
    """It is scaffolding for one call. Persisted, every later turn would carry a
    standing 'stop calling tools' order."""
    async def h(args):
        return {"content": [{"type": "text", "text": "(exit 0)\n"}]}

    b = _backend([_tool("run_bash", h)])
    b._client = _FakeClient(_stuck_scripts(), [SALVAGE])
    await _drain(b)
    joined = json.dumps(b.messages)
    assert "Do not call any more tools" not in joined


@pytest.mark.asyncio
async def test_the_salvaged_answer_is_kept_in_history():
    """The user saw it, so the next turn must know it was said."""
    async def h(args):
        return {"content": [{"type": "text", "text": "(exit 0)\n"}]}

    b = _backend([_tool("run_bash", h)])
    b._client = _FakeClient(_stuck_scripts(), [SALVAGE])
    await _drain(b)
    assert any(m.get("role") == "assistant" and "3 listening ports" in str(m.get("content"))
               for m in b.messages)


@pytest.mark.asyncio
async def test_the_tool_results_are_still_in_context_when_salvage_runs():
    """Salvage can only work if the findings it summarises are actually there."""
    async def h(args):
        return {"content": [{"type": "text", "text": "PORT 8080 OPEN"}]}

    b = _backend([_tool("run_bash", h)])
    fake = _FakeClient(_stuck_scripts(), [SALVAGE])
    b._client = fake
    await _drain(b)
    assert "PORT 8080 OPEN" in json.dumps(fake.posted[-1]["messages"])


@pytest.mark.asyncio
async def test_a_failed_salvage_degrades_quietly(caplog):
    """Salvage must never become a second way for the turn to fail."""
    async def h(args):
        return {"content": [{"type": "text", "text": "(exit 0)\n"}]}

    b = _backend([_tool("run_bash", h)])
    b._client = _FakeClient(_stuck_scripts(), [], post_fails=True)
    events = await _drain(b)
    assert _kinds(events)[-1] == "result"
    assert any("Nothing could be salvaged" in t for t in _texts(events, "system"))


@pytest.mark.asyncio
async def test_an_empty_salvage_reply_is_treated_as_nothing_salvaged():
    async def h(args):
        return {"content": [{"type": "text", "text": "(exit 0)\n"}]}

    b = _backend([_tool("run_bash", h)])
    b._client = _FakeClient(_stuck_scripts(),
                            [{"choices": [{"message": {"content": "  ", "tool_calls": []}}]}])
    events = await _drain(b)
    assert not _texts(events, "assistant_done")
    assert any("Nothing could be salvaged" in t for t in _texts(events, "system"))


@pytest.mark.asyncio
async def test_the_result_event_still_reports_the_loop_detection():
    """Salvaging changes what the user sees, not what the session records — a
    caller checking why the turn ended must still see loop_detected."""
    async def h(args):
        return {"content": [{"type": "text", "text": "(exit 0)\n"}]}

    b = _backend([_tool("run_bash", h)])
    b._client = _FakeClient(_stuck_scripts(), [SALVAGE])
    events = await _drain(b)
    assert events[-1].data["subtype"] == "loop_detected"
    assert events[-1].data["is_error"] is True


@pytest.mark.asyncio
async def test_a_normal_turn_never_calls_salvage():
    """No extra request on the happy path."""
    b = _backend([])
    fake = _FakeClient([[_sse({"choices": [{"delta": {"content": "hi"},
                                            "finish_reason": "stop"}]}), "data: [DONE]"]], [])
    b._client = fake
    events = await _drain(b)
    assert _texts(events, "assistant_done") == ["hi"]
    assert fake.posted == []
