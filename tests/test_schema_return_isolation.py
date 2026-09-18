"""Returned request metadata must not mutate registered argument constraints."""

from copy import deepcopy

import pytest

from dream.core.context_budget import estimate
from test_schema_deferral import _backend, _tool, _FakeClient, _text_round


def fixture_backend(deferred):
    calls = []
    target = _tool("write_file", calls=calls)
    target.input_schema["required"] = ["p0"]
    tools = [target]
    if deferred:
        optional = _tool("optional_large")
        optional.description = "界" * 4000
        tools.append(optional)
    backend = _backend(tools, n_ctx=8192)
    backend._pinned = backend._pinned | {target.name}
    backend._local_options = {"max_tokens": 256, "reasoning_effort": "high"}
    return backend, target, calls


def returned_schema(backend, cache_hit):
    first = backend._request_tools()
    returned = backend._request_tools() if cache_hit else first
    return next(s["function"] for s in returned if s["function"]["name"] == "write_file")


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("cache_hit", [False, True])
@pytest.mark.parametrize("field", ["description", "properties", "required"])
def test_nested_return_mutation_preserves_registry_and_cache(deferred, cache_hit, field):
    backend, tool, _ = fixture_backend(deferred)
    source = deepcopy(backend.tool_schemas)
    registered = deepcopy(tool.input_schema)
    returned = returned_schema(backend, cache_hit)
    cache = deepcopy(backend._schema_cache)
    assert bool(backend._deferred_now) == deferred
    if field == "description":
        returned[field] = "changed externally"
    elif field == "properties":
        returned["parameters"][field]["p0"]["type"] = "integer"
    else:
        returned["parameters"][field].clear()
    assert backend.tool_schemas == source
    assert tool.input_schema == registered
    assert backend._schema_cache == cache
    assert backend._request_tools() == cache[1]


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("cache_hit", [False, True])
@pytest.mark.parametrize("constraint", ["required", "type"])
@pytest.mark.asyncio
async def test_return_mutation_cannot_relax_argument_validation(deferred, cache_hit, constraint):
    backend, _, calls = fixture_backend(deferred)
    returned = returned_schema(backend, cache_hit)

    async def forbidden_permission(*args):
        pytest.fail("Invalid arguments must fail before permission or execution")

    backend.permission_cb = forbidden_permission
    if constraint == "required":
        returned["parameters"]["required"].clear()
        args = {}
    else:
        returned["parameters"]["properties"]["p0"]["type"] = "integer"
        args = {"p0": 1}
    result, failed = await backend._exec_tool("write_file", args)
    assert failed and "Invalid arguments" in result
    assert not calls


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.asyncio
async def test_authoritative_constraint_edit_still_updates_selection_and_validation(deferred):
    backend, tool, calls = fixture_backend(deferred)
    first = backend._request_tools()
    cache_key = backend._schema_cache[0]
    result, failed = await backend._exec_tool("write_file", {"p0": 1})
    assert failed and not calls
    tool.input_schema["properties"]["p0"]["type"] = "integer"
    selected = backend._request_tools()
    assert backend._schema_cache[0] != cache_key
    assert backend._schema_fingerprint(selected) != backend._schema_fingerprint(first)
    target = next(s["function"] for s in selected if s["function"]["name"] == tool.name)
    assert target["parameters"] == tool.input_schema
    result, failed = await backend._exec_tool("write_file", {"p0": 1})
    assert not failed and calls == [("write_file", {"p0": 1})]
    assert backend._admit_request(backend.messages, selected).tools == estimate(selected)


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.asyncio
async def test_normal_fake_turn_preserves_payload_and_settings(deferred):
    backend, _, _ = fixture_backend(deferred)
    source = deepcopy(backend.tool_schemas)
    expected = backend._request_tools()
    backend._client = _FakeClient([_text_round()])
    for prompt in ("hello", "again"):
        events = [event async for event in backend.ask(prompt)]
        assert any(e.kind == "result" and e.data["subtype"] == "success" for e in events)
    assert len(backend._client.payloads) == 2
    for payload in backend._client.payloads:
        assert payload["tools"] == expected
        assert payload["max_tokens"] == 256
        assert payload["reasoning_effort"] == "high"
    assert backend.tool_schemas == source
    assert backend.context_report["tools"] == estimate(expected)
