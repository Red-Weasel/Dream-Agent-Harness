"""Nested Dream P3 (DREAM-190): a worker's words arrive as they are generated.

A backend built for a session with a view (`worker_streaming`, the Engine sets it when it has an emitter) sends its
workers' requests streamed, through the lead's own lease, retry and completion fence, and shows each round's text
and reasoning as agent_activity `text_delta` / `thinking_delta` rows: at most one per kind every ~150 ms, sooner once
400 characters wait, the rest when the stream ends; every row carries the run's id. The round then goes on exactly
as a plain reply would (the `response` row with the whole text, the tool calls, the lead's answer). A server that
refuses the streamed request outright gets the plain request, once, and the session's workers stop streaming.
Reconnect history merges a run's consecutive deltas, never across runs or with the lead's. Scripted transports only:
no engine, no model, no GPU.
"""
import asyncio
import copy
import json

import pytest

from dream.agent_activity import bounded_agent_activity
from dream.core.backends import openai_compat
from dream.core.backends.base import Event
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.inference_coordination import EndpointCoordinator
from dream.core.providers import Provider
from dream.gui.bus import EventBus
from dream.gui.conversation import Conversation
from test_agent_activity import activities
from test_failure_observability import engine_server, text_reply  # noqa: F401  (fixture)
from test_local_subagents import _PostResp, _backend, _researcher, _sub_final, _tool


# --- a scripted streaming transport ---------------------------------------------------------------------------

def _sse(obj):
    return "data: " + json.dumps(obj)


def _delta(**delta):
    return _sse({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]})


def _finish(reason="stop", usage=None):
    lines = [_sse({"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]})]
    if usage:
        lines.append(_sse({"choices": [], "usage": usage}))
    return lines + ["data: [DONE]"]


def _text_stream(text, size=5, reasoning=None, usage=None):
    lines = [_delta(role="assistant")]
    if reasoning:
        lines += [_delta(reasoning_content=reasoning[i:i + size]) for i in range(0, len(reasoning), size)]
    lines += [_delta(content=text[i:i + size]) for i in range(0, len(text), size)]
    return 200, lines + _finish("stop", usage), b""


def _call_stream(name, args_json, call_id="c1", pieces=3):
    """One tool call in fragments: the id and half the name, the rest of the name, the arguments in `pieces`."""
    half = len(name) // 2
    lines = [_sse({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": call_id, "type": "function",
                                                                       "function": {"name": name[:half], "arguments": ""}}]},
                                "finish_reason": None}]}),
             _sse({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"name": name[half:]}}]},
                                "finish_reason": None}]})]
    step = -(-len(args_json) // pieces)
    lines += [_sse({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": args_json[i:i + step]}}]},
                                 "finish_reason": None}]}) for i in range(0, len(args_json), step)]
    return 200, lines + _finish("tool_calls"), b""


class _Resp:
    def __init__(self, status, lines=(), body=b"", pause_every=None):
        self.status_code, self._lines, self._body, self._pause = status, list(lines), body, pause_every
        self.headers = {}

    async def aiter_lines(self):
        for i, line in enumerate(self._lines):
            if self._pause and i and i % self._pause == 0:
                await asyncio.sleep(0)               # the network's pauses: the loop runs between bursts
            yield line

    async def aread(self):
        return self._body

    @property
    def text(self):
        return self._body.decode()


class _Ctx:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *args):
        return False


class _StreamClient:
    """Answers each streamed request from `answers` (a list of (status, lines, body), the last repeats, or a
    callable of the payload) and the plain fallback from `posts`."""

    def __init__(self, answers, posts=(), pause_every=None):
        self.answers = answers if callable(answers) else list(answers)
        self.posts, self.pause_every = list(posts), pause_every
        self.streamed, self.posted = [], []

    def stream(self, method, url, json=None):
        self.streamed.append(copy.deepcopy(json))
        answer = (self.answers(json) if callable(self.answers)
                  else self.answers[min(len(self.streamed) - 1, len(self.answers) - 1)])
        return _Ctx(_Resp(*answer, pause_every=self.pause_every))

    async def post(self, url, json=None):
        self.posted.append(copy.deepcopy(json))
        return _PostResp(self.posts[min(len(self.posted) - 1, len(self.posts) - 1)])


def streaming_worker(events, answers, handler=None, posts=(), pause_every=None):
    async def default(args):
        return {"content": [{"type": "text", "text": "source observed"}]}
    backend = _backend([_tool("web_search", handler or default)], _researcher())
    backend._background_emit = events.append
    backend._client = _StreamClient(answers, posts, pause_every)
    backend.worker_streaming = True
    return backend


def deltas(rows, kind="text_delta"):
    return [r["text"] for r in rows if r["kind"] == kind]


@pytest.fixture
def frozen_clock(monkeypatch):
    """The coalescer's clock, under the test's control: frozen, only the size rule flushes."""
    now = {"t": 1000.0}
    monkeypatch.setattr(openai_compat._DeltaCoalescer, "clock", staticmethod(lambda: now["t"]))
    return now


# --- the streamed round ---------------------------------------------------------------------------------------

async def test_streamed_deltas_concatenate_to_exactly_the_final_content(frozen_clock):
    events = []
    text, reasoning = "The source confirms the reported observation.", "I will check the source first."
    usage = {"prompt_tokens": 21, "completion_tokens": 9}
    backend = streaming_worker(events, [_text_stream(text, size=4, reasoning=reasoning, usage=usage)])
    answer, failed = await backend._run_subagent("researcher", "inspect")
    assert (answer, failed) == (text, False)
    rows = activities(events)
    assert "".join(deltas(rows)) == text and "".join(deltas(rows, "thinking_delta")) == reasoning
    assert not any(r["kind"] == "thinking_report" for r in rows), "streamed reasoning is not reported twice"
    kinds = [r["kind"] for r in rows]
    assert kinds[:2] == ["status", "request"] and kinds[-2:] == ["response", "status"]
    assert set(kinds[2:-2]) == {"thinking_delta", "text_delta"} and kinds.index("text_delta") > kinds.index("thinking_delta")
    response = rows[-2]
    assert response["text"] == text and response["usage"] == usage and type(response["duration_ms"]) is int
    assert all(r["run_id"] == rows[0]["run_id"] and r["request_index"] == 1 and r["model"] == "m"
               for r in rows if r["kind"] in {"text_delta", "thinking_delta"})
    assert backend._delegated_usage == usage
    assert backend._client.streamed[0]["stream"] is True and backend._client.streamed[0]["stream_options"] == {"include_usage": True}


async def test_a_tool_call_split_across_chunks_runs_once(frozen_clock):
    ran = []

    async def search(args):
        ran.append(args)
        return {"content": [{"type": "text", "text": "source observed"}]}

    events = []
    args = json.dumps({"query": "observations of the reported source"})
    backend = streaming_worker(events, [_call_stream("web_search", args, pieces=4), _text_stream("Done.")], search)
    answer, failed = await backend._run_subagent("researcher", "inspect")
    assert (answer, failed) == ("Done.", False)
    assert ran == [{"query": "observations of the reported source"}]
    rows = activities(events)
    use = next(r for r in rows if r["kind"] == "tool_use")
    assert use["data"]["name"] == "web_search" and use["data"]["input"] == {"query": "observations of the reported source"}
    assert use["data"]["id"].endswith(":c1")
    sent = backend._client.streamed[1]["messages"]
    call = next(m for m in sent if m.get("tool_calls"))["tool_calls"]
    assert call == [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": args}}]
    assert [r["kind"] for r in rows].count("response") == 2


async def test_deltas_are_coalesced_by_size_and_by_time(frozen_clock):
    text = "abcdefghi " * 100                                  # 1,000 characters in 10-character chunks
    events = []
    backend = streaming_worker(events, [_text_stream(text, size=10)])
    await backend._run_subagent("researcher", "inspect")
    shown = deltas(activities(events))
    assert "".join(shown) == text and [len(t) for t in shown] == [400, 400, 200]
    events = []
    backend = streaming_worker(events, [_text_stream(text, size=10)])
    original = openai_compat._DeltaCoalescer.clock

    def ticking():
        frozen_clock["t"] += 0.2                                # every chunk arrives 200 ms after the last
        return frozen_clock["t"]
    openai_compat._DeltaCoalescer.clock = staticmethod(ticking)
    try:
        await backend._run_subagent("researcher", "inspect")
    finally:
        openai_compat._DeltaCoalescer.clock = original
    shown = deltas(activities(events))
    assert "".join(shown) == text and len(shown) == 100


async def test_a_single_oversized_chunk_is_split_into_bounded_deltas(frozen_clock):
    text = "word " * 1800                                      # 9,000 characters in one chunk
    events = []
    backend = streaming_worker(events, [_text_stream(text, size=len(text))])
    answer, _ = await backend._run_subagent("researcher", "inspect")
    shown = deltas(activities(events))
    assert "".join(shown) == text and [len(t) for t in shown] == [4000, 4000, 1000]
    assert answer == text.strip()


async def test_a_round_without_content_shows_no_delta_and_the_plain_path_is_untouched(frozen_clock):
    events = []
    backend = streaming_worker(events, [_call_stream("web_search", "{}"), _text_stream("done")])
    await backend._run_subagent("researcher", "inspect")
    rows = activities(events)
    first_round = [r["kind"] for r in rows if r.get("request_index") == 1]
    assert "text_delta" not in first_round and "thinking_delta" not in first_round
    events = []
    plain = _backend([_tool("web_search", lambda a: None)], _researcher())
    plain._background_emit = events.append
    plain._client = _StreamClient([], posts=[_sub_final("plain")])
    assert plain.worker_streaming is False
    assert await plain._run_subagent("researcher", "inspect") == ("plain", False)
    assert plain._client.streamed == [] and len(plain._client.posted) == 1
    assert not any(r["kind"] in {"text_delta", "thinking_delta"} for r in activities(events))


def test_the_backend_takes_worker_streaming_from_its_constructor():
    from types import SimpleNamespace
    provider = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=False, api_key=lambda: "n")
    on = OpenAICompatBackend(provider=provider, model="m", system_prompt="s", tools=[], permission_cb=None,
                             worker_streaming=True)
    off = OpenAICompatBackend(provider=provider, model="m", system_prompt="s", tools=[], permission_cb=None)
    assert on.worker_streaming is True and off.worker_streaming is False


# --- the flood budget -----------------------------------------------------------------------------------------

def _twenty_k(payload):
    prompt = payload["messages"][1]["content"]
    text = (prompt + " ") * (20_000 // (len(prompt) + 1) + 1)
    return _text_stream(text[:20_000], size=100)


async def test_sixteen_concurrent_streams_of_20k_chars_stay_under_the_replay_budget(frozen_clock):
    bus = EventBus()
    backend = streaming_worker([], _twenty_k, pause_every=1)
    backend._background_emit = bus.publish
    answers = await asyncio.gather(*[backend._run_subagent("researcher", f"worker {n:02d} report") for n in range(16)])
    replay = bus.conversation.snapshot()["events"]
    assert len(replay) < 2000, len(replay)
    assert all(not failed and len(text) == 20_000 for text, failed in answers)
    runs = {}
    for event in replay:
        data = event["data"]
        if event["kind"] == "agent_activity" and data["kind"] == "text_delta":
            runs.setdefault(data["run_id"], []).append(data["text"])
    assert len(runs) == 16 and sorted("".join(parts) for parts in runs.values()) == sorted(text for text, _ in answers)
    responses = [e["data"] for e in replay if e["kind"] == "agent_activity" and e["data"]["kind"] == "response"]
    assert len(responses) == 16 and all(r["text"].endswith("\n[Display truncated]") for r in responses)


async def test_worker_bursts_do_not_evict_the_leads_events_from_a_view(frozen_clock):
    bus = EventBus()                                           # 512 deep per view, oldest dropped when full
    backend = streaming_worker([], _twenty_k, pause_every=50)  # 5,000-character bursts between pauses
    seen, published = [], []

    def publish(event):
        published.append(event)
        bus.publish(event)
    backend._background_emit = publish
    with bus.subscribe() as sub:
        async def consume():
            while True:
                seen.append(await sub.get())

        async def lead():
            for i in range(60):
                bus.publish(Event("text_delta", f"lead {i}"))
                await asyncio.sleep(0)

        consumer = asyncio.create_task(consume())
        await asyncio.gather(lead(), *[backend._run_subagent("researcher", f"worker {n:02d} report") for n in range(16)])
        for _ in range(3):
            await asyncio.sleep(0)
        consumer.cancel()
        assert [e.data for e in seen if e.kind == "text_delta"] == [f"lead {i}" for i in range(60)]
        assert sub.dropped == 0
    worker_deltas = [e for e in published if e.data.get("kind") == "text_delta"]
    assert len(worker_deltas) <= 16 * (20_000 // 400 + 1), len(worker_deltas)


# --- the lease fence and the plain fallback -------------------------------------------------------------------

def _incomplete(h):
    """A stream that ends without a finish_reason or [DONE]: the engine went quiet mid-reply."""
    h.sse({"choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]}, done=False)


async def local_streaming_backend(tmp_path, server):
    provider = Provider(key="machx", label="MachX", kind="openai", base_url=server.url, default_api_key="not-needed")

    async def search(args):
        return {"content": [{"type": "text", "text": "source observed"}]}
    backend = OpenAICompatBackend(provider=provider, model="fixture", system_prompt="SYSTEM",
                                  tools=[_tool("web_search", search)], permission_cb=None, subagents=_researcher(),
                                  worker_streaming=True)
    backend._coordination_override = EndpointCoordinator(f"loopback:{server.port}", root=tmp_path / "coord")
    await backend.connect()
    return backend


async def test_an_incomplete_stream_fences_the_lease_and_the_next_run_waits_for_idle(tmp_path, engine_server):
    engine_server.script = [_incomplete, text_reply("recovered")]
    backend = await local_streaming_backend(tmp_path, engine_server)
    events = []
    backend._background_emit = events.append
    answer, failed = await backend._run_subagent("researcher", "inspect")
    assert failed and "completion signal" in answer, answer
    assert backend.coordination_status()["state"] == "uncertain"
    rows = activities(events)
    assert deltas(rows) == ["partial"] and rows[-1]["status"] == "failed"
    assert backend.worker_streaming is True
    answer, failed = await backend._run_subagent("researcher", "inspect again")
    assert (answer, failed) == ("recovered", False)
    assert backend.coordination_status()["state"] != "uncertain"
    assert engine_server.chats() == 2


async def test_a_server_that_rejects_streaming_gets_the_plain_request_once_and_workers_stop_streaming(frozen_clock):
    body = json.dumps({"error": {"message": "streaming is not supported here", "type": "invalid_request_error"}}).encode()
    events = []
    backend = streaming_worker(events, [(400, [], body)], posts=[_sub_final("plain answer")])
    answer, failed = await backend._run_subagent("researcher", "inspect")
    assert (answer, failed) == ("plain answer", False)
    assert backend.worker_streaming is False
    assert len(backend._client.streamed) == 1 and len(backend._client.posted) == 1
    rows = activities(events)
    note = next(r for r in rows if r["kind"] == "status" and "stream" in r.get("text", ""))
    assert note["status"] == "running" and "streaming is not supported here" in note["text"]
    assert rows[-2]["kind"] == "response" and rows[-2]["text"] == "plain answer"
    events = []
    backend._background_emit = events.append
    assert await backend._run_subagent("researcher", "inspect") == ("plain answer", False)
    assert len(backend._client.streamed) == 1 and len(backend._client.posted) == 2


async def test_a_rejection_the_plain_request_shares_leaves_streaming_on(frozen_clock):
    body = json.dumps({"error": {"message": "context too long", "type": "invalid_request_error"}}).encode()

    class _RefusingBoth(_StreamClient):
        async def post(self, url, json=None):
            self.posted.append(copy.deepcopy(json))
            resp = _PostResp({"error": {"message": "context too long", "type": "invalid_request_error"}})
            resp.status_code = 400
            return resp

    events = []
    backend = streaming_worker(events, [(400, [], body)])
    backend._client = _RefusingBoth([(400, [], body)])
    answer, failed = await backend._run_subagent("researcher", "inspect")
    assert failed and "context too long" in answer
    assert backend.worker_streaming is True and len(backend._client.streamed) == 1 and len(backend._client.posted) == 1
    assert activities(events)[-1]["status"] == "failed"


async def test_a_protocol_failure_is_not_a_streaming_rejection(frozen_clock):
    events = []
    lines = [_delta(content="partial answer")]                 # no finish_reason, no [DONE]
    backend = streaming_worker(events, [(200, lines, b"")], posts=[_sub_final("never asked")])
    answer, failed = await backend._run_subagent("researcher", "inspect")
    assert failed and "completion signal" in answer, answer
    assert backend.worker_streaming is True and backend._client.posted == []
    rows = activities(events)
    assert deltas(rows) == ["partial answer"] and rows[-1]["status"] == "failed"


# --- the whitelist and reconnect history ----------------------------------------------------------------------

def test_delta_rows_pass_the_whitelist_bounded():
    for kind in ("text_delta", "thinking_delta"):
        row = bounded_agent_activity({"run_id": "r", "agent": "a", "kind": kind, "request_index": 2, "text": "w " * 2500,
                                      "model": "m", "context": {"used": 5, "window": 10}})
        assert row["kind"] == kind and row["request_index"] == 2 and row["model"] == "m"
        assert row["text"] == ("w " * 2500)[:4000] + "\n[Display truncated]"
        assert "reported_after_response" not in row
    assert bounded_agent_activity({"run_id": "r", "agent": "a", "kind": "text_delta", "text": "data:image/png;base64," + "A" * 500})["text"] == "[Media payload omitted]"


def _worker_delta(run, text, kind="text_delta", request_index=1):
    return Event("agent_activity", {"run_id": run, "agent": "researcher", "kind": kind, "request_index": request_index, "text": text})


def test_reconnect_history_merges_a_runs_consecutive_deltas_and_never_across_runs():
    conversation = Conversation()
    conversation.append(Event("text_delta", "lead "))
    conversation.append(_worker_delta("one", "alpha "))
    conversation.append(_worker_delta("one", "beta "))
    conversation.append(_worker_delta("two", "gamma "))
    conversation.append(_worker_delta("two", "delta "))
    conversation.append(_worker_delta("one", "epsilon"))
    conversation.append(Event("text_delta", "more"))
    conversation.append(_worker_delta("one", "thought", kind="thinking_delta"))
    conversation.append(_worker_delta("one", "zeta", request_index=2))
    conversation.append(_worker_delta("one", "eta", request_index=2))
    events = conversation.snapshot()["events"]
    assert [(e["kind"], e["data"] if e["kind"] != "agent_activity" else (e["data"]["run_id"], e["data"]["kind"], e["data"]["text"]))
            for e in events] == [
        ("text_delta", "lead "),
        # "epsilon" joins run one's open row though run two's came between (gate P3 round 1, blocking 2)
        ("agent_activity", ("one", "text_delta", "alpha beta epsilon")),
        ("agent_activity", ("two", "text_delta", "gamma delta ")),
        ("text_delta", "more"),
        ("agent_activity", ("one", "thinking_delta", "thought")),
        ("agent_activity", ("one", "text_delta", "zetaeta")),
    ]
    conversation.append(Event("agent_activity", {"run_id": "one", "agent": "researcher", "kind": "response", "request_index": 2, "text": "zetaeta"}))
    conversation.append(_worker_delta("one", "after", request_index=2))
    kinds = [e["data"]["kind"] for e in conversation.snapshot()["events"] if e["kind"] == "agent_activity"]
    assert kinds[-3:] == ["text_delta", "response", "text_delta"], "a response row between deltas ends the merge"


def test_reconnect_history_counts_merged_deltas_by_their_text():
    conversation = Conversation(max_chars=100_000)
    for _ in range(200):
        conversation.append(_worker_delta("one", "x " * 200))
    events = conversation.snapshot()["events"]
    assert len(events) == 1 and len(events[0]["data"]["text"]) == 80_000
    assert 80_000 <= conversation.chars < 81_000


# --- gate P3 round 1 --------------------------------------------------------------------------------------------
# Blocking 2: the record merged a worker's delta only into the LAST retained event. With two or more workers
# streaming at once and each chunk 150 ms or more after the last (a slow local model: the coalescer's time rule),
# the runs' pieces interleave, nothing merged, and every piece became a row: 2 runs x 20,000 characters filled the
# 2,000-row record and evicted the lead's earlier events. A run's delta now joins that run's open row (same request,
# same kind) wherever other runs' rows or the lead's events fall; any other row of the run closes it.

def test_a_runs_delta_joins_its_open_row_across_other_runs_and_the_leads_events():
    conversation = Conversation()
    tool_row = Event("agent_activity", {"run_id": "one", "agent": "researcher", "kind": "tool_use", "request_index": 1,
                                        "data": {"id": "one:1:0:x", "name": "web_search", "input": {}}})
    for event in (_worker_delta("one", "a"), _worker_delta("two", "b"), Event("text_delta", "lead"),
                  Event("tool_use", {"name": "task", "input": {}, "id": "t1"}),
                  _worker_delta("one", "c"), _worker_delta("two", "d"),
                  tool_row, _worker_delta("one", "e"),
                  _worker_delta("two", "f", kind="thinking_delta"), _worker_delta("two", "g"), _worker_delta("two", "h")):
        conversation.append(event)
    shown = [(e["data"]["run_id"], e["data"]["kind"], e["data"].get("text")) if e["kind"] == "agent_activity"
             else (e["kind"],) for e in conversation.snapshot()["events"]]
    assert shown == [
        ("one", "text_delta", "ac"),       # "c" joined across run two's row, the lead's text and the lead's call
        ("two", "text_delta", "bd"),
        ("text_delta",), ("tool_use",),
        ("one", "tool_use", None),         # any other row of the run closes its open row ...
        ("one", "text_delta", "e"),        # ... so its next piece starts a new one, after it
        ("two", "thinking_delta", "f"),    # another kind replaces the run's open row
        ("two", "text_delta", "gh"),
    ]


def test_a_runs_deltas_of_two_requests_stay_two_rows():
    # P3 gate round 2: nothing came between these two, but they are two requests' replies.
    conversation = Conversation()
    conversation.append(_worker_delta("one", "first reply", request_index=1))
    conversation.append(_worker_delta("one", "second reply", request_index=2))
    assert [(e["data"]["request_index"], e["data"]["text"]) for e in conversation.snapshot()["events"]] == [
        (1, "first reply"), (2, "second reply")]


def test_an_open_row_that_left_the_record_is_not_written_to():
    conversation = Conversation(max_events=3)
    conversation.append(_worker_delta("one", "early "))
    for i in range(3):
        conversation.append(Event("system", f"line {i}"))        # the run's open row is evicted
    conversation.append(_worker_delta("one", "late"))
    assert [e["data"]["text"] for e in conversation.snapshot()["events"] if e["kind"] == "agent_activity"] == ["late"]
    conversation.clear()
    conversation.append(_worker_delta("one", "fresh"))
    assert [e["data"]["text"] for e in conversation.snapshot()["events"]] == ["fresh"]
    assert conversation.chars == len(str(conversation.snapshot()["events"][0]["data"]))


@pytest.fixture
def ticking_clock(monkeypatch):
    """Every reading of the coalescer's clock is 200 ms after the last: only the time rule flushes."""
    now = {"t": 1000.0}

    def tick():
        now["t"] += 0.2
        return now["t"]
    monkeypatch.setattr(openai_compat._DeltaCoalescer, "clock", staticmethod(tick))
    return now


def _twenty_k_in(size):
    def answer(payload):
        prompt = payload["messages"][1]["content"]
        text = (prompt + " ") * (20_000 // (len(prompt) + 1) + 1)
        return _text_stream(text[:20_000], size=size)
    return answer


@pytest.mark.parametrize("runs, size", [(2, 5), (16, 100)])
async def test_interleaved_streams_on_the_time_rule_merge_per_run_and_keep_the_leads_events(ticking_clock, runs, size):
    bus = EventBus()
    bus.publish(Event("text_delta", "The lead's words before the workers."))
    bus.publish(Event("assistant_done", "The lead's words before the workers."))
    backend = streaming_worker([], _twenty_k_in(size), pause_every=1)
    live = []

    def publish(event):
        live.append(event)
        bus.publish(event)
    backend._background_emit = publish
    answers = await asyncio.gather(*[backend._run_subagent("researcher", f"worker {n:02d} report") for n in range(runs)])
    assert all(not failed and len(text) == 20_000 for text, failed in answers)
    pieces = [e.data["run_id"] for e in live if e.kind == "agent_activity" and e.data["kind"] == "text_delta"]
    assert len(pieces) > 2000, "the pieces alone would fill the record"
    assert sum(a != b for a, b in zip(pieces, pieces[1:])) > 1000, "the runs' pieces interleave"
    snapshot = bus.conversation.snapshot()
    rows = snapshot["events"]
    assert not snapshot["trimmed"] and len(rows) == 2 + 5 * runs, len(rows)
    assert [e["kind"] for e in rows[:2]] == ["text_delta", "assistant_done"], "the lead's earlier events are kept"
    merged = {}
    for e in rows:
        if e["kind"] == "agent_activity" and e["data"]["kind"] == "text_delta":
            merged.setdefault(e["data"]["run_id"], []).append(e["data"]["text"])
    assert len(merged) == runs and all(len(parts) == 1 for parts in merged.values()), "one row per run's reply"
    assert sorted(parts[0] for parts in merged.values()) == sorted(text for text, _ in answers)


# Known-limit 3: the Engine's line `worker_streaming=self.emit is not None` had no test (turning it off left every
# streaming test green). A session with a view streams its workers; a headless engine sends the plain request.

async def test_the_engine_streams_its_workers_only_for_a_session_with_a_view(tmp_path):
    from dream.core.engine import Engine
    from dream.mcp_client import McpClients
    from dream.memory.store import MemoryStore
    from dream.memory.working import WorkingMemory
    from dream.tools.context import ToolContext

    async def backend_of(emit, *, no_emit_attribute=False):
        engine = Engine(provider="machx", model="fixture", workspace=tmp_path, emit=emit)
        if no_emit_attribute:
            del engine.emit           # as a fixture builds one without __init__ (object.__new__): P5 gate finding
        engine.store = MemoryStore(tmp_path / f"{engine.session_id}-{id(emit)}-{no_emit_attribute}.db")
        try:
            engine.store.start_session(engine.session_id)
            engine.working = WorkingMemory(engine.store, engine.session_id)
            engine._tool_context = ToolContext(engine.store, engine.working, None, engine.session_id, workspace=tmp_path)
            engine._mcp = McpClients()
            engine._built_tools = {"tools": [], "server": None, "exempt_tool_ids": set(), "names": [], "warnings": []}
            return await engine._create_backend()
        finally:
            engine.store.close()

    with_view = await backend_of([].append)
    headless = await backend_of(None)
    bare = await backend_of(None, no_emit_attribute=True)
    assert isinstance(with_view, OpenAICompatBackend) and with_view.worker_streaming is True
    assert isinstance(headless, OpenAICompatBackend) and headless.worker_streaming is False
    assert isinstance(bare, OpenAICompatBackend) and bare.worker_streaming is False, "no emitter at all: no streaming"
