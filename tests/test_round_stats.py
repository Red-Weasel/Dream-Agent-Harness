"""Per-round "stats" events: the openai-compat backend reports exact speed the
moment each round ends, the engine passes them through untouched, and the App
feeds them (like every event) into the inference meter."""

from __future__ import annotations

import json
from types import SimpleNamespace

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.backends.base import Event
from dream.core.engine import Engine


def _backend(tools=()) -> OpenAICompatBackend:
    p = SimpleNamespace(
        key="machx", label="MachX", base_url="http://x/v1",
        multimodal=False, api_key=lambda: "n",
    )
    return OpenAICompatBackend(
        provider=p, model="m", system_prompt="s", tools=list(tools), permission_cb=None
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
    """Pops one prepared line-set per request — multi-round turns replay in order."""

    def __init__(self, rounds):
        self._rounds = list(rounds)

    def stream(self, method, url, json=None):
        return _FakeStream(self._rounds.pop(0))


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


def _text_round(text="hello", timings=None, usage=None):
    lines = [_sse({"choices": [{"delta": {"content": text}}]})]
    tail: dict = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    if usage:
        tail["usage"] = usage
    if timings:
        tail["timings"] = timings
    lines.append(_sse(tail))
    lines.append("data: [DONE]")
    return lines


def _tool_round(name="ping", timings=None):
    lines = [_sse({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": name, "arguments": "{}"}}
    ]}}]})]
    tail: dict = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
    if timings:
        tail["timings"] = timings
    lines.append(_sse(tail))
    lines.append("data: [DONE]")
    return lines


async def _collect(backend, prompt="hi"):
    return [ev async for ev in backend.ask(prompt)]


async def test_exact_stats_event_from_server_timings():
    b = _backend()
    b._client = _FakeClient([_text_round(
        timings={"prompt_n": 1000, "prompt_ms": 500.0, "predicted_n": 100,
                 "predicted_ms": 2000.0},
        usage={"prompt_tokens": 1000, "completion_tokens": 100},
    )])
    events = await _collect(b)
    stats = [e for e in events if e.kind == "stats"]
    assert len(stats) == 1
    s = stats[0].data
    assert s["exact"] is True
    assert s["pp_n"] == 1000 and s["pp_ms"] == 500.0
    assert s["gen_n"] == 100 and s["gen_ms"] == 2000.0
    assert s["ttft_ms"] is not None and s["ttft_ms"] >= 0
    # the terminal result still carries the whole-turn aggregate
    result = [e for e in events if e.kind == "result"][0].data
    assert result["stats"]["pp_tps"] == 2000.0 and result["stats"]["gen_tps"] == 50.0


async def test_estimated_stats_event_from_usage_only():
    b = _backend()
    b._client = _FakeClient([_text_round(
        usage={"prompt_tokens": 800, "completion_tokens": 40},
    )])
    events = await _collect(b)
    s = [e for e in events if e.kind == "stats"][0].data
    assert s["exact"] is False
    assert s["pp_n"] == 800 and s["gen_n"] == 40
    assert s["pp_ms"] >= 0 and s["gen_ms"] >= 0


async def test_stats_event_per_round_and_aggregate():
    async def handler(args):
        return {"content": "pong"}

    tool = SimpleNamespace(name="ping", description="d",
                           input_schema={"type": "object", "properties": {}},
                           handler=handler)
    b = _backend(tools=[tool])
    t1 = {"prompt_n": 100, "prompt_ms": 100.0, "predicted_n": 10, "predicted_ms": 100.0}
    t2 = {"prompt_n": 300, "prompt_ms": 100.0, "predicted_n": 30, "predicted_ms": 300.0}
    b._client = _FakeClient([
        _tool_round(timings=t1),
        _text_round(text="done", timings=t2,
                    usage={"prompt_tokens": 300, "completion_tokens": 30}),
    ])
    events = await _collect(b)
    stats = [e.data for e in events if e.kind == "stats"]
    assert len(stats) == 2  # one per round, emitted as each round ends
    assert stats[0]["pp_n"] == 100 and stats[1]["pp_n"] == 300
    # ordering: round-1 stats arrive BEFORE round-2 output
    kinds = [e.kind for e in events]
    assert kinds.index("stats") < kinds.index("tool_result")
    result = [e for e in events if e.kind == "result"][0].data
    assert result["stats"]["pp_tps"] == (400 / 0.2)  # summed across rounds


async def test_no_stats_event_without_timings_or_usage():
    b = _backend()
    b._client = _FakeClient([_text_round()])  # bare stream, no usage/timings
    events = await _collect(b)
    assert not [e for e in events if e.kind == "stats"]


class _StubWorking:
    def log_turn(self, *a, **k):
        pass


class _StatsBackend:
    async def ask(self, prompt):
        yield Event("stats", {"pp_n": 1, "pp_ms": 2.0, "gen_n": 3, "gen_ms": 4.0})
        yield Event("assistant_done", "ok")
        yield Event("result", {"usage": {}, "stats": {}, "subtype": "success",
                               "is_error": False})


async def test_engine_passes_stats_events_through():
    eng = Engine(provider="machx", model="m")
    eng._started = True
    eng._turn_index = 3
    eng._titled = True  # the session already has its title (DREAM-130): no store write
    eng.working = _StubWorking()
    eng.backend = _StatsBackend()
    from dream.tools.context import ToolContext
    eng._tool_context = ToolContext(None, eng.working, None, eng.session_id)
    kinds = [ev.kind async for ev in eng.ask("hi")]
    assert kinds == ["stats", "assistant_done", "result"]


def test_app_feeds_meter_on_every_event(tmp_path):
    from dream.tui.app import App

    app = App(provider="machx", model="m", workspace=tmp_path)
    app.meter.turn_start()
    ev = SimpleNamespace(kind="stats",
                         data={"pp_n": 500, "pp_ms": 250.0, "gen_n": 50, "gen_ms": 1000.0})
    app._feed_cockpit(ev)
    assert app.meter.pp_tps == 2000.0 and app.meter.gen_tps == 50.0
    app._feed_cockpit(SimpleNamespace(kind="text_delta", data="hello world!"))
    assert app.meter.out_tokens_est() == 3
    old = app.meter
    app._reset_session_accounting()
    assert app.meter is not old  # /new starts a fresh meter
