"""Transport fixtures only: no server, model, or hardware probes."""
import copy

import pytest

from test_schema_deferral import _backend, _tool, _FakeClient, _text_round, _tool_round
from dream.core import tool_budget_schemas


@pytest.mark.asyncio
async def test_mode_changes_only_next_turn_and_restores_baseline():
    b = _backend([_tool("read_file")], n_ctx=131072)
    b._local_options = {"max_tokens": 12000, "reasoning_effort": "max"}
    original = copy.deepcopy(b._local_options)
    assert b.performance_status()["current"] == "custom"
    b.set_performance_mode("quick")
    b._client = _FakeClient([_tool_round("read_file"), _text_round(), _text_round()])
    async for ev in b.ask("first"):
        if ev.kind == "tool_use":
            b.set_performance_mode("thorough")
    assert [p["max_tokens"] for p in b._client.payloads] == [2048, 2048]
    assert all(p["reasoning_effort"] == "max" for p in b._client.payloads)
    [ev async for ev in b.ask("second")]
    assert b._client.payloads[-1]["max_tokens"] == 12000
    b.set_performance_mode("custom")
    assert b.performance_status()["effective"]["output_tokens"] == 12000
    assert b._local_options == original


def test_schema_selection_reuses_identical_inputs_but_invalidates_changes(monkeypatch):
    b = _backend([_tool("read_file"), _tool("other", 20)], n_ctx=8192)
    calls = []
    original = tool_budget_schemas.select
    def select(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(tool_budget_schemas, "select", select)
    first = b._request_tools()
    assert b._request_tools() == first
    assert len(calls) == 1
    first.clear()
    assert b._request_tools()
    b._revealed.add("other")
    b._request_tools()
    assert len(calls) == 2
    monkeypatch.setattr("dream.extensions.filter_tools", lambda ts: [t for t in ts if t.name != "other"])
    assert "other" not in [s["function"]["name"] for s in b._request_tools()]
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_invalid_arguments_do_not_prompt_or_execute():
    calls = []
    tool = _tool("write_file", calls=calls)
    tool.input_schema["required"] = ["p0"]
    b = _backend([tool])
    async def permission(*args):
        pytest.fail("Invalid arguments must not reach permission UI")
    b.permission_cb = permission
    result, failed = await b._exec_tool("write_file", {})
    assert failed and "p0" in result and not calls


@pytest.mark.asyncio
async def test_direct_output_edit_rebases_custom_for_the_next_turn(monkeypatch):
    monkeypatch.setattr("dream.config.MAX_OUTPUT_TOKENS", 4096)
    b = _backend(n_ctx=131072)
    b._local_options = {}
    assert b.performance_status()["effective"]["output_tokens"] == 4096
    monkeypatch.setattr("dream.config.MAX_OUTPUT_TOKENS", 8192)
    b._client = _FakeClient([_text_round()])
    [event async for event in b.ask("hello")]
    assert b._client.payloads[0]["max_tokens"] == 8192
