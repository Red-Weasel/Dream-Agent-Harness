"""Nested Dream P4 (DREAM-191): a worker's place in the queue for an engine lane, and the lanes themselves.

A reply's `task` calls that go out together (concurrent_tasks) share the lanes _task_gate bounds: the engine's
/props total_slots, or Parallel workers on a cloud provider. A worker has its run id from the moment it queues
(_bounded_task, before the gate), so one id follows it from `queued` to done: status `queued` ("Waiting for an
engine lane (k of N busy)") while it waits, `running` ("Subagent started.") once it holds a lane, `interrupted`
when the turn stops it while it waits. A worker that waits for the cross-process lease (another Dream request on
the local engine) says `waiting` with the reason, then `running` when the engine is free; a wait that times out
fails the run with the timeout's reason on its terminal row. The `lanes` event {served, busy, queued} says the
counts as they change; session info carries the same snapshot. Scripted transports and a temporary lease root
only: no engine, no model, no GPU.
"""
import asyncio
import copy
import functools
from types import SimpleNamespace

import pytest

from dream.core.backends.openai_compat import _AGENT_ACTIVITY, OpenAICompatBackend
from dream.core.inference_coordination import EndpointCoordinator
from dream.tools.native import NATIVE_TOOLS
from dream.tui.app import App
from test_local_subagents import _FakeClient as _PostClient, _PostResp, _researcher, _sub_final, _tool
from test_parallel_subagents import _backend_for, _round, _task, _tool_msgs
from test_schema_deferral import _FakeClient, _text_round

TERMINAL = {"completed", "failed", "unknown", "interrupted"}


def _rows(events):
    return [e.data for e in events if e.kind == "agent_activity"]


def _lanes(events):
    return [e.data for e in events if e.kind == "lanes"]


def _cards(events):
    """Each worker card's status rows, by run id."""
    cards = {}
    for r in _rows(events):
        if r["kind"] == "status":
            cards.setdefault(r["run_id"], []).append(r["status"])
    return cards


def _stuck(events):
    """The cards that showed a status and never a terminal one."""
    return {run: statuses for run, statuses in _cards(events).items() if not set(statuses) & TERMINAL}


def _paired(b):
    """Every tool call in the lead's history has its result."""
    ids = [tc["id"] for m in b.messages if m.get("role") == "assistant" and m.get("tool_calls") for tc in m["tool_calls"]]
    return sorted(m["tool_call_id"] for m in _tool_msgs(b)) == sorted(ids)


def _lanes_backend(slots, events):
    """A MachX session whose engine serves `slots` lanes (its /props total_slots), with a view attached."""
    b = _backend_for("machx")
    b._server_props = {"default_generation_settings": {"n_ctx": 32768}, "total_slots": slots}
    b._server_props_model = b.model
    b._background_emit = events.append
    return b


class _Meter:
    """Stands in for _run_subagent: counts workers in flight and keeps each one's run id from the activity context."""

    def __init__(self, hold=0.1, release=None):
        self.inflight = self.peak = 0
        self.order, self.ids = [], {}
        self.hold, self.release = hold, release

    async def run(self, sub, prompt, **kwargs):
        context = _AGENT_ACTIVITY.get()
        self.ids[prompt] = context["run_id"] if context else None
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        self.order.append(f"start {prompt}")
        try:
            if self.release is not None:
                await self.release.wait()
            else:
                await asyncio.sleep(self.hold)
            return f"did {prompt}", False
        finally:
            self.inflight -= 1
            self.order.append(f"end {prompt}")


class _LeadAndWorkers:
    """The lead's streamed rounds, and each worker's plain reply after a short hold."""

    def __init__(self, rounds, hold=0.05):
        self.lead, self.hold, self.posted = _FakeClient(rounds), hold, []

    def stream(self, method, url, json=None):
        return self.lead.stream(method, url, json=json)

    async def post(self, url, json=None):
        self.posted.append(copy.deepcopy(json))
        await asyncio.sleep(self.hold)
        return _PostResp(_sub_final("did " + json["messages"][-1]["content"]))


async def _until(events, status, tries=300):
    for _ in range(tries):
        if any(r.get("status") == status for r in _rows(events)):
            return next(r for r in _rows(events) if r.get("status") == status)
        await asyncio.sleep(0.01)
    raise AssertionError(f"no {status!r} row: {[r.get('status') for r in _rows(events)]}")


# --- the in-process gate: queued, running, interrupted ---------------------------------------------------------

async def test_five_workers_on_two_lanes_queue_three_and_run_two_at_a_time(monkeypatch):
    events = []
    b = _lanes_backend(2, events)
    meter = _Meter(hold=0.1)
    monkeypatch.setattr(b, "_run_subagent", meter.run)
    names = ["one", "two", "three", "four", "five"]
    b._client = _FakeClient([_round([_task(f"c{i}", n) for i, n in enumerate(names, 1)]), _text_round("ok")])
    [ev async for ev in b.ask("go")]
    assert meter.peak == 2 and len(meter.ids) == 5 and len(set(meter.ids.values())) == 5
    rows = _rows(events)
    queued = [r for r in rows if r.get("status") == "queued"]
    running = [r for r in rows if r.get("status") == "running"]
    assert len(queued) == 3 and {r["text"] for r in queued} == {"Waiting for an engine lane (2 of 2 busy)"}
    assert len(running) == 5 and all(r["text"] == "Subagent started." for r in running)
    assert rows.index(queued[-1]) < rows.index(running[2]), "all three queue before a third one runs"
    for q in queued:
        run = next(r for r in running if r["run_id"] == q["run_id"])
        assert rows.index(run) > rows.index(q), "a queued worker runs under the id it queued with"
    assert set(meter.ids.values()) == {r["run_id"] for r in running}, "the id the work saw is the id shown"
    lanes = _lanes(events)
    assert lanes and all(l["served"] == 2 for l in lanes)
    assert max(l["busy"] for l in lanes) == 2 and max(l["queued"] for l in lanes) == 3
    assert lanes[-1] == {"served": 2, "busy": 0, "queued": 0}
    busy = [0] + [l["busy"] for l in lanes]
    assert sum(later > earlier for earlier, later in zip(busy, busy[1:])) == 5, "one lanes event per acquire"
    assert [m["content"] for m in _tool_msgs(b)] == [f"did {n}" for n in names]


async def test_a_workers_id_holds_from_queued_to_done():
    events = []
    b = _lanes_backend(2, events)
    b._client = _LeadAndWorkers([_round([_task("c1", "one"), _task("c2", "two"), _task("c3", "three")]),
                                 _text_round("ok")])
    [ev async for ev in b.ask("go")]
    rows = _rows(events)
    queued = [r for r in rows if r.get("status") == "queued"]
    assert len(queued) == 1 and queued[0]["text"] == "Waiting for an engine lane (2 of 2 busy)"
    run = [r for r in rows if r["run_id"] == queued[0]["run_id"]]
    assert [(r["kind"], r.get("status")) for r in run] == [
        ("status", "queued"), ("status", "running"), ("request", "awaiting_response"),
        ("response", "received"), ("status", "completed")]
    assert len({r["run_id"] for r in rows}) == 3
    assert sorted(m["content"] for m in _tool_msgs(b)) == ["did one", "did three", "did two"]
    assert _lanes(events)[-1] == {"served": 2, "busy": 0, "queued": 0}


async def test_a_queued_worker_stopped_with_the_turn_ends_interrupted(monkeypatch):
    events = []
    b = _lanes_backend(2, events)
    release = asyncio.Event()
    meter = _Meter(release=release)
    monkeypatch.setattr(b, "_run_subagent", meter.run)
    b._client = _FakeClient([_round([_task("c1", "one"), _task("c2", "two"), _task("c3", "three")]), _text_round("ok")])

    async def turn():
        return [ev async for ev in b.ask("go")]

    task = asyncio.ensure_future(turn())
    try:
        queued = await _until(events, "queued")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    mine = [r for r in _rows(events) if r["run_id"] == queued["run_id"]]
    assert [r["status"] for r in mine] == ["queued", "interrupted"]
    assert "waiting for an engine lane" in mine[-1]["text"] and "nothing was sent" in mine[-1]["text"]
    assert _lanes(events)[-1] == {"served": 2, "busy": 0, "queued": 0}
    assert meter.inflight == 0
    assert _paired(b), "the lead's history pairs every call"


# --- gate P4 round 1: a worker card that starts ends; busy counts every worker; each lease waiter says so ----------

async def test_a_loop_guarded_batch_ends_its_cards_with_the_text_the_lead_got():
    events = []
    b = _lanes_backend(2, events)
    x, y = {"subagent_type": "worker", "prompt": "x"}, {"subagent_type": "worker", "prompt": "y"}
    rounds = [_round([(f"c{r}a", "task", x), (f"c{r}b", "task", y)]) for r in range(3)] + [_text_round("ok")]
    b._client = _LeadAndWorkers(rounds)
    [ev async for ev in b.ask("go")]
    assert _paired(b) and not _stuck(events), _stuck(events)
    blocked = [r for r in _rows(events) if r.get("status") == "failed"]
    assert len(blocked) == 2 and all(r["text"].startswith(
        "Subagent returned a failed or incomplete result: [loop guard] Not executed") for r in blocked)
    assert _lanes(events)[-1] == {"served": 2, "busy": 0, "queued": 0}


async def test_an_empty_subagent_type_keeps_the_lanes_agent_and_ends_its_card():
    events = []
    b = _lanes_backend(2, events)
    b._client = _LeadAndWorkers([_round([("c1", "task", {"subagent_type": "", "prompt": "x"}), _task("c2", "y")]),
                                 _text_round("ok")])
    [ev async for ev in b.ask("go")]
    assert _paired(b) and not _stuck(events), _stuck(events)
    failed = next(r for r in _rows(events) if r.get("status") == "failed")
    assert failed["agent"] == "subagent" and failed["text"] == "Requested subagent is unavailable."
    assert _tool_msgs(b)[0]["content"].startswith("Error: unknown subagent ''")


async def test_task_calls_without_subagents_start_no_worker_card():
    events = []
    provider = SimpleNamespace(key="openai", label="openai", base_url="https://api.example.com/v1", multimodal=False,
                               api_key=lambda: "n")
    b = OpenAICompatBackend(provider=provider, model="m", system_prompt="S", tools=list(NATIVE_TOOLS),
                            permission_cb=None, subagents=None)
    b.n_ctx = 16384
    b._background_emit = events.append
    b._client = _FakeClient([_round([_task("c1", "one"), _task("c2", "two")]), _text_round("ok")])
    [ev async for ev in b.ask("go")]
    assert _paired(b) and _rows(events) == [] and _lanes(events) == []
    assert all(m["content"].startswith("Error: unknown tool 'task'") for m in _tool_msgs(b))


class _StopWhenOneAnswers(_LeadAndWorkers):
    """Worker "one" answers once released and stops the turn in that same step (it frees a lane as the stop lands);
    the other workers never answer."""

    def __init__(self, rounds, release, box):
        super().__init__(rounds)
        self.release, self.box = release, box

    async def post(self, url, json=None):
        prompt = json["messages"][-1]["content"]
        if prompt == "one":
            await self.release.wait()
            self.box["turn"].cancel()
            return _PostResp(_sub_final("did one"))
        await asyncio.sleep(30)
        return _PostResp(_sub_final("did " + prompt))


async def test_a_stop_landing_as_a_lane_frees_still_ends_the_queued_workers_card():
    events = []
    b = _lanes_backend(2, events)
    box, release = {}, asyncio.Event()
    b._client = _StopWhenOneAnswers([_round([_task("c1", "one"), _task("c2", "two"), _task("c3", "three")]),
                                     _text_round("ok")], release, box)

    async def turn():
        return [ev async for ev in b.ask("go")]
    box["turn"] = task = asyncio.ensure_future(turn())
    queued = await _until(events, "queued")
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert not _stuck(events), _stuck(events)
    mine = _cards(events)[queued["run_id"]]
    assert mine[0] == "queued" and mine[-1] == "interrupted"
    assert b.lanes_status() == {"served": 2, "busy": 0, "queued": 0}
    assert _paired(b)


class _Sampling(_LeadAndWorkers):
    """Records the lanes each worker's request saw."""

    def __init__(self, rounds, backend, seen):
        super().__init__(rounds)
        self.backend, self.seen = backend, seen

    async def post(self, url, json=None):
        self.seen.append(self.backend.lanes_status())
        return await super().post(url, json=json)


async def test_a_lone_task_on_a_two_lane_engine_holds_a_lane():
    events, seen = [], []
    b = _lanes_backend(2, events)
    b._client = _Sampling([_round([_task("c1", "one")]), _text_round("ok")], b, seen)
    [ev async for ev in b.ask("go")]
    assert seen == [{"served": 2, "busy": 1, "queued": 0}]
    assert _lanes(events) == [{"served": 2, "busy": 1, "queued": 0}, {"served": 2, "busy": 0, "queued": 0}]


async def test_workers_on_a_one_lane_engine_each_hold_its_lane():
    events, seen = [], []
    b = _backend_for("machx")                      # no /props: one lane, the task calls run one after another
    b._background_emit = events.append
    b._client = _Sampling([_round([_task("c1", "one"), _task("c2", "two")]), _text_round("ok")], b, seen)
    [ev async for ev in b.ask("go")]
    assert seen == [{"served": 1, "busy": 1, "queued": 0}] * 2, "lanes: 1 of 1 busy while each runs"
    assert _lanes(events)[-1] == {"served": 1, "busy": 0, "queued": 0}
    assert [m["content"] for m in _tool_msgs(b)] == ["did one", "did two"]


# --- gate P4 round 2: the two branches round 1's tests did not reach -------------------------------------------------

async def test_a_batched_worker_whose_first_checkpoint_times_out_ends_its_card(monkeypatch):
    # _exec_tool's first checkpoint raises before the run begins (a work deadline that has passed): _bounded_task's
    # exception branch ends each such card, once, with the text the lead gets; the queued workers then run as usual.
    events = []
    b = _lanes_backend(2, events)
    real, hit = b._check_interruption, set()

    def check(generation):
        context = _AGENT_ACTIVITY.get()
        if context and context.get("lane") and context["run_id"] not in hit and len(hit) < 2:
            hit.add(context["run_id"])
            raise TimeoutError("Work deadline expired.")
        return real(generation)
    monkeypatch.setattr(b, "_check_interruption", check)
    names = ["one", "two", "three", "four"]
    b._client = _LeadAndWorkers([_round([_task(f"c{i}", n) for i, n in enumerate(names, 1)]), _text_round("ok")])
    [ev async for ev in b.ask("go")]
    cards = _cards(events)
    assert len(cards) == 4 and all(sum(s in TERMINAL for s in statuses) == 1 and statuses[-1] in TERMINAL
                                   for statuses in cards.values()), cards
    failed = [r for r in _rows(events) if r.get("status") == "failed"]
    assert {r["run_id"] for r in failed} == hit and {r["text"] for r in failed} == {
        "Subagent returned a failed or incomplete result: Error: TimeoutError: Work deadline expired."}
    assert [m["content"] for m in _tool_msgs(b)] == ["Error: TimeoutError: Work deadline expired."] * 2 + [
        "did three", "did four"]
    assert _paired(b) and _lanes(events)[-1] == {"served": 2, "busy": 0, "queued": 0}


async def test_a_scoped_ask_without_task_starts_no_worker_card():
    events = []
    b = _lanes_backend(2, events)
    b.tool_scope = frozenset({"read_file"})
    b._client = _LeadAndWorkers([_round([_task("c1", "one"), _task("c2", "two")]), _text_round("ok")])
    [ev async for ev in b.ask("go")]
    assert _rows(events) == [] and _lanes(events) == [] and _paired(b)
    assert all(m["content"].startswith("Error: tool 'task' is not offered to this step") for m in _tool_msgs(b))


# --- the cross-process lease: waiting, then running or failed with the reason ----------------------------------

def _leased_backend(tmp_path, events, posts):
    async def search(args):
        return {"content": [{"type": "text", "text": "source observed"}]}
    provider = SimpleNamespace(key="machx", label="MachX", base_url="http://127.0.0.1:1/v1", multimodal=False,
                               api_key=lambda: "n")
    b = OpenAICompatBackend(provider=provider, model="m", system_prompt="s", tools=[_tool("web_search", search)],
                            permission_cb=None, subagents=_researcher())
    b._coordination_override = EndpointCoordinator("loopback:1", root=tmp_path / "coord")
    b._background_emit = events.append
    b._client = _PostClient(post_scripts=posts)
    return b


async def _hold(coordinator):
    """Another Dream request holding the one lease slot until `free` is set."""
    held, free = asyncio.Event(), asyncio.Event()

    async def holder():
        async with coordinator.request():
            held.set()
            await free.wait()
    holding = asyncio.ensure_future(holder())
    await held.wait()
    return free, holding


async def test_a_lease_wait_is_shown_and_ends_when_the_engine_is_free(tmp_path):
    events = []
    b = _leased_backend(tmp_path, events, [_sub_final("done")])
    free, holding = await _hold(b._coordination_override)
    run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    try:
        waiting = await _until(events, "waiting")
    finally:
        free.set()
        await holding
    assert await asyncio.wait_for(run, 5) == ("done", False)
    assert waiting["text"].startswith("Waiting for another Dream request on this local engine")
    rows = [r for r in _rows(events) if r["run_id"] == waiting["run_id"]]
    # the request row comes first (the request is being made); the wait for the lease is inside it
    assert [r["status"] for r in rows] == ["running", "awaiting_response", "waiting", "running", "received", "completed"]
    assert rows[3]["text"].startswith("The local engine is free after") and "sending this request" in rows[3]["text"]
    assert any(e.kind == "system" and "Waiting for another Dream request" in e.data for e in events), "the owner's line stays"


async def test_a_lease_wait_that_times_out_fails_the_run_with_the_reason(tmp_path, monkeypatch):
    events = []
    b = _leased_backend(tmp_path, events, [_sub_final("never sent")])
    coordinator = b._coordination_override
    monkeypatch.setattr(coordinator, "request", functools.partial(coordinator.request, timeout=0.3))
    free, holding = await _hold(coordinator)
    try:
        answer, failed = await asyncio.wait_for(b._run_subagent("researcher", "inspect"), 10)
    finally:
        free.set()
        await holding
    assert failed and "Timed out waiting for another Dream request" in answer
    rows = _rows(events)
    assert [r["status"] for r in rows] == ["running", "awaiting_response", "waiting", "failed"]
    assert "Timed out waiting for another Dream request" in rows[-1]["text"]
    assert b._client.posted == [], "nothing was sent"


async def test_two_workers_waiting_for_the_lease_each_say_so(tmp_path):
    events = []
    b = _leased_backend(tmp_path, events, [_sub_final("done")])
    b._coordination_override.slots = 2
    other = EndpointCoordinator("loopback:1", root=tmp_path / "coord")    # another Dream process holds both slots
    other.slots = 2
    free1, held1 = await _hold(other)
    free2, held2 = await _hold(other)
    first = asyncio.ensure_future(b._run_subagent("researcher", "a"))
    second = asyncio.ensure_future(b._run_subagent("researcher", "b"))
    try:
        for _ in range(200):
            waiting = {r["run_id"] for r in _rows(events) if r.get("status") == "waiting"}
            if len(waiting) == 2:
                break
            await asyncio.sleep(0.01)
    finally:
        free1.set()
        free2.set()
        await held1
        await held2
    assert await asyncio.wait_for(asyncio.gather(first, second), 10) == [("done", False), ("done", False)]
    assert len(waiting) == 2, "each worker waiting for the lease says so on its card"
    assert sum(e.kind == "system" and "Waiting for another Dream request" in e.data for e in events) == 2


# --- the lanes in session info ---------------------------------------------------------------------------------

def test_session_info_carries_the_lanes(tmp_path):
    b = _lanes_backend(2, [])
    assert b.lanes_status() == {"served": 2, "busy": 0, "queued": 0}
    engine = SimpleNamespace(session_id="s", model="m", effort=None, vision_status=lambda: {"enabled": False},
                             backend=b, _steering_inbox=None)
    app = SimpleNamespace(engine=engine, provider_label="MachX", workspace=tmp_path)
    info = App._studio_session_info(app)
    assert info["lanes"] == {"served": 2, "busy": 0, "queued": 0} and info["model"] == "m"
    assert _backend_for("machx").lanes_status() == {"served": 1, "busy": 0, "queued": 0}, "no lanes known: one at a time"
    assert _backend_for("openai").lanes_status() == {"served": 4, "busy": 0, "queued": 0}, "Parallel workers, 4 without a profile"
    engine.backend = None
    assert App._studio_session_info(app)["lanes"] is None, "no backend yet: nothing invented"
