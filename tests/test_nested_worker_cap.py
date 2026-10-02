"""Nested Dream P10 (DREAM-197): the owner's cap on how many workers one reply of the lead may start.

`nested.max_workers` (the global runtime settings file only) is 3, 7, 11 or 15 -- the Nested view's 4 / 8 / 12 / 16
control counts the orchestrator -- and 7 when unset. The `task` tool says the cap and how many workers run at once here
(the lanes; the rest queue). A reply's task calls past the cap are not run: each is answered, in its place, with a
refusal the lead can act on, so its history stays paired; the calls within the cap run or queue as before. The Studio
Settings control (`settings_save`) and /settings write it, and the running session takes it from its next reply: a
reply is judged by the cap its request's `task` tool said.
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
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import get_provider
from dream.tui.app import App
from test_local_subagents import _sub_final, _tool
from test_nested_worker_control import _Engine, _drain, _lane_backend, _lanes, _look, _paired, _rows
from test_parallel_subagents import _round, _task, _tool_msgs
from test_schema_deferral import _sse, _text_round

PROJECT_REFUSAL = ("nested.max_workers: the worker limit is set on this computer only (the global scope); a project "
                   "file may not set it.")
# When a saved cap applies (DREAM-209): the cap from the main agent's next reply, the local engine's queue from the
# engine's next start. /settings and `dream settings` print the first; the Settings tab shows the second.
APPLIES = ("applies to new sessions, and the running session from its next reply when set in Studio or with "
           "/settings; the local engine's queue (--max-queue) from the engine's next start, and a running engine "
           "keeps its queue")
APPLIES_STUDIO = ("Now: from the main agent's next reply, and in new sessions. The local engine's queue (--max-queue): "
                  "from the engine's next start; a running engine keeps its queue.")


def _refusal(cap, n, total):
    return (f"[Dream] This task call was not run: one reply can start at most {cap} subagents (the owner's worker "
            f"limit, nested.max_workers), and this was task call {n} of {total}. Send it again in a later reply, "
            "once these have finished.")


def _task_text(b):
    [schema] = [s for s in b.tool_schemas if s["function"]["name"] == "task"]
    return schema["function"]["description"]


def _save(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, **data}), encoding="utf-8")


def _raw_round(calls):
    """One round whose tool calls carry their arguments as sent, parseable or not: [(id, name, raw), ...]."""
    deltas = [{"index": i, "id": cid, "function": {"name": name, "arguments": raw}}
              for i, (cid, name, raw) in enumerate(calls)]
    return [_sse({"choices": [{"delta": {"tool_calls": deltas}, "finish_reason": None}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}), "data: [DONE]"]


# --- the cap on one reply ---------------------------------------------------------------------------------------------

async def test_sixteen_task_calls_at_cap_seven_run_seven_and_refuse_nine_each_paired():
    events = []
    names = [f"w{i:02d}" for i in range(1, 17)]
    client = _Engine([_round([_task(f"c{i}", n) for i, n in enumerate(names, 1)]), _text_round("seven reported")],
                     {n: [_sub_final(f"did {n}")] for n in names})
    b = _lane_backend(2, events, client)
    lead = await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert sorted(prompt for prompt, _ in client.posted) == names[:7], "the first seven run, and only they"
    results = _tool_msgs(b)
    assert [m["tool_call_id"] for m in results] == [f"c{i}" for i in range(1, 17)] and _paired(b)
    assert [m["content"] for m in results[:7]] == [f"did {n}" for n in names[:7]]
    assert [m["content"] for m in results[7:]] == [_refusal(7, n, 16) for n in range(8, 17)]
    sent = client.lead.payloads[1]["messages"]
    assert [m["tool_call_id"] for m in sent if m["role"] == "tool"] == [f"c{i}" for i in range(1, 17)]
    rows = _rows(events)
    assert len({r["run_id"] for r in rows}) == 7, "a refused call starts no worker card"
    assert sum(r.get("status") == "queued" for r in rows) == 5, "seven on two lanes: two run, five queue"
    assert sum(r.get("status") == "completed" for r in rows) == 7
    assert _lanes(events)[-1] == {"served": 2, "busy": 0, "queued": 0}
    assert lead[-1].kind == "result" and not lead[-1].data.get("is_error")


async def test_the_saved_cap_binds_and_only_task_calls_count():
    _save({"nested": {"max_workers": 3}})
    events = []
    client = _Engine([_round([_task("c1", "a"), _task("c2", "b"), _task("c3", "c"), ("c4", "look", {}),
                              _task("c5", "d"), _task("c6", "e")]), _text_round("done")],
                     {n: [_sub_final(f"did {n}")] for n in "abcde"})
    b = _lane_backend(3, events, client)
    b.apply_worker_cap()
    assert "Up to 3 subagents per reply" in _task_text(b)
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert sorted(prompt for prompt, _ in client.posted) == ["a", "b", "c"]
    assert [m["content"] for m in _tool_msgs(b)] == ["did a", "did b", "did c", "seen", _refusal(3, 4, 5),
                                                     _refusal(3, 5, 5)]
    assert _paired(b)


async def test_task_calls_in_a_scope_without_task_are_not_counted():
    _save({"nested": {"max_workers": 3}})
    events = []
    client = _Engine([_round([_task(f"c{i}", f"p{i}") for i in range(1, 11)]), _text_round("ok")], {})
    b = _lane_backend(2, events, client)
    b.apply_worker_cap()
    b.tool_scope = frozenset({"look"})                    # a scoped ask that does not offer `task` (DREAM-118)
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    results = _tool_msgs(b)
    assert len(results) == 10 and not any("This task call was not run" in m["content"] for m in results)
    assert not client.posted and not _rows(events) and _paired(b)


async def test_task_calls_without_sub_agents_are_not_counted():
    _save({"nested": {"max_workers": 3}})
    events = []
    client = _Engine([_round([_task(f"c{i}", f"p{i}") for i in range(1, 11)]), _text_round("ok")], {})
    provider = SimpleNamespace(key="machx", label="MachX", base_url="https://api.example.com/v1", multimodal=False,
                               api_key=lambda: "n")
    b = OpenAICompatBackend(provider=provider, model="m", system_prompt="S", tools=[_tool("look", _look)],
                            permission_cb=None, subagents=None)
    b._server_props = {"default_generation_settings": {"n_ctx": 32768}, "total_slots": 2}
    b._server_props_model = b.model
    b._background_emit = events.append
    b._client = client
    b.apply_worker_cap()
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    results = _tool_msgs(b)
    assert len(results) == 10 and not any("This task call was not run" in m["content"] for m in results)
    assert not client.posted and not _rows(events) and _paired(b)


async def test_task_calls_whose_arguments_do_not_parse_are_not_counted():
    _save({"nested": {"max_workers": 3}})
    good = lambda prompt: json.dumps({"subagent_type": "worker", "prompt": prompt})  # noqa: E731
    cut = '{"subagent_type": "worker", "prompt": '
    client = _Engine([_raw_round([("c1", "task", cut), ("c2", "task", good("a")), ("c3", "task", cut),
                                  ("c4", "task", good("b")), ("c5", "task", good("c"))]), _text_round("done")],
                     {n: [_sub_final(f"did {n}")] for n in "abc"})
    b = _lane_backend(3, [], client)
    b.apply_worker_cap()
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert sorted(prompt for prompt, _ in client.posted) == ["a", "b", "c"], "the three that parse are the cap's three"
    results = _tool_msgs(b)
    assert [m["content"] for m in results if m["tool_call_id"] in ("c2", "c4", "c5")] == ["did a", "did b", "did c"]
    assert not any("This task call was not run" in m["content"] for m in results) and _paired(b)


async def test_a_call_past_the_cap_is_not_a_failed_tool(monkeypatch):
    """DREAM-125: once a turn has made a change, a request that follows a round with no failed tool runs with thinking
    off. Seven workers did their work and the eighth call was refused, not failed: the next request runs without it."""
    monkeypatch.delenv("DREAM_BUILD_THINKING_CAP", raising=False)
    names = [f"w{i}" for i in range(1, 9)]
    client = _Engine([_round([_task(f"c{i}", n) for i, n in enumerate(names, 1)]), _text_round("done")],
                     {n: [_sub_final(f"did {n}")] for n in names})
    b = _lane_backend(2, [], client)
    b._local_capabilities = {"architecture": "mimo_v2", "load": ["thinking"]}     # a model DREAM-125 caps
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    assert [m["content"] for m in _tool_msgs(b)][7:] == [_refusal(7, 8, 8)]
    assert [p.get("enable_thinking", "absent") for p in client.lead.payloads] == ["absent", False]


async def test_a_save_while_the_reply_streams_applies_from_the_next_reply():
    one = [f"a{i:02d}" for i in range(1, 11)]
    two = [f"b{i}" for i in range(1, 6)]
    client = _Engine([_round([_task(f"a{i}", n) for i, n in enumerate(one, 1)]),
                      _round([_task(f"b{i}", n) for i, n in enumerate(two, 1)]), _text_round("done")],
                     {n: [_sub_final(f"did {n}")] for n in one + two})
    b = _lane_backend(2, [], client)
    stream = client.lead.stream

    def saving_stream(method, url, json=None):
        reply = stream(method, url, json=json)
        if len(client.lead.payloads) == 1:          # the first request left saying "Up to 7"; the owner saves 3 now
            settings.set_value("nested.max_workers", "3")
            b.apply_worker_cap()
        return reply
    client.lead.stream = saving_stream
    await asyncio.wait_for(_drain(b.ask("go")), 10)
    said = [[t["function"]["description"] for t in p["tools"] if t["function"]["name"] == "task"][0]
            for p in client.lead.payloads]
    assert "Up to 7 subagents per reply" in said[0] and "Up to 3 subagents per reply" in said[1]
    results = [m["content"] for m in _tool_msgs(b)]
    assert results[:10] == [f"did {n}" for n in one[:7]] + [_refusal(7, n, 10) for n in (8, 9, 10)]
    assert results[10:] == [f"did {n}" for n in two[:3]] + [_refusal(3, 4, 5), _refusal(3, 5, 5)]
    assert _paired(b)


@pytest.mark.parametrize("cap, lanes, sentence", [
    (7, 1, "Up to 7 subagents per reply; one runs at a time on this engine, the rest queue."),
    (7, 2, "Up to 7 subagents per reply; 2 run at once on this engine, the rest queue."),
    (15, 16, "Up to 15 subagents per reply, all running at once on this engine."),
    (3, 3, "Up to 3 subagents per reply, all running at once on this engine."),
])
def test_the_task_tool_says_the_cap_and_how_many_run_at_once(cap, lanes, sentence):
    _save({"nested": {"max_workers": cap}})
    b = _lane_backend(lanes, [], None)
    b.apply_worker_cap()
    text = _task_text(b)
    assert sentence in text
    assert "Subagents run one at a time (not in parallel)" not in text and "can run at the same time" not in text


@pytest.mark.parametrize("cap, sentence", [
    (7, "Up to 7 subagents per reply; 4 run at once on this provider, the rest queue."),
    (3, "Up to 3 subagents per reply, all running at once on this provider."),
])
def test_a_cloud_provider_says_its_parallel_workers(cap, sentence):
    _save({"nested": {"max_workers": cap}})
    b = _lane_backend(1, [], None)
    b.provider.key = "openai"                                  # its task calls go out together, 4 without a profile
    b.apply_worker_cap()
    assert sentence in _task_text(b)


# --- the setting -------------------------------------------------------------------------------------------------------

def test_the_setting_is_3_7_11_or_15_in_the_global_file_and_7_by_default():
    assert settings.nested_max_workers() == 7
    assert settings.effective()["nested.max_workers"] == settings.Effective(7, "default")
    assert "nested.max_workers" in settings.SETTABLE
    assert settings.set_value("nested.max_workers", "11") == APPLIES
    assert read_settings()["nested"] == {"max_workers": 11}
    assert settings.nested_max_workers() == 11
    assert settings.effective()["nested.max_workers"] == settings.Effective(11, "global")
    for bad in ("4", "16", "0", "seven", "7.0", "-3", "8", "12", "-1", "abc"):
        with pytest.raises(ValueError, match="nested.max_workers must be 3, 7, 11 or 15"):
            settings.set_value("nested.max_workers", bad)
    assert read_settings()["nested"] == {"max_workers": 11}, "a refused value writes nothing"
    with pytest.raises(ValueError, match="a project file may not set nested.max_workers"):
        settings.set_value("nested.max_workers", "7", scope="project")
    settings.unset_value("nested.max_workers")
    assert "nested" not in read_settings() and settings.nested_max_workers() == 7


def test_a_hand_edited_bad_cap_names_the_file_and_the_session_keeps_its_cap():
    _save({"nested": {"max_workers": 12}})
    with pytest.raises(ValueError, match="nested.max_workers must be 3, 7, 11 or 15"):
        settings.nested_max_workers()
    b = _lane_backend(2, [], None)
    b.apply_worker_cap()
    assert "Up to 7 subagents per reply" in _task_text(b), "the file's problem is reported elsewhere"


@pytest.mark.parametrize("section", [[7], "7"])
def test_the_cli_refuses_a_nested_section_that_is_not_an_object(section, capsys):
    """DREAM-209's gate: `dream settings set` on a hand-edited file whose nested section is a list or a string raised
    a TypeError (a traceback). It is an error line naming the file and the fix, exit 2, and the file is untouched."""
    _save({"nested": section})
    before = settings_path().read_bytes()
    assert management.main(["settings", "set", "nested.max_workers", "7"]) == 2
    said = capsys.readouterr().out
    assert said.startswith("Dream settings: Cannot use runtime settings") and settings_path().name in said
    assert "nested must be an object; make it one or remove it" in said and settings_path().read_bytes() == before


async def test_the_studio_control_saves_the_cap_and_the_running_session_takes_it(tmp_path):
    b = _lane_backend(2, [], None)
    app = App(provider="machx", workspace=tmp_path)
    app.engine = SimpleNamespace(provider=SimpleNamespace(key="machx"), model="fixture-model", backend=b)
    result = await app._runtime_control({"action": "settings_save", "key": "nested.max_workers", "value": 15})
    assert result["key"] == "nested.max_workers" and read_settings()["nested"] == {"max_workers": 15}
    assert "Up to 15 subagents per reply" in _task_text(b), "the running session takes it from its next reply"
    row = {r["key"]: r for s in result["settings"]["sections"] for r in s["rows"]}["nested.max_workers"]
    assert row["value"] == 15 and row["origin"] == "global"
    assert row["applies"] == APPLIES_STUDIO and row["restart"] is False
    assert result["applies"] == "Saved. " + APPLIES_STUDIO
    assert row["edit"]["kind"] == "choice" and row["edit"]["choices"] == [3, 7, 11, 15]
    assert row["edit"]["labels"] == {"3": "4 agents", "7": "8 agents", "11": "12 agents", "15": "16 agents"}
    await app._runtime_control({"action": "settings_save", "key": "nested.max_workers", "value": "11"})
    assert read_settings()["nested"] == {"max_workers": 11}, "a control may send the number as text"
    saved = settings_path().read_bytes()
    for bad in (16, True, "lots", "8", "12", -1, "abc"):
        with pytest.raises(ValueError) as refused:
            await app._runtime_control({"action": "settings_save", "key": "nested.max_workers", "value": bad})
        assert str(refused.value) == settings._MAX_WORKERS_TEXT and settings_path().read_bytes() == saved, bad
    with pytest.raises(ValueError) as refused:
        await app._runtime_control({"action": "settings_save", "key": "nested.max_workers", "value": 7,
                                    "scope": "project"})
    assert str(refused.value) == PROJECT_REFUSAL
    assert settings_path().read_bytes() == saved and not (tmp_path / ".dream" / "settings.json").exists()
    await app._runtime_control({"action": "settings_save", "key": "nested.max_workers", "value": None})
    assert "nested" not in read_settings() and "Up to 7 subagents per reply" in _task_text(b)
    workers = {s["id"]: s for s in result["settings"]["sections"]}["workers"]
    for text in (workers["intro"], row["help"]):          # how many run at once: the lanes, or Parallel workers
        assert "engine lanes" in text and "Parallel workers with a cloud provider" in text, text


async def test_slash_settings_reaches_the_running_session():
    b = _lane_backend(2, [], None)
    app = App(provider="machx", model="fixture")
    app.engine = SimpleNamespace(store=None, backend=b, provider=get_provider("machx"), model="fixture")
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    await app._command("/settings set nested.max_workers 11")
    assert b._worker_cap == 11 and "Up to 11 subagents per reply" in _task_text(b)
    said = " ".join(app.renderer.console.file.getvalue().split())       # the console wraps long lines
    assert f"nested.max_workers: {APPLIES}" in said
    await app._command("/settings unset nested.max_workers")
    assert b._worker_cap == 7 and "Up to 7 subagents per reply" in _task_text(b)
