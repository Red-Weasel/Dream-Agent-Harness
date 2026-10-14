"""Red-team finding: a MachX/OpenAI-compat generation cut off at max_tokens
reports finish_reason="length" but the backend used to emit subtype="success",
so consolidation trusted a truncated dream (and, with the note-recovery path,
permanently retired stray notes it never really folded in). The terminal result
must carry a non-success subtype when the run was length-truncated.
"""

from __future__ import annotations

import json

import pytest
from types import SimpleNamespace

from dream.core.backends.openai_compat import OpenAICompatBackend


def _backend() -> OpenAICompatBackend:
    p = SimpleNamespace(
        key="machx", label="MachX", base_url="http://x/v1",
        multimodal=False, api_key=lambda: "n",
    )
    return OpenAICompatBackend(
        provider=p, model="m", system_prompt="s", tools=[], permission_cb=None
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


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, json=None):
        return _FakeStream(self._lines)


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


async def _result_of(lines) -> dict:
    b = _backend()
    b._client = _FakeClient(lines)
    result = None
    async for ev in b.ask("hi"):
        if ev.kind == "result":
            result = ev.data
    return result


async def test_length_truncation_reports_non_success_subtype():
    lines = [
        _sse({"choices": [{"delta": {"content": "half a sum"}, "finish_reason": None}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "length"}]}),
        "data: [DONE]",
    ]
    result = await _result_of(lines)
    assert result is not None
    assert result["subtype"] == "length"  # consolidation's guard will catch this
    assert result["is_error"] is True  # Every consumer must see that the answer is incomplete.


async def test_clean_stop_reports_success_subtype():
    lines = [
        _sse({"choices": [{"delta": {"content": "all done"}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    result = await _result_of(lines)
    assert result is not None
    assert result["subtype"] == "success"


async def _system_texts(lines) -> list[str]:
    b = _backend()
    b._client = _FakeClient(lines)
    return [ev.data for ev in [ev async for ev in b.ask("hi")] if ev.kind == "system"]


@pytest.mark.parametrize("body", [
    '<invoke name="write_file"><parameter name="path">a.py</parameter><parameter name="content">print(',
    "<tool_call>\n<function=write_file>\n<parameter=path>\na.py\n</parameter>\n<parameter=content>\nprint("])
async def test_an_unfinished_call_in_either_text_form_is_reported(body):
    """2026-10-12: Qwen's text form (<tool_call><function=...>, which MachX passes through for an unlisted function,
    fd936ef) left open was not recognised as a call at all; DeepSeek's <invoke> form already was. Both are now reported
    (the turn still ends, as it did for <invoke>). A reply the server ENDED ("stop") is not blamed on a token limit
    (gate round 1); one cut at the limit ("length") still says so."""
    stopped = await _system_texts([_sse({"choices": [{"delta": {"content": body}, "finish_reason": None}]}),
                                   _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}), "data: [DONE]"])
    assert any("A tool call was left unfinished" in t and "nothing ran" in t for t in stopped), stopped
    assert not any("DREAM_MAX_TOKENS" in t or "cut off at" in t for t in stopped), stopped
    cut = await _system_texts([_sse({"choices": [{"delta": {"content": body}, "finish_reason": None}]}),
                               _sse({"choices": [{"delta": {}, "finish_reason": "length"}]}), "data: [DONE]"])
    assert any("A tool call was cut off at" in t for t in cut), cut


QUOTED = ("Qwen writes a call as text. The opening looks like this:\n\n```\n<tool_call>\n<function=NAME>\n```\n\n"
          "and `<tool_call><function=x>` inline too; MachX parses it when the function is listed.")


async def test_an_opening_quoted_in_code_is_prose_for_the_lead():
    texts = await _system_texts([_sse({"choices": [{"delta": {"content": QUOTED}, "finish_reason": None}]}),
                                 _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}), "data: [DONE]"])
    assert not any("tool call" in t for t in texts), texts


async def test_an_opening_quoted_in_code_is_prose_for_a_worker_cut_at_its_limit():
    """(gate round 1, finding 4) A worker's answer that quotes the syntax, cut at the reply limit, is kept and continued
    -- not discarded as a half-written call."""
    import test_worker_lane_context as wl
    backend = wl._lanes_session(wl._cut("Part one of the answer. " + QUOTED + " Then "), wl._sub_final("the rest."))
    out, failed = await backend._run_subagent("researcher", "explain")
    assert "Part one of the answer." in out and not failed
    assert "while writing a tool call" not in backend._client.posted[1]["messages"][-1]["content"]
