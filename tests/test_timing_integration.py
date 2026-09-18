"""Measured event sequencing with a deterministic clock and fake SSE transport."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from dream.core.backends.base import Event
from dream.core.engine import Engine
from dream.telemetry.turn import TurnTiming
from dream.tools.context import ToolContext
from test_schema_deferral import _backend, _sse, _FakeClient, _text_round


@pytest.mark.asyncio
async def test_empty_header_is_not_first_activity_and_cache_is_reported(monkeypatch):
    now = [0.0]
    clock = lambda: now[0]
    b = _backend()
    b.turn_timing = TurnTiming(clock)
    monkeypatch.setattr("dream.core.backends.openai_compat.time", SimpleNamespace(monotonic=clock))
    b._client = object()
    chunks = [
        (1, {"choices": [{"delta": {"role": "assistant"}}]}),
        (3, {"choices": [{"delta": {"reasoning_content": "thinking"}}]}),
        (5, {"choices": [{"delta": {"content": "answer"}}]}),
        (6, {"usage": {"prompt_tokens": 10, "completion_tokens": 3,
                       "prompt_tokens_details": {"cached_tokens": 0}}, "choices": []}),
    ]
    class Response:
        async def aiter_lines(self):
            for timestamp, chunk in chunks:
                now[0] = timestamp
                yield _sse(chunk)
    @asynccontextmanager
    async def stream(payload):
        yield Response()
    monkeypatch.setattr(b, "_stream_with_retry", stream)
    [event async for event in b.ask("hello")]
    result = b.turn_timing.finish("completed")
    assert result["first_activity_s"] == 3
    assert result["first_text_s"] == 5
    assert result["requests"][0]["first_activity_s"] == 3
    assert result["requests"][0]["first_text_s"] == 5
    assert result["requests"][0]["server_timings"] == {}
    assert result["cache"] == {"reported_requests": 1, "unreported_requests": 0, "cached_tokens": 0}
    assert "answer" not in str(result) and "thinking" not in str(result)


@pytest.mark.asyncio
async def test_generation_error_request_recorded_before_result_can_freeze_timing():
    b = _backend()
    b.turn_timing = TurnTiming()
    b._client = _FakeClient([[_sse({"error": {"message": "fixture failure", "type": "server_error"}})]])
    async for event in b.ask("hello"):
        if event.kind == "result":
            b.turn_timing.finish("error")
    assert b.turn_timing.summary()["request_count"] == 1


def engine_fixture(tmp_path, emit):
    engine = Engine(provider="openai", workspace=tmp_path, emit=emit)
    engine.backend = _backend()
    engine._started = True
    engine._tool_context = ToolContext(None, None, None, engine.session_id, workspace=tmp_path)
    return engine


@pytest.mark.asyncio
async def test_engine_freezes_mode_before_preparation_and_emits_timing(tmp_path, monkeypatch):
    emitted = []
    engine = engine_fixture(tmp_path, emitted.append)
    b = engine.backend
    b.set_performance_mode("quick")
    b._client = _FakeClient([_text_round("ok")])
    async def inner(prompt):
        # Simulate a mode edit while task-guidance preparation is in progress.
        b.set_performance_mode("thorough")
        async for event in b.ask(prompt):
            yield event
    monkeypatch.setattr(engine, "_ask", inner)
    events = [event async for event in engine.ask("hello")]
    assert b._client.payloads[0]["max_tokens"] == 2048
    assert events[-1].data["stats"]["timing"]["outcome"] == "completed"
    assert emitted[-1].kind == "turn_timing"
    assert emitted[-1].data["first_text_s"] is not None
    await b.close_background()


@pytest.mark.asyncio
async def test_engine_cancel_emits_interrupted_timing(tmp_path, monkeypatch):
    emitted = []
    engine = engine_fixture(tmp_path, emitted.append)
    started = asyncio.Event()
    async def inner(prompt):
        started.set()
        await asyncio.Event().wait()
        yield Event("result", {})
    monkeypatch.setattr(engine, "_ask", inner)
    async def consume():
        return [event async for event in engine.ask("hello")]
    task = asyncio.create_task(consume())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert emitted[-1].kind == "turn_timing"
    assert emitted[-1].data["outcome"] == "interrupted"
    await engine.backend.close_background()
