"""DREAM-175..177: context management -- what the main agent and its sub-agents do when their context fills.

The owner's design (2026-09-27): the main agent chooses compact, handoff or stop; a sub-agent compact, handoff or
return; one trigger for all of them (80 % of the window by default) and a warning a few points before it (5 by
default). All four are behaviour settings (the file, `/settings`, the Settings tab) and apply to the running
session. Fresh start is now called Handoff. `error`, the old name of `stop`, keeps loading.

No engine, no GPU.
"""
from __future__ import annotations

import asyncio
import copy
import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream.core import settings
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import get_provider
from dream.local.settings import BY_NAME, parse_value, validate_options


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("DREAM_COMPACT_AT", raising=False)


def _write(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _backend(**local_options):
    b = OpenAICompatBackend(
        provider=SimpleNamespace(key="machx", label="machx", base_url="http://x/v1", multimodal=False,
                                 api_key=lambda: "n"),
        model="fixture", system_prompt="system", tools=[], permission_cb=None)
    b._local_options = dict(local_options)
    return b


# --- P1: the settings -------------------------------------------------------------------------------------

def test_defaults_are_the_owners_design():
    assert settings.context_policy() == {"context_overflow": "compact", "subagent_overflow": "return",
                                         "context_trigger": 80, "context_warning": 5}


@pytest.mark.parametrize("key,text,stored", [
    ("behaviour.context_overflow", "handoff", "handoff"),
    ("behaviour.context_overflow", "Stop", "stop"),
    ("behaviour.context_overflow", "error", "stop"),          # the old name is stored as the new one
    ("behaviour.subagent_overflow", "compact", "compact"),
    ("behaviour.subagent_overflow", "handoff", "handoff"),
    ("behaviour.context_trigger", "85", 85),
    ("behaviour.context_trigger", "70%", 70),
    ("behaviour.context_warning", "0", 0),
    ("behaviour.context_warning", "10", 10),
])
def test_each_value_is_set_and_read_back(key, text, stored):
    settings.set_value(key, text)
    assert read_settings()["behaviour"][key.split(".")[1]] == stored
    assert settings.context_policy()[key.split(".")[1]] == stored
    assert settings.effective()[key].value == stored and settings.effective()[key].source == "global"


@pytest.mark.parametrize("key,text,named", [
    ("behaviour.context_overflow", "return", "compact, handoff, stop"),
    ("behaviour.subagent_overflow", "stop", "compact, handoff, return"),
    ("behaviour.subagent_overflow", "error", "compact, handoff, return"),   # the alias is the lead's only
    ("behaviour.context_trigger", "9", "10 to 95"),
    ("behaviour.context_trigger", "96", "10 to 95"),
    ("behaviour.context_trigger", "0.8", "whole percent of the context window from 10 to 95"),
    ("behaviour.context_trigger", "-5", "whole percent of the context window from 10 to 95"),
    ("behaviour.context_warning", "-1", "0 to 50"),
    ("behaviour.context_warning", "", "0 to 50"),
    ("behaviour.context_trigger", "８０", "10 to 95"),                    # non-ASCII digits
    ("behaviour.context_warning", "51", "0 to 50"),
])
def test_a_bad_value_is_refused_by_name_and_nothing_is_written(key, text, named):
    with pytest.raises(ValueError, match=named):
        settings.set_value(key, text)
    assert not settings_path().exists() or "behaviour" not in read_settings()


def test_a_warning_at_or_past_the_saved_trigger_is_refused_and_the_file_keeps_its_value():
    settings.set_value("behaviour.context_trigger", "30")
    with pytest.raises(ValueError, match="below behaviour.context_trigger"):
        settings.set_value("behaviour.context_warning", "30")
    assert read_settings()["behaviour"] == {"context_trigger": 30}


def test_an_old_file_with_error_still_loads_as_stop():
    _write({"version": 1, "behaviour": {"context_overflow": "error"}})
    assert settings.context_policy()["context_overflow"] == "stop"
    assert settings.effective()["behaviour.context_overflow"].value == "stop"
    settings.validate(read_settings())                         # the file is valid as it stands


def _project(tmp_path, data):
    settings.set_workspace(tmp_path)
    (tmp_path / ".dream").mkdir(exist_ok=True)
    (tmp_path / ".dream" / "settings.json").write_text(json.dumps(data), encoding="utf-8")


def test_a_hand_edited_pair_across_files_keeps_loading_and_the_warning_goes_below_the_trigger(tmp_path):
    _write({"version": 1, "behaviour": {"context_warning": 40}})
    _project(tmp_path, {"behaviour": {"context_trigger": 30}})
    policy = settings.context_policy()
    assert policy["context_trigger"] == 30 and policy["context_warning"] == 5     # the project file still counts
    assert settings.effective()["behaviour.context_trigger"].source == "project"   # and nothing raises


def test_a_global_warning_past_the_projects_trigger_is_refused(tmp_path):
    _project(tmp_path, {"behaviour": {"context_trigger": 30}})
    with pytest.raises(ValueError, match=r"context_warning \(40\) must be below behaviour.context_trigger \(30\)"):
        settings.set_value("behaviour.context_warning", "40")
    settings.set_value("behaviour.context_warning", "20")
    assert settings.context_policy()["context_warning"] == 20


def test_a_one_file_pair_within_range_is_used_as_written():
    _write({"version": 1, "behaviour": {"context_trigger": 10, "context_warning": 9}})
    assert settings.context_policy()["context_warning"] == 9


def test_the_environment_trigger_is_shown_as_governing(monkeypatch):
    monkeypatch.setenv("DREAM_COMPACT_AT", "0.9")
    row = settings.effective()["behaviour.context_trigger"]
    assert row.source == "env DREAM_COMPACT_AT" and row.value == 90          # a percent, like the setting


def test_unset_returns_to_the_default():
    settings.set_value("behaviour.context_trigger", "60")
    assert "removed" in settings.unset_value("behaviour.context_trigger")
    assert settings.context_policy()["context_trigger"] == 80 and "behaviour" not in read_settings()


def test_all_four_are_settable():
    assert set(settings.CONTEXT_SETTINGS) <= set(settings.SETTABLE)


def test_the_backend_takes_the_policy_and_a_launch_choice_wins_for_its_launch():
    settings.set_value("behaviour.context_overflow", "handoff")
    b = _backend()
    b.apply_context_policy()
    assert b._context_overflow == "handoff" and b._context_policy["subagent_overflow"] == "return"
    launched = _backend(context_overflow="error")               # an old saved launch
    launched.apply_context_policy()
    assert launched._context_overflow == "stop"


def _app(live):
    from dream.tui.app import App
    app = App(provider="machx", model="fixture")
    app.engine = SimpleNamespace(store=None, backend=live, provider=get_provider("machx"), model="fixture")
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    return app


def test_slash_settings_applies_to_the_running_session():
    live = _backend()
    live.apply_context_policy()
    app = _app(live)
    asyncio.run(app._command("/settings set behaviour.context_overflow stop"))
    assert live._context_overflow == "stop"
    asyncio.run(app._command("/settings set behaviour.context_trigger 70"))
    assert live._context_policy["context_trigger"] == 70
    asyncio.run(app._command("/settings unset behaviour.context_overflow"))
    assert live._context_overflow == "compact"


# --- P1: the local launch control -------------------------------------------------------------------------

@pytest.mark.parametrize("raw,value", [("compact", "compact"), ("handoff", "handoff"), ("stop", "stop"),
                                       ("error", "stop")])
def test_the_launch_control_takes_the_new_words_and_reads_error_as_stop(raw, value):
    assert parse_value(BY_NAME["context_overflow"], raw) == value
    assert validate_options({"context_overflow": raw})["context_overflow"] == value


def test_the_desktop_picker_offers_the_three_words_and_an_old_saved_error_loads_as_stop():
    from dream.desktop.startup import validate_selection
    from dream.local.settings import available_controls
    control = next(c for c in available_controls({}) if c.name == "context_overflow")
    assert control.choices == ("compact", "handoff", "stop")        # the desktop picker lists the Control's choices
    picker = {"controls": [{"name": "context_overflow", "kind": "overflow", "choices": list(control.choices)}]}
    picked = validate_selection({"ctx": 4096, "gpus": None, "options": {"context_overflow": "error"}}, picker)
    assert picked["options"]["context_overflow"] == "stop"


def test_the_launch_control_refuses_other_words():
    with pytest.raises(ValueError, match="compact, handoff or stop"):
        parse_value(BY_NAME["context_overflow"], "return")


# --- P1: the Settings tab ---------------------------------------------------------------------------------

def test_the_settings_tab_shows_and_saves_them():
    from dream.gui import settings_panel
    view = settings_panel.view(provider="machx", model=None)
    section = next(s for s in view["sections"] if s["id"] == "context")
    rows = {row["key"]: row for row in section["rows"]}
    assert set(rows) == set(settings.CONTEXT_SETTINGS)
    assert rows["behaviour.context_overflow"]["edit"] == {"kind": "choice", "value": "compact",
                                                          "choices": ["compact", "handoff", "stop"]}
    assert rows["behaviour.subagent_overflow"]["edit"]["choices"] == ["compact", "handoff", "return"]
    assert rows["behaviour.context_trigger"]["edit"]["kind"] == "number"
    assert rows["behaviour.context_trigger"]["applies"].startswith("Now")
    settings_panel.save("behaviour.subagent_overflow", "handoff")
    settings_panel.save("behaviour.context_warning", "3")
    assert read_settings()["behaviour"] == {"subagent_overflow": "handoff", "context_warning": 3}
    with pytest.raises(ValueError):
        settings_panel.save("behaviour.context_overflow", True)
    for bad in (-1, "", "abc"):                                        # the panel shows the range, not a generic refusal
        with pytest.raises(ValueError, match="0 to 50"):
            settings_panel.save("behaviour.context_warning", bad)


# --- P1: the name ------------------------------------------------------------------------------------------

def test_nothing_the_owner_sees_says_fresh_start():
    from pathlib import Path

    from dream.tui.app import HELP
    root = Path(__file__).resolve().parent.parent / "dream" / "gui" / "static"
    for name in ("index.html", "companion.js"):
        text = (root / name).read_text(encoding="utf-8")
        assert ">Fresh start<" not in text and 'title="Fresh start' not in text, name
        assert ">Handoff</button>" in text, name
    assert "Fresh start" not in HELP and "/handoff" in HELP


def test_the_backend_never_treats_the_old_word_as_compact():
    b = _backend()
    b._context_overflow = "error"                               # whatever still says it gets stop
    assert b._context_overflow == "stop" and b._maybe_compact() == []


# --- P2: the main agent at the warning and the trigger (DREAM-176) --------------------------------------------

from dream.core.backends import openai_compat                                   # noqa: E402
from test_cache_friendly_head import FakeEngine, Meter, backend as fake_backend, tool, turn   # noqa: E402

WINDOW = 20_000


def _rounds(n):
    return [{"calls": [("read_file", {"path": f"f{i}.txt"})]} for i in range(n)] + [{"text": "done"}]


def _session(policy, *, n=40, size=2000, side=()):
    engine = FakeEngine(lead=_rounds(n), side=list(side))
    b = fake_backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + "y" * size)], n_ctx=WINDOW)
    b.runtime_meter = Meter()
    b.apply_context_policy({**settings.CONTEXT_DEFAULTS, **policy})
    return engine, b


@pytest.fixture
def long_turns(monkeypatch):
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 1000)
    monkeypatch.setattr(openai_compat, "_COMPACT_AT", None)


def _lead(engine):
    return [r for r in engine.requests if r["kind"] == "lead"]


@pytest.mark.usefixtures("long_turns")
async def test_the_trigger_setting_is_the_line_the_lead_never_passes():
    engine, b = _session({"context_trigger": 60})
    await turn(b, "read them all")
    assert max(r["prompt_tokens"] for r in _lead(engine)) <= 0.60 * WINDOW
    assert [f["threshold"] for e, f in b.runtime_meter.records if e == "compaction"][0] == 0.60


@pytest.mark.usefixtures("long_turns")
async def test_the_warning_is_said_once_per_crossing_on_the_rounds_tool_result():
    engine, b = _session({"context_overflow": "compact"})
    events = await turn(b, "read them all")
    warnings = [f for e, f in b.runtime_meter.records if e == "context_warning"]
    compactions = [f for e, f in b.runtime_meter.records if e == "compaction"]
    assert warnings and len(warnings) <= len(compactions) + 1          # one per climb to the line, never per round
    assert all(0.75 * WINDOW < f["fill"] for f in warnings)
    said = [e.data for e in events if e.kind == "system" and str(e.data).startswith("Context at about")]
    assert len(said) == len(warnings) and "at 80% it will compact" in said[0]
    sent = [m for r in _lead(engine) for m in r["messages"]]
    noted = [m for m in sent if "context warning" in str(m.get("content"))]
    assert noted and all(m["role"] == "tool" for m in noted)            # no message of its own
    assert "save what the next step needs" in noted[0]["content"] and "PLAN.md" in noted[0]["content"]


@pytest.mark.usefixtures("long_turns")
async def test_a_warning_of_zero_says_nothing():
    engine, b = _session({"context_warning": 0})
    await turn(b, "read them all")
    assert not [e for e, _ in b.runtime_meter.records if e == "context_warning"]


@pytest.mark.usefixtures("long_turns")
async def test_handoff_rewrites_the_conversation_mid_turn_and_the_turn_carries_on():
    engine, b = _session({"context_overflow": "handoff"})
    events = await turn(b, "Read every file and total the ys.")
    handoffs = [r for r in _lead(engine) if len(r["messages"]) == 2 and r["messages"][1]["role"] == "user"
                and "automatic Handoff" in r["messages"][1]["content"]]
    assert handoffs, "no request went out as one Handoff"
    text = handoffs[0]["messages"][1]["content"]
    assert "Read every file and total the ys." in text                    # the request in progress
    assert "read_file(" in text and "f0.txt" in text                     # this turn's steps
    assert "## Files written or edited" in text
    said = [str(e.data) for e in events if e.kind == "system" and str(e.data).startswith("Handoff (automatic")]
    assert len(said) == len(handoffs) and "The turn carries on" in said[0]
    assert not [e for e, _ in b.runtime_meter.records if e == "compaction"]     # a Handoff never compacts
    assert max(r["prompt_tokens"] for r in _lead(engine)) <= 0.80 * WINDOW
    result = next(e.data for e in events if e.kind == "result")
    assert not result.get("is_error") and _lead(engine)[-1]["messages"][-1]["role"] == "tool"   # it finished
    assert [f["automatic"] for e, f in b.runtime_meter.records if e == "fresh_start"] == [True] * len(handoffs)


@pytest.mark.usefixtures("long_turns")
async def test_stop_ends_the_turn_at_the_trigger_with_what_it_found_and_elides_nothing():
    engine, b = _session({"context_overflow": "stop"}, side=[{"text": "Read 20 files; 20 are left."}])
    events = await turn(b, "read them all")
    result = next(e.data for e in events if e.kind == "result")
    assert result["is_error"] and result["subtype"] == "context_stop"
    said = " ".join(str(e.data) for e in events if e.kind == "system")
    assert "Context stop" in said and "behaviour.context_overflow = stop" in said and "/handoff" in said
    assert "error" not in said.lower()
    assert b.messages[-1] == {"role": "assistant", "content": "Read 20 files; 20 are left."}   # the salvage
    assert not any("[elided" in str(m.get("content")) for m in b.messages)
    assert max(r["prompt_tokens"] for r in _lead(engine)) <= 0.80 * WINDOW
    assert [e for e, _ in b.runtime_meter.records if e == "context_stop"]


@pytest.mark.usefixtures("long_turns")
async def test_stop_at_the_first_round_asks_for_no_summary_and_an_old_error_setting_stops_too():
    engine, b = _session({"context_overflow": "stop"})
    b.messages.append({"role": "user", "content": "x" * (4 * WINDOW)})      # already past the line
    b.messages.append({"role": "assistant", "content": "ok"})
    events = await turn(b, "next")
    assert next(e.data for e in events if e.kind == "result")["subtype"] == "context_stop"
    assert not [r for r in engine.requests if r["kind"] != "lead"]         # no salvage request
    engine2, old = _session({})
    old._context_overflow = "error"
    old.messages += [{"role": "user", "content": "x" * (4 * WINDOW)}, {"role": "assistant", "content": "ok"}]
    events = await turn(old, "next")
    assert next(e.data for e in events if e.kind == "result")["subtype"] == "context_stop"


@pytest.mark.usefixtures("long_turns")
async def test_a_handoff_at_the_start_of_a_turn_names_the_new_request_and_no_steps():
    engine, b = _session({"context_overflow": "handoff"}, n=0)
    b.messages += [{"role": "user", "content": "x" * (4 * WINDOW)}, {"role": "assistant", "content": "ok"}]
    await turn(b, "Now write the summary.")
    first = _lead(engine)[0]["messages"]
    assert len(first) == 2 and "automatic Handoff" in first[1]["content"]
    assert "Now write the summary." in first[1]["content"] and "(none yet)" in first[1]["content"]


# --- P3: a sub-agent at the warning and the trigger (DREAM-177) ---------------------------------------------

from test_local_subagents import _PostResp, _backend as sub_backend, _researcher, _sub_final, _sub_toolcall, \
    _tool as sub_tool                                                           # noqa: E402

SUB_WINDOW = 16_384


class _SubEngine:
    """Answers a sub-agent: a report when asked for one (the tools-off instruction), else a tool call until
    `rounds` calls ran, then DONE. Keeps every payload."""

    def __init__(self, rounds=60, report="Did 12 searches; found X in f3; left: verify Y.", usage=None):
        self.rounds, self.report, self.posted, self.calls = rounds, report, [], 0
        self.usage = usage          # counts a prompt as the server would: that many tokens per chars/4 token

    def _reply(self, body, payload):
        if self.usage:
            body = {**body, "usage": {"prompt_tokens": int(openai_compat._est_tokens(payload["messages"]) * self.usage),
                                      "completion_tokens": 20}}
        return _PostResp(body)

    async def post(self, url, json=None):
        self.posted.append(copy.deepcopy(json))
        last = json["messages"][-1]
        if last.get("name") == "dream_recovery_instruction" and "STOP." in str(last.get("content")):
            return self._reply(_sub_final(self.report), json)
        self.calls += 1
        return self._reply(_sub_toolcall("web_search", {"q": str(self.calls)}) if self.calls <= self.rounds
                           else _sub_final("DONE"), json)


def _sub_session(policy, **engine):
    n = {"k": 0}

    async def search(a):
        n["k"] += 1
        return {"content": [{"type": "text", "text": f"R{n['k']} " + "y" * 1600}]}
    b = sub_backend([sub_tool("web_search", search), sub_tool("recall", search)], _researcher())
    b.n_ctx = SUB_WINDOW
    b.runtime_meter = Meter()
    b.apply_context_policy({**settings.CONTEXT_DEFAULTS, **policy})
    b._client = _SubEngine(**engine)
    return b


def _work(b):
    return [p for p in b._client.posted if "STOP." not in str(p["messages"][-1].get("content"))]


@pytest.fixture
def no_env_line(monkeypatch):
    monkeypatch.setattr(openai_compat, "_COMPACT_AT", None)


@pytest.mark.usefixtures("no_env_line")
async def test_return_is_the_default_and_hands_back_a_marked_incomplete_report():
    b = _sub_session({})
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed
    assert out.startswith("(subagent 'researcher' stopped at about ") and "behaviour.subagent_overflow = return" in out
    assert "NOT complete" in out and out.endswith("Did 12 searches; found X in f3; left: verify Y.")
    assert all(openai_compat._est_tokens(p["messages"]) <= 0.80 * SUB_WINDOW for p in _work(b))
    warned = [m for p in _work(b) for m in p["messages"] if "context warning" in str(m.get("content"))]
    assert warned and warned[0]["role"] == "tool" and "return your report" in warned[0]["content"]
    assert [f["choice"] for e, f in b.runtime_meter.records if e == "subagent_overflow"] == ["return"]


@pytest.mark.usefixtures("no_env_line")
async def test_handoff_continues_in_a_fresh_context_from_its_own_report_and_finishes():
    b = _sub_session({"subagent_overflow": "handoff"}, rounds=45)
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("DONE", False)
    fresh = [p["messages"] for p in _work(b) if len(p["messages"]) == 2 and "automatic Handoff" in
             str(p["messages"][1]["content"])]
    assert fresh, "no request went out from a fresh context"
    handed = fresh[-1][1]["content"]
    assert handed.startswith("find X") and "automatic Handoff 1" in handed
    assert handed.endswith("Did 12 searches; found X in f3; left: verify Y.")
    assert fresh[-1][0] == {"role": "system", "content": "You are the researcher."}
    assert [f["choice"] for e, f in b.runtime_meter.records if e == "subagent_overflow"] == ["handoff"]


@pytest.mark.usefixtures("no_env_line")
async def test_after_three_handoffs_a_task_that_keeps_outgrowing_comes_back(monkeypatch):
    monkeypatch.setattr(openai_compat, "_SUB_MAX_ROUNDS", 500)       # the round limit still applies across handoffs
    b = _sub_session({"subagent_overflow": "handoff"}, rounds=500)
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed and "already handed off 3 time(s)" in out
    assert [f["choice"] for e, f in b.runtime_meter.records if e == "subagent_overflow"] == ["handoff"] * 3 + ["return"]


@pytest.mark.usefixtures("no_env_line")
async def test_a_handoff_without_a_report_returns_instead_of_starting_over_blind():
    b = _sub_session({"subagent_overflow": "handoff"}, report="")
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed and "progress report for a Handoff could not be written" in out
    assert not [p for p in _work(b) if len(p["messages"]) == 2 and "automatic Handoff" in str(p["messages"][1])]


@pytest.mark.usefixtures("no_env_line")
async def test_a_subagent_uses_the_trigger_and_a_zero_warning_says_nothing():
    b = _sub_session({"context_trigger": 50, "context_warning": 0})
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed and all(openai_compat._est_tokens(p["messages"]) <= 0.50 * SUB_WINDOW for p in _work(b))
    assert not [m for p in _work(b) for m in p["messages"] if "context warning" in str(m.get("content"))]


@pytest.mark.usefixtures("no_env_line")
async def test_the_activity_line_says_which_choice_fired():
    for policy, said in (({"subagent_overflow": "handoff"}, "handed off to a fresh context (1 of 3)"),
                         ({}, "stopped and returned its report to the main agent")):
        b = _sub_session(policy, rounds=45)
        seen = []
        b._background_emit = seen.append
        await b._run_subagent("researcher", "find X")
        texts = [e.data.get("text") for e in seen if e.kind == "agent_activity"]
        assert any(said in (t or "") for t in texts), texts


async def test_a_task_the_routed_window_cannot_hold_at_all_is_refused_with_its_numbers_under_return():
    from test_settings_s3_routing import SMALL, SMALL_CTX, _props, _sub_backend, _write as write_roles
    write_roles({"roles": {"subagents": {"researcher": {"model": SMALL}}}})
    b, front = _sub_backend(FakeEngine(side=[{"text": "found it"}]), props_by_model={SMALL: _props(SMALL_CTX)})
    text, failed = await b._run_subagent("researcher", "look at this\n" + "w" * 60_000)
    assert failed and front.sent == [] and ("8,192" in text or "8192" in text), text


# --- P2 gate findings (DREAM-176 gate 2) --------------------------------------------------------------------

@pytest.mark.usefixtures("long_turns")
async def test_a_handoff_that_cannot_get_under_the_line_is_not_repeated_and_tool_results_get_through():
    engine = FakeEngine(lead=_rounds(7))
    b = fake_backend(engine, tools=[tool("read_file", lambda a: a["path"] + " " + "y" * 400)], n_ctx=WINDOW,
                     system="S" * int(4 * 0.85 * WINDOW))                     # the head alone is past the line
    b.runtime_meter = Meter()
    b.apply_context_policy({**settings.CONTEXT_DEFAULTS, "context_overflow": "handoff"})
    events = await turn(b, "read them")
    handed = [r for r in _lead(engine) if "automatic Handoff" in str(r["messages"][-1].get("content"))]
    assert len(handed) == 1                                                   # once, not every round
    assert sum(1 for r in _lead(engine) if r["messages"][-1]["role"] == "tool") >= 5
    assert sum("the rest of this turn compacts instead" in str(e.data) for e in events if e.kind == "system") == 1


@pytest.mark.usefixtures("long_turns")
async def test_a_later_handoff_in_the_same_turn_still_lists_the_steps_from_before_the_first():
    engine, b = _session({"context_overflow": "handoff"}, n=60)
    await turn(b, "warm up")                                                  # not the first turn of the session
    engine.lead[:] = _rounds(60)
    await turn(b, "Read sixty files.")
    texts = [r["messages"][1]["content"] for r in _lead(engine)
             if len(r["messages"]) == 2 and "automatic Handoff" in str(r["messages"][1].get("content"))
             and "Read sixty files." in r["messages"][1]["content"]]
    assert len(texts) >= 2
    counts = [t.count("- read_file(") for t in texts]
    assert all(c > 0 for c in counts) and counts == sorted(counts) and counts[-1] > counts[0], counts
    assert '"f0.txt"' in texts[-1] or "f0.txt" in texts[-1]                   # the first steps are still there


def test_a_waiting_council_transfer_compacts_instead_of_handing_off():
    engine, b = _session({"context_overflow": "handoff"})
    b.messages += [{"role": "user", "name": "dream_handoff_user", "content": "Retained exactly"},
                   {"role": "assistant", "content": "ok"}]
    b._council_context = {"pending": True}
    events = b._auto_handoff(1000, WINDOW)
    assert "Council handoff is waiting" in str(events[0].data)
    assert any(m.get("content") == "Retained exactly" for m in b.messages)
    assert not any("automatic Handoff" in str(m.get("content")) for m in b.messages)


def test_a_correction_sent_while_the_turn_ran_is_carried_and_takes_precedence():
    engine, b = _session({"context_overflow": "handoff"})
    b._turn_prompt, b._turn_start = "Build the red page.", len(b.messages)
    b.messages += [{"role": "user", "content": "Build the red page.\n\n[id:m0001]"},
                   {"role": "assistant", "content": None, "tool_calls": [
                       {"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]},
                   {"role": "tool", "tool_call_id": "c1", "content": "ok"},
                   {"role": "user", "name": "dream_steering_user", "content": "Make it blue instead.\n\n[id:m0002]"}]
    b._auto_handoff(1000, WINDOW)
    text = b.messages[1]["content"]
    assert "## The owner's corrections while this turn ran" in text and "- Make it blue instead." in text
    assert "[id:m0002]" not in text and "as corrected by any corrections listed after it" in text


# --- P3 gate findings (DREAM-177 gate 3): a runtime profile's admission gate never cuts a return / handoff run ------

@pytest.mark.usefixtures("no_env_line")
@pytest.mark.parametrize("usage", [None, 1.18], ids=["no server count", "server counts a sixth more"])
@pytest.mark.parametrize("name,window", [("lean", 16_384), ("balanced", 32_768), ("frontier", 75_000)])
@pytest.mark.parametrize("choice", ["return", "handoff"])
async def test_with_a_profile_the_choice_fires_and_the_admission_gate_never_compacts(monkeypatch, name, window,
                                                                                    usage, choice):
    from dream.core.profiles import PROFILES
    monkeypatch.setattr(openai_compat, "_SUB_MAX_ROUNDS", 400)
    n = {"k": 0}

    async def search(a):
        n["k"] += 1
        return {"content": [{"type": "text", "text": f"R{n['k']} " + "y" * (window // 10)}]}
    b = OpenAICompatBackend(
        provider=SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=False,
                                 api_key=lambda: "n"),
        model="m", system_prompt="s", tools=[sub_tool("web_search", search), sub_tool("recall", search)],
        permission_cb=None, subagents=_researcher(), profile=PROFILES[name])
    b.n_ctx = window
    b.runtime_meter = Meter()
    b.apply_context_policy({**settings.CONTEXT_DEFAULTS, "subagent_overflow": choice})   # lead: compact
    b._client = _SubEngine(rounds=400, usage=usage)
    out, failed = await b._run_subagent("researcher", "find X")
    records = b.runtime_meter.records
    assert not [f for e, f in records if e == "compaction"], [f for e, f in records if e == "compaction"][:2]
    fired = [f["choice"] for e, f in records if e == "subagent_overflow"]
    assert fired and fired[-1] == "return" and failed, (fired, out[:200])
    assert "Did 12 searches" in out
    if choice == "handoff":
        assert fired.count("handoff") == 3


@pytest.mark.usefixtures("no_env_line")
async def test_after_the_cap_the_warning_names_return_and_a_subagent_is_not_told_to_update_the_plan(monkeypatch):
    monkeypatch.setattr(openai_compat, "_SUB_MAX_ROUNDS", 500)
    b = _sub_session({"subagent_overflow": "handoff"}, rounds=500)
    await b._run_subagent("researcher", "find X")
    notes = [m["content"] for p in _work(b) for m in p["messages"] if "context warning" in str(m.get("content"))]
    assert notes and all("update_plan" not in t for t in notes)
    assert "return your report of the work so far" in notes[-1]            # the fourth context returns


def test_the_lead_warns_only_when_the_note_itself_stays_below_the_trigger():
    engine, b = _session({"context_overflow": "compact"})
    b.messages += [{"role": "user", "content": "go"}, {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "x"}]
    b.messages[-1]["content"] = "x" * (4 * int(0.795 * WINDOW) - 4 * openai_compat._est_tokens(b.messages))
    assert b._context_warning() == [] and not b._warned                  # 79.5 %: the note would carry it past 80
    b.messages[-1]["content"] = "x" * (4 * int(0.76 * WINDOW) - 4 * openai_compat._est_tokens(b.messages[:-1]))
    assert b._context_warning() and b._warned and "context warning" in b.messages[-1]["content"]


# --- P3 gate round 2 (non-blocking): the two return paths without a report of work, and a jump past the window ---

def _profiled(window, *, text, schemas_size=0):
    from dream.core.profiles import PROFILES

    async def search(a):
        return {"content": [{"type": "text", "text": text(a)}]}
    tools = [sub_tool("web_search", search), sub_tool("recall", search)]
    if schemas_size:                     # big tool schemas: admission refuses before the trigger
        tools[0].description = "d" * schemas_size
    b = OpenAICompatBackend(
        provider=SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=False,
                                 api_key=lambda: "n"),
        model="m", system_prompt="s", tools=tools, permission_cb=None, subagents=_researcher(),
        profile=PROFILES["frontier"])
    b.n_ctx = window
    b.runtime_meter = Meter()
    return b


@pytest.mark.usefixtures("no_env_line")
@pytest.mark.parametrize("choice", ["return", "handoff"])
async def test_a_task_alone_past_the_trigger_does_not_go_on_and_writes_no_report(choice):
    b = _sub_session({"subagent_overflow": choice})
    b._client = _SubEngine()
    out, failed = await b._run_subagent("researcher", "x" * int(4 * 0.85 * SUB_WINDOW))
    assert failed and "did not go on: its task alone fill about" in out and f"{SUB_WINDOW:,}-token" in out
    assert b._client.posted == []                                          # no work, no report request


@pytest.mark.usefixtures("no_env_line")
async def test_a_history_past_the_whole_window_still_gets_a_report_from_a_scratch_copy():
    b = _profiled(16_384, text=lambda a: "y")
    b.apply_context_policy({**settings.CONTEXT_DEFAULTS, "subagent_overflow": "return"})
    b._client = _SubEngine()
    history = [{"role": "system", "content": "You are the researcher."}, {"role": "user", "content": "find X"}]
    for i in range(12):                                                 # about 1.4x the window
        history += [{"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 8000}]
    kept = copy.deepcopy(history)
    report = await b._subagent_report(history, None, None, "return", 140, 80)
    assert report == "Did 12 searches; found X in f3; left: verify Y."
    assert history == kept                                              # the sub-agent's own history is untouched
    sent = b._client.posted[-1]["messages"]
    assert any("[elided" in str(m.get("content")) for m in sent) and sent[1]["content"] == "find X"


@pytest.mark.usefixtures("no_env_line")
@pytest.mark.parametrize("choice", ["return", "handoff"])
async def test_a_refusal_before_the_trigger_returns_the_work_as_a_report(monkeypatch, choice):
    monkeypatch.setattr(openai_compat, "_SUB_MAX_ROUNDS", 200)
    b = _profiled(16_384, text=lambda a: "y" * 2000, schemas_size=30_000)   # the tools take most of the window
    b.apply_context_policy({**settings.CONTEXT_DEFAULTS, "subagent_overflow": choice})
    b._client = _SubEngine(rounds=200)
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed and "stopped when the window could not take another request" in out, out[:300]
    assert out.endswith("Did 12 searches; found X in f3; left: verify Y.")
    assert not [f for e, f in b.runtime_meter.records if e == "compaction"]


def test_an_automatic_handoff_keeps_a_long_request_in_progress_whole():
    """DREAM-184 quality check: a 1,800-character task prompt lost its question list at every Handoff."""
    from dream.core import handoff
    request = "Read every file in order, then answer:\n" + "\n".join(f"Q{i}. What is fact number {i}?" for i in range(1, 60))
    assert len(request) > 1500
    text = handoff.compose(handoff.Parts(), handoff.FileLedger(), [],
                           automatic={"fill": 82, "trigger": 80, "request": request, "steps": [], "latest": ""})
    assert "Q59. What is fact number 59?" in text and "[the handoff was cut to fit]" not in text
