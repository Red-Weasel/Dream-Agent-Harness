"""Red-team finding: a MachX/OpenAI-compat generation cut off at max_tokens
reports finish_reason="length" but the backend used to emit subtype="success",
so consolidation trusted a truncated dream (and, with the note-recovery path,
permanently retired stray notes it never really folded in). The terminal result
must carry a non-success subtype when the run was length-truncated.
"""

from __future__ import annotations

import json
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
