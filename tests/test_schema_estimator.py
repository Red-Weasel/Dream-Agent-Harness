"""Schema selection must price the same serialized input as admission."""

import copy
import json
from dataclasses import replace

import pytest

from dream.core import tool_budget_schemas as tbs
from dream.core.context_budget import ContextOverflow, account, admit, estimate
from dream.core.profiles import PROFILES
from test_schema_deferral import (
    _backend, _FakeClient, _fat_toolset, _text_round, _tool, NATIVE_TOOLS,
)


TEXTS = ["ascii", "界", "🙂", 'mix界🙂"\\\t', "\x01"]


def schema(name="optional_lookup", description="界" * 1300):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {}},
    }}


def names(schemas):
    return [s["function"]["name"] for s in schemas]


def backend(description="界" * 1300, policy="compact"):
    tool = _tool("optional_lookup")
    tool.description = description
    tool.input_schema = {"type": "object", "properties": {}}
    b = _backend([tool], n_ctx=4096)
    b.messages = [{"role": "system", "content": "s" * 8500}]
    b._local_options = {"max_tokens": 256}
    b._context_overflow = policy
    b.temperature = 0.23
    b._sampling = {"top_p": 0.81, "seed": 42}
    b._client = _FakeClient([_text_round()])
    return b


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.parametrize("count", [0, 1, 3])
def test_measure_prices_the_serialized_array(text, count):
    schemas = [schema(f"optional_{i}", text * 13) for i in range(count)]
    assert tbs.measure(schemas) == estimate(schemas)
    assert tbs.measure(tuple(schemas)) == estimate(schemas)


def test_original_full_schema_refuses_but_selection_admits_same_messages():
    schemas = [schema()]
    messages = [{"role": "system", "content": "s" * 8500},
                {"role": "user", "content": "hello"}]
    original = copy.deepcopy(messages)
    report = account(messages, schemas, 4096, 256)
    assert (report.instructions, report.history, report.tools, report.remaining) == (2439, 11, 1153, -19)
    with pytest.raises(ContextOverflow, match="4,115"):
        admit(messages, schemas, 4096, 256)
    sent, deferred = tbs.select(schemas, 4096)
    assert deferred == ["optional_lookup"]
    assert names(sent) == [tbs.LOOKUP_TOOL_NAME]
    assert admit(messages, sent, 4096, 256).output == 256
    assert messages == original


@pytest.mark.parametrize("policy", ["compact", "error"])
async def test_fake_turn_admits_unchanged_text_and_explicit_settings(policy):
    b = backend(policy=policy)
    events = [event async for event in b.ask("hello")]
    assert b._client.payloads, [event.data for event in events if event.kind == "error"]
    payload = b._client.payloads[0]
    assert payload["messages"] == [
        {"role": "system", "content": "s" * 8500},
        {"role": "user", "content": "hello\n\n[id:m0001]"},
    ]
    assert payload["max_tokens"] == 256
    assert (payload["temperature"], payload["top_p"], payload["seed"]) == (0.23, 0.81, 42)
    assert names(payload["tools"]) == [tbs.LOOKUP_TOOL_NAME]
    assert b.context_report["tools"] == estimate(payload["tools"])
    assert b.context_report["admitted"] and b.context_report["elided_messages"] == 0
    assert any(event.kind == "result" and event.data["subtype"] == "success" for event in events)


@pytest.mark.parametrize("policy", ["compact", "error"])
async def test_mandatory_full_schema_still_refuses_visibly(policy):
    b = backend(policy=policy)
    b._pinned = frozenset({"optional_lookup"})
    original = copy.deepcopy(b.tool_schemas)
    events = [event async for event in b.ask("hello")]
    assert not b._client.payloads
    assert b._request_tools() == original
    assert b.context_report["remaining"] == -23
    assert b.context_report["tools"] == 1153
    assert b.messages[-1]["content"] == "hello\n\n[id:m0001]"
    assert any(event.kind == "result" and event.data["subtype"] == "context_overflow" for event in events)


@pytest.mark.parametrize("text", TEXTS)
def test_catalog_allowance_prices_escaped_utf8_description(text):
    schemas = [schema(description=text * 100)]
    line = tbs.catalog_line(schemas[0])
    # Lines live inside a JSON string: include escapes and the line separator.
    line_cost = estimate(json.dumps(line + "\n", ensure_ascii=False)[1:-1])
    below = tbs.lookup_schema(schemas, line_cost - 1)
    at = tbs.lookup_schema(schemas, line_cost)
    assert line not in below["function"]["description"]
    assert line in at["function"]["description"]
    assert at["function"]["parameters"] == below["function"]["parameters"]


@pytest.mark.parametrize("text", TEXTS)
def test_final_catalog_arrays_respect_budget_boundaries(text):
    schemas = [schema(f"tool_{i}", text * (30 + i * 70)) for i in range(6)]
    before = copy.deepcopy(schemas)
    fixed = estimate([tbs.lookup_schema(schemas, 0)])
    full = estimate(schemas)
    for budget in [fixed - 1, fixed, fixed + 1, fixed + 50, full - 1, full, full + 1]:
        sent, deferred = tbs.select(schemas, budget, budget_frac=1.0)
        assert sent == tbs.select(schemas, budget, budget_frac=1.0)[0]
        assert tbs.measure(sent) == estimate(sent)
        if budget >= fixed:
            assert estimate(sent) <= budget
        else:
            assert names(sent) == [tbs.LOOKUP_TOOL_NAME]
        assert names(sent) == [n for n in names(schemas) if n not in deferred] + ([tbs.LOOKUP_TOOL_NAME] if deferred else [])
        if deferred:
            hatch = sent[-1]["function"]
            assert hatch["parameters"]["properties"]["name"]["enum"] == deferred
            for name in deferred:
                assert tbs.search_catalog(schemas, name)
        else:
            assert sent == schemas
    assert schemas == before


def test_cache_tracks_changed_schema_window_fraction_and_reveals():
    b = backend(description="a" * 1300)
    # Same number of characters; bytes and membership change.
    b.n_ctx = 8192
    assert b._request_tools() == b.tool_schemas
    b.tool_schemas[0]["function"]["description"] = "界" * 1300
    assert names(b._request_tools()) == [tbs.LOOKUP_TOOL_NAME]
    b.profile = replace(PROFILES["lean"], schema_fraction=0.2)
    assert b._request_tools() == b.tool_schemas
    b.n_ctx = 4096
    assert names(b._request_tools()) == [tbs.LOOKUP_TOOL_NAME]
    text, failed = b._lookup_tool_schema("", search="optional_lookup")
    assert not failed and "optional_lookup" in text
    text, failed = b._lookup_tool_schema("optional_lookup")
    assert not failed and json.loads(text) == b.tool_schemas[0]["function"]
    assert b._request_tools() == b.tool_schemas
    with pytest.raises(ContextOverflow):
        b._admit_request(b.messages + [{"role": "user", "content": "hello"}], b._request_tools())


async def test_deferred_tool_remains_callable_without_lookup():
    b = backend()
    assert names(b._request_tools()) == [tbs.LOOKUP_TOOL_NAME]
    content, failed = await b._exec_tool("optional_lookup", {})
    assert not failed and str(content) == "optional_lookup ran"


def test_fixed_16k_native_tools_defer_optional_schemas_to_admission_budget():
    b = _backend(NATIVE_TOOLS, n_ctx=16384)
    assert estimate(b.tool_schemas) > 1638
    sent = b._request_tools()
    assert b._deferred_now and estimate(sent) <= 1638
    assert b._pinned <= set(names(sent))
    for s in sent[:-1]:
        assert s == next(original for original in b.tool_schemas
                         if original["function"]["name"] == s["function"]["name"])


def test_fixed_16k_large_toolset_preserves_only_mandatory_floor():
    b = _backend(_fat_toolset(), n_ctx=16384)
    pinned = [s for s in b.tool_schemas if s["function"]["name"] in b._pinned]
    optional = [s for s in b.tool_schemas if s["function"]["name"] not in b._pinned]
    mandatory = pinned + [tbs.lookup_schema(optional, 0)]
    assert estimate(mandatory) > 1638
    assert b._request_tools() == mandatory


def test_mandatory_discovery_enum_that_exceeds_window_still_refuses():
    schemas = [schema("long_name_" + "x" * 15000)]
    sent, deferred = tbs.select(schemas, 4096)
    assert sent == [tbs.lookup_schema(schemas, 0)]
    assert sent[0]["function"]["parameters"]["properties"]["name"]["enum"] == deferred
    with pytest.raises(ContextOverflow):
        admit([{"role": "user", "content": "hello"}], sent, 4096, 256)
