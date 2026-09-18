"""Phase 14: the escape hatch pays its own way.

The catalogue inside `tool_schema` carried one line per deferred tool, so the
hatch grew with every tool Dream was given — 2,375 tokens at 94 tools, half the
floor at a 16K window. These tests measure the REAL assembled toolset (not a
synthetic one) against the window, and pin the property that matters: a tool the
catalogue has no room to name is still reachable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_schema_deferral import _FakeClient, _text_round  # noqa: E402

from dream.core import tool_budget_schemas as tbs  # noqa: E402
from dream.core.backends.openai_compat import OpenAICompatBackend, _tool_schema  # noqa: E402
from dream.core.subagents import local_subagents  # noqa: E402
from dream.core.profiles import PROFILES  # noqa: E402
from dream.tools.native import NATIVE_TOOLS  # noqa: E402
from dream.tools.registry import _BASE_TOOLS  # noqa: E402
from dream.tools.demonstration_tools import DEMONSTRATION_TOOLS  # noqa: E402
from dream.tools.capability_tools import CAPABILITY_TOOLS  # noqa: E402

pytestmark = pytest.mark.asyncio

# What the engine really assembles: every registered tool plus the native ones.
FULL = list(_BASE_TOOLS) + list(NATIVE_TOOLS) + DEMONSTRATION_TOOLS + CAPABILITY_TOOLS

# Exercise the profiles the real Engine now supplies. Optional schemas must fit
# the target; full mandatory schemas plus discovery can exceed these ceilings.
CEILING = {8192: 0.15, 16384: 0.15, 32768: 0.11, 131072: 0.11}


async def _sent(window: int):
    prov = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1",
                           multimodal=False, api_key=lambda: "n")
    b = OpenAICompatBackend(provider=prov, model="m", system_prompt="S" * 100,
                            tools=FULL, permission_cb=None, subagents=local_subagents(),
                            profile=PROFILES["lean" if window <= 32768 else "frontier"])
    b.n_ctx = window
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("hello")]
    return b, b._client.payloads[0]["tools"]


@pytest.mark.parametrize("window", [8192, 16384, 32768, 131072])
async def test_the_tools_array_fits_the_window(window):
    _b, sent = await _sent(window)
    cost = tbs.measure(sent)
    share = cost / window
    pinned = [s for s in _b.tool_schemas if s["function"]["name"] in _b._pinned]
    optional = [s for s in _b.tool_schemas if s["function"]["name"] not in _b._pinned]
    mandatory = pinned + [tbs.lookup_schema(optional, 0)]
    target = int(window * _b.profile.schema_fraction)
    if tbs.measure(mandatory) > target:
        # Full mandatory schemas can exceed the target; no optional schema or
        # catalog description may accompany them when they do.
        assert sent == mandatory
    else:
        assert cost <= target
        assert share <= CEILING[window], (
            f"{cost:,} tokens is {share:.1%} of a {window:,} window, over {CEILING[window]:.0%}")


async def test_small_window_hatch_preserves_every_name_and_native_schema():
    original = [_tool_schema(tool) for tool in FULL]
    b, sent = await _sent(8192)
    pinned = [s for s in b.tool_schemas if s["function"]["name"] in b._pinned]
    optional = [s for s in b.tool_schemas if s["function"]["name"] not in b._pinned]
    mandatory = pinned + [tbs.lookup_schema(optional, 0)]
    assert tbs.measure(mandatory) > int(8192 * .15)
    assert sent == mandatory
    functions = {s["function"]["name"]: s["function"] for s in sent}
    hatch = functions[tbs.LOOKUP_TOOL_NAME]
    deferred = hatch["parameters"]["properties"]["name"]["enum"]
    assert set(deferred) == b._deferred_now
    assert all(tool.name in functions or tool.name in deferred for tool in b.tools)
    for name in ("read_file", "write_file", "run_bash", "str_replace_edit"):
        assert functions[name] == next(s["function"] for s in original if s["function"]["name"] == name)


async def test_a_tool_the_catalogue_cannot_name_is_still_reachable():
    """Criterion 2 and 3: the catalogue is bounded, the enum is not, and search
    finds what the catalogue had no room for."""
    b, sent = await _sent(16384)
    hatch = next(s for s in sent if s["function"]["name"] == tbs.LOOKUP_TOOL_NAME)
    catalogue = hatch["function"]["description"]
    enum = hatch["function"]["parameters"]["properties"]["name"]["enum"]
    sent_names = {s["function"]["name"] for s in sent}

    listed = [n for n in enum if f"{n} — " in catalogue]
    unlisted = [n for n in enum if n not in listed]
    # At 16K the pinned hands already exceed the 10% budget, so the catalogue
    # gets no allowance at all and describes nothing. That is the designed
    # degradation, not a failure: the NAMES still ride in the enum below, and
    # `search` buys back the descriptions on demand.
    assert unlisted, "at a 16K window the catalogue cannot fit them all"
    assert f"{len(unlisted)} more" in catalogue, "and it says how many it left out"

    # every deferred tool is in the enum, listed or not
    for t in b.tools:
        assert t.name in sent_names or t.name in enum, t.name

    # an unlisted tool is findable by its own name, and callable by it
    name = unlisted[0]
    text, err = b._lookup_tool_schema("", search=name)
    assert not err and name in text, f"{name} was unfindable by search"
    text, err = b._lookup_tool_schema(name)
    assert not err and f'"{name}"' in text, f"{name} was not callable by name"


async def test_search_finds_by_what_a_tool_does_not_only_its_name():
    b, _sent_list = await _sent(16384)
    text, err = b._lookup_tool_schema("", search="pptx")
    assert not err and "gen_pptx" in text
    text, err = b._lookup_tool_schema("", search="screenshot")
    assert not err and "screenshot" in text
    text, err = b._lookup_tool_schema("", search="zzzz no such thing")
    assert not err and "No not-loaded tool matches" in text


async def test_a_looked_up_tool_stays_loaded_only_for_current_turn():
    b, first = await _sent(16384)
    hatch = next(s for s in first if s["function"]["name"] == tbs.LOOKUP_TOOL_NAME)
    name = hatch["function"]["parameters"]["properties"]["name"]["enum"][0]
    b._lookup_tool_schema(name)
    assert name in {s["function"]["name"] for s in b._request_tools()}
    b._client = _FakeClient([_text_round("ok")])
    [ev async for ev in b.ask("again")]
    assert name not in {s["function"]["name"] for s in b._client.payloads[0]["tools"]}


async def test_a_roomier_window_buys_catalogue_lines():
    """More window exposes more complete tools; descriptions may leave the
    lookup catalog because their complete schema is now loaded."""
    _b16, s16 = await _sent(16384)
    _b32, s32 = await _sent(32768)
    def listed(sent):
        h = [s for s in sent if s["function"]["name"] == tbs.LOOKUP_TOOL_NAME]
        if not h:
            return 10_000            # nothing deferred at all: the best case
        d = h[0]["function"]["description"]
        return sum(1 for n in h[0]["function"]["parameters"]["properties"]["name"]["enum"]
                   if f"{n} — " in d)
    assert len(s32) + listed(s32) > len(s16) + listed(s16)
    assert len(_b32._deferred_now) < len(_b16._deferred_now)


def test_the_catalogue_is_bounded_by_its_allowance_not_by_tool_count():
    many = [{"type": "function",
             "function": {"name": f"tool_{i:03d}",
                          "description": "Does a distinct thing worth one line of prose.",
                          "parameters": {"type": "object", "properties": {}}}}
            for i in range(200)]
    unbounded = tbs.measure([tbs.lookup_schema(many)])
    bounded = tbs.measure([tbs.lookup_schema(many, 400)])
    assert bounded < unbounded / 2, (bounded, unbounded)
    # the allowance bounds the LINES; the enum of 200 names rides regardless
    assert len(tbs.lookup_schema(many, 400)["function"]["parameters"]
               ["properties"]["name"]["enum"]) == 200
    # an allowance of nothing still yields a usable hatch
    empty = tbs.lookup_schema(many, 0)
    assert "200 more" in empty["function"]["description"]
    assert len(empty["function"]["parameters"]["properties"]["name"]["enum"]) == 200


def test_search_catalog_matches_every_word():
    schemas = [{"type": "function", "function": {"name": "gen_pptx",
                "description": "Build a PowerPoint deck from slides.", "parameters": {}}},
               {"type": "function", "function": {"name": "open_for_print",
                "description": "Open a print view for a page.", "parameters": {}}}]
    assert tbs.search_catalog(schemas, "powerpoint") and not tbs.search_catalog(schemas, "")
    assert not tbs.search_catalog(schemas, "powerpoint print")  # every word must match
    assert len(tbs.search_catalog(schemas, "a", limit=1)) <= 1
