"""Tool-schema deferral on the OpenAI-compat (local MachX) backend.

Every schema went out on every round — the self-built `custom/` set included,
which only grows — so a 16k local window spent a large slice of itself on tool
definitions before the first token, and spent it again each tool round. The
Claude path gets this handled by the SDK's tool search; this backend has to do
it itself.

The policy (which schemas earn their tokens) is `core.tool_budget_schemas` and
is tested there. What is pinned here is the WIRING: the budget is applied at
request-build time against the same window compaction uses, the core tools are
never taken away, the lookup hatch really executes, and deferral stays a context
optimisation rather than a permission boundary — a deferred tool called cold
still runs.
"""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from dream.core import tool_budget_schemas as tbs
from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.tools import memory_tools, notes
from dream.tools.native import NATIVE_TOOLS

# read/write/shell and Dream's own mind: what the loop cannot lose to a deferral.
# The real tool objects, because pinning keys on where a tool is DEFINED — a
# same-named fake would prove nothing about the tools that actually ship.
_CORE_TOOLS = [*NATIVE_TOOLS, memory_tools.remember, memory_tools.recall, notes.note]
# The loop's hands: never deferred. NATIVE_TOOLS also carries five file tools
# (grep, copy_files, delete_file, image_metadata, sleep) that MAY defer.
_PINS = tuple(t.name for t in _CORE_TOOLS
              if t.name not in {"grep", "copy_files", "delete_file", "image_metadata", "sleep"})


def _tool(name, nprops=1, calls=None):
    async def handler(args):
        if calls is not None:
            calls.append((name, args))
        return {"content": [{"type": "text", "text": f"{name} ran"}]}

    props = {f"p{i}": {"type": "string", "description": "an argument"}
             for i in range(nprops)}
    return SimpleNamespace(
        name=name, description=f"{name} does a thing. And then some more prose.",
        input_schema={"type": "object", "properties": props, "required": []},
        handler=handler,
    )


def _backend(tools=(), n_ctx=16384, subagents=None) -> OpenAICompatBackend:
    p = SimpleNamespace(
        key="machx", label="MachX", base_url="http://x/v1",
        multimodal=False, api_key=lambda: "n",
    )
    b = OpenAICompatBackend(
        provider=p, model="m", system_prompt="SYSTEM PROMPT",
        tools=list(tools), permission_cb=None, subagents=subagents,
    )
    b.n_ctx = n_ctx
    return b


class _FakeResponse:
    def __init__(self, lines):
        self.status_code = 200
        self._lines = lines

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

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
    """Serves one script per request and keeps a SNAPSHOT of each payload — the
    live payload holds the backend's own message list, which keeps mutating."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.payloads = []

    def stream(self, method, url, json=None):
        self.payloads.append(copy.deepcopy(json))
        i = min(len(self.payloads) - 1, len(self._rounds) - 1)
        return _FakeStream(self._rounds[i])


def _sse(obj) -> str:
    return "data: " + json.dumps(obj)


def _text_round(text="done"):
    return [
        _sse({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]


def _tool_round(name, args="{}"):
    return [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": name, "arguments": args}}
        ]}, "finish_reason": None}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]


def _sent_names(payload) -> list[str]:
    return [s["function"]["name"] for s in payload["tools"]]


def _hatch(payload) -> dict:
    return next(s for s in payload["tools"]
                if s["function"]["name"] == tbs.LOOKUP_TOOL_NAME)


# --- a small toolset is untouched --------------------------------------------


async def test_a_toolset_that_fits_is_sent_whole_with_no_hatch():
    b = _backend(NATIVE_TOOLS)
    b.n_ctx = int(tbs.measure(b.tool_schemas) / tbs.DEFAULT_BUDGET_FRAC) + 1
    assert tbs.measure(b.tool_schemas) <= int(b.n_ctx * tbs.DEFAULT_BUDGET_FRAC)
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    sent = b._client.payloads[0]["tools"]
    assert sent == b.tool_schemas  # byte-for-byte what it always sent
    assert tbs.LOOKUP_TOOL_NAME not in _sent_names(b._client.payloads[0])


async def test_no_tools_at_all_still_sends_an_empty_array():
    b = _backend([])
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    assert b._client.payloads[0]["tools"] == []


# --- a big toolset is cut to the budget --------------------------------------


def _fat_toolset(n=20, calls=None):
    return _CORE_TOOLS + [_tool(f"custom_{i:02d}", nprops=8, calls=calls)
                          for i in range(n)]


async def test_a_big_toolset_is_cut_to_the_budget_and_catalogued():
    # The full pins and discovery hatch leave room for catalog lines. 21000 since 2026-09-22:
    # read_file gained start_line/line_count and the pinned tools carry approval notes (fix #43/#44).
    window = 21000
    b = _backend(_fat_toolset(), n_ctx=window)
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    payload = b._client.payloads[0]

    assert tbs.measure(b.tool_schemas) > int(window * tbs.DEFAULT_BUDGET_FRAC)
    assert tbs.measure(payload["tools"]) <= int(window * tbs.DEFAULT_BUDGET_FRAC)

    # Nothing vanished. Phase 14 bounded the catalog by budget rather than by
    # tool count, so a tool may now be reachable WITHOUT a catalog line: every
    # deferred name still travels in the hatch's `name` enum (which is what a
    # grammar-constrained server reads), and `search` finds it by what it does.
    hatch = _hatch(payload)
    catalog = hatch["function"]["description"]
    enum = hatch["function"]["parameters"]["properties"]["name"]["enum"]
    for t in b.tools:
        assert t.name in _sent_names(payload) or t.name in enum, t.name
    # ... and the catalog is a real subset, not everything and not nothing
    listed = [t.name for t in b.tools if f"{t.name} — " in catalog]
    assert listed, "the catalog names something"
    assert "more — use `search`" in catalog, "and says how to reach the rest"
    # every unlisted tool is findable by its own words
    deferred = [openai_compat._tool_schema(t) for t in b.tools
                if t.name not in _sent_names(payload)]
    for t in b.tools:
        if t.name in _sent_names(payload) or t.name in listed:
            continue
        assert tbs.search_catalog(deferred, t.name), f"{t.name} is unfindable"


async def test_the_pinned_tools_survive_a_window_too_small_for_them():
    """read/write/shell/memory are the loop's hands: it cannot lose them to a
    context optimisation, even when the budget cannot pay for them."""
    b = _backend(_fat_toolset(), n_ctx=1024)
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    sent = _sent_names(b._client.payloads[0])
    assert set(_PINS) <= set(sent)
    assert tbs.LOOKUP_TOOL_NAME in sent
    assert [n for n in sent if n.startswith("custom_")] == []


async def test_the_task_tool_is_pinned_when_subagents_are_enabled():
    subs = {"researcher": SimpleNamespace(description="digs things up",
                                          prompt="p", tool_names=["read_file"])}
    b = _backend(_fat_toolset(), n_ctx=1024, subagents=subs)
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    assert "task" in _sent_names(b._client.payloads[0])


async def test_deferral_is_stable_across_the_rounds_of_a_turn():
    b = _backend(_fat_toolset())
    b._client = _FakeClient([_tool_round("custom_00"), _text_round()])
    [ev async for ev in b.ask("go")]
    first, second = b._client.payloads[0], b._client.payloads[1]
    assert _sent_names(first) == _sent_names(second)


# --- the lookup hatch executes -----------------------------------------------


def _one_fat_tool(calls=None):
    """A toolset with exactly one tool too fat to keep — so which name gets
    deferred is arithmetic, not a tie-break."""
    return _CORE_TOOLS + [_tool("fetch_moon_phase", nprops=200, calls=calls)]


async def test_lookup_returns_the_real_schema_and_pins_it_from_then_on():
    calls: list = []
    b = _backend(_one_fat_tool(calls))
    b._stable_tool_list = lambda: False  # a remote provider: reveals and per-turn relevance update the list
    b._client = _FakeClient([
        _tool_round(tbs.LOOKUP_TOOL_NAME, '{"name": "fetch_moon_phase"}'),
        _tool_round("fetch_moon_phase", '{"p0": "x"}'),
        _text_round(),
    ])
    events = [ev async for ev in b.ask("what phase is the moon in?")]

    assert "fetch_moon_phase" not in _sent_names(b._client.payloads[0])
    looked_up = [e.data for e in events if e.kind == "tool_result"][0]
    assert looked_up["is_error"] is False
    fn = json.loads(looked_up["content"])
    assert fn["name"] == "fetch_moon_phase"
    assert "p0" in fn["parameters"]["properties"]  # the real parameters, not a summary

    # Asked for, so it stays in the window from the next round on.
    assert "fetch_moon_phase" in _sent_names(b._client.payloads[1])
    assert ("fetch_moon_phase", {"p0": "x"}) in calls


async def test_lookup_of_an_unknown_name_is_answered_not_crashed():
    b = _backend(_one_fat_tool())
    b._client = _FakeClient([
        _tool_round(tbs.LOOKUP_TOOL_NAME, '{"name": "no_such_tool"}'),
        _text_round(),
    ])
    events = [ev async for ev in b.ask("go")]
    res = [e.data for e in events if e.kind == "tool_result"][0]
    assert res["is_error"] is True
    assert "no_such_tool" in res["content"]
    # the turn carries on rather than dying on a bad lookup
    assert [e for e in events if e.kind == "result"][0].data["subtype"] == "success"


async def test_lookup_of_a_tool_that_was_already_sent_still_answers():
    b = _backend(_one_fat_tool())
    b._client = _FakeClient([
        _tool_round(tbs.LOOKUP_TOOL_NAME, '{"name": "read_file"}'),
        _text_round(),
    ])
    events = [ev async for ev in b.ask("go")]
    res = [e.data for e in events if e.kind == "tool_result"][0]
    assert res["is_error"] is False
    assert json.loads(res["content"])["name"] == "read_file"


# --- deferral is not a permission boundary -----------------------------------


async def test_a_deferred_tool_called_cold_still_executes():
    """The model may know a name from the catalog (or from the last turn) and
    call it straight out. Its schema being out of the window is a context
    optimisation — refusing the call would make it a permission boundary."""
    calls: list = []
    b = _backend(_one_fat_tool(calls))
    b._client = _FakeClient([
        _tool_round("fetch_moon_phase", '{"p0": "waxing"}'),
        _text_round(),
    ])
    events = [ev async for ev in b.ask("go")]

    assert "fetch_moon_phase" not in _sent_names(b._client.payloads[0])
    assert ("fetch_moon_phase", {"p0": "waxing"}) in calls
    res = [e.data for e in events if e.kind == "tool_result"][0]
    assert res["is_error"] is False and res["content"] == "fetch_moon_phase ran"


# --- the window it budgets against is the one compaction uses ----------------


async def test_an_unknown_window_budgets_against_the_assumed_one():
    b = _backend(_fat_toolset(), n_ctx=None)
    assert b.n_ctx is None
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    payload = b._client.payloads[0]
    budget = int(openai_compat._ASSUMED_CTX * tbs.DEFAULT_BUDGET_FRAC)
    assert tbs.measure(payload["tools"]) <= budget
    assert set(_PINS) <= set(_sent_names(payload))


async def test_a_roomy_window_defers_nothing():
    b = _backend(_fat_toolset(), n_ctx=1_000_000)
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    assert b._client.payloads[0]["tools"] == b.tool_schemas


# --- usage counts come from the store when there is one ----------------------


def _two_fat_tools():
    return list(NATIVE_TOOLS), [_tool("fat_a", nprops=40), _tool("fat_b", nprops=40)]


def _window_for_one_of_two(pins, fats) -> int:
    """A window whose tool budget pays for the pins, ONE fat tool, and the hatch
    — so which fat tool survives is decided by the ranking and nothing else."""
    schemas = [openai_compat._tool_schema(t) for t in (*pins, fats[0])]
    deferred = [openai_compat._tool_schema(fats[1])]
    # Price the hatch the way the selector will (Phase 14): its catalog is
    # bounded by what the budget has left after the sent schemas AND the
    # hatch's own fixed cost, so a helper that prices it unbounded computes a
    # window one token too small to keep the tool it means to keep.
    budget = tbs.measure([*schemas, tbs.lookup_schema(deferred)])
    for _ in range(4):  # converges: the allowance depends on the budget it sets
        allowance = tbs._allowance(budget, schemas, deferred)
        budget = tbs.measure([*schemas, tbs.lookup_schema(deferred, allowance)])
    return int(budget / tbs.DEFAULT_BUDGET_FRAC) + 1


async def test_without_a_store_the_ranking_still_works():
    pins, fats = _two_fat_tools()
    b = _backend([*pins, *fats], n_ctx=_window_for_one_of_two(pins, fats))
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    sent = _sent_names(b._client.payloads[0])
    assert "fat_a" in sent and "fat_b" not in sent  # equal cost, name breaks the tie


async def test_a_used_tool_earns_its_place_from_the_store(monkeypatch):
    from dream.memory.store import MemoryStore
    from dream.tools import context as tool_context

    store = MemoryStore(Path(tempfile.mkdtemp()) / "t.db")
    store.bump_tool_use("fat_b")
    monkeypatch.setattr(tool_context, "_CTX", SimpleNamespace(store=store))

    pins, fats = _two_fat_tools()
    b = _backend([*pins, *fats], n_ctx=_window_for_one_of_two(pins, fats))
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("hi")]
    sent = _sent_names(b._client.payloads[0])
    assert "fat_b" in sent and "fat_a" not in sent  # usage beat the name tie-break



async def test_a_local_backend_keeps_one_tool_list_for_the_session():
    """Dream fix #1/#15: on a local server (prompt cache) the tools render right
    after the system text, so the sent list must not change between requests:
    per-turn relevance, and a schema fetched with tool_schema, would re-read the
    whole conversation. The fetched tool stays callable through its result."""
    calls: list = []
    b = _backend(_one_fat_tool(calls))
    assert b._stable_tool_list()
    b._client = _FakeClient([
        _tool_round(tbs.LOOKUP_TOOL_NAME, '{"name": "fetch_moon_phase"}'),
        _tool_round("fetch_moon_phase", '{"p0": "x"}'),
        _text_round(),
    ])
    [ev async for ev in b.ask("what phase is the moon in?")]
    first = _sent_names(b._client.payloads[0])
    assert "fetch_moon_phase" not in first
    assert all(_sent_names(p) == first for p in b._client.payloads)   # byte-stable across rounds
    assert calls, "the revealed tool still ran"
    b.prepare_turn(["fetch_moon_phase"])                                 # next turn's relevance
    b._client = _FakeClient([_text_round()])
    [ev async for ev in b.ask("and tomorrow?")]
    assert _sent_names(b._client.payloads[0]) == first


def test_a_local_list_is_rechosen_only_when_its_inputs_change():
    b = _backend(_fat_toolset(), n_ctx=20000)
    first = b._request_tools()
    b._revealed.add(next(s["function"]["name"] for s in b.tool_schemas
                         if s["function"]["name"] not in _sent_names({"tools": first})))
    assert b._request_tools() == first
    b.n_ctx = 60000
    assert b._request_tools() != first    # a different window is a different budget
