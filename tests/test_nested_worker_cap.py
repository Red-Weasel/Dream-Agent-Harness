"""Max agents (the owner's settings plan of 2026-10-01; it replaces DREAM-197's per-reply worker cap).

`nested.max_agents` (the global runtime settings file only; empty = no limit, the default) caps how many sub-agents RUN
AT THE SAME TIME, for every provider. Past it a worker waits in line -- `queued` on its card, `running` when a slot
frees -- and every task call runs: none is refused for count. On the local engine what runs at once is the smaller of
its lanes and max agents; on a provider whose calls go out together (OpenAI, xAI at a remote address) it is max agents,
and without it every call of a reply runs at once. The `task` tool says how many run at once and that the rest wait
their turn. A save in Studio or with /settings reaches the running session; the old nested.max_workers caps nothing.
Scripted transports and the isolated settings file only: no engine, no model, no GPU.
"""
import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream import management
from dream.core import settings
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import get_provider
from dream.tui.app import App
from test_local_subagents import _sub_final
from test_nested_worker_control import _Engine, _drain, _lane_backend, _lanes, _paired, _rows
from test_parallel_subagents import _round, _task, _tool_msgs
from test_schema_deferral import _text_round

APPLIES = ("applies to new sessions, and the running session from its next reply when set in Studio or with "
           "/settings (a Claude-led session when it starts)")


class _Counted(_Engine):
    """The fake engine, counting the workers' requests in flight: `peak` is how many ran at the same time, and
    `peaks[letter]` the same among the workers whose prompt starts with that letter."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.flying, self.peak, self.peaks = [], 0, {}

    async def post(self, url, json=None):
        prompt = json["messages"][1]["content"]
        self.flying.append(prompt)
        self.peak = max(self.peak, len(self.flying))
        same = sum(p[0] == prompt[0] for p in self.flying)
        self.peaks[prompt[0]] = max(self.peaks.get(prompt[0], 0), same)
        try:
            return await super().post(url, json=json)
        finally:
            self.flying.remove(prompt)


def _task_text(b):
    [schema] = [s for s in b.tool_schemas if s["function"]["name"] == "task"]
    return schema["function"]["description"]


def _save(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, **data}), encoding="utf-8")


def _burst(n, prefix="w"):
    names = [f"{prefix}{i:02d}" for i in range(1, n + 1)]
    return names, _Counted([_round([_task(f"c{i}", name) for i, name in enumerate(names, 1)]), _text_round("all done")],
                           {name: [_sub_final(f"did {name}")] for name in names})


def _cloud(events, client):
    """A provider whose task calls go out together: OpenAI at a remote address, no engine lanes."""
    b = _lane_backend(1, events, client)
    b.provider.key = "openai"
    return b


# --- the local engine: the smaller of its lanes and max agents run at once; the rest wait, none is refused ---------

async def test_sixteen_calls_at_max_agents_seven_on_two_lanes_all_run_two_at_a_time():
    _save({"nested": {"max_agents": 7}})
    events = []
    names, client = _burst(16)
    b = _lane_backend(2, events, client)
    b.apply_worker_cap()
    lead = await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert sorted(prompt for prompt, _ in client.posted) == names, "every task call ran"
    assert client.peak == 2, "two lanes: two at a time"
    results = _tool_msgs(b)
    assert [m["content"] for m in results] == [f"did {n}" for n in names] and _paired(b)
    assert not any("not run" in m["content"] for m in results)
    rows = _rows(events)
    assert len({r["run_id"] for r in rows}) == 16
    queued = [r for r in rows if r.get("status") == "queued"]
    assert len(queued) == 14 and all(r["text"].startswith("Waiting for an engine lane (2 of 2 busy)") for r in queued)
    assert sum(r.get("status") == "completed" for r in rows) == 16
    assert _lanes(events)[-1] == {"served": 2, "busy": 0, "queued": 0}
    assert lead[-1].kind == "result" and not lead[-1].data.get("is_error")


async def test_max_agents_below_the_lanes_binds_and_the_rest_wait_their_turn():
    _save({"nested": {"max_agents": 3}})
    events = []
    names, client = _burst(9)
    b = _lane_backend(5, events, client)
    b.apply_worker_cap()
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert sorted(prompt for prompt, _ in client.posted) == names and client.peak == 3
    queued = [r for r in _rows(events) if r.get("status") == "queued"]
    assert len(queued) == 6
    assert queued[0]["text"] == "Waiting its turn (3 of 3 running at once: your max agents)"
    assert {"served": 3, "busy": 3, "queued": 6} in _lanes(events)
    assert _paired(b)


async def test_no_limit_runs_as_many_as_the_engine_has_lanes():
    events = []
    names, client = _burst(10)
    b = _lane_backend(4, events, client)
    b.apply_worker_cap()
    assert b._agent_limit is None
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert sorted(prompt for prompt, _ in client.posted) == names and client.peak == 4
    assert b.lanes_status() == {"served": 4, "busy": 0, "queued": 0}


async def test_on_a_one_lane_engine_every_call_runs_one_at_a_time():
    _save({"nested": {"max_agents": 5}})
    names, client = _burst(6)
    b = _lane_backend(1, [], client)
    b.apply_worker_cap()
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert [prompt for prompt, _ in client.posted] == names and client.peak == 1
    assert [m["content"] for m in _tool_msgs(b)] == [f"did {n}" for n in names]


async def test_the_old_max_workers_caps_nothing():
    _save({"nested": {"max_workers": 3}})
    names, client = _burst(8)
    b = _lane_backend(8, [], client)
    b.apply_worker_cap()
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert sorted(prompt for prompt, _ in client.posted) == names and client.peak == 8


async def test_every_call_ran_so_none_counts_as_a_failed_tool(monkeypatch):
    """DREAM-125: once a turn has made a change, a request that follows a round with no failed tool runs with thinking
    off. Eight workers on two lanes all did their work: the next request runs without thinking."""
    monkeypatch.delenv("DREAM_BUILD_THINKING_CAP", raising=False)
    names, client = _burst(8)
    b = _lane_backend(2, [], client)
    b._local_capabilities = {"architecture": "mimo_v2", "load": ["thinking"]}     # a model DREAM-125 caps
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert [m["content"] for m in _tool_msgs(b)] == [f"did {n}" for n in names]
    assert [p.get("enable_thinking", "absent") for p in client.lead.payloads] == ["absent", False]


# --- a provider whose calls go out together ---------------------------------------------------------------------------

async def test_a_cloud_provider_runs_max_agents_at_a_time_and_queues_the_rest():
    _save({"nested": {"max_agents": 2}})
    events = []
    names, client = _burst(6)
    b = _cloud(events, client)
    b.apply_worker_cap()
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert sorted(prompt for prompt, _ in client.posted) == names and client.peak == 2
    queued = [r for r in _rows(events) if r.get("status") == "queued"]
    assert len(queued) == 4 and queued[0]["text"] == "Waiting its turn (2 of 2 running at once: your max agents)"
    assert [m["content"] for m in _tool_msgs(b)] == [f"did {n}" for n in names] and _paired(b)
    assert _lanes(events)[-1] == {"served": 2, "busy": 0, "queued": 0}


async def test_a_cloud_provider_without_a_limit_runs_every_call_of_a_reply_at_once():
    events = []
    names, client = _burst(12)
    b = _cloud(events, client)
    b.apply_worker_cap()
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert sorted(prompt for prompt, _ in client.posted) == names and client.peak == 12
    assert not any(r.get("status") == "queued" for r in _rows(events))
    assert b.lanes_status() == {"served": None, "busy": 0, "queued": 0}


async def test_a_limit_saved_mid_session_queues_the_next_reply_by_it():
    one, two = [f"a{i}" for i in range(1, 5)], [f"b{i}" for i in range(1, 7)]
    client = _Counted([_round([_task(f"a{i}", n) for i, n in enumerate(one, 1)]),
                       _round([_task(f"b{i}", n) for i, n in enumerate(two, 1)]), _text_round("done")],
                      {n: [_sub_final(f"did {n}")] for n in one + two})
    b = _lane_backend(4, [], client)
    b.apply_worker_cap()
    post = client.post

    async def saving_post(url, json=None):
        if json["messages"][1]["content"] == "a4":          # the first reply's last worker: the owner saves 2 now
            settings.set_value("nested.max_agents", "2")
            b.apply_worker_cap()
        return await post(url, json=json)
    client.post = saving_post
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    said = [[t["function"]["description"] for t in p["tools"] if t["function"]["name"] == "task"][0]
            for p in client.lead.payloads]
    assert "Up to 4 subagents run at once on this engine" in said[0]
    assert "Up to 2 subagents run at once on this engine" in said[1]
    assert client.peaks == {"a": 4, "b": 2} and len(client.posted) == 10 and _paired(b)


# --- what the task tool and the connect note say ------------------------------------------------------------------------

@pytest.mark.parametrize("limit, lanes, sentence", [
    (None, 1, "One subagent runs at a time on this engine; send as many task calls as the job needs: the rest wait "
              "their turn and start as others finish."),
    (7, 2, "Up to 2 subagents run at once on this engine; send as many task calls as the job needs"),
    (None, 16, "Up to 16 subagents run at once on this engine;"),
    (3, 16, "Up to 3 subagents run at once on this engine;"),
])
def test_the_task_tool_says_how_many_run_at_once_and_that_the_rest_wait(limit, lanes, sentence):
    if limit is not None:
        _save({"nested": {"max_agents": limit}})
    b = _lane_backend(lanes, [], None)
    b.apply_worker_cap()
    text = _task_text(b)
    assert sentence in text
    assert "per reply" not in text and "not run" not in text


@pytest.mark.parametrize("limit, sentence", [
    (None, "Every task call of a reply runs at the same time on this provider, so split a big job into as many "
           "independent subtasks as help."),
    (5, "Up to 5 subagents run at once on this provider; send as many task calls as the job needs: the rest wait "
        "their turn"),
    (1, "One subagent runs at a time on this provider;"),
])
def test_a_cloud_provider_says_its_max_agents(limit, sentence):
    if limit is not None:
        _save({"nested": {"max_agents": limit}})
    b = _cloud([], None)
    b.apply_worker_cap()
    assert sentence in _task_text(b)


@pytest.mark.parametrize("limit, says", [
    (None, "up to 4 sub-agents run at the same time; more wait their turn."),
    (2, "up to 2 sub-agents run at the same time (your max agents); more wait their turn."),
])
def test_the_connect_note_names_max_agents_when_it_binds(limit, says):
    if limit is not None:
        _save({"nested": {"max_agents": limit}})
    b = _lane_backend(4, [], None)
    b.apply_worker_cap()
    [note] = b._lanes_notes()
    assert note.startswith("Dream's own note: MachX serves 4 lanes for m") and says in note
    assert "nested.max_workers" not in note and "one step" not in note


def test_a_hand_edited_bad_limit_names_the_file_and_the_session_keeps_its_own():
    _save({"nested": {"max_agents": 0}})
    with pytest.raises(ValueError, match="nested.max_agents must be a whole number of 1 or more"):
        settings.max_agents()
    b = _lane_backend(2, [], None)
    b.apply_worker_cap()
    b._refresh_task_schema()                                   # as connect() does once the engine's lanes are read
    assert b._agent_limit is None and "Up to 2 subagents run at once on this engine" in _task_text(b)


# --- dream settings, /settings and Studio --------------------------------------------------------------------------------

def test_dream_settings_sets_and_unsets_it(capsys):
    assert management.main(["settings", "set", "nested.max_agents", "6"]) == 0
    assert json.loads(capsys.readouterr().out)["note"] == APPLIES
    assert read_settings()["nested"] == {"max_agents": 6}
    assert management.main(["settings", "get", "nested.max_agents"]) == 0
    assert json.loads(capsys.readouterr().out)["settings"] == {"nested.max_agents": {"value": 6, "source": "global"}}
    assert management.main(["settings", "unset", "nested.max_agents"]) == 0
    assert "nested" not in read_settings()


@pytest.mark.parametrize("section", [[7], "7"])
def test_the_cli_refuses_a_nested_section_that_is_not_an_object(section, capsys):
    """DREAM-209's gate: `dream settings set` on a hand-edited file whose nested section is a list or a string is an
    error line naming the file and the fix, exit 2, and the file is untouched."""
    _save({"nested": section})
    before = settings_path().read_bytes()
    assert management.main(["settings", "set", "nested.max_agents", "7"]) == 2
    said = capsys.readouterr().out
    assert said.startswith("Dream settings: Cannot use runtime settings") and settings_path().name in said
    assert "nested must be an object; make it one or remove it" in said and settings_path().read_bytes() == before


async def test_the_studio_save_reaches_the_running_session(tmp_path):
    b = _lane_backend(8, [], None)
    app = App(provider="machx", workspace=tmp_path)
    app.engine = SimpleNamespace(provider=SimpleNamespace(key="machx"), model="fixture-model", backend=b)
    result = await app._runtime_control({"action": "settings_save", "key": "nested.max_agents", "value": 5})
    assert read_settings()["nested"] == {"max_agents": 5}
    assert b._agent_limit == 5 and "Up to 5 subagents run at once" in _task_text(b)
    assert result["applies"].startswith("Saved. Now: from the main agent's next reply")
    await app._runtime_control({"action": "settings_save", "key": "nested.max_agents", "value": ""})
    assert "nested" not in read_settings()
    assert b._agent_limit is None and "Up to 8 subagents run at once" in _task_text(b)


async def test_slash_settings_reaches_the_running_session():
    b = _lane_backend(8, [], None)
    app = App(provider="machx", model="fixture")
    app.engine = SimpleNamespace(store=None, backend=b, provider=get_provider("machx"), model="fixture")
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    await app._command("/settings set nested.max_agents 3")
    assert b._agent_limit == 3 and "Up to 3 subagents run at once" in _task_text(b)
    said = " ".join(app.renderer.console.file.getvalue().split())       # the console wraps long lines
    assert f"nested.max_agents: {APPLIES}" in said
    await app._command("/settings unset nested.max_agents")
    assert b._agent_limit is None and "Up to 8 subagents run at once" in _task_text(b)
