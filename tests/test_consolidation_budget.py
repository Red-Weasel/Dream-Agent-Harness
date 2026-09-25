"""Fix list #90 and #94 (DREAM-118): the exit consolidation has bounds of its own and says when memory was not saved.

Live 2026-09-25 02:47 (var/logs/cli.log; runtime log 20260924-230103-dfda): the owner asked Dream to save to memory
and closed it. The 3.8-hour build's last turn had made 24 tool calls (Blender, write_file, see) on the run's meter,
which stays on the backend after a turn, so the consolidation ran on it. Its ONE reply -- input 20,465 tokens, output
16,384 (the ceiling), cached 0 -- asked for memory_read 814 times or more (814 errored results; also read_notes,
task_list, skill_list, recall_sessions, memory_list, list_dir, project_outline, checkpoint_list once each): 376 calls
ran and took the meter from 24 to 400, the rest were refused ("Run tool budget reached (400)"), the next round's check
ended the ask with an error event and an is_error result, and no memory file was written. The only trace was in
cli.log. A budget of its own would not have saved that close by itself: the same reply would have run up to the cap.

Now, for the length of the consolidation: (1) an OpenAI-compatible backend offers only the tools the consolidation may
use (engine.CONSOLIDATION_TOOLS: the ten its prompt names and the four read-only lookups the system prompt teaches)
and refuses the rest at no cost -- a refusal ran nothing and is not a failure of the consolidation; (2) a reply asking
for one tool more than engine.CONSOLIDATION_REPLY_REPEATS times is a runaway and fails before any of its calls run;
(3) the calls are counted on a meter of its own, capped at engine.CONSOLIDATION_TOOL_CALLS and swapped onto every
enforcement point (engine, tool context, backend), and the run's meter is put back with its count untouched. A
consolidation that did not save is a `consolidation_failed` runtime event plus one `system` line through the engine's
event funnel (the console prints it, the chat pane shows it as Dream's own note): "memory not saved: <reason>", or
"memory partly saved: <what was kept>; <reason>" when memory-writing calls or the episodic save succeeded first --
kept as they happen, so an interrupt names them too. A saved one is a `consolidation_saved` event with the curated
facet count. Nothing here shortens the 16,384-token decode of a runaway reply; the guard acts once it has arrived.

The model is the in-process fake client of test_schema_deferral (no engine process, no network); the store, the
memory tree, the cli.log and the runtime log are the test's own (conftest). The e2ce and f5ed shapes below are the
owner's logged consolidations 20260918-183131-e2ce and 20260923-094144-f5ed, reply by reply.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from dream import config
from dream.core import engine as engine_module
from dream.core.backends.base import Event
from dream.core.engine import Engine
from dream.memory.store import MemoryStore
from dream.telemetry.runtime import RunMeter
from dream.tools import context as tool_context
from test_schema_deferral import _FakeClient, _backend, _sse, _text_round, _tool

pytestmark = pytest.mark.asyncio

RECAP = "Kept the build's decisions."
SUMMARY = _text_round("SUMMARY: " + RECAP)
# The tools the consolidation may use (the prompt's ten, then the system prompt's four read-only lookups) ...
OFFERED = ("read_notes", "remember", "recall", "forget", "project_note", "skill_list", "skill_save", "skill_patch",
           "task_add", "task_update", "task_list", "recall_sessions", "memory_list", "skill_find")
# ... and tools it may not, all of which the owner's logged consolidations or the 02:47 reply called (c4a7 also
# called `see`, which this fixture's non-multimodal provider is never offered, scope or no scope).
NOT_OFFERED = ("memory_read", "list_dir", "run_bash", "skill_load", "read_file")
E2CE = [["read_notes", "task_list"], ["recall_sessions", "skill_list"], ["memory_list", "recall_sessions"],
        ["run_bash"], ["skill_load"], ["remember", "remember"], ["skill_save"], ["task_add"]]
F5ED = [["read_notes", "task_list", "memory_list", "skill_list"], ["project_note", "recall_sessions"], ["skill_save"],
        ["remember", "task_add"]]


def _calls_round(calls):
    """One reply asking for every (name, i) in `calls` at once; the argument differs per call so the repetition guard
    stays out of it (the 02:47 reply's calls were not identical either: at least 184 distinct argument sets)."""
    lines = [_sse({"choices": [{"delta": {"tool_calls": [
        {"index": k, "id": f"c{k}", "function": {"name": name, "arguments": json.dumps({"p0": f"n{i}"})}}
    ]}, "finish_reason": None}]}) for k, (name, i) in enumerate(calls)]
    lines.append(_sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}))
    lines.append("data: [DONE]")
    return lines


def _call_round(name, i):
    return _calls_round([(name, i)])


def _shape(replies):
    """The owner's logged shapes: a list of replies, each a list of tool names, then the SUMMARY reply."""
    rounds, i = [], 0
    for names in replies:
        rounds.append(_calls_round([(name, i + k) for k, name in enumerate(names)]))
        i += len(names)
    return rounds + [SUMMARY]


def _error_round(message):
    return [_sse({"error": {"message": message, "type": "server_error"}}), "data: [DONE]"]


@pytest.fixture
def engine(tmp_path):
    e = Engine(provider="machx", model="fixture", workspace=tmp_path)
    e.store = MemoryStore(tmp_path / "fixture.db")
    e.store.start_session(e.session_id)
    e.store.add_note(e.session_id, "Retain the build's decisions.")
    e._turn_index = 3
    yield e
    e.store.close()


def wire(engine, rounds, *, spent):
    """The engine as a turn leaves it: the turn's run meter on the engine and on the backend with `spent` tool calls
    already counted, the backend offering every tool above, and the fixture model answering the consolidation with
    `rounds` (the last one repeats)."""
    calls = []
    backend = _backend([_tool(name, calls=calls) for name in OFFERED + NOT_OFFERED])
    backend._client = _FakeClient(rounds)
    engine.backend = backend
    meter = RunMeter(engine.session_id, 3, engine.profile, config.LOG_DIR / "runtime" / f"{engine.session_id}.jsonl")
    meter.tools = spent
    engine.runtime_meter = backend.runtime_meter = meter
    notes = []
    engine.emit = notes.append
    return meter, calls, notes


def runtime_events(engine, kind):
    path = config.LOG_DIR / "runtime" / f"{engine.session_id}.jsonl"
    if not path.exists():
        return []
    return [row for row in map(json.loads, path.read_text().splitlines()) if row["event"] == kind]


def cli_log():
    path = config.LOG_DIR / "cli.log"
    return path.read_text().splitlines() if path.exists() else []


def save_status(engine):
    return engine.runtime_status()["memory"]["save"]


def unconsolidated(engine):
    return engine.store.session_notes(engine.session_id, only_unconsolidated=True)


def sent_tools(payload):
    return sorted(schema["function"]["name"] for schema in payload.get("tools") or [])


def tool_messages(engine):
    return [m["content"] for m in engine.backend.messages if m.get("role") == "tool"]


def restored(engine, meter):
    """The run's meter is back on the engine and the backend, and the backend is unscoped again."""
    return (engine.runtime_meter is meter and engine.backend.runtime_meter is meter
            and engine.backend.tool_scope is None and engine.backend.reply_repeat_limit is None)


# --- the run's budget is spent: the consolidation still runs its tools and saves ------------------------------------


async def test_a_spent_run_budget_no_longer_refuses_the_consolidation(engine, monkeypatch):
    spent = engine.profile.max_run_tools                                  # 400 of 400
    meter, calls, notes = wire(engine, [_call_round("read_notes", i) for i in range(3)] + [SUMMARY], spent=spent)
    monkeypatch.setattr(engine_module.curation, "curate", lambda store: {"classified": 2, "personal": 1, "reference": 1})
    summary = await engine.consolidate()
    assert summary == RECAP
    assert save_status(engine)["state"] == "saved"
    assert len(calls) == 3                                                # the memory tools ran
    assert unconsolidated(engine) == []                                   # and the notes were folded in
    assert meter.tools == spent                                           # the run's count is untouched...
    assert restored(engine, meter)                                        # ...and its meter is back
    saved, = runtime_events(engine, "consolidation_saved")
    assert saved["turn"] == 3 and saved["facets"] == 2
    assert runtime_events(engine, "consolidation_failed") == []
    assert [row["tool"] for row in runtime_events(engine, "tool_started")] == ["read_notes"] * 3
    assert notes == []                                                    # nothing to say: it saved


async def test_an_unspent_run_budget_is_left_exactly_as_it_was(engine):
    meter, calls, notes = wire(engine, [_call_round("read_notes", i) for i in range(2)] + [SUMMARY], spent=3)
    summary = await engine.consolidate()
    assert summary == RECAP and save_status(engine)["state"] == "saved"
    assert len(calls) == 2
    assert meter.tools == 3                       # not 5: the consolidation's calls are not the run's
    assert restored(engine, meter)
    assert len(runtime_events(engine, "consolidation_saved")) == 1
    assert notes == []


# --- the owner's real consolidations save, out-of-scope calls included ---------------------------------------------


@pytest.mark.parametrize("shape", ["e2ce", "f5ed"])
async def test_the_owners_logged_consolidations_save(engine, shape):
    """e2ce: 12 calls over 8 tool replies, run_bash and skill_load among them; f5ed: 9 calls, task_list, memory_list
    and recall_sessions among them. Both saved on pristine; round 2 ended them `failed` on the refusals."""
    replies = E2CE if shape == "e2ce" else F5ED
    meter, calls, notes = wire(engine, _shape(replies), spent=19)
    summary = await engine.consolidate()
    assert summary == RECAP and save_status(engine)["state"] == "saved", save_status(engine)
    asked = [name for reply in replies for name in reply]
    assert [name for name, _ in calls] == [name for name in asked if name in OFFERED]   # the rest ran nothing
    refused = [name for name in asked if name not in OFFERED]
    assert [row["tool"] for row in runtime_events(engine, "tool_started")] == [n for n in asked if n in OFFERED]
    assert [line for line in cli_log() if line.startswith("consolidation tool refused: ")] == [
        f"consolidation tool refused: {name}" for name in refused]
    assert not [line for line in cli_log() if line.startswith("consolidation tool error: ")]
    assert unconsolidated(engine) == []
    assert len(runtime_events(engine, "consolidation_saved")) == 1 and notes == []
    assert meter.tools == 19 and restored(engine, meter)


# --- the cap is sized for real consolidations: the summary round always comes ---------------------------------------


@pytest.mark.parametrize("count", [12, "one below the cap"])
async def test_a_consolidation_under_the_cap_gets_its_summary_round(engine, count):
    """The owner's 20260918-183131-e2ce consolidation made 12 calls and then its SUMMARY reply; the gate's round-1
    finding: a cap of 12 refused that reply. Every count below the cap must be answered."""
    if count == "one below the cap":
        count = engine_module.CONSOLIDATION_TOOL_CALLS - 1
    names = OFFERED[1:10]                                                 # the prompt's tools, in turn
    meter, calls, notes = wire(engine, [_call_round(names[i % len(names)], i) for i in range(count)] + [SUMMARY],
                               spent=17)
    summary = await engine.consolidate()
    assert summary == RECAP and save_status(engine)["state"] == "saved", save_status(engine)
    assert len(calls) == count
    assert len(engine.backend._client.payloads) == count + 1              # every call's round, then the summary's
    assert meter.tools == 17 and restored(engine, meter)
    assert notes == []


async def test_twenty_five_calls_with_conflicts_and_strays_save(engine, monkeypatch):
    """The owner's 20260920-220222-c4a7 consolidation made 25 calls (remember x2, skill_save, project_note, task_*
    after the twelfth). With conflict groups and stray notes in the prompt, the same count saves and retires both."""
    store = engine.store
    first = store.upsert_memory("semantic", "Build cadence", "The owner builds in the evening.", slug="build-cadence",
                                embed=False)
    second = store.upsert_memory("semantic", "Build cadence again", "The owner builds late in the evening.",
                                 slug="build-cadence-again", embed=False)
    monkeypatch.setattr(store, "find_conflicts", lambda *args, **kwargs: [[first, second]])
    store.start_session("earlier-session")                                 # ended without a consolidation
    stray_id = store.add_note("earlier-session", "An earlier session's unconsolidated note.")
    store.end_session("earlier-session", None)
    assert [note["id"] for note in store.stray_notes(engine.session_id)] == [stray_id]
    reconciled = []
    original = store.mark_reconciled
    monkeypatch.setattr(store, "mark_reconciled", lambda slugs: (reconciled.append(list(slugs)), original(slugs)))
    names = ("recall", "remember", "project_note", "forget", "skill_list", "skill_save", "skill_patch", "task_add",
             "task_update", "read_notes")
    meter, calls, notes = wire(engine, [_call_round(names[i % len(names)], i) for i in range(25)] + [SUMMARY],
                               spent=19)
    summary = await engine.consolidate()
    assert summary == RECAP and save_status(engine)["state"] == "saved", save_status(engine)
    prompt = engine.backend._client.payloads[0]["messages"][-1]["content"]
    assert "Reconcile these stored memories" in prompt and "build-cadence-again" in prompt
    assert "Earlier sessions left these working notes" in prompt
    assert len(calls) == 25
    assert reconciled == [["build-cadence", "build-cadence-again"]]
    assert store.stray_notes(engine.session_id) == [] and unconsolidated(engine) == []
    assert meter.tools == 19 and restored(engine, meter)
    assert notes == []


# --- the 02:47 shape: one reply that asks for one tool hundreds of times ------------------------------------------------


async def test_a_runaway_reply_fails_before_any_of_its_calls_run(engine):
    limit = engine_module.CONSOLIDATION_REPLY_REPEATS
    meter, calls, notes = wire(engine, [_calls_round([("read_notes", i) for i in range(1000)])], spent=24)
    summary = await engine.consolidate()
    assert summary is None
    status = save_status(engine)
    assert status["state"] == "failed" and "runaway: read_notes repeated 1000 times in one reply" in status["error"]
    assert calls == []                                                    # not one of the 1000 ran
    assert len(engine.backend._client.payloads) == 1                      # and nothing was asked again
    assert meter.tools == 24 and restored(engine, meter)
    assert runtime_events(engine, "tool_started") == []
    failed, = runtime_events(engine, "consolidation_failed")
    assert "runaway: read_notes repeated 1000 times in one reply" in failed["reason"] and failed["kept"] == ""
    note, = notes
    assert note.kind == "system" and note.data.startswith("memory not saved: ")
    assert "runaway: read_notes repeated 1000 times in one reply" in note.data
    assert unconsolidated(engine)                                         # the notes wait for the next dream
    # The reply's calls are each paired with a stub, so the history is never left with a dangling tool_calls message.
    assert len(tool_messages(engine)) == 1000
    assert limit < 1000


@pytest.mark.parametrize("count", ["the limit", "one over the limit"])
async def test_the_repeat_limit_fits_a_real_batch_and_stops_one_over_it(engine, count):
    """The gate's round-2 finding: a limit of 8 failed nine remember calls in one reply, which pristine saved (the
    prompt's own worst case is 20 stray notes folded in one reply). At the limit the reply runs in full; one over
    is a runaway."""
    limit = engine_module.CONSOLIDATION_REPLY_REPEATS
    count = limit if count == "the limit" else limit + 1
    meter, calls, notes = wire(engine, [_calls_round([("remember", i) for i in range(count)])] + [SUMMARY], spent=0)
    summary = await engine.consolidate()
    if count == limit:
        assert summary == RECAP and save_status(engine)["state"] == "saved"
        assert len(calls) == limit and notes == []
    else:
        assert summary is None
        assert f"runaway: remember repeated {count} times in one reply" in save_status(engine)["error"]
        assert calls == [] and len(notes) == 1
    assert restored(engine, meter)


@pytest.mark.parametrize("count", ["within the limit", "over the limit"])
async def test_an_out_of_scope_burst_costs_nothing_and_over_the_limit_is_a_runaway(engine, count):
    """The 02:47 reply's tool, memory_read, is not offered. A burst within the repeat limit is refused call by call at
    no cost and the consolidation still saves; past the limit the reply is a runaway before any refusal."""
    limit = engine_module.CONSOLIDATION_REPLY_REPEATS
    count = limit - 2 if count == "within the limit" else limit + 6
    meter, calls, notes = wire(engine, [_calls_round([("memory_read", i) for i in range(count)]),
                                        _call_round("read_notes", count), SUMMARY], spent=24)
    summary = await engine.consolidate()
    assert calls == [] or [name for name, _ in calls] == ["read_notes"]
    assert [row["tool"] for row in runtime_events(engine, "tool_started")] == [n for n, _ in calls]   # no cost
    if count < limit:
        assert summary == RECAP and save_status(engine)["state"] == "saved", save_status(engine)
        refusals = tool_messages(engine)[:count]
        assert len(refusals) == count
        assert all(text.startswith("Error: tool 'memory_read' is not offered to this step. Its tools: ")
                   for text in refusals)
        assert "memory_read" not in refusals[0].split("Its tools: ")[1] and "task_list" in refusals[0]
        assert [name for name, _ in calls] == ["read_notes"]
        assert notes == [] and unconsolidated(engine) == []
    else:
        assert summary is None and calls == []
        assert f"runaway: memory_read repeated {count} times in one reply" in save_status(engine)["error"]
        note, = notes
        assert note.data.startswith("memory not saved: ")
    assert meter.tools == 24 and restored(engine, meter)


async def test_the_cap_ends_a_model_that_never_stops_calling_tools(engine):
    """Distinct calls, one per reply, more replies than the cap allows: the consolidation's own meter refuses the
    round after the cap-th call, the run's meter is untouched, and the failure is said and logged."""
    cap = engine_module.CONSOLIDATION_TOOL_CALLS
    names = OFFERED[1:10]
    meter, calls, notes = wire(engine, [_call_round(names[i % len(names)], i) for i in range(cap + 5)], spent=0)
    summary = await engine.consolidate()
    assert summary is None
    status = save_status(engine)
    assert status["state"] == "failed" and f"Run tool budget reached ({cap})" in status["error"]
    assert len(calls) == cap                                              # exactly the budget ran, then no more
    assert len(engine.backend._client.payloads) == cap
    assert meter.tools == 0 and restored(engine, meter)
    assert len(runtime_events(engine, "tool_started")) == cap
    failed, = runtime_events(engine, "consolidation_failed")
    assert f"Run tool budget reached ({cap})" in failed["reason"]
    note, = notes
    assert note.data.startswith("memory partly saved: ") and f"Run tool budget reached ({cap})" in note.data
    assert unconsolidated(engine)


# --- only the consolidation's tools are offered ------------------------------------------------------------------------


async def test_only_the_consolidations_tools_are_offered_and_the_backend_is_unscoped_afterwards(engine):
    meter, calls, notes = wire(engine, [_call_round("memory_read", 0), _call_round("list_dir", 1),
                                        _call_round("read_notes", 2), SUMMARY], spent=24)
    summary = await engine.consolidate()
    payloads = engine.backend._client.payloads
    assert all(sent_tools(p) == sorted(OFFERED) for p in payloads)        # what the model could see, every round
    assert [name for name, _ in calls] == ["read_notes"]                  # the two refusals ran nothing
    assert summary == RECAP and save_status(engine)["state"] == "saved"   # and are not failures of the consolidation
    assert notes == [] and meter.tools == 24 and restored(engine, meter)
    # Unscoped again: the backend's next request offers the whole list.
    engine.backend._client = _FakeClient([_text_round("ok")])
    async for _ in engine.backend.ask("next"):
        pass
    assert set(NOT_OFFERED) <= set(sent_tools(engine.backend._client.payloads[0]))


async def test_a_scoped_request_offers_no_verifier_hatch():
    """The `task` and `fork_verifier_agent` schemas ride outside the plain tool list; a scoped ask offers neither."""
    verifier = SimpleNamespace(description="Checks the page.")
    backend = _backend([_tool("read_notes"), _tool("memory_read")], subagents={"verifier": verifier})
    backend._client = _FakeClient([_text_round("ok")])
    async for _ in backend.ask("unscoped"):
        pass
    assert {"fork_verifier_agent", "task", "memory_read", "read_notes"} <= set(sent_tools(backend._client.payloads[0]))
    backend.tool_scope = frozenset({"read_notes"})
    async for _ in backend.ask("scoped"):
        pass
    assert sent_tools(backend._client.payloads[1]) == ["read_notes"]


# --- every failure is said and logged, honestly about what was kept -------------------------------------------------


async def test_a_provider_failure_is_said_and_logged(engine):
    meter, calls, notes = wire(engine, [_error_round("engine unavailable: device lost (card 1)")], spent=7)
    assert await engine.consolidate() is None
    assert save_status(engine)["state"] == "failed"
    assert calls == [] and meter.tools == 7 and restored(engine, meter)
    failed, = runtime_events(engine, "consolidation_failed")
    assert "device lost" in failed["reason"] and failed["kept"] == ""
    note, = notes
    assert note.kind == "system" and note.data.startswith("memory not saved: ") and "device lost" in note.data
    assert unconsolidated(engine)


async def test_a_partly_saved_consolidation_names_what_was_kept(engine):
    """Two remember() calls and a project_note ran before a runaway reply: the note and the event say so."""
    over = engine_module.CONSOLIDATION_REPLY_REPEATS + 6
    meter, calls, notes = wire(engine, [_call_round("remember", 0), _call_round("remember", 1),
                                        _call_round("project_note", 2),
                                        _calls_round([("read_notes", i) for i in range(over)])], spent=7)
    assert await engine.consolidate() is None
    assert [name for name, _ in calls] == ["remember", "remember", "project_note"]
    failed, = runtime_events(engine, "consolidation_failed")
    assert failed["kept"] == "project_note x1, remember x2"
    assert f"runaway: read_notes repeated {over} times in one reply" in failed["reason"]
    note, = notes
    assert note.data.startswith("memory partly saved: project_note x1, remember x2; ")
    assert f"runaway: read_notes repeated {over} times in one reply" in note.data
    assert meter.tools == 7 and restored(engine, meter)


async def test_a_write_before_an_interrupt_is_named(engine):
    """The gate's round-2 note 3: one remember landed, then the owner's Stop. The note says what was kept."""
    waiting = asyncio.Event()

    class HangingBackend:
        async def ask(self, prompt):
            yield Event("tool_result", {"name": "remember", "content": "Saved.", "is_error": False, "id": "c0"})
            waiting.set()
            await asyncio.Event().wait()

    engine.backend = HangingBackend()
    notes = []
    engine.emit = notes.append
    job = asyncio.create_task(engine.consolidate())
    await asyncio.wait_for(waiting.wait(), 2)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    status = save_status(engine)
    assert status["state"] == "failed" and "interrupted" in status["error"].lower()
    failed, = runtime_events(engine, "consolidation_failed")
    assert failed["kept"] == "remember x1"
    note, = notes
    assert note.data.startswith("memory partly saved: remember x1; Consolidation interrupted")
    assert unconsolidated(engine)


async def test_a_consolidation_that_raises_still_restores_the_run_meter_and_says_so(engine, monkeypatch):
    meter, calls, notes = wire(engine, [SUMMARY], spent=7)

    def fail(*args):
        raise OSError("fixture session commit failed")

    monkeypatch.setattr(engine.store, "end_session", fail)
    with pytest.raises(OSError, match="fixture session commit failed"):
        await engine.consolidate()
    assert save_status(engine)["state"] == "failed"
    assert meter.tools == 7 and restored(engine, meter)
    failed, = runtime_events(engine, "consolidation_failed")
    assert "fixture session commit failed" in failed["reason"]
    assert failed["kept"] == "the session's episodic memory"             # written before the commit failed
    note, = notes
    assert note.data.startswith("memory partly saved: the session's episodic memory; OSError: fixture session commit")


# --- the engine's own enforcement point (a CLI/SDK backend's tools) ----------------------------------------------------


async def test_the_engines_own_enforcement_point_runs_under_the_consolidations_meter(engine, monkeypatch, tmp_path):
    """A CLI/SDK backend's tools reach the engine through _wrap_tool, which enforces on engine.runtime_meter and binds
    the tool context (whose meter council and review tools read). Both must be the consolidation's meter, or a spent
    run refuses the consolidation's memory calls there too."""
    seen = []

    async def handler(args):
        seen.append(tool_context.bound_runtime_meter())
        return {"content": [{"type": "text", "text": "notes"}]}

    tool = SimpleNamespace(name="read_notes", description="read_notes tool",
                           input_schema={"type": "object", "properties": {}}, handler=handler)
    wrapped = engine._wrap_tool(tool)
    results = []

    class CliLikeBackend:
        """Stands in for a CLI/SDK backend: the provider's process calls Dream's tools through the bridge, here
        directly on the wrapped handler, then answers with the summary."""
        async def ask(self, prompt):
            result = await wrapped.handler({})
            results.append(result)
            failed = bool(result.get("is_error") or result.get("isError"))
            yield Event("tool_result", {"name": "read_notes", "content": str(result.get("content")), "is_error": failed})
            yield Event("assistant_done", "SUMMARY: " + RECAP)

    spent = RunMeter(engine.session_id, 3, engine.profile, config.LOG_DIR / "runtime" / f"{engine.session_id}.jsonl")
    spent.tools = engine.profile.max_run_tools                            # 400 of 400
    engine.runtime_meter = spent
    engine._tool_context = tool_context.ToolContext(engine.store, None, None, engine.session_id, workspace=tmp_path,
                                                    runtime_meter=spent)
    monkeypatch.setattr(tool_context, "_CTX", engine._tool_context)
    engine.backend = CliLikeBackend()
    notes = []
    engine.emit = notes.append
    summary = await engine.consolidate()
    assert summary == RECAP and save_status(engine)["state"] == "saved", save_status(engine)
    result, = results
    assert not result.get("is_error"), result                             # the spent run meter did not refuse it
    bound, = seen
    assert bound is not spent and bound.profile.max_run_tools == engine_module.CONSOLIDATION_TOOL_CALLS
    assert spent.tools == engine.profile.max_run_tools
    assert engine.runtime_meter is spent and engine._tool_context.runtime_meter is spent
    assert [row["tool"] for row in runtime_events(engine, "tool_started")] == ["read_notes"]
    assert notes == []


# --- through Engine.stop(), the close-time path ---------------------------------------------------------------------


async def test_stop_saves_memory_after_a_build_that_spent_the_run_budget(engine, monkeypatch, tmp_path):
    spent = engine.profile.max_run_tools
    meter, calls, notes = wire(engine, [_call_round("read_notes", i) for i in range(3)] + [SUMMARY], spent=spent)
    engine._started = True
    # What Engine.start() leaves for the tools and the hook recorder; the process-wide fallback is the test's own.
    monkeypatch.setattr(tool_context, "_CTX", tool_context.ToolContext(engine.store, None, None, engine.session_id,
                                                                       workspace=tmp_path))
    summary = await engine.stop()
    assert summary == RECAP
    assert engine._memory_save_status["state"] == "saved"
    assert len(calls) == 3 and meter.tools == spent
    assert len(runtime_events(engine, "consolidation_saved")) == 1
    assert notes == []
