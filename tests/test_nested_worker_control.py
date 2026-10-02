"""Nested Dream P5 (DREAM-192): the owner stops, pauses and resumes one worker, or pauses them all.

Every running sub-agent is registered by its run id with the scope that stops that run alone and the event its round
boundary waits on while paused (worker_control, reached through /api/control -> App._runtime_control). A stop cancels
only that call's scope: the lead gets an interrupted result for that call, every call in its history is paired, and
the turn goes on. A request cut off by a stop leaves its local lease uncertain until the engine's /health reports it
idle. A pause takes effect at the next round boundary: the worker finishes its current request, says so, and sends
nothing more until it is resumed. A run id that is not a live worker of this session is refused with the reason.
Scripted transports and a temporary lease root only: no engine, no model, no GPU.
"""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from dream.core.backends.openai_compat import _AGENT_ACTIVITY, OpenAICompatBackend
from dream.core.inference_coordination import EndpointCoordinator
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tui.app import App
from test_local_subagents import _PostResp, _researcher, _sub_final, _sub_toolcall, _tool
from test_nested_worker_queue import _hold
from test_parallel_subagents import _round, _task, _tool_msgs
from test_schema_deferral import _FakeClient, _text_round

STOPPED = "(subagent '{agent}' was stopped by the owner {where}. Do not run it again unless the owner asks.)"


def _rows(events):
    return [e.data for e in events if e.kind == "agent_activity"]


def _run(events, run_id):
    return [r for r in _rows(events) if r["run_id"] == run_id]


def _lanes(events):
    return [e.data for e in events if e.kind == "lanes"]


def _paired(b):
    ids = [tc["id"] for m in b.messages if m.get("role") == "assistant" and m.get("tool_calls") for tc in m["tool_calls"]]
    return sorted(m["tool_call_id"] for m in _tool_msgs(b)) == sorted(ids)


async def _until(check, tries=500):
    for _ in range(tries):
        value = check()
        if value:
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("the awaited state never came")


async def _drain(stream):
    return [event async for event in stream]


class _Engine:
    """The lead's streamed rounds, and each worker's plain replies by its prompt, one per request. A request listed in
    `gates` ((prompt, n) -> Event) waits for its event -- or for its worker's stop; the others answer after `hold`."""

    def __init__(self, rounds, replies, hold=0.02):
        self.lead, self.replies, self.hold = _FakeClient(rounds), replies, hold
        self.gates, self.posted, self.run_ids = {}, [], {}
        self.health_checks = 0

    def stream(self, method, url, json=None):
        return self.lead.stream(method, url, json=json)

    async def post(self, url, json=None):
        prompt = json["messages"][1]["content"]
        n = self.sent(prompt)
        self.run_ids[prompt] = _AGENT_ACTIVITY.get()["run_id"]
        self.posted.append((prompt, copy.deepcopy(json)))
        gate = self.gates.get((prompt, n))
        await (gate.wait() if gate is not None else asyncio.sleep(self.hold))
        return _PostResp(self.replies[prompt][n])

    async def get(self, url, timeout=None):
        assert url.endswith("/health"), url
        self.health_checks += 1
        return _PostResp({"status": "ok", "inflight": 0, "queued": 0})

    def sent(self, prompt):
        return sum(1 for p, _ in self.posted if p == prompt)


async def _look(args):
    return {"content": [{"type": "text", "text": "seen"}]}


def _lane_backend(slots, events, client):
    """A session whose engine serves `slots` lanes, with one kind of worker (one tool) and a view attached."""
    provider = SimpleNamespace(key="machx", label="MachX", base_url="https://api.example.com/v1", multimodal=False,
                               api_key=lambda: "n")
    b = OpenAICompatBackend(provider=provider, model="m", system_prompt="S", tools=[_tool("look", _look)],
                            permission_cb=None,
                            subagents={"worker": SimpleNamespace(description="does work", prompt="p",
                                                                 tool_names=["look"])})
    b._server_props = {"default_generation_settings": {"n_ctx": 32768}, "total_slots": slots}
    b._server_props_model = b.model
    b._background_emit = events.append
    b._client = client
    return b


def _researcher_backend(events, client, base_url="https://api.example.com/v1"):
    """A session with the researcher sub-agent (web_search), run directly as a lone worker."""
    provider = SimpleNamespace(key="machx", label="MachX", base_url=base_url, multimodal=False, api_key=lambda: "n")
    b = OpenAICompatBackend(provider=provider, model="m", system_prompt="s", tools=[_tool("web_search", _look)],
                            permission_cb=None, subagents=_researcher())
    b._background_emit = events.append
    b._client = client
    return b


# --- stop ---------------------------------------------------------------------------------------------------------

async def test_stopping_one_of_three_workers_interrupts_that_call_only():
    events = []
    client = _Engine([_round([_task("c1", "one"), _task("c2", "two"), _task("c3", "three")]),
                      _text_round("two of three reported")],
                     {"one": [_sub_final("did one")], "two": [_sub_final("never shown")], "three": [_sub_final("did three")]})
    client.gates[("two", 0)] = asyncio.Event()                  # two's request is in flight until it is stopped
    b = _lane_backend(3, events, client)
    turn = asyncio.ensure_future(_drain(b.ask("go")))
    two = await _until(lambda: client.run_ids.get("two"))
    assert b.worker_control("agent_stop", two) == {"run_id": two, "agent": "worker", "state": "stopping", "changed": True}
    assert b.worker_control("agent_stop", two)["changed"] is False, "a second stop changes nothing"
    lead = await asyncio.wait_for(turn, 5)
    mine = _run(events, two)
    assert [r["status"] for r in mine if r["kind"] == "status"] == ["running", "interrupted"]
    assert mine[-1]["text"] == "Stopped by you during request 1."
    others = [client.run_ids["one"], client.run_ids["three"]]
    assert all(_run(events, run)[-1]["status"] == "completed" for run in others)
    results = _tool_msgs(b)
    assert [m["tool_call_id"] for m in results] == ["c1", "c2", "c3"] and _paired(b)
    assert [results[0]["content"], results[2]["content"]] == ["did one", "did three"]
    assert results[1]["content"] == STOPPED.format(agent="worker", where="during its request 1; its work is incomplete")
    sent = client.lead.payloads[1]["messages"]
    assert [m["tool_call_id"] for m in sent if m["role"] == "tool"] == ["c1", "c2", "c3"], "the lead's next request pairs every call"
    assert lead[-1].kind == "result" and not lead[-1].data.get("is_error")
    assert _lanes(events)[-1] == {"served": 3, "busy": 0, "queued": 0}
    with pytest.raises(ValueError, match="No running worker has run id"):
        b.worker_control("agent_stop", two)


async def test_on_a_one_lane_engine_a_stopped_worker_ends_its_call_and_the_next_one_runs():
    """Task calls run one after another inside the lead's own turn: a stop ends only the running one's call."""
    events = []
    client = _Engine([_round([_task("c1", "one"), _task("c2", "two")]), _text_round("one was stopped")],
                     {"one": [_sub_final("never shown")], "two": [_sub_final("did two")]})
    client.gates[("one", 0)] = asyncio.Event()
    b = _lane_backend(1, events, client)
    turn = asyncio.ensure_future(_drain(b.ask("go")))
    one = await _until(lambda: client.run_ids.get("one"))
    assert b.lanes_status() == {"served": 1, "busy": 1, "queued": 0}
    assert b.worker_control("agent_stop", one)["state"] == "stopping"
    lead = await asyncio.wait_for(turn, 5)
    assert _run(events, one)[-1]["text"] == "Stopped by you during request 1."
    assert _run(events, client.run_ids["two"])[-1]["status"] == "completed"
    assert [m["content"] for m in _tool_msgs(b)] == [
        STOPPED.format(agent="worker", where="during its request 1; its work is incomplete"), "did two"]
    assert _paired(b) and lead[-1].kind == "result" and not lead[-1].data.get("is_error")
    assert _lanes(events)[-1] == {"served": 1, "busy": 0, "queued": 0} and b._workers == {}


async def test_a_worker_stopped_while_queued_sends_nothing_and_the_others_go_on():
    events = []
    names = ("one", "two", "three")
    client = _Engine([_round([_task(f"c{i}", n) for i, n in enumerate(names, 1)]), _text_round("done")],
                     {n: [_sub_final(f"did {n}")] for n in names})
    release = asyncio.Event()
    client.gates[("one", 0)] = client.gates[("two", 0)] = release
    b = _lane_backend(2, events, client)
    turn = asyncio.ensure_future(_drain(b.ask("go")))
    queued = await _until(lambda: next((r for r in _rows(events) if r.get("status") == "queued"), None))
    assert b.worker_control("agent_stop", queued["run_id"])["state"] == "stopping"
    await _until(lambda: _run(events, queued["run_id"])[-1].get("status") == "interrupted")
    release.set()
    await asyncio.wait_for(turn, 5)
    mine = _run(events, queued["run_id"])
    assert [r["status"] for r in mine] == ["queued", "interrupted"]
    assert mine[-1]["text"] == "Stopped while waiting for an engine lane; nothing was sent."
    assert queued["run_id"] not in client.run_ids.values(), "its request was never sent"
    results = _tool_msgs(b)
    assert [m["tool_call_id"] for m in results] == ["c1", "c2", "c3"] and _paired(b)
    assert [m["content"] for m in results] == ["did one", "did two", STOPPED.format(
        agent="worker", where="before it sent a request; nothing was done")]
    assert _lanes(events)[-1] == {"served": 2, "busy": 0, "queued": 0}


async def test_a_worker_stopped_mid_request_leaves_the_lease_uncertain_until_the_engine_reports_idle(tmp_path):
    events = []
    client = _Engine([], {"inspect": [_sub_final("never shown")], "again": [_sub_final("sent once the engine was idle")]})
    client.gates[("inspect", 0)] = asyncio.Event()             # the engine never answers the first request
    b = _researcher_backend(events, client, base_url="http://127.0.0.1:1/v1")
    b._coordination_override = EndpointCoordinator("loopback:1", root=tmp_path / "coord")   # never /tmp/dream-inference-<uid>
    first = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    run = await _until(lambda: client.run_ids.get("inspect"))
    assert b.coordination_status()["state"] == "running"
    assert b.worker_control("agent_stop", run)["state"] == "stopping"
    answer, failed = await asyncio.wait_for(first, 5)
    assert failed and answer == STOPPED.format(agent="researcher", where="during its request 1; its work is incomplete")
    assert b.coordination_status()["state"] == "uncertain", "the engine may still be computing the cut-off request"
    assert _run(events, run)[-1]["text"] == "Stopped by you during request 1."
    assert client.health_checks == 0
    assert await asyncio.wait_for(b._run_subagent("researcher", "again"), 5) == ("sent once the engine was idle", False)
    assert client.health_checks == 1, "the next request asked the engine before it was sent"
    assert b.coordination_status()["state"] == "idle"
    assert [p for p, _ in client.posted] == ["inspect", "again"]


async def test_a_worker_stopped_while_it_waits_for_the_local_engine_sent_nothing(tmp_path):
    # P5 gate known limit: the card and the lead used to say "during request 1" although nothing had been sent.
    events = []
    client = _Engine([], {"inspect": [_sub_final("never sent")]})
    b = _researcher_backend(events, client, base_url="http://127.0.0.1:1/v1")
    coordinator = b._coordination_override = EndpointCoordinator("loopback:1", root=tmp_path / "coord")
    free, holding = await _hold(coordinator)                  # another Dream request holds the one slot
    held = coordinator.records()
    assert held[0]["state"] == "running"
    try:
        run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
        waiting = await _until(lambda: next((r for r in _rows(events) if r.get("status") == "waiting"), None))
        assert b.worker_control("agent_pause", waiting["run_id"])["state"] == "pausing"
        assert _rows(events)[-1]["text"] == "Pauses after its current request."
        assert b.worker_control("agent_stop", waiting["run_id"])["state"] == "stopping"
        answer, failed = await asyncio.wait_for(run, 5)
        # gate be7: the holder's lease is untouched while the holder still holds it
        assert coordinator.records() == held and not holding.done()
    finally:
        free.set()
        await holding
    assert failed and answer == STOPPED.format(
        agent="researcher", where="while it waited for the local engine, before request 1 was sent; nothing was sent for it")
    assert _rows(events)[-1]["status"] == "interrupted" and _rows(events)[-1]["text"] == (
        "Stopped by you while it waited for the local engine before request 1; nothing was sent.")
    assert client.posted == [], "nothing was sent"
    assert coordinator.status()["state"] == "idle", "no request of its own was cut off"
    assert any(e.kind == "system" and e.data == "Stopped by you while waiting; this request was not sent." for e in events)


async def test_once_the_engine_is_free_a_stop_reads_during_the_request_again(tmp_path):
    # gate be7: after the wait ends and the lease is taken, the request is in flight: a stop cuts it off.
    events = []
    client = _Engine([], {"inspect": [_sub_final("never shown")]})
    client.gates[("inspect", 0)] = asyncio.Event()            # once sent, the request stays in flight
    b = _researcher_backend(events, client, base_url="http://127.0.0.1:1/v1")
    coordinator = b._coordination_override = EndpointCoordinator("loopback:1", root=tmp_path / "coord")
    free, holding = await _hold(coordinator)
    run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    try:
        waiting = await _until(lambda: next((r for r in _rows(events) if r.get("status") == "waiting"), None))
    finally:
        free.set()
        await holding
    await _until(lambda: client.run_ids.get("inspect"))       # the lease is taken and the request is out
    assert b.worker_control("agent_pause", waiting["run_id"])["state"] == "pausing"
    assert _rows(events)[-1]["text"] == "Pauses after its current request."
    assert b.worker_control("agent_stop", waiting["run_id"])["state"] == "stopping"
    answer, failed = await asyncio.wait_for(run, 5)
    assert failed and answer == STOPPED.format(agent="researcher", where="during its request 1; its work is incomplete")
    assert (_rows(events)[-1]["status"], _rows(events)[-1]["text"]) == ("interrupted", "Stopped by you during request 1.")
    assert len(client.posted) == 1
    assert coordinator.records()[0]["state"] == "uncertain", "a request cut off in flight fences its slot (P5)"


# --- pause and resume -----------------------------------------------------------------------------------------------

async def test_a_paused_worker_stops_at_its_round_boundary_and_resumes_in_order():
    events = []
    client = _Engine([], {"inspect": [_sub_toolcall("web_search", {"query": "source"}), _sub_final("the source holds")]})
    first = client.gates[("inspect", 0)] = asyncio.Event()
    b = _researcher_backend(events, client)
    app = SimpleNamespace(engine=SimpleNamespace(backend=b))
    run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    run_id = await _until(lambda: client.run_ids.get("inspect"))
    paused = await App._runtime_control(app, {"action": "agent_pause", "run_id": run_id})
    assert paused == {"run_id": run_id, "agent": "researcher", "state": "pausing", "changed": True}
    assert b.worker_control("agent_pause", run_id)["changed"] is False, "asking twice changes nothing"
    first.set()
    await _until(lambda: any(r.get("status") == "paused" for r in _rows(events)))
    await asyncio.sleep(0.05)
    assert client.sent("inspect") == 1, "nothing is sent while it is paused"
    assert b.worker_control("agent_pause", run_id) == {"run_id": run_id, "agent": "researcher", "state": "paused",
                                                       "changed": False}
    resumed = await App._runtime_control(app, {"action": "agent_resume", "run_id": run_id})
    assert resumed == {"run_id": run_id, "agent": "researcher", "state": "running", "changed": True}
    assert b.worker_control("agent_resume", run_id)["changed"] is False
    assert await asyncio.wait_for(run, 5) == ("the source holds", False)
    rows = _rows(events)
    assert [(r["kind"], r.get("status")) for r in rows] == [
        ("status", "running"), ("request", "awaiting_response"), ("status", "pausing"),
        ("response", "received"), ("tool_use", "requested"), ("tool_result", "returned"),
        ("status", "paused"), ("status", "running"), ("request", "awaiting_response"),
        ("response", "received"), ("status", "completed")]
    assert rows[2]["text"] == "Pauses after its current request."
    assert rows[6]["text"] == "Paused by you before request 2; it goes on when you resume it."
    assert rows[7]["text"] == "Resumed by you."
    second = client.posted[1][1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"], "it goes on from where it paused"


async def test_the_time_a_worker_is_paused_does_not_count_against_its_deadline():
    from dataclasses import replace
    from dream.core.profiles import PROFILES
    events = []
    client = _Engine([], {"inspect": [_sub_toolcall("web_search", {"query": "source"}), _sub_final("in time")]})
    b = _researcher_backend(events, client)
    b.profile = replace(PROFILES["lean"], subagent_timeout_s=1.0)
    run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    run_id = await _until(lambda: client.run_ids.get("inspect"))
    b.worker_control("agent_pause", run_id)
    await _until(lambda: any(r.get("status") == "paused" for r in _rows(events)))
    await asyncio.sleep(1.5)                                   # paused past its 1 s time limit
    b.worker_control("agent_resume", run_id)
    assert await asyncio.wait_for(run, 5) == ("in time", False)


async def test_stopping_the_turn_ends_paused_workers_too():
    events = []
    names = ("one", "two")
    client = _Engine([_round([_task(f"c{i}", n) for i, n in enumerate(names, 1)]), _text_round("never")],
                     {n: [_sub_toolcall("look", {}), _sub_final(f"did {n}")] for n in names})
    b = _lane_backend(2, events, client)
    turn = asyncio.ensure_future(_drain(b.ask("go")))
    await _until(lambda: len(client.run_ids) == 2)
    b.worker_control("agents_pause_all")
    await _until(lambda: sum(r.get("status") == "paused" for r in _rows(events)) == 2)
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(turn, 5)
    for run in client.run_ids.values():
        assert _run(events, run)[-1]["status"] == "interrupted"
        assert _run(events, run)[-1]["text"] == "Subagent observation was interrupted. In-flight effects may need checking."
    assert b._workers == {} and _paired(b)
    assert _lanes(events)[-1] == {"served": 2, "busy": 0, "queued": 0}


async def test_a_paused_worker_can_be_stopped():
    events = []
    client = _Engine([], {"inspect": [_sub_toolcall("web_search", {"query": "source"}), _sub_final("never asked")]})
    b = _researcher_backend(events, client)
    run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    run_id = await _until(lambda: client.run_ids.get("inspect"))
    b.worker_control("agent_pause", run_id)
    await _until(lambda: any(r.get("status") == "paused" for r in _rows(events)))
    assert b.worker_control("agent_stop", run_id) == {"run_id": run_id, "agent": "researcher", "state": "stopping",
                                                      "changed": True}
    answer, failed = await asyncio.wait_for(run, 5)
    assert failed and answer == STOPPED.format(agent="researcher", where="after its request 1; its work is incomplete")
    assert [r["status"] for r in _rows(events) if r["kind"] == "status"] == [
        "running", "pausing", "paused", "interrupted"]
    assert _rows(events)[-1]["text"] == "Stopped by you."
    assert client.sent("inspect") == 1


async def test_pause_all_holds_every_worker_at_its_boundary_and_the_leads_order_holds():
    events = []
    names = ("one", "two", "three")
    client = _Engine([_round([_task(f"c{i}", n) for i, n in enumerate(names, 1)]), _text_round("done")],
                     {n: [_sub_toolcall("look", {}), _sub_final(f"did {n}")] for n in names})
    release = asyncio.Event()
    for n in names:
        client.gates[(n, 0)] = release
    b = _lane_backend(3, events, client)
    turn = asyncio.ensure_future(_drain(b.ask("go")))
    await _until(lambda: len(client.run_ids) == 3)
    paused = await App._runtime_control(SimpleNamespace(engine=SimpleNamespace(backend=b)), {"action": "agents_pause_all"})
    assert sorted(r["run_id"] for r in paused["runs"]) == sorted(client.run_ids.values())
    assert all(r["state"] == "pausing" and r["changed"] and r["agent"] == "worker" for r in paused["runs"])
    assert all(r["changed"] is False for r in b.worker_control("agents_pause_all")["runs"])
    release.set()
    await _until(lambda: sum(r.get("status") == "paused" for r in _rows(events)) == 3)
    await asyncio.sleep(0.05)
    assert len(client.posted) == 3, "no worker sent its next request while paused"
    for n in reversed(names):                                  # resumed in the opposite order
        assert b.worker_control("agent_resume", client.run_ids[n])["state"] == "running"
    await asyncio.wait_for(turn, 5)
    assert [m["content"] for m in _tool_msgs(b)] == ["did one", "did two", "did three"], "the lead's order holds"
    assert all(_run(events, run)[-1]["status"] == "completed" for run in client.run_ids.values())
    assert _lanes(events)[-1] == {"served": 3, "busy": 0, "queued": 0}
    with pytest.raises(ValueError, match="No worker is running in this session; there is nothing to pause."):
        b.worker_control("agents_pause_all")


# --- refusals ---------------------------------------------------------------------------------------------------------

async def test_unknown_finished_and_malformed_run_ids_are_refused_with_the_reason():
    events = []
    client = _Engine([], {"inspect": [_sub_final("done")]})
    b = _researcher_backend(events, client)
    with pytest.raises(ValueError, match="No running worker has run id 'ffff"):
        b.worker_control("agent_stop", "f" * 32)
    for bad in (None, "", 7, "x" * 101):
        with pytest.raises(ValueError, match="run_id must be the run id"):
            b.worker_control("agent_pause", bad)
    with pytest.raises(ValueError, match="nothing to pause"):
        b.worker_control("agents_pause_all")
    with pytest.raises(ValueError, match="Unknown worker action"):
        b.worker_control("agent_restart", "f" * 32)
    assert await b._run_subagent("researcher", "inspect") == ("done", False)
    finished = _rows(events)[0]["run_id"]
    for action in ("agent_stop", "agent_pause", "agent_resume"):
        with pytest.raises(ValueError, match="it has finished, or it never ran here"):
            b.worker_control(action, finished)


def test_worker_controls_through_the_studio_route_answer_400_with_the_reason(tmp_path):
    app = App(provider="machx", workspace=tmp_path)
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        headers = {"x-dream-token": server.token}

        def control(payload):
            reply = client.post("/api/control", headers=headers, json=payload)
            return reply.status_code, reply.json()
        status, body = control({"action": "agent_stop", "run_id": "f" * 32})
        assert status == 400 and body["error"].startswith("Worker controls are unavailable")   # no backend yet
        app.engine = SimpleNamespace(backend=SimpleNamespace())                                  # a provider without workers
        assert control({"action": "agents_pause_all"})[1]["error"].startswith("Worker controls are unavailable")
        app.engine = SimpleNamespace(backend=_researcher_backend([], _Engine([], {})))
        status, body = control({"action": "agent_stop", "run_id": "f" * 32})
        assert status == 400 and body["error"].startswith("No running worker has run id 'ffff")
        status, body = control({"action": "agent_resume"})
        assert status == 400 and "run_id must be the run id" in body["error"]
        status, body = control({"action": "agents_pause_all"})
        assert status == 400 and body["error"] == "No worker is running in this session; there is nothing to pause."
