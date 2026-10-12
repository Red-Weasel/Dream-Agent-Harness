"""A worker on a local engine with lanes: its reply budget, a reply cut at the limit, and its context window.

The owner's run of 2026-10-02 (Qwen3.8-35B-A3B on Mach X v0.2.8, `ie serve --ctx 262144 --parallel 16`; lanes 1..15
hold 32,768 tokens): two sub-agents wrote exactly 16,384 tokens (reasoning counts) and were cut, though each lane had
about 25,000 tokens of room; Dream returned "(subagent 'explorer' output truncated at the token limit ...)" and the
lead redid the work. The 16,384 was the launch preset's max_tokens (the model card's recommended max output), which
Dream used as every request's ceiling, the workers' included.

Since Mach X v0.2.6 `GET /props` says `slot_ctx`: the positions each lane after the first holds (0 with one lane),
beside default_generation_settings.n_ctx (lane 0, the lead's) and total_slots.

Scripted transports only: no engine, no model, no GPU.
"""
from __future__ import annotations

import copy
import json
import math
import re
from types import SimpleNamespace

import pytest

from dream import config
from dream.core import settings
from dream.core.backends import openai_compat
from test_local_subagents import _PostResp, _backend as sub_backend, _researcher, _sub_final, _sub_toolcall, \
    _tool as sub_tool

N_CTX, SLOT_CTX, PRESET = 262_144, 32_768, 16_384


def _props(slot_ctx=SLOT_CTX, slots=16, n_ctx=N_CTX):
    props = {"default_generation_settings": {"n_ctx": n_ctx}, "total_slots": slots}
    if slot_ctx is not None:
        props["slot_ctx"] = slot_ctx
    return props


class _Engine:
    """Answers a worker from a script of plain replies (the last one repeats); keeps every payload."""

    def __init__(self, *replies):
        self.replies, self.posted = list(replies), []

    async def post(self, url, json=None):
        self.posted.append(copy.deepcopy(json))
        return _PostResp(self.replies[min(len(self.posted), len(self.replies)) - 1])


def _lanes_session(*replies, props=None, result_chars=1600, ran=None, result=None, describe=None):
    """The owner's session: MachX, 16 lanes of 32,768 beside a 262,144 lead, the launch preset's max_tokens 16,384.
    `result`: the k-th call's text, instead of "R<k> yyy..."; `describe`: the tools' description, instead of
    "<name> tool" (both None = the session as it always was)."""
    n = {"k": 0}

    async def search(a):
        if ran is not None:
            ran.append(a)
        n["k"] += 1
        text = f"R{n['k']} " + "y" * result_chars if result is None else result(n["k"])
        return {"content": [{"type": "text", "text": text}]}
    tools = [sub_tool("web_search", search), sub_tool("recall", search)]
    if describe is not None:
        for tool in tools:
            tool.description = describe
    b = sub_backend(tools, _researcher())
    b._local_options = {"max_tokens": PRESET}
    b._server_props = _props() if props is None else props
    b._server_props_model = b.model
    b.n_ctx = b._server_props["default_generation_settings"]["n_ctx"]
    b._client = _Engine(*(replies or (_sub_final("DONE"),)))
    return b


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.setattr(openai_compat, "_COMPACT_AT", None)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 131_072)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None)


def _prompt(payload):
    """A request's size as Dream's admission counts it, tools included."""
    return openai_compat.account(payload["messages"], payload["tools"], SLOT_CTX, 0).input_tokens


# --- 1: a lane worker's reply budget is the room its lane has ------------------------------------------------

async def test_a_lane_workers_reply_budget_is_its_lanes_room_not_the_launch_preset():
    b = _lanes_session()
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("DONE", False)
    sent = b._client.posted[0]
    assert sent["max_tokens"] > PRESET, "the worker was held to the launch preset's 16,384"
    assert sent["max_tokens"] == SLOT_CTX - _prompt(sent) - openai_compat._CTX_MARGIN


async def test_the_budget_follows_the_workers_prompt_and_never_passes_its_lane():
    b = _lanes_session(_sub_toolcall("web_search", {"q": "1"}), _sub_toolcall("web_search", {"q": "2"}),
                       _sub_final("DONE"), result_chars=20_000)
    await b._run_subagent("researcher", "find X")
    budgets = [p["max_tokens"] for p in b._client.posted]
    assert budgets == sorted(budgets, reverse=True) and len(set(budgets)) == 3, budgets
    for sent in b._client.posted:
        assert sent["max_tokens"] + _prompt(sent) + openai_compat._CTX_MARGIN <= SLOT_CTX


def test_the_lead_keeps_its_own_budget():
    b = _lanes_session()
    assert b._max_tokens(b._ctx_fill()) == PRESET
    assert b._admit_request(b.messages + [{"role": "user", "content": "hello"}], b._request_tools()).output == PRESET


@pytest.mark.parametrize("props", [_props(slot_ctx=0), _props(slot_ctx=None), _props(slots=1),
                                   _props(slot_ctx="32768"), _props(slot_ctx=True)],
                         ids=["slot_ctx 0", "no slot_ctx (an engine before v0.2.6)", "one lane", "a string", "a bool"])
async def test_without_a_lane_size_the_worker_is_sized_as_before(props):
    b = _lanes_session(props=props)
    await b._run_subagent("researcher", "find X")
    assert b._client.posted[0]["max_tokens"] == PRESET


async def test_dreams_own_ceiling_and_a_chosen_shorter_mode_still_bound_a_worker(monkeypatch):
    b = _lanes_session()
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 4000)            # /maxtokens 4000, or DREAM_MAX_TOKENS
    await b._run_subagent("researcher", "find X")
    assert b._client.posted[0]["max_tokens"] == 4000
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 131_072)
    for mode, cap in (("quick", 2048), ("balanced", 8192)):
        b = _lanes_session()
        b.performance_status()
        b._performance.select(mode)
        await b.prepare_user_turn()
        await b._run_subagent("researcher", "find X")
        assert b._client.posted[0]["max_tokens"] == cap, mode
    for mode in ("custom", "thorough"):                               # these carry the lead's own ceiling: the preset
        b = _lanes_session()
        b.performance_status()
        b._performance.select(mode)
        await b.prepare_user_turn()
        await b._run_subagent("researcher", "find X")
        assert b._client.posted[0]["max_tokens"] > PRESET, mode


async def test_a_lane_larger_than_the_leads_window_is_held_to_that_window():
    b = _lanes_session(props=_props(slot_ctx=SLOT_CTX, n_ctx=24_000))
    await b._run_subagent("researcher", "find X")
    sent = b._client.posted[0]
    assert sent["max_tokens"] == 24_000 - _prompt(sent) - openai_compat._CTX_MARGIN


# --- 2: a reply cut at the reply limit is asked for again, not failed ----------------------------------------

REPORT_ASK = ("Your reasoning was cut off at the reply limit. Give your final report now, from what you have, "
              "without more reasoning.")
SMALL = 16_384


def _cut(content=None, *, reasoning=None, tool_calls=None):
    """A reply the server cut at max_tokens: finish_reason "length"."""
    message = {"content": content, "tool_calls": tool_calls or []}
    if reasoning:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message, "finish_reason": "length"}]}


def _small_session(*replies, policy=None, **kwargs):
    """One lane of 16,384 tokens: a worker whose window is small enough to fill."""
    b = _lanes_session(*replies, props=_props(slot_ctx=0, slots=1, n_ctx=SMALL), **kwargs)
    b.apply_context_policy({**settings.CONTEXT_DEFAULTS, **(policy or {})})
    return b


def _status_texts(events):
    return [e.data.get("text") or "" for e in events if e.kind == "agent_activity" and e.data["kind"] == "status"]


async def test_a_reply_cut_inside_its_reasoning_is_asked_once_for_its_final_report_without_reasoning():
    b = _lanes_session(_sub_toolcall("web_search", {"q": "1"}), _cut(reasoning="weighing it up " * 40),
                       _sub_final("REPORT: X is in f3."))
    b._sampling["enable_thinking"] = True
    events = []
    b._background_emit = events.append
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("REPORT: X is in f3.", False)
    posted = b._client.posted
    assert len(posted) == 3
    assert posted[2]["messages"][-1] == {"role": "user", "name": "dream_recovery_instruction", "content": REPORT_ASK}
    assert posted[2]["messages"][:-1] == posted[1]["messages"], "the ask is a follow-up in the same worker context"
    assert posted[2]["tools"] == posted[1]["tools"]
    assert [p["enable_thinking"] for p in posted] == [True, True, False]
    assert any("cut at the reply limit" in t and "final report" in t for t in _status_texts(events))
    assert events[-1].data["status"] == "completed"


async def test_without_a_per_request_thinking_switch_the_report_round_gets_a_short_budget():
    b = _lanes_session(_cut(reasoning="weighing it up"), _sub_final("REPORT"))
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("REPORT", False)
    first, second = b._client.posted
    assert "enable_thinking" not in second and second["max_tokens"] == openai_compat._SALVAGE_MAX_TOKENS
    assert first["max_tokens"] > openai_compat._SALVAGE_MAX_TOKENS


async def test_an_answer_cut_at_the_limit_is_continued_where_it_stopped_and_comes_back_whole():
    b = _lanes_session(_cut("The answer begins here and ", reasoning="plan"), _cut("goes on in a second piece and "),
                       _sub_final("ends."))
    b._sampling["enable_thinking"] = True
    events = []
    b._background_emit = events.append
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("The answer begins here and goes on in a second piece and ends.", False)
    posted = b._client.posted
    assert len(posted) == 3
    assert posted[1]["messages"][-2] == {"role": "assistant", "content": "The answer begins here and "}
    ask = posted[1]["messages"][-1]
    assert ask["role"] == "user" and "Continue exactly where you stopped" in ask["content"]
    assert posted[2]["messages"][-2] == {"role": "assistant", "content": "goes on in a second piece and "}
    assert [p["enable_thinking"] for p in posted] == [True, False, False]
    assert posted[1]["max_tokens"] > openai_compat._SALVAGE_MAX_TOKENS, "a continuation keeps the whole budget"
    assert sum("asked it to continue" in t for t in _status_texts(events)) == 2


async def test_an_answer_still_cut_after_two_continuations_comes_back_marked_incomplete_with_all_of_it():
    b = _lanes_session(_cut("part one, "), _cut("part two, "), _cut("part three"), _sub_final("never asked for"))
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed and len(b._client.posted) == 1 + openai_compat._SUB_LENGTH_CONTINUATIONS == 3
    assert out.startswith("(incomplete: still cut off after 2 continuations)") and "treat as incomplete" in out
    assert out.endswith("part one, part two, part three")


async def test_a_report_cut_again_with_no_answer_comes_back_marked_incomplete_after_the_one_ask():
    b = _lanes_session(_cut(reasoning="first"), _cut(reasoning="second"), _sub_final("never asked for"))
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed and len(b._client.posted) == 2
    assert out.startswith("(incomplete: cut off again when asked for its final report)")
    assert out.endswith("It wrote no answer.")


async def test_no_tool_from_a_cut_reply_runs_and_a_cut_call_is_named_in_the_ask():
    ran = []
    call = [{"id": "s1", "function": {"name": "web_search", "arguments": '{"q": "half a ca'}}]
    b = _lanes_session(_cut(None, tool_calls=call), _sub_final("REPORT"), ran=ran)
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("REPORT", False) and ran == []
    ask = b._client.posted[1]["messages"][-1]["content"]
    assert ask.startswith("Your reply was cut off at the reply limit while writing a tool call, so the call did not "
                          "run.") and ask.endswith("Give your final report now, from what you have, without more "
                                                   "reasoning.")


async def test_a_worker_that_goes_back_to_its_tools_after_a_cut_reports_in_its_own_words():
    b = _lanes_session(_cut("Half an answer, "), _sub_toolcall("web_search", {"q": "one more"}),
                       _sub_final("REPORT after one more search."))
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("REPORT after one more search.", False)


@pytest.mark.parametrize("choice", ["return", "compact"])
async def test_a_cut_answer_in_a_full_context_comes_back_as_it_is_marked_incomplete(choice):
    partial = "A long answer. " * 3800                      # about 14,000 tokens: past 80 % of the window with the task
    b = _small_session(_cut(partial), _sub_final("never asked for"), policy={"subagent_overflow": choice})
    events = []
    b._background_emit = events.append
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed and len(b._client.posted) == 1, "no request goes out from a context that is full"
    assert out.startswith("(incomplete: the worker's context is full)") and out.endswith(partial.strip())
    assert any("context is full" in t for t in _status_texts(events))


async def test_under_compact_older_tool_results_make_room_and_the_answer_is_continued():
    partial = "A long answer. " * 400                       # about 1,500 tokens
    b = _small_session(_sub_toolcall("web_search", {"q": "1"}), _sub_toolcall("web_search", {"q": "2"}),
                       _cut(partial), _sub_final("The end."), policy={"subagent_overflow": "compact"},
                       result_chars=22_000)
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == (partial + "The end.", False)
    last = b._client.posted[-1]["messages"]
    assert any(str(m.get("content")).startswith("[elided:") for m in last if m.get("role") == "tool")
    assert {"role": "assistant", "content": partial} in last, "the answer so far stays in the context it continues"


async def test_under_handoff_a_full_context_goes_on_in_a_fresh_one_and_reports_from_there():
    partial = "A long answer. " * 3800
    b = _small_session(_cut(partial), _sub_final("Progress: half the answer is written."), _sub_final("DONE"),
                       policy={"subagent_overflow": "handoff"})
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("DONE", False)
    fresh = b._client.posted[-1]["messages"]
    assert len(fresh) == 2 and "automatic Handoff 1" in fresh[1]["content"]


async def test_a_check_inside_the_leads_conversation_keeps_its_cut_result():
    b = _lanes_session(_cut("partial verdict"), _sub_final("never asked for"))
    out, failed = await b._subagent_loop(b._subagents["researcher"], "researcher", "check it", continuation=True)
    assert failed and len(b._client.posted) == 1
    assert out.startswith("(subagent 'researcher' output truncated at the token limit")


@pytest.mark.parametrize("name", ["verifier", "filer"])
async def test_the_verifier_and_the_filer_keep_their_cut_result(name):
    from dream.core.subagents import LocalSubagentSpec

    async def look(a):
        return {"content": [{"type": "text", "text": "seen"}]}
    inspection = ("show_html", "get_webview_logs", "save_screenshot", "see")
    b = openai_compat.OpenAICompatBackend(
        provider=SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=True,
                                 api_key=lambda: "n"),
        model="m", system_prompt="s", tools=[sub_tool(n, look) for n in inspection], permission_cb=None,
        subagents={name: LocalSubagentSpec(name=name, description=name, prompt="p", tool_names=inspection)})
    b._client = _Engine(_cut("partial"), _sub_final("never asked for"))
    out, failed = await b._run_subagent(name, "do it")
    assert failed and len(b._client.posted) == 1
    assert out.startswith(f"(subagent '{name}' output truncated at the token limit")


async def test_a_streamed_worker_cut_in_its_reasoning_is_asked_again_the_same_way():
    from test_nested_worker_streaming import _delta, _finish, _text_stream, streaming_worker
    cut = (200, [_delta(role="assistant"), _delta(reasoning_content="weighing it up"), *_finish("length")], b"")
    events = []
    b = streaming_worker(events, [cut, _text_stream("REPORT: X is in f3.")])
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("REPORT: X is in f3.", False)
    assert len(b._client.streamed) == 2 and b._client.streamed[1]["messages"][-1]["content"] == REPORT_ASK


async def test_the_runtime_log_says_each_cut_and_what_was_done():
    from test_cache_friendly_head import Meter
    b = _lanes_session(_cut(reasoning="first"), _cut("half an answer, "), _sub_final("the rest."))
    b.runtime_meter = Meter()
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("half an answer, the rest.", False)
    assert [f for e, f in b.runtime_meter.records if e == "subagent_length"] == [
        {"agent": "researcher", "action": "report", "cut": "reasoning"},
        {"agent": "researcher", "action": "continue", "cut": "answer"}]
    b = _small_session(_cut("A long answer. " * 3800))
    b.runtime_meter = Meter()
    await b._run_subagent("researcher", "find X")
    assert [f for e, f in b.runtime_meter.records if e == "subagent_length"][-1] == {
        "agent": "researcher", "cut": "answer", "action": "full"}


# --- 3: a lane worker's context window is its lane (the engine's /props slot_ctx) ----------------------------

from test_context_management import _SubEngine          # noqa: E402  (a worker that keeps searching)


def _busy_lane_session(policy=None, *, rounds=40, props=None, result_chars=6000):
    """A worker that makes `rounds` searches of about 1,500 tokens each, then says DONE."""
    b = _lanes_session(props=props, result_chars=result_chars)
    b.apply_context_policy({**settings.CONTEXT_DEFAULTS, **(policy or {})})
    b._client = _SubEngine(rounds=rounds)
    return b


def _work(b):
    return [p for p in b._client.posted if "STOP." not in str(p["messages"][-1].get("content"))]


def _windows(events):
    return {e.data["context"]["window"] for e in events if e.kind == "agent_activity" and "context" in e.data}


async def test_a_lane_workers_card_shows_its_lane_not_the_leads_window():
    b = _lanes_session()
    events = []
    b._background_emit = events.append
    await b._run_subagent("researcher", "find X")
    assert _windows(events) == {SLOT_CTX}
    assert b._window() == N_CTX, "the lead keeps n_ctx"


async def test_a_lane_worker_is_warned_and_returns_at_the_trigger_of_its_lane():
    b = _busy_lane_session()
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed and out.startswith("(subagent 'researcher' stopped at about ")
    assert "behaviour.subagent_overflow = return" in out
    assert all(openai_compat._est_tokens(p["messages"]) <= 0.80 * SLOT_CTX for p in _work(b))
    warned = [m["content"] for p in _work(b) for m in p["messages"] if "context warning" in str(m.get("content"))]
    assert warned and "at 80% Dream will stop this task" in warned[0]


async def test_under_compact_a_lane_worker_stays_inside_its_lane_and_finishes():
    b = _busy_lane_session({"subagent_overflow": "compact"})
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("DONE", False)
    assert all(openai_compat._est_tokens(p["messages"]) <= 0.80 * SLOT_CTX for p in _work(b))
    assert any(str(m.get("content")).startswith("[elided:") for m in _work(b)[-1]["messages"])


async def test_a_task_that_alone_fills_its_lane_does_not_go_on_and_names_the_lanes_size():
    b = _lanes_session()
    out, failed = await b._run_subagent("researcher", "look at this\n" + "w" * 120_000)
    assert failed and b._client.posted == [] and "32,768-token context window" in out


@pytest.mark.parametrize("props", [_props(slot_ctx=0), _props(slot_ctx=None), _props(slots=1)],
                         ids=["slot_ctx 0", "no slot_ctx (an engine before v0.2.6)", "one lane"])
async def test_without_a_lane_size_a_workers_window_is_the_leads_as_before(props):
    b = _busy_lane_session(props=props)
    events = []
    b._background_emit = events.append
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("DONE", False) and _windows(events) == {N_CTX}


async def test_a_cut_answer_that_filled_its_lane_comes_back_marked_and_nothing_more_is_sent():
    partial = "A long answer. " * 6400                      # about 24,000 tokens: the lane's room, used up
    b = _lanes_session(_cut(partial), _sub_final("never asked for"))
    out, failed = await b._run_subagent("researcher", "find X")
    assert failed and len(b._client.posted) == 1
    assert out.startswith("(incomplete: the worker's context is full)") and out.endswith(partial.strip())


def test_the_owner_is_told_what_a_lane_holds_as_the_engine_reports_it():
    b = _lanes_session()
    note, = b._lanes_notes()
    assert note.startswith("Dream's own note: MachX serves 16 lanes")
    assert note.endswith("Each lane after the first holds 32,768 tokens (the engine's /props slot_ctx): that is a "
                         "sub-agent's context window.")
    b._local_options["slot_ctx"] = 65_536                    # the load screen asked for more than the engine gave
    note, = b._lanes_notes()
    assert "holds 32,768 tokens (the engine's /props slot_ctx, not the 65,536 this launch asked for)" in note
    b._local_options["slot_ctx"] = SLOT_CTX
    assert "not the" not in b._lanes_notes()[0]
    assert "slot_ctx" not in _lanes_session(props=_props(slot_ctx=None))._lanes_notes()[0]


# --- 4: the task tool tells the lead how large a worker's context is ------------------------------------------

# The `task` tool's description as it was before this sentence, word for word: {runs} is how many run at once and
# {context} is the new sentence's place (empty wherever the lane's size is not known: the prompt's head is unchanged).
TASK_TOOL = ("Delegate a scoped, self-contained subtask to a specialist subagent that runs in its OWN isolated context "
             "on this engine and returns a summary. {runs} on this engine; send as many task calls as the job needs: "
             "the rest wait their turn and start as others finish.{context} Use this to keep your own context clean on "
             "repetitive or research-heavy work — the subagent does the messy gathering and hands back only the "
             "result.\nAvailable subagents:\n- researcher: web research")


def _task_text(b):
    b._refresh_task_schema()                                 # as connect() does once the engine's /props is read
    [schema] = [s for s in b.tool_schemas if s["function"]["name"] == "task"]
    return schema["function"]["description"]


def test_the_task_tool_says_how_large_a_workers_context_is_on_its_lane():
    assert _task_text(_lanes_session()) == TASK_TOOL.format(
        runs="Up to 16 subagents run at once",
        context=" Each has a context of about 32,768 tokens: give it a task that fits (a few files), and ask for a "
                "short report.")
    small = _lanes_session(props=_props(slot_ctx=65_536, n_ctx=40_000))     # never above the lead's own window
    assert " Each has a context of about 40,000 tokens: " in _task_text(small)


@pytest.mark.parametrize("props, runs", [(_props(slot_ctx=None), "Up to 16 subagents run at once"),
                                         (_props(slot_ctx=0), "Up to 16 subagents run at once"),
                                         (_props(slots=1), "One subagent runs at a time")],
                         ids=["no slot_ctx (an engine before v0.2.6)", "slot_ctx 0", "one lane"])
def test_without_a_lane_size_the_task_tool_is_word_for_word_as_before(props, runs):
    assert _task_text(_lanes_session(props=props)) == TASK_TOOL.format(runs=runs, context="")


def test_before_the_engine_has_said_anything_the_task_tool_is_word_for_word_as_before():
    b = sub_backend([], _researcher())                       # no /props read: a cloud endpoint, or before connect()
    [schema] = [s for s in b.tool_schemas if s["function"]["name"] == "task"]
    assert schema["function"]["description"] == TASK_TOOL.format(runs="One subagent runs at a time", context="")
    assert _task_text(b) == schema["function"]["description"]


# --- 5: DREAM-252 in the lane: one large tool result is cut to fit, and the worker's next reply still has room ----
#
# The whole path, on the session above (a 32,768 lane beside the 262,144 lead): the worker's first request, one
# tool call whose result is far larger than the lane, the result as it enters the worker's history, the second
# request, the final answer. Every outgoing payload is re-counted here with the counter admission itself uses
# (backend._calibrated_counter, so a calibration other than 1.0 is counted too) and the max_tokens actually sent.

CAP = 24_000                                                 # config.TOOL_RESULT_CAP's default, pinned below
FIRST_LINE = "R1 FIRST LINE: première ligne — 最初の行 — первая строка\n"
# 4 UTF-8 bytes a character: the 24,000 characters _clamp keeps are about 27,400 tokens as admission counts them
# (bytes / 3.5), past 80 % of the lane (26,214) on their own, though only 6,000 at chars/4.
BIG_RESULT = FIRST_LINE + "🎼" * 60_000
# Tool descriptions are part of every request (the schemas), in bytes: non-ASCII ones cost more than they look.
DESCRIPTION = "检索工具 — recherche détaillée dans les sources 🎼. " * 40
CUT_RE = re.compile(r"\A(.*)\n\n\[\.\.\. (\d+) chars elided\]\n\[Dream: this result was cut to leave room in this "
                    r"worker's context window for its next reply\. Ask for a narrower range to read the rest\.\]",
                    re.DOTALL)
OVERFLOW_CHOICES = ["return", "compact", "handoff"]


def _full_account(b, payload, window=SLOT_CTX):
    """One sent payload, counted whole as admission counts it: messages, schemas, the max_tokens it asks for and
    the margin, with the session's calibrated counter and method."""
    counter, method = b._calibrated_counter()
    return openai_compat.account(payload["messages"], payload["tools"], window, payload["max_tokens"],
                                 counter=counter, method=method)


def _big_read_session(choice, calib, monkeypatch, *, result=BIG_RESULT):
    monkeypatch.setattr(openai_compat, "_TOOL_RESULT_CAP", CAP)
    b = _lanes_session(_sub_toolcall("web_search", {"q": "1"}), _sub_final("DONE"),
                       result=lambda k: result, describe=DESCRIPTION)
    b.apply_context_policy({**settings.CONTEXT_DEFAULTS, "subagent_overflow": choice})
    b._calib = calib
    return b


def _no_recovery_or_handoff(posted):
    for payload in posted:
        for m in payload["messages"]:
            text = str(m.get("content"))
            assert m.get("name") != "dream_recovery_instruction", "a recovery / report request went out"
            assert "automatic Handoff" not in text and "STOP." not in text
            assert not (m.get("role") == "tool" and text.startswith("[elided:")), "the observation was stubbed away"


@pytest.mark.parametrize("calib", [1.0, 1.25], ids=["uncalibrated", "calibrated x1.25"])
@pytest.mark.parametrize("choice", OVERFLOW_CHOICES)
async def test_a_large_result_in_a_lane_is_cut_to_fit_and_the_worker_answers_in_two_requests(choice, calib, monkeypatch):
    b = _big_read_session(choice, calib, monkeypatch)
    assert b._lane_ctx() == SLOT_CTX and b._window() == N_CTX
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("DONE", False)
    posted = b._client.posted
    assert len(posted) == 2, "two ordinary requests: the task, then the answer from what was read"
    _no_recovery_or_handoff(posted)
    first, second = posted
    assert [m["role"] for m in second["messages"]] == ["system", "user", "assistant", "tool"]

    # The observation: its first characters as the tool returned them, then what was left out, counted once.
    observation = second["messages"][3]["content"]
    cut = CUT_RE.match(observation)
    assert cut, observation[-400:]
    kept, elided = cut.group(1), int(cut.group(2))
    assert kept.startswith(FIRST_LINE) and BIG_RESULT.startswith(kept)
    assert len(FIRST_LINE) < len(kept) < CAP, "this result needed a cut below the cap, and kept more than its head"
    assert len(kept) + elided == len(BIG_RESULT), "the count is of the whole result, _clamp's part added once"
    assert observation.count("chars elided]") == 1

    # Without the cut the same request would not have fitted the lane: the case is the one DREAM-252 is about.
    counter, method = b._calibrated_counter()
    assert (method is None) == (calib == 1.0)
    limit = math.floor(b._trigger_at(SLOT_CTX) * SLOT_CTX)
    whole = copy.deepcopy(second["messages"])
    whole[3]["content"] = openai_compat._clamp(BIG_RESULT, CAP)
    assert openai_compat.account(whole, second["tools"], SLOT_CTX, 0, counter=counter,
                                 method=method).input_tokens > limit

    # Only that content changed: the call's id and arguments, the schemas and the older messages are as they were.
    assert second["messages"][:2] == first["messages"]
    assert second["messages"][3]["tool_call_id"] == "s1"
    [call] = second["messages"][2]["tool_calls"]
    assert call["id"] == "s1" and call["function"]["name"] == "web_search"
    assert json.loads(call["function"]["arguments"]) == {"q": "1"}
    assert second["tools"] == first["tools"]
    assert DESCRIPTION in json.dumps(first["tools"], ensure_ascii=False), "the non-ASCII descriptions are sent"

    # Every payload, whole: input + the max_tokens asked for + the margin stay inside the lane.
    for payload in posted:
        report = _full_account(b, payload)
        assert report.window == SLOT_CTX and report.output == payload["max_tokens"] >= 1
        assert report.margin == openai_compat._CTX_MARGIN
        assert report.remaining >= 0, report.as_dict()
    after_cut = _full_account(b, second)
    assert after_cut.input_tokens <= limit, "past the trigger line"
    assert second["max_tokens"] >= b._worker_reply_reserve(b._lane_ctx()), "the reply the cut made room for"


@pytest.mark.parametrize("choice", OVERFLOW_CHOICES)
async def test_a_result_that_fits_its_lane_enters_whole_under_a_calibrated_count(choice, monkeypatch):
    fitting = "R1 " + "y" * 20_000                           # about 7,150 calibrated tokens: well inside the lane
    b = _big_read_session(choice, 1.25, monkeypatch, result=fitting)
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("DONE", False)
    posted = b._client.posted
    assert len(posted) == 2
    _no_recovery_or_handoff(posted)
    assert posted[1]["messages"][3] == {"role": "tool", "tool_call_id": "s1", "content": fitting}
    assert posted[1]["tools"] == posted[0]["tools"]
    for payload in posted:
        assert _full_account(b, payload).remaining >= 0


@pytest.mark.parametrize("calib", [1.0, 1.25], ids=["uncalibrated", "calibrated x1.25"])
@pytest.mark.parametrize("choice", OVERFLOW_CHOICES)
async def test_the_leads_budgeting_is_unchanged_by_a_lane_worker_that_was_cut_to_fit(choice, calib, monkeypatch):
    b = _big_read_session(choice, calib, monkeypatch)
    lead_history, lead_tools = copy.deepcopy(b.messages), copy.deepcopy(b._request_tools())
    before = b._admit_request(b.messages + [{"role": "user", "content": "hello"}], b._request_tools())
    out, failed = await b._run_subagent("researcher", "find X")
    assert (out, failed) == ("DONE", False)
    assert b.messages == lead_history, "the worker's rounds stay out of the lead's history"
    assert b._request_tools() == lead_tools
    assert b._window() == N_CTX and b.n_ctx == N_CTX and b._calib == calib
    assert b._max_tokens(b._ctx_fill()) == PRESET, "the lead keeps the launch preset's ceiling"
    after = b._admit_request(b.messages + [{"role": "user", "content": "hello"}], b._request_tools())
    assert (after.window, after.output) == (N_CTX, PRESET)
    assert after.as_dict() == before.as_dict()


# --- 6: _cut_observation on a tail it did not write --------------------------------------------------------

CUT_SAYS = ("[Dream: this result was cut to leave room in this worker's context window for its next reply. Ask for "
            "a narrower range to read the rest.]")


@pytest.mark.parametrize("digits", ["9" * 5000, "1" + "0" * 100_000],
                         ids=["5,000 digits (past Python's int-from-str limit)", "100,001 digits"])
def test_a_result_ending_in_an_elision_marker_with_a_huge_count_is_cut_without_a_crash(digits):
    # A tool's own text may end the way _clamp's tail does, with any number in it: that is data, not Dream's count.
    text = "HEAD " + "x" * 500 + f"\n\n[... {digits} chars elided]"
    out = openai_compat._cut_observation(text, 100)
    assert isinstance(out, str) and out.startswith(text[:100]) and out.endswith(CUT_SAYS)


@pytest.mark.parametrize("keep", [-5, 0, 10**9])
def test_a_cut_asked_for_outside_the_result_is_held_to_it(keep):
    text = "HEAD " + "x" * 500
    out = openai_compat._cut_observation(text, keep)
    kept = text[:max(0, min(keep, len(text)))]
    assert out == f"{kept}\n\n[... {len(text) - len(kept)} chars elided]\n{CUT_SAYS}"
