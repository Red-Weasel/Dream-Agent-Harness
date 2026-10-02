"""Nested Dream P7 (DREAM-194), the backend of the Needs-you stack: a permission request made inside a worker's tool call
names that worker.

StudioServer.request_permission reads the running worker through openai_compat.active_worker() -- the run's activity
context, the one every worker row carries -- and adds `run_id` and `agent` to the `permission` event; the lead's own
requests carry neither key, exactly as before. `/api/permission` answers as before and `permission_done` {id} clears it.
Scripted transports and an in-process ASGI client only: no engine, no model, no GPU.
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import _AGENT_ACTIVITY, OpenAICompatBackend
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_local_subagents import _sub_final, _sub_toolcall, _tool
from test_nested_worker_control import _Engine, _drain, _rows, _until
from test_parallel_subagents import _round, _tool_msgs
from test_schema_deferral import _text_round

CHOICES = {"y": "Allow once", "n": "Deny"}


async def _next(sub, kind):
    while True:
        event = await sub.get()
        if event.kind == kind:
            return event


async def _answer(srv, identifier, choice):
    """The Needs-you stack's answer: POST /api/permission, in this event loop."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1") as http:
        return await http.post("/api/permission", headers={"X-Dream-Token": srv.token},
                               json={"id": identifier, "choice": choice})


def _builders(srv, events, client, slots=1):
    """Workers whose one tool, run_bash, asks the owner through the Studio exactly as the app does."""
    ran = []

    async def run_bash(args):
        ran.append(args["command"])
        return {"content": [{"type": "text", "text": "/workspace"}]}

    async def permission(name, args):
        return await srv.request_permission(name, args, "Run a command", CHOICES) == "y"
    provider = SimpleNamespace(key="machx", label="MachX", base_url="https://api.example.com/v1", multimodal=False,
                               api_key=lambda: "n")
    b = OpenAICompatBackend(provider=provider, model="m", system_prompt="S", tools=[_tool("run_bash", run_bash)],
                            permission_cb=permission,
                            subagents={"builder": SimpleNamespace(description="builds", prompt="You build.",
                                                                  tool_names=["run_bash"])})
    b._server_props = {"default_generation_settings": {"n_ctx": 32768}, "total_slots": slots}
    b._server_props_model = b.model
    b._background_emit = events.append
    b._client = client
    return b, ran


async def test_a_workers_run_bash_approval_names_the_worker():
    srv, events = StudioServer(EventBus()), []
    client = _Engine([], {"build": [_sub_toolcall("run_bash", {"command": "pwd"}), _sub_final("built")]})
    b, ran = _builders(srv, events, client)
    with srv.bus.subscribe() as sub:
        run = asyncio.ensure_future(b._run_subagent("builder", "build"))
        asked = await asyncio.wait_for(_next(sub, "permission"), 5)
        run_id = _rows(events)[0]["run_id"]
        assert asked.data == {"id": asked.data["id"], "tool": "run_bash", "input": {"command": "pwd"},
                              "reason": "Run a command", "choices": CHOICES, "run_id": run_id, "agent": "builder"}
        assert ran == [], "nothing runs before the owner answers"
        assert (await _answer(srv, asked.data["id"], "y")).status_code == 200
        assert await asyncio.wait_for(run, 5) == ("built", False)
        assert (await asyncio.wait_for(_next(sub, "permission_done"), 5)).data == {"id": asked.data["id"]}
    assert ran == ["pwd"]


async def test_two_workers_asking_at_once_each_name_their_own_run():
    srv, events = StudioServer(EventBus()), []
    client = _Engine([_round([("c1", "task", {"subagent_type": "builder", "prompt": "one"}),
                              ("c2", "task", {"subagent_type": "builder", "prompt": "two"})]), _text_round("done")],
                     {n: [_sub_toolcall("run_bash", {"command": f"make {n}"}), _sub_final(f"built {n}")]
                      for n in ("one", "two")})
    b, ran = _builders(srv, events, client, slots=2)
    with srv.bus.subscribe() as sub:
        turn = asyncio.ensure_future(_drain(b.ask("go")))
        asked = [await asyncio.wait_for(_next(sub, "permission"), 5) for _ in range(2)]
        by_command = {event.data["input"]["command"]: event.data for event in asked}
        assert {by_command["make one"]["run_id"], by_command["make two"]["run_id"]} == set(client.run_ids.values())
        assert by_command["make one"]["run_id"] == client.run_ids["one"]
        assert by_command["make two"]["run_id"] == client.run_ids["two"]
        assert {event.data["agent"] for event in asked} == {"builder"}
        assert (await _answer(srv, by_command["make two"]["id"], "n")).status_code == 200
        assert (await _answer(srv, by_command["make one"]["id"], "y")).status_code == 200
        await asyncio.wait_for(turn, 5)
    assert ran == ["make one"], "only the approved command ran"
    assert [m["content"] for m in _tool_msgs(b)] == ["built one", "built two"]


async def test_the_leads_own_approval_carries_no_worker():
    srv = StudioServer(EventBus())
    with srv.bus.subscribe() as sub:
        pending = asyncio.ensure_future(srv.request_permission("write_file", {"path": "a.txt"}, "Allow file edit",
                                                               CHOICES))
        asked = await asyncio.wait_for(_next(sub, "permission"), 5)
        assert set(asked.data) == {"id", "tool", "input", "reason", "choices"}
        assert (await _answer(srv, asked.data["id"], "n")).status_code == 200
        assert await asyncio.wait_for(pending, 5) == "n"


def test_a_reconnecting_view_gets_the_waiting_worker_request_with_its_worker():
    from starlette.testclient import TestClient
    srv = StudioServer(EventBus())

    async def worker_asks():                       # a worker's tool call, in the worker's own context
        token = _AGENT_ACTIVITY.set({"run_id": "r7", "agent": "builder", "phase": "subagent", "round": 1})
        try:
            return await srv.request_permission("run_bash", {"command": "ls"}, "Run a command", CHOICES)
        finally:
            _AGENT_ACTIVITY.reset(token)

    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        pending = None
        try:
            with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}") as ws:
                assert ws.receive_json()["kind"] == "hello"
                pending = client.portal.start_task_soon(worker_asks)
                live = ws.receive_json()
            assert live["kind"] == "permission" and live["data"]["run_id"] == "r7" and live["data"]["agent"] == "builder"
            with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}&history=1") as ws:
                assert ws.receive_json()["kind"] == "hello"
                assert ws.receive_json()["kind"] == "history"
                assert ws.receive_json() == live, "the waiting request is replayed as it was published"
                reply = client.post("/api/permission", headers={"X-Dream-Token": srv.token},
                                    json={"id": live["data"]["id"], "choice": "y"})
                assert reply.status_code == 200 and pending.result(timeout=2) == "y"
                assert ws.receive_json() == {"kind": "permission_done", "data": {"id": live["data"]["id"]}}
        finally:
            if pending is not None and not pending.done():
                pending.cancel()          # a failed assertion must not leave the portal waiting on the request


def test_active_worker_reads_the_running_workers_context():
    assert openai_compat.active_worker() is None, "the lead's own work names no worker"
    token = _AGENT_ACTIVITY.set({"run_id": "r1", "agent": "builder", "phase": "subagent", "round": 2, "model": "m",
                                 "lane": True, "ended": False})
    try:
        assert openai_compat.active_worker() == {"run_id": "r1", "agent": "builder"}
    finally:
        _AGENT_ACTIVITY.reset(token)


# --- gate be7 follow-ups -----------------------------------------------------------------------------------------------

BASE = {"id", "tool", "input", "reason", "choices"}


@pytest.mark.parametrize("names", [("one",), ("one", "two")])
async def test_after_its_workers_end_the_leads_own_request_names_no_worker(names):
    # A worker on one lane runs inside the lead's own task: its context must end with it (_run_subagent resets it),
    # or the lead's next permission request would carry that worker's run_id.
    srv, events = StudioServer(EventBus()), []
    client = _Engine([_round([(f"c{i}", "task", {"subagent_type": "builder", "prompt": n}) for i, n in enumerate(names, 1)]),
                      _round([("c9", "run_bash", {"command": "lead cmd"})]), _text_round("done")],
                     {n: [_sub_toolcall("run_bash", {"command": f"make {n}"}), _sub_final(f"built {n}")] for n in names})
    b, ran = _builders(srv, events, client, slots=1)
    assert not b.concurrent_tasks(), "one lane: the task calls run one after another in the lead's task"
    with srv.bus.subscribe() as sub:
        turn = asyncio.ensure_future(_drain(b.ask("go")))
        for name in names:
            asked = (await asyncio.wait_for(_next(sub, "permission"), 5)).data
            assert set(asked) == BASE | {"run_id", "agent"} and asked["run_id"] == client.run_ids[name]
            assert (await _answer(srv, asked["id"], "y")).status_code == 200
        lead = (await asyncio.wait_for(_next(sub, "permission"), 5)).data
        assert lead["input"] == {"command": "lead cmd"} and set(lead) == BASE, lead
        assert (await _answer(srv, lead["id"], "y")).status_code == 200
        await asyncio.wait_for(turn, 5)
    assert ran == [f"make {n}" for n in names] + ["lead cmd"]
    assert openai_compat.active_worker() is None


def test_the_payloads_agent_is_bounded_as_the_workers_rows_are():
    from dream.agent_activity import bounded_agent_activity
    long = "a-sub-agent-with-a-very-long-name-" * 10                    # 340 characters
    context = {"run_id": "r2", "agent": long, "phase": "subagent", "round": 1}
    token = _AGENT_ACTIVITY.set(context)
    try:
        named = openai_compat.active_worker()
    finally:
        _AGENT_ACTIVITY.reset(token)
    row = bounded_agent_activity({**context, "kind": "status", "status": "running", "text": "Subagent started."})
    assert named["agent"] == row["agent"] and len(named["agent"]) < len(long), "the payload names it as its card does"
