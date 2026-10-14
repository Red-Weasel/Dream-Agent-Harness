"""DREAM-258 phase 2: description-first discovery on the OpenAI-compatible backend's own loop.

Driven like tests/test_schema_deferral.py: a fake client serves scripted SSE rounds and keeps a snapshot of every
payload, so what is asserted is what would have been sent and what really ran.

Held here: outside the mode nothing changes, byte for byte, and an unknown value is said rather than applied; in
the mode the tools array is fixed (window, toolset size, lookups and calls do not move it) and a definition
arrives only in a lookup's result; a search, then a definition, then a call through the wrapper reaches the real
handler through validation and permission under the model's own call ids; and everything that should not run --
unknown, disabled, out of scope, a control, malformed, declined, stopped -- does not.

Synthetic throughout: no model, no engine, no tokenizer. Nothing here qualifies the mode on a model.
"""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from dream import extensions
from dream.core import discovery, system_prompt
from dream.core import tool_budget_schemas as tbs
from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.skills import loader, selection

LOOKUP, INVOKE = tbs.LOOKUP_TOOL_NAME, discovery.INVOKE_TOOL_NAME
_DEEP_WORD = "perigee"


def _started_with(monkeypatch, value):
    """As if the process had started with DREAM_DISCOVERY_MODE=`value` (None: unset): the variable is set and the
    startup decision is taken from it. Dream itself takes that decision once, at import, and never again; a
    later monkeypatch.setenv / delenv on its own is "the variable changed while Dream runs"."""
    if value is None:
        monkeypatch.delenv(discovery.MODE_ENV, raising=False)
    else:
        monkeypatch.setenv(discovery.MODE_ENV, value)
    monkeypatch.setattr(discovery, "_STARTUP", discovery.read_environment())


@pytest.fixture(autouse=True)
def _no_ambient_mode(monkeypatch):
    # Unset is description discovery since 2026-10-13; the tests of the other path name it explicitly.
    _started_with(monkeypatch, "default")


def _catalog_entries(description):
    """The tool names a catalog lists: every line under `Available:` is one tool except a bracketed notice."""
    body = description.split("Available:\n", 1)[1] if "Available:\n" in description else ""
    return [line.split(" — ", 1)[0] for line in body.splitlines() if discovery.is_entry(line)]


def _tool(name, calls, description=None, required=()):
    async def handler(args):
        calls.append((name, args))
        return {"content": [{"type": "text", "text": f"{name} ran"}]}

    props = {key: {"type": "string", "description": "an argument"} for key in ("p0", "p1", *required)}
    return SimpleNamespace(
        name=name, description=description or f"{name} does a thing. And then some more prose.",
        input_schema={"type": "object", "properties": props, "required": list(required)}, handler=handler)


def _toolset(calls, n=30):
    """`n` fillers and one external-style tool whose telling word sits far past any preview."""
    moon = _tool("ext__moon_phase", calls, required=("date",),
                 description="[ext · external MCP] Reports on the sky. " + "It is thorough. " * 40
                             + f"Includes the {_DEEP_WORD} distance.")
    return [_tool(f"custom_{i:04d}", calls) for i in range(n)] + [moon]


def _backend(tools, mode=None, n_ctx=16384, subagents=None) -> OpenAICompatBackend:
    provider = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=False,
                               api_key=lambda: "n")
    backend = OpenAICompatBackend(provider=provider, model="m", system_prompt="SYSTEM PROMPT", tools=list(tools),
                                  permission_cb=None, subagents=subagents, discovery_mode=mode)
    backend.n_ctx = n_ctx
    return backend


class _FakeResponse:
    def __init__(self, lines):
        self.status_code = 200
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _FakeStream:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResponse(self._lines)

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.payloads = []

    def stream(self, method, url, json=None):
        self.payloads.append(copy.deepcopy(json))
        return _FakeStream(self._rounds[min(len(self.payloads) - 1, len(self._rounds) - 1)])


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


def _text_round(text="done"):
    return [_sse({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}), "data: [DONE]"]


def _call_round(name, args, call_id="c1"):
    raw = args if isinstance(args, str) else json.dumps(args)
    return [_sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": call_id, "function": {"name": name, "arguments": raw}}]},
                "finish_reason": None}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}), "data: [DONE]"]


async def _run(backend, rounds, prompt="go"):
    backend._client = _FakeClient(rounds)
    return [event async for event in backend.ask(prompt)]


def _names(payload):
    return [schema["function"]["name"] for schema in payload["tools"]]


def _results(events):
    return [event.data for event in events if event.kind == "tool_result"]


def _system(events):
    return [str(event.data) for event in events if event.kind == "system"]


def _subtype(events):
    return [event for event in events if event.kind == "result"][-1].data["subtype"]


# --- outside the mode: nothing moves ------------------------------------------------


async def test_the_default_payload_is_the_same_bytes_however_the_default_is_reached(monkeypatch):
    calls: list = []
    plain = _backend(_toolset(calls))                             # started with DREAM_DISCOVERY_MODE=default
    explicit = _backend(_toolset(calls), mode="default")
    _started_with(monkeypatch, "off")                             # turned off another way
    off = _backend(_toolset(calls))
    _started_with(monkeypatch, "descriptons")                     # started with a typo: said; the default stays on
    typo = _backend(_toolset(calls))
    _started_with(monkeypatch, "default")

    plain_events = await _run(plain, [_text_round()])
    await _run(explicit, [_text_round()])
    await _run(off, [_text_round()])
    typo_events = await _run(typo, [_text_round()])

    sent = plain._client.payloads[0]
    assert json.dumps(explicit._client.payloads[0], sort_keys=True) == json.dumps(sent, sort_keys=True)
    assert json.dumps(off._client.payloads[0], sort_keys=True) == json.dumps(sent, sort_keys=True)
    assert INVOKE not in _names(sent)
    assert not any(discovery.MODE_ENV in line for line in _system(plain_events))
    assert _names(typo._client.payloads[0]) == [LOOKUP, INVOKE]
    said = [line for line in _system(typo_events) if "descriptons" in line]
    assert len(said) == 1 and discovery.MODE_ENV in said[0] and "stays on" in said[0]
    assert not typo.discovery_report()["qualified"] and typo.discovery_report()["within_target"] is None


async def test_outside_the_mode_the_wrapper_is_not_a_tool(monkeypatch):
    calls: list = []
    backend = _backend(_toolset(calls))
    events = await _run(backend, [_call_round(INVOKE, {"name": "custom_0000", "arguments": {"p0": "x"}}),
                                  _text_round()])
    [result] = _results(events)
    assert result["is_error"] is True and "unknown tool" in result["content"]
    assert calls == []


async def test_a_registered_tool_with_a_reserved_name_keeps_the_default_and_says_so():
    calls: list = []
    backend = _backend(_toolset(calls) + [_tool(INVOKE, calls)], mode="descriptions")
    reference = _backend(_toolset(calls) + [_tool(INVOKE, calls)])
    events = await _run(backend, [_text_round()])
    await _run(reference, [_text_round()])
    assert backend._client.payloads[0]["tools"] == reference._client.payloads[0]["tools"]
    assert any("was not applied" in line for line in _system(events))


# --- the fixed, bounded array ---------------------------------------------------------


async def test_the_array_is_the_lookup_and_the_wrapper_whatever_the_window_or_toolset_size():
    calls: list = []
    payloads = {}
    for label, n, window in (("small", 30, 16_384), ("wide", 30, 1_000_000), ("many", 3000, 16_384)):
        backend = _backend(_toolset(calls, n), mode="descriptions", n_ctx=window)
        await _run(backend, [_text_round()])
        payloads[label] = backend._client.payloads[0]["tools"]
        assert _names(backend._client.payloads[0]) == [LOOKUP, INVOKE]
    assert payloads["small"] == payloads["wide"], "the window does not size it"
    grown = len(json.dumps(payloads["many"])) - len(json.dumps(payloads["small"]))
    assert abs(grown) < discovery.CATALOG_CHARS + 400, "a hundred times the tools grows it only up to the catalog's bound"
    catalog = payloads["many"][0]["function"]["description"]
    listed = _catalog_entries(catalog)
    assert listed and set(listed) <= {f"custom_{i:04d}" for i in range(3000)} | {"ext__moon_phase"}
    assert len(listed) == len(set(listed)) and listed == [f"custom_{i:04d}" for i in range(len(listed))]
    notice = catalog.splitlines()[-1]
    assert notice == discovery.omission_notice(3001 - len(listed)) and "more not listed" in notice
    assert not discovery.is_entry(notice) and " — " not in notice, "the count never reads as a tool"
    # the small toolset's catalog, read the same way, names real tools only
    small_catalog = payloads["small"][0]["function"]["description"]
    assert set(_catalog_entries(small_catalog)) <= {f"custom_{i:04d}" for i in range(30)} | {"ext__moon_phase"}
    assert "enum" not in json.dumps(payloads["many"]), "no list of every name rides along"
    assert '"date"' not in json.dumps(payloads["many"]), "no tool's parameters are in the initial array"


async def test_the_controls_the_loop_needs_are_sent_whole_and_answered_as_direct():
    calls: list = []
    subs = {"researcher": SimpleNamespace(description="digs things up", prompt="p", tool_names=["custom_0000"])}
    backend = _backend(_toolset(calls), mode="descriptions", subagents=subs)
    events = await _run(backend, [_call_round(LOOKUP, {"name": "task"}), _text_round()])
    sent = backend._client.payloads[0]["tools"]
    assert "task" in _names(backend._client.payloads[0])
    def structural(value):
        if isinstance(value, dict):
            return {k: structural(v) for k, v in value.items() if k != "description"}
        if isinstance(value, list):
            return [structural(v) for v in value]
        return value
    # Only prose is compacted; every argument type, enum and output requirement is retained.
    actual = next(s for s in sent if s["function"]["name"] == "task")
    assert structural(actual) == structural(backend._task_schema())
    assert "isolated context" in actual["function"]["description"]
    assert "absent, unchanged or invalid" in actual["function"]["parameters"]["properties"]["outputs"]["description"]
    [result] = _results(events)
    assert result["is_error"] is False and "directly" in result["content"]


# --- search -> definition -> call -----------------------------------------------------


async def test_a_tool_found_by_a_deep_word_is_defined_on_demand_and_then_really_runs():
    calls: list = []
    backend = _backend(_toolset(calls, 3000), mode="descriptions")
    events = await _run(backend, [
        _call_round(LOOKUP, {"search": _DEEP_WORD}, "c1"),
        _call_round(LOOKUP, {"name": "ext__moon_phase"}, "c2"),
        _call_round(INVOKE, {"name": "ext__moon_phase", "arguments": {"date": "2026-10-05"}}, "c3"),
        _text_round("the moon is waning"),
    ])
    assert _subtype(events) == "success"
    payloads = backend._client.payloads
    assert "ext__moon_phase — " not in payloads[0]["tools"][0]["function"]["description"], \
        "the premise: the tool had no catalog line and its word was past any preview"

    found, defined, ran = _results(events)
    assert found["is_error"] is False and "ext__moon_phase" in found["content"]
    assert _DEEP_WORD not in found["content"], "a search answers with the short description"
    assert defined["is_error"] is False
    definition = json.loads(defined["content"].split("\n", 1)[1])
    assert definition["name"] == "ext__moon_phase" and definition["parameters"]["required"] == ["date"]
    assert _DEEP_WORD in definition["description"], "the whole definition, not a summary"
    assert ran["is_error"] is False and ran["content"] == "ext__moon_phase ran"
    assert calls == [("ext__moon_phase", {"date": "2026-10-05"})], "the real handler, the inner arguments, once"

    # ids: each result answers the model's own call, in the events and in the history
    assert [r["id"] for r in (found, defined, ran)] == ["c1", "c2", "c3"]
    history = backend.messages
    assert [m["tool_call_id"] for m in history if m.get("role") == "tool"] == ["c1", "c2", "c3"]
    wrote = [call["function"]["name"] for m in history for call in m.get("tool_calls") or []]
    assert wrote == [LOOKUP, LOOKUP, INVOKE], "the history keeps what the model wrote"
    shown = [event.data["name"] for event in events if event.kind == "tool_use"]
    assert shown == [LOOKUP, LOOKUP, "ext__moon_phase"], "the owner is shown the tool that runs"

    # the head never moved: same tools and system text in every request; the definition lives in the tail
    assert all(p["tools"] == payloads[0]["tools"] for p in payloads)
    assert all(p["messages"][0] == payloads[0]["messages"][0] for p in payloads)
    tail = [m for m in payloads[3]["messages"] if m.get("role") == "tool" and '"required": ["date"]' in m["content"]]
    assert len(tail) == 1 and backend._revealed == set()


async def test_a_real_name_called_directly_still_takes_the_ordinary_path():
    calls: list = []
    backend = _backend(_toolset(calls), mode="descriptions")
    events = await _run(backend, [_call_round("custom_0003", {"p0": "x"}), _text_round()])
    assert _results(events)[0]["is_error"] is False and calls == [("custom_0003", {"p0": "x"})]


# --- what must not run ------------------------------------------------------------------


@pytest.mark.parametrize("args, says", [
    ({"name": "no_such_tool", "arguments": {}}, "unknown tool"),
    ({"name": INVOKE, "arguments": {"name": "custom_0000", "arguments": {}}}, "control"),
    ({"name": "task", "arguments": {"subagent_type": "researcher", "prompt": "do it"}}, "control"),
    ({"name": LOOKUP, "arguments": {"name": "custom_0000"}}, "control"),
    ({"name": "snip", "arguments": {"from_id": "m0001", "to_id": "m0001"}}, "control"),
    ({"name": "custom_0000", "p0": "x"}, "inside `arguments`"),
    ({"name": "custom_0000", "arguments": "{not json"}, "JSON object"),
    ({"name": "custom_0000", "arguments": [1, 2]}, "JSON object"),
    ({"arguments": {"p0": "x"}}, "needs `name`"),
    ({"name": "ext__moon_phase", "arguments": {}}, "Invalid arguments"),
    ('{"name": "custom_0000", "arguments": {', "not valid JSON"),
])
async def test_a_call_that_should_not_run_is_refused_and_the_turn_goes_on(args, says):
    calls: list = []
    subs = {"researcher": SimpleNamespace(description="digs things up", prompt="p", tool_names=["custom_0000"])}
    backend = _backend(_toolset(calls), mode="descriptions", subagents=subs)
    events = await _run(backend, [_call_round(INVOKE, args), _text_round()])
    [result] = _results(events)
    assert result["is_error"] is True and says in result["content"], result["content"]
    assert result["id"] == "c1" and calls == []
    assert _subtype(events) == "success" and len(backend._client.payloads) == 2
    assert backend._client.payloads[1]["tools"] == backend._client.payloads[0]["tools"]


@pytest.mark.parametrize("lookup", [{"name": "no_such_tool"}, {}])
async def test_a_lookup_that_names_nothing_real_is_an_error_not_a_definition(lookup):
    calls: list = []
    backend = _backend(_toolset(calls), mode="descriptions")
    events = await _run(backend, [_call_round(LOOKUP, lookup), _text_round()])
    assert _results(events)[0]["is_error"] is True
    missed = await _run(backend, [_call_round(LOOKUP, {"search": "zzzznothing"}), _text_round()])
    assert "No enabled tool matches" in _results(missed)[0]["content"]


async def test_a_disabled_tool_is_not_described_found_defined_or_run(monkeypatch):
    calls: list = []
    off = "custom_0001"
    monkeypatch.setattr(extensions, "filter_tools", lambda tools: [t for t in tools if t.name != off])
    monkeypatch.setattr(extensions, "tool_enabled", lambda tool: tool.name != off)
    backend = _backend(_toolset(calls, 5), mode="descriptions")
    events = await _run(backend, [
        _call_round(LOOKUP, {"search": off}, "c1"),
        _call_round(LOOKUP, {"name": off}, "c2"),
        _call_round(INVOKE, {"name": off, "arguments": {"p0": "x"}}, "c3"),
        _text_round(),
    ])
    catalog = backend._client.payloads[0]["tools"][0]["function"]["description"]
    assert "custom_0000 — " in catalog and f"{off} — " not in catalog
    found, defined, ran = _results(events)
    assert off not in found["content"].replace(repr(off), "")
    assert defined["is_error"] is True and "disabled" in defined["content"] and "parameters" not in defined["content"]
    assert ran["is_error"] is True and "disabled" in ran["content"]
    assert calls == []


async def test_a_scoped_ask_holds_the_wrapper_to_its_scope():
    calls: list = []
    backend = _backend(_toolset(calls, 5), mode="descriptions")
    backend.tool_scope = frozenset({"custom_0000"})
    events = await _run(backend, [
        _call_round(INVOKE, {"name": "custom_0004", "arguments": {"p0": "x"}}, "c1"),
        _call_round(LOOKUP, {"name": "custom_0004"}, "c2"),
        _text_round(),
    ])
    assert _names(backend._client.payloads[0]) == ["custom_0000"], "a scoped ask offers its own tools, whole"
    outside, lookup = _results(events)
    assert outside["is_error"] is True and outside.get("refused") is True
    assert lookup["is_error"] is True and lookup.get("refused") is True
    assert calls == []


async def test_permission_is_asked_for_the_real_tool_and_a_no_runs_nothing():
    calls: list = []
    asked: list = []

    async def decline(name, args):
        asked.append((name, args))
        return False

    backend = _backend(_toolset(calls), mode="descriptions")
    backend.permission_cb = decline
    events = await _run(backend, [
        _call_round(INVOKE, {"name": "ext__moon_phase", "arguments": {"date": "2026-10-05"}}), _text_round()])
    assert asked == [("ext__moon_phase", {"date": "2026-10-05"})], "the prompt names the tool, not the wrapper"
    [result] = _results(events)
    assert result["is_error"] is True and "Declined" in result["content"]
    assert calls == []


async def test_a_yes_runs_the_real_handler_once_with_the_inner_arguments():
    calls: list = []
    asked: list = []

    async def approve(name, args):
        asked.append(name)
        return True

    backend = _backend(_toolset(calls), mode="descriptions")
    backend.permission_cb = approve
    events = await _run(backend, [
        _call_round(INVOKE, {"name": "ext__moon_phase", "arguments": {"date": "2026-10-05"}}), _text_round()])
    assert asked == ["ext__moon_phase"] and calls == [("ext__moon_phase", {"date": "2026-10-05"})]
    assert _results(events)[0]["is_error"] is False


async def test_a_stop_while_permission_is_pending_runs_nothing_and_pairs_the_call():
    calls: list = []
    backend = _backend(_toolset(calls), mode="descriptions")

    async def approve_too_late(name, args):
        backend._interrupts.generation += 1          # the owner's Stop lands while the prompt is open
        return True

    backend.permission_cb = approve_too_late
    with pytest.raises(asyncio.CancelledError):
        await _run(backend, [
            _call_round(INVOKE, {"name": "ext__moon_phase", "arguments": {"date": "2026-10-05"}}, "c9"),
            _text_round()])
    assert calls == [], "approval after a Stop authorizes nothing"
    assert len(backend._client.payloads) == 1
    stub = [m for m in backend.messages if m.get("role") == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in stub] == [("c9", openai_compat._INTERRUPTED)]


# --- a check that runs inside the lead's conversation (the continuation verifier) -----------------
# Driven through the real worker path: _run_subagent -> _subagent_loop -> _guarded_exec -> _exec_tool, with the
# worker's requests served by the same scripted SSE rounds as the lead's. Nothing of the loop is stubbed.

_INSPECTIONS = ("show_html", "get_webview_logs", "save_screenshot", "see")


def _inspection(name, calls):
    async def handler(args):
        calls.append((name, args))
        content = [{"type": "text", "text": f"{name} ran"}]
        if name == "see":                      # the pixels a PASS needs to have been submitted
            content.append({"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgo="})
        return {"content": content}

    props = {key: {"type": "string", "description": "an argument"} for key in ("p0", "p1")}
    return SimpleNamespace(name=name, description=f"{name} inspects the page. It reports what it found.",
                           input_schema={"type": "object", "properties": props, "required": []}, handler=handler)


def _checking_backend(calls, mode="descriptions", permission_cb=None):
    """A lead whose verifier may use the four inspections and one gated external tool, and nothing else."""
    tools = (_toolset(calls, 5) + [_inspection(name, calls) for name in _INSPECTIONS]
             + [_tool("ext__probe", calls)])
    provider = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=True,
                               api_key=lambda: "n")
    subs = {"verifier": SimpleNamespace(description="checks a page", prompt="You are the verifier.",
                                        tool_names=[*_INSPECTIONS, "ext__probe"])}
    backend = OpenAICompatBackend(provider=provider, model="m", system_prompt="SYSTEM PROMPT", tools=tools,
                                  permission_cb=permission_cb, subagents=subs, discovery_mode=mode)
    backend.n_ctx = 16384
    backend.worker_streaming = True            # the worker's requests go through the scripted SSE client
    return backend


async def _check(backend, rounds):
    backend._client = _FakeClient(rounds)
    return await backend._run_subagent("verifier", "Check page.html.", continuation=True)


def _tool_messages(backend):
    return {m["tool_call_id"]: m["content"] for m in backend.messages if m.get("role") == "tool"}


def _wrapped(name, arguments=None, call_id="c1"):
    return _call_round(INVOKE, {"name": name, **({"arguments": arguments} if arguments is not None else {})}, call_id)


async def test_a_check_inside_the_conversation_looks_up_and_runs_its_inspections_through_the_wrapper(monkeypatch):
    """The gate's finding 1: the check was offered tool_schema / tool_call from the lead's array and both were
    refused under its scope -- zero handlers, verification unverified."""
    calls: list = []
    started: list = []
    real_started = openai_compat.worker_evidence.Evidence.tool_started

    def spy(self, call_id, name, *a, **k):
        started.append((call_id, name))
        return real_started(self, call_id, name, *a, **k)

    monkeypatch.setattr(openai_compat.worker_evidence.Evidence, "tool_started", spy)
    backend = _checking_backend(calls)
    text, failed = await _check(backend, [
        _call_round(LOOKUP, {"name": "show_html"}, "v1"),
        _wrapped("show_html", {"p0": "page.html"}, "v2"),
        _wrapped("get_webview_logs", None, "v3"),
        _wrapped("save_screenshot", {"p0": "shot.png"}, "v4"),
        _wrapped("see", {"p0": "shot.png"}, "v5"),
        _text_round("PASS"),
    ])
    assert (text, failed) == ("PASS", False), "every required inspection really ran, so the PASS stands"
    assert calls == [("show_html", {"p0": "page.html"}), ("get_webview_logs", {}),
                     ("save_screenshot", {"p0": "shot.png"}), ("see", {"p0": "shot.png"})], \
        "the real handlers, the inner arguments, once each, in order"

    payloads = backend._client.payloads
    assert len(payloads) == 6
    names = _names(payloads[0])
    assert names[:2] == [LOOKUP, INVOKE] and not set(names) & set(_INSPECTIONS), \
        "the premise: the check is sent the lead's description array, not its own tools"
    assert all(p["tools"] == payloads[0]["tools"] for p in payloads), "and that array never moves"
    assert all(p["messages"][0] == payloads[0]["messages"][0] for p in payloads)

    # the history keeps what the model wrote, and every result answers the model's own call id
    wrote = [(call["id"], call["function"]["name"]) for m in backend.messages for call in m.get("tool_calls") or []]
    assert wrote == [("v1", LOOKUP), ("v2", INVOKE), ("v3", INVOKE), ("v4", INVOKE), ("v5", INVOKE)]
    results = _tool_messages(backend)
    assert list(results) == ["v1", "v2", "v3", "v4", "v5"]
    assert results["v1"].startswith("Definition of show_html") and '"parameters"' in results["v1"]
    assert results["v2"] == "show_html ran" and results["v3"] == "get_webview_logs ran"
    # the bookkeeping names the tool that ran, under the same id
    assert started == [("v1", LOOKUP), ("v2", "show_html"), ("v3", "get_webview_logs"),
                       ("v4", "save_screenshot"), ("v5", "see")]


async def test_a_check_cannot_use_the_wrapper_or_the_lookup_to_leave_its_own_tools():
    calls: list = []
    asked: list = []

    async def decline(name, args):
        asked.append((name, args))
        return False

    backend = _checking_backend(calls, permission_cb=decline)
    text, failed = await _check(backend, [
        _wrapped("custom_0000", {"p0": "x"}, "r1"),                               # a tool of the lead's
        _wrapped("task", {"subagent_type": "verifier", "prompt": "again"}, "r2"),  # a control
        _wrapped(INVOKE, {"name": "show_html", "arguments": {}}, "r3"),            # the wrapper, twice
        _wrapped(LOOKUP, {"name": "show_html"}, "r4"),                             # the lookup, wrapped
        _call_round(LOOKUP, {"name": "custom_0000"}, "r5"),                        # a definition out of scope
        _call_round(LOOKUP, {"search": "custom"}, "r6"),                           # a search out of scope
        _call_round("task", {"subagent_type": "verifier", "prompt": "again"}, "r7"),
        _call_round("fork_verifier_agent", {"task": "again", "path": "page.html"}, "r8"),
        _wrapped("ext__probe", {"p0": "x"}, "r9"),                                 # in scope, needs a yes
        _text_round("PASS"),
    ])
    results = _tool_messages(backend)
    assert list(results) == ["r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9"], "each call got its own answer"
    assert "not available to this subagent" in results["r1"]
    for call_id in ("r2", "r3", "r4"):
        assert "control" in results[call_id] and "Nothing ran" in results[call_id]
    assert "not available to this check" in results["r5"] and "parameters" not in results["r5"]
    assert "custom_0000" not in results["r6"] and "No enabled tool matches" in results["r6"]
    assert "not available to this subagent" in results["r7"] and "not available to this subagent" in results["r8"]
    assert asked == [("ext__probe", {"p0": "x"})], "permission is asked once, for the real tool and its arguments"
    assert "Declined" in results["r9"]
    assert calls == [], "nothing ran"
    assert failed is True and text.startswith("Verification unverified"), "a PASS without inspections is refused"
    assert "show_html" in text and "see" in text


async def test_a_refused_wrapper_is_answered_and_the_check_can_still_pass_by_really_inspecting():
    calls: list = []
    backend = _checking_backend(calls)
    text, failed = await _check(backend, [
        _call_round(INVOKE, {"name": "show_html", "p0": "page.html"}, "m1"),      # arguments beside `arguments`
        _call_round(INVOKE, '{"name": "show_html", "arguments": {', "m2"),         # not valid JSON
        _wrapped("show_html", {"p0": "page.html"}, "m3"),
        _wrapped("get_webview_logs", {}, "m4"),
        _wrapped("save_screenshot", {}, "m5"),
        _wrapped("see", {}, "m6"),
        _text_round("PASS"),
    ])
    results = _tool_messages(backend)
    assert "inside `arguments`" in results["m1"] and "not valid JSON" in results["m2"]
    assert [name for name, _ in calls] == list(_INSPECTIONS)
    assert (text, failed) == ("PASS", False)


async def test_a_check_that_skips_an_inspection_is_still_unverified_in_description_mode():
    calls: list = []
    backend = _checking_backend(calls)
    text, failed = await _check(backend, [
        _wrapped("show_html", {"p0": "page.html"}, "s1"),
        _wrapped("get_webview_logs", {}, "s2"),
        _text_round("PASS"),
    ])
    assert [name for name, _ in calls] == ["show_html", "get_webview_logs"]
    assert failed is True and text.startswith("Verification unverified")
    assert "save_screenshot" in text and "see" in text and "show_html" not in text.split(";")[0]


async def test_an_independent_worker_is_handed_its_own_tools_whole_and_gets_no_wrapper():
    calls: list = []
    subs = {"researcher": SimpleNamespace(description="digs things up", prompt="You research.",
                                          tool_names=["custom_0000", "custom_0001"])}
    backend = _backend(_toolset(calls, 5), mode="descriptions", subagents=subs)
    backend.worker_streaming = True
    backend._role_model = lambda subagent_type: None       # no owner settings file decides this test
    backend._client = _FakeClient([
        _wrapped("custom_0000", {"p0": "x"}, "w1"),
        _call_round(LOOKUP, {"name": "custom_0000"}, "w2"),
        _call_round("custom_0000", {"p0": "x"}, "w3"),
        _text_round("found it"),
    ])
    text, failed = await backend._run_subagent("researcher", "Find it.")
    assert (text, failed) == ("found it", False)
    payloads = backend._client.payloads
    assert all(_names(p) == ["custom_0000", "custom_0001"] for p in payloads), "its scoped tools, as before"
    assert all("parameters" in schema["function"] for schema in payloads[0]["tools"])
    answers = {m["tool_call_id"]: m["content"] for m in payloads[-1]["messages"] if m.get("role") == "tool"}
    assert "not available to this subagent" in answers["w1"] and "not available to this subagent" in answers["w2"]
    assert answers["w3"] == "custom_0000 ran"
    assert calls == [("custom_0000", {"p0": "x"})], "only its own direct call ran"


# --- sessions do not share a mode ---------------------------------------------------------


async def test_backends_running_side_by_side_keep_their_own_mode_and_tools():
    a_calls, b_calls, c_calls = [], [], []
    described = _backend([_tool("alpha_only", a_calls)], mode="descriptions")
    other = _backend([_tool("beta_only", b_calls)], mode="descriptions")
    default = _backend([_tool("gamma_only", c_calls)])
    await asyncio.gather(
        _run(described, [_call_round(INVOKE, {"name": "alpha_only", "arguments": {"p0": "a"}}), _text_round()]),
        _run(other, [_call_round(INVOKE, {"name": "alpha_only", "arguments": {"p0": "b"}}), _text_round()]),
        _run(default, [_call_round("gamma_only", {"p0": "c"}), _text_round()]),
    )
    assert _names(described._client.payloads[0]) == [LOOKUP, INVOKE] == _names(other._client.payloads[0])
    assert _names(default._client.payloads[0]) == ["gamma_only"]
    assert "alpha_only" in json.dumps(described._client.payloads[0]["tools"])
    assert "alpha_only" not in json.dumps(other._client.payloads[0]["tools"])
    assert a_calls == [("alpha_only", {"p0": "a"})] and c_calls == [("gamma_only", {"p0": "c"})]
    assert b_calls == [], "another session's tool is unknown here, and nothing of this session ran instead"


async def test_the_mode_is_fixed_at_start_and_a_later_change_is_said_not_applied(monkeypatch):
    calls: list = []
    _started_with(monkeypatch, "descriptions")
    backend = _backend(_toolset(calls))
    first = await _run(backend, [_text_round()])
    assert any("Description discovery is on" in line for line in _system(first))
    start = copy.deepcopy(backend._client.payloads[0])

    monkeypatch.setenv(discovery.MODE_ENV, "default")
    second = await _run(backend, [_text_round()])
    said = [line for line in _system(second) if "changed while this session runs" in line]
    assert len(said) == 1 and "'default'" in said[0] and "task skill selection" in said[0] and "Restart" in said[0]
    assert backend._client.payloads[0]["tools"] == start["tools"]
    assert backend._client.payloads[0]["messages"][0] == start["messages"][0]

    third = await _run(backend, [_text_round()])
    assert not [line for line in _system(third) if discovery.MODE_ENV in line], "said once per change"
    assert backend._client.payloads[0]["tools"] == start["tools"]

    # The decision is the process's: a backend built after the change, without a mode of its own, is still in
    # the mode Dream started in, and it too says the variable no longer matches.
    later = _backend(_toolset(calls))
    events = await _run(later, [_text_round()])
    assert _names(later._client.payloads[0]) == [LOOKUP, INVOKE]
    assert [line for line in _system(events) if "changed while this session runs" in line]


@pytest.fixture
def named_skill(tmp_path, monkeypatch):
    root = tmp_path / "bigskill"
    root.mkdir()
    manifest = root / "SKILL.md"
    manifest.write_text("---\nname: bigskill\ndescription: Use it for big jobs.\n---\nBODY-START\n"
                        + "step line\n" * 2000, encoding="utf-8")
    skill = loader.FileSkill(name="bigskill", description="Use it for big jobs.", manifest=manifest, root=root,
                             source="test")
    monkeypatch.setattr(selection.loader, "enabled", lambda s: True)
    reads = []
    real = selection._read_workflow
    monkeypatch.setattr(selection, "_read_workflow", lambda s, limit: (reads.append(s.name), real(s, limit))[1])
    return skill, reads


async def test_a_session_that_announced_descriptions_gets_no_workflow_body_after_the_variable_is_removed(
        named_skill, monkeypatch):
    """The gate's finding 3, across the two halves the Engine drives: the backend it built at start and the
    selector it calls every turn without a mode."""
    skill, reads = named_skill
    calls: list = []
    _started_with(monkeypatch, "descriptions")
    backend = _backend(_toolset(calls))
    monkeypatch.setenv(discovery.MODE_ENV, "default")      # the owner's shell changes it while Dream runs

    guidance = selection.select_for_task("$bigskill please tidy this", [skill], max_chars=4000, used=())
    backend.prepare_turn(guidance.tools)
    events = await _run(backend, [_text_round()], prompt="$bigskill please tidy this" + guidance.text)

    assert guidance == selection.TaskGuidance() and reads == [], "no body was read or injected"
    assert _names(backend._client.payloads[0]) == [LOOKUP, INVOKE], "and the backend is still in the mode"
    sent = json.dumps(backend._client.payloads[0])
    assert "BODY-START" not in sent and "Task workflows" not in sent
    assert any("changed while this session runs" in line for line in _system(events))


async def test_a_session_that_started_in_the_default_keeps_selection_after_the_variable_is_set(
        named_skill, monkeypatch):
    skill, reads = named_skill
    calls: list = []
    backend = _backend(_toolset(calls))                    # started without the opt-in
    reference = _backend(_toolset(calls))
    await _run(reference, [_text_round()])
    monkeypatch.setenv(discovery.MODE_ENV, "descriptions")

    guidance = selection.select_for_task("$bigskill please tidy this", [skill], max_chars=4000, used=())
    events = await _run(backend, [_text_round()])

    assert guidance.names == ("bigskill",) and "BODY-START" in guidance.text and reads == ["bigskill"]
    assert backend._client.payloads[0]["tools"] == reference._client.payloads[0]["tools"], "the default array"
    assert INVOKE not in _names(backend._client.payloads[0])
    assert any("changed while this session runs" in line for line in _system(events))


async def test_a_backend_built_in_another_mode_than_the_process_says_what_does_not_follow_it():
    calls: list = []
    backend = _backend(_toolset(calls), mode="descriptions")          # the process started in the default
    events = await _run(backend, [_text_round()])
    said = [line for line in _system(events) if "Dream started in 'default'" in line]
    assert len(said) == 1 and "task skill selection" in said[0] and "'descriptions'" in said[0]
    again = await _run(backend, [_text_round()])
    assert not [line for line in _system(again) if "Dream started in" in line], "said once"
    # no such line when the builder's mode is the process's own
    same = _backend(_toolset(calls), mode="default")
    same_events = await _run(same, [_text_round()])
    assert not [line for line in _system(same_events) if "Dream started in" in line]


# --- honest accounting ------------------------------------------------------------------


async def test_the_surface_report_has_no_token_count_unless_one_is_injected():
    calls: list = []
    subs = {"researcher": SimpleNamespace(description="digs things up", prompt="p", tool_names=["custom_0000"])}
    backend = _backend(_toolset(calls, 3000), mode="descriptions", subagents=subs)
    await _run(backend, [_text_round()])
    sent = backend._client.payloads[0]["tools"]

    bare = backend.discovery_report()
    assert bare["count_status"] == discovery.UNAVAILABLE and bare["total_tokens"] == discovery.UNAVAILABLE
    assert bare["within_target"] is None and bare["qualified"] is False
    assert bare["missing_components"], "the prompt's own parts were not supplied, and the report says so"
    # the array's parts are the whole array: nothing is left out of the measure
    measured = sum(bare["components"][key]["chars"] for key in ("lookup_tool", "invocation_tool", "control_tools"))
    assert measured >= sum(len(json.dumps(schema, ensure_ascii=False)) for schema in sent)
    assert bare["components"]["control_tools"]["chars"] > len(json.dumps(backend._task_schema()["function"]["parameters"]))
    assert bare["unlisted_tools"] > 0 and bare["not_offered"] == ["snip"]

    # The gate's finding 2: these seven parts used to pass as the whole surface. The base prompt's guidance and
    # the Engine's stable sections are required too; this backend's system prompt is a plain string, so they
    # cannot be reconstructed, and a counted report without them makes no comparison.
    seven = {"learned_skill_index": "", "installed_skill_index": "- writing — Draft prose.",
             "mcp_section": "", "task_guidance": ""}
    without_base = backend.discovery_report(counter=lambda text: len(text) // 4, counter_label="synthetic chars/4",
                                            prompt_components=seven)
    assert without_base["count_status"] == "COUNTED"
    assert without_base["missing_components"] == ["base_prompt", "stable_sections"]
    assert without_base["complete"] is False and without_base["within_target"] is None
    assert without_base["prompt_parts_from_assembly"] == []

    prompt_parts = {**seven, "base_prompt": "Use tools for evidence.\n", "stable_sections": ""}
    counted = backend.discovery_report(counter=lambda text: len(text) // 3, counter_label="synthetic chars/3",
                                       prompt_components=prompt_parts)
    assert counted["count_status"] == "COUNTED" and counted["counter"] == "synthetic chars/3"
    assert counted["complete"] is True and isinstance(counted["within_target"], bool)
    assert counted["within_target"] is (counted["total_tokens"] <= counted["target_tokens"])
    assert counted["qualified"] is False, "a synthetic count is not a qualification"
    assert counted["total_kind"] == discovery.TOTAL_KIND
    partial = backend.discovery_report(counter=lambda text: 1)
    assert partial["within_target"] is None


def _assembled(base, stable="\n## Delegating\nUse the task tool for independent work.\n"):
    """A system prompt as build_system_prompt records one: base tier, a stable section, the wake-up."""
    wake = system_prompt.WAKE_HEADER + "nothing yet"
    prompt = system_prompt.SystemPrompt(base + stable + wake)
    prompt.provenance = ((0, len(base), "base", {"prompt_style": "full"}),
                         (len(base), len(base) + len(stable), "stable_config", {"index": 0}),
                         (len(base) + len(stable), len(prompt), "wake", None))
    prompt.discovery_mode = "descriptions"
    return prompt, stable


async def test_the_report_counts_the_base_prompt_this_backend_really_holds_and_says_when_it_is_over():
    calls: list = []
    subs = {"researcher": SimpleNamespace(description="digs things up", prompt="p", tool_names=["custom_0000"])}
    backend = _backend(_toolset(calls, 300), mode="descriptions", subagents=subs)
    base = system_prompt.BASE
    prompt, stable = _assembled(base)
    backend.set_system_prompt(prompt)
    await _run(backend, [_text_round()])
    assert backend._client.payloads[0]["messages"][0]["content"] == str(prompt), "the prompt went out unchanged"

    rest = {"learned_skill_index": "", "installed_skill_index": "- writing — Draft prose.",
            "mcp_section": "", "task_guidance": ""}
    report = backend.discovery_report(counter=lambda text: len(text) // 4, counter_label="synthetic chars/4",
                                      prompt_components=rest)
    assert report["prompt_parts_from_assembly"] == ["base_prompt", "stable_sections"]
    assert report["complete"] is True and report["missing_components"] == []
    assert report["components"]["base_prompt"]["chars"] == len(base)
    assert report["components"]["stable_sections"]["chars"] == len(stable)
    assert report["components"]["control_tools"]["chars"] > 0, "the compulsory task schema is still in the count"
    # every part, compulsory ones included, is in the total; by this synthetic measure the full base tier alone
    # is over the target, and the report says over rather than leaving it out or calling the target met
    assert report["total_tokens"] == sum(part["tokens"] for part in report["components"].values())
    assert report["total_tokens"] >= len(base) // 4 > discovery.TARGET_TOKENS
    assert report["within_target"] is False and report["over_target_by"] == (
        report["total_tokens"] - discovery.TARGET_TOKENS)
    assert report["qualified"] is False
    # the same report without a counter claims nothing at all
    bare = backend.discovery_report(prompt_components=rest)
    assert bare["complete"] is True and bare["total_tokens"] == discovery.UNAVAILABLE
    assert bare["within_target"] is None and bare["over_target_by"] is None
    assert bare["total_chars"] >= len(base) + len(stable)
    # the skill indexes and task guidance are never guessed: without them the surface is incomplete
    alone = backend.discovery_report(counter=lambda text: len(text) // 4)
    assert set(alone["missing_components"]) == set(rest) and alone["within_target"] is None


async def test_drift_after_reserved_name_fallback_reports_process_and_backend_separately(monkeypatch):
    _started_with(monkeypatch, "descriptions")
    calls = []
    backend = _backend([_tool("tool_call", calls)])
    assert backend._discovery.name == "default"
    await _run(backend, [_text_round()])
    monkeypatch.setenv(discovery.MODE_ENV, "off")
    events = await _run(backend, [_text_round()])
    [notice] = [line for line in _system(events) if "changed while this session runs" in line]
    assert "started with ('descriptions')" in notice
    assert "backend keeps its own tool discovery mode ('default')" in notice
    assert "started with ('default')" not in notice
    assert discovery.resolve().descriptions
    assert _names(backend._client.payloads[0]) == ["tool_call"]


def test_verifier_presentation_keeps_all_arguments_and_both_scheduling_contracts():
    backend = _backend([], mode="descriptions")
    original = backend._verifier_schema()
    shortened = backend._concise_discovery_control(original)
    assert shortened["function"]["parameters"] == original["function"]["parameters"]
    description = shortened["function"]["description"]
    for semantic in ("No task: queue after clean done", "silent on pass", "findings next turn",
                     "With task: check now", "always report", "no done required"):
        assert semantic in description
    assert backend._verifier_schema() == original, "presentation must not mutate execution authority"


def test_concise_task_guidance_reports_actual_queue_and_lane_capacity(monkeypatch):
    subs = {"coder": SimpleNamespace(description="Implementation specialist.", prompt="p", tool_names=[])}
    backend = _backend([], mode="descriptions", subagents=subs)
    monkeypatch.setattr(backend, "_task_lanes", lambda: 3)
    monkeypatch.setattr(backend, "subagent_lanes", lambda: 8)
    monkeypatch.setattr(backend, "_lane_ctx", lambda: 32768)
    actual = backend._request_tools()
    task = next(s for s in actual if s["function"]["name"] == "task")
    assert "3 at once; extra calls queue on this engine" in task["function"]["description"]
    assert "32,768 tokens; scope tasks to fit" in task["function"]["description"]
    assert task["function"]["parameters"]["properties"]["subagent_type"]["enum"] == ["coder"]
    default = _backend([], mode="default", subagents=subs)
    assert next(s for s in default._request_tools() if s["function"]["name"] == "task") == default._task_schema()
