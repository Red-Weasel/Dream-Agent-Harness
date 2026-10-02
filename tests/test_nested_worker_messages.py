"""Nested Dream P6 (DREAM-193): the owner writes to one worker while it works.

`agent_message` {run_id, text} (/api/control -> App._runtime_control -> the backend's worker registry) puts the text in
that run's inbox. Before the worker's next request -- at the top of its next round, after a pause has ended -- each
waiting message joins that worker's own history as a user message, in the order sent, with a receipt row
(`message_received`, quoting a preview). A message the run ends before it could take is reported (`message_undelivered`)
before the card's last row. Never another run's history, never the lead's. Refused with the reason: a run that has
ended, a stopping worker, an empty or overlong text (the limit is in the refusal), a check that runs inside the lead's
own conversation. Scripted transports only: no engine, no model, no GPU.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tui.app import App
from test_local_subagents import _sub_final, _sub_toolcall
from test_nested_worker_control import _Engine, _drain, _lane_backend, _researcher_backend, _rows, _run, _until
from test_parallel_subagents import _round, _task
from test_schema_deferral import _text_round

FRAME = "[A message from the owner, sent while you work on this task.]\n\n"


def _statuses(events, run_id=None):
    return [r["status"] for r in _rows(events) if r["kind"] == "status" and run_id in (None, r["run_id"])]


async def test_a_message_reaches_only_that_workers_next_request():
    events = []
    names = ("one", "two")
    client = _Engine([_round([_task("c1", "one"), _task("c2", "two")]), _text_round("both reported")],
                     {n: [_sub_toolcall("look", {}), _sub_final(f"did {n}")] for n in names})
    release = asyncio.Event()
    client.gates[("one", 0)] = client.gates[("two", 0)] = release
    b = _lane_backend(2, events, client)
    turn = asyncio.ensure_future(_drain(b.ask("go")))
    await _until(lambda: len(client.run_ids) == 2)
    one = client.run_ids["one"]
    reply = await App._runtime_control(SimpleNamespace(engine=SimpleNamespace(backend=b)),
                                       {"action": "agent_message", "run_id": one, "text": "Check the 2024 figures too."})
    assert reply == {"run_id": one, "agent": "worker", "state": "running", "pending": 1}
    release.set()
    await asyncio.wait_for(turn, 5)
    sent_one = [payload for prompt, payload in client.posted if prompt == "one"]
    sent_two = [payload for prompt, payload in client.posted if prompt == "two"]
    assert "2024 figures" not in json.dumps(sent_one[0]), "the request in flight was sent before the message"
    assert [m["role"] for m in sent_one[1]["messages"]] == ["system", "user", "assistant", "tool", "user"]
    assert sent_one[1]["messages"][-1] == {"role": "user", "content": FRAME + "Check the 2024 figures too."}
    assert not any("2024 figures" in json.dumps(payload) for payload in sent_two), "never another run's"
    assert "2024 figures" not in json.dumps(b.messages) + json.dumps(client.lead.payloads), "never the lead's"
    receipts = [r for r in _rows(events) if r.get("status") == "message_received"]
    assert [(r["run_id"], r["text"]) for r in receipts] == [(one, 'Received your message: "Check the 2024 figures too."')]
    mine = [(r["kind"], r.get("status")) for r in _run(events, one)]
    assert mine.index(("status", "message_received")) == mine.index(("request", "awaiting_response"), 2) - 1, \
        "the receipt comes right before the request that carries it"


async def test_a_paused_worker_gets_its_messages_when_resumed_in_the_order_sent():
    events = []
    client = _Engine([], {"inspect": [_sub_toolcall("web_search", {"query": "source"}), _sub_final("done")]})
    first = client.gates[("inspect", 0)] = asyncio.Event()
    b = _researcher_backend(events, client)
    run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    run_id = await _until(lambda: client.run_ids.get("inspect"))
    b.worker_control("agent_pause", run_id)
    assert b.worker_control("agent_message", run_id, "First note.")["pending"] == 1
    first.set()
    await _until(lambda: "paused" in _statuses(events))
    assert b.worker_control("agent_message", run_id, "Second note.") == {
        "run_id": run_id, "agent": "researcher", "state": "paused", "pending": 2}
    await asyncio.sleep(0.05)
    assert client.sent("inspect") == 1 and "message_received" not in _statuses(events), "nothing while paused"
    b.worker_control("agent_resume", run_id)
    assert await asyncio.wait_for(run, 5) == ("done", False)
    second = client.posted[1][1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool", "user", "user"]
    assert [m["content"] for m in second[-2:]] == [FRAME + "First note.", FRAME + "Second note."]
    rows = [(r["kind"], r.get("status"), r.get("text")) for r in _rows(events)]
    tail = rows[[r[1] for r in rows].index("paused"):]
    assert [(kind, status) for kind, status, _ in tail] == [
        ("status", "paused"), ("status", "running"), ("status", "message_received"), ("status", "message_received"),
        ("request", "awaiting_response"), ("response", "received"), ("status", "completed")]
    assert [text for _, status, text in tail if status == "message_received"] == [
        'Received your message: "First note."', 'Received your message: "Second note."']


async def test_a_message_the_run_ends_before_is_reported_undelivered():
    events = []
    client = _Engine([], {"inspect": [_sub_final("the answer")]})
    first = client.gates[("inspect", 0)] = asyncio.Event()
    b = _researcher_backend(events, client)
    run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    run_id = await _until(lambda: client.run_ids.get("inspect"))
    long = "Also compare it with the second source, " * 5
    assert b.worker_control("agent_message", run_id, long)["pending"] == 1
    first.set()
    assert await asyncio.wait_for(run, 5) == ("the answer", False), "the lead's result is unchanged"
    assert _statuses(events) == ["running", "message_undelivered", "completed"]
    undelivered = next(r for r in _rows(events) if r.get("status") == "message_undelivered")
    preview = " ".join(long.split())[:80] + "…"
    assert undelivered["text"] == f'Not delivered: it ended before its next request ("{preview}").'
    assert client.sent("inspect") == 1


async def test_a_message_after_the_run_ends_is_refused_with_the_reason(tmp_path):
    events = []
    client = _Engine([], {"inspect": [_sub_final("done")]})
    b = _researcher_backend(events, client)
    assert await b._run_subagent("researcher", "inspect") == ("done", False)
    finished = _rows(events)[0]["run_id"]
    with pytest.raises(ValueError, match="No running worker has run id .* it has finished, or it never ran here"):
        b.worker_control("agent_message", finished, "Too late.")
    app = App(provider="machx", workspace=tmp_path)
    app.engine = SimpleNamespace(backend=b)
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    with TestClient(server.app, base_url="http://127.0.0.1") as http:
        reply = http.post("/api/control", headers={"x-dream-token": server.token},
                          json={"action": "agent_message", "run_id": finished, "text": "Too late."})
    assert reply.status_code == 400 and reply.json()["error"].startswith(f"No running worker has run id '{finished}'")


async def test_the_text_must_be_a_message_of_at_most_4000_characters():
    events = []
    client = _Engine([], {"inspect": [_sub_final("done")]})
    gate = client.gates[("inspect", 0)] = asyncio.Event()
    b = _researcher_backend(events, client)
    run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    run_id = await _until(lambda: client.run_ids.get("inspect"))
    with pytest.raises(ValueError) as refused:
        b.worker_control("agent_message", run_id, "x" * 4001)
    assert str(refused.value) == "The message is 4,001 characters; a message to a worker can be at most 4,000."
    for empty in ("", "   \n", None, 7):
        with pytest.raises(ValueError, match="Write a message: text must be a non-empty string."):
            b.worker_control("agent_message", run_id, empty)
    assert b.worker_control("agent_message", run_id, "y" * 4000)["pending"] == 1, "the limit itself is accepted"
    gate.set()
    await asyncio.wait_for(run, 5)
    assert "message_received" not in _statuses(events)


async def test_a_stopping_worker_and_a_check_inside_the_leads_conversation_refuse_messages():
    events = []
    client = _Engine([], {"inspect": [_sub_final("never shown")]})
    client.gates[("inspect", 0)] = asyncio.Event()
    b = _researcher_backend(events, client)
    run = asyncio.ensure_future(b._run_subagent("researcher", "inspect"))
    run_id = await _until(lambda: client.run_ids.get("inspect"))
    b.worker_control("agent_stop", run_id)
    with pytest.raises(ValueError) as refused:
        b.worker_control("agent_message", run_id, "Wait.")
    assert str(refused.value) == "Worker 'researcher' is stopping; the message was not delivered."
    await asyncio.wait_for(run, 5)

    events = []
    history = "the lead's own history"         # a check inside the lead's conversation sends the lead's messages
    client = _Engine([], {history: [_sub_final("PASS")]})
    gate = client.gates[(history, 0)] = asyncio.Event()
    lead = _researcher_backend(events, client)
    lead.messages.append({"role": "user", "content": history})
    check = asyncio.ensure_future(lead._run_subagent("researcher", "check", continuation=True))
    run_id = await _until(lambda: next(iter(client.run_ids.values()), None))
    with pytest.raises(ValueError) as refused:
        lead.worker_control("agent_message", run_id, "Look again.")
    assert str(refused.value) == ("Worker 'researcher' runs inside the lead's own conversation, so a message to it "
                                  "would enter the lead's history; steer the lead instead.")
    gate.set()
    await asyncio.wait_for(check, 5)
    assert "Look again." not in json.dumps(lead.messages)
