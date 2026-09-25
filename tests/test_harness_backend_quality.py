"""Observable backend outcomes and bounded, turn-specific tool preparation."""
from types import SimpleNamespace

import pytest

from dream import extensions
from dream.core import tool_budget_schemas
from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.profiles import PROFILES
from dream.core.subagents import LocalSubagentSpec
from test_compaction import _FakeClient, _sse, _text_round, _tool_round
from test_local_subagents import _FakeClient as DelegatedClient, _sub_toolcall
from test_schema_deferral import _tool


@pytest.fixture(autouse=True)
def isolated_extension_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAM_EXTENSION_SETTINGS", str(tmp_path / "extensions.json"))


def backend(tools=(), subagents=None):
    result = OpenAICompatBackend(
        provider=SimpleNamespace(key="openai", label="Fixture", base_url="http://fixture/v1",
                                 multimodal=False, api_key=lambda: "unused"),
        model="fixture", system_prompt="Fixture instructions", tools=list(tools),
        permission_cb=None, profile=PROFILES["lean"], subagents=subagents,
    )
    result.n_ctx = 16384
    return result


def names(payload):
    return {schema["function"]["name"] for schema in payload["tools"]}


@pytest.mark.asyncio
async def test_truncated_answer_is_failed_without_discarding_partial_text():
    b = backend()
    b._client = _FakeClient([[
        _sse({"choices": [{"delta": {"content": "Useful but unfinished"},
                            "finish_reason": "length"}]}), "data: [DONE]",
    ]])
    events = [event async for event in b.ask("Explain this")]
    result = next(event.data for event in events if event.kind == "result")
    assert result["is_error"] is True
    assert result["subtype"] == "length"
    assert next(event.data for event in events if event.kind == "assistant_done") == "Useful but unfinished"
    assert b.messages[-1]["content"] == "Useful but unfinished"
    # DREAM-117 (fix #85): a cut with no completed tool call is continued twice, then the turn ends as
    # incomplete; the partial text stays on every attempt and nothing that ran is repeated (nothing ran).
    assert len(b._client.payloads) == 3
    assert [m["content"] for m in b.messages if m.get("role") == "assistant"] == ["Useful but unfinished"] * 3


@pytest.mark.asyncio
async def test_delegated_round_exhaustion_is_failed(monkeypatch):
    monkeypatch.setattr(openai_compat, "_SUB_MAX_ROUNDS", 1)
    b = backend([_tool("read_file")], {
        "reader": LocalSubagentSpec("reader", "Read evidence", "Read carefully", ("read_file",)),
    })
    b._client = DelegatedClient(post_scripts=[_sub_toolcall("read_file", {"path": "fixture"})])
    text, failed = await b._run_subagent("reader", "Read and explain")
    assert failed is True
    assert "round limit" in text


@pytest.mark.asyncio
async def test_prepared_tool_reaches_first_request_within_budget_and_does_not_leak():
    tools = [_tool(f"cheap_{i:02}") for i in range(30)] + [_tool("extract_document", nprops=25)]
    b = backend(tools)
    b._client = _FakeClient([_text_round()])
    b.prepare_turn(iter(["extract_document"]))
    assert [event async for event in b.ask("Read the document")]
    first = b._client.payloads[-1]
    assert "extract_document" in names(first)
    assert tool_budget_schemas.measure(first["tools"]) <= 1638
    assert [event async for event in b.ask("Unrelated question")]
    assert "extract_document" not in names(b._client.payloads[-1])


@pytest.mark.asyncio
async def test_oversized_prepared_schema_stays_deferred_and_unknown_names_do_not_appear():
    b = backend([_tool("read_file"), _tool("oversized", nprops=500)])
    b._client = _FakeClient([_text_round()])
    b.prepare_turn(["oversized", "not_installed"])
    assert [event async for event in b.ask("Use the large tool")]
    payload = b._client.payloads[0]
    assert names(payload) == {"read_file", "tool_schema"}
    assert tool_budget_schemas.measure(payload["tools"]) <= 1638
    assert "oversized" in b._deferred_now


@pytest.mark.asyncio
async def test_lookup_reveal_survives_current_turn_but_not_next_turn():
    tools = [_tool(f"cheap_{i:02}") for i in range(30)] + [_tool("rare_tool", nprops=25)]
    b = backend(tools)
    b._client = _FakeClient([_tool_round("tool_schema", '{"name":"rare_tool"}'), _text_round()])
    assert [event async for event in b.ask("Use a rare tool")]
    assert "rare_tool" not in names(b._client.payloads[0])
    assert "rare_tool" in names(b._client.payloads[1])
    assert [event async for event in b.ask("Unrelated task")]
    assert "rare_tool" not in names(b._client.payloads[-1])


@pytest.mark.asyncio
async def test_preparation_cannot_enable_a_disabled_tool():
    calls = []
    b = backend([_tool("extract_document", calls=calls)])
    b._client = _FakeClient([_text_round()])
    b.prepare_turn(["extract_document"])
    extensions.set_enabled("tool:extract_document", False)
    assert [event async for event in b.ask("Read document")]
    assert "extract_document" not in names(b._client.payloads[-1])
    text, failed = await b._exec_tool("extract_document", {})
    assert failed and "disabled" in text
    assert calls == []
    extensions.set_enabled("tool:extract_document", True)
    b.prepare_turn(["extract_document"])
    assert [event async for event in b.ask("Try with the enabled tool")]
    assert "extract_document" in names(b._client.payloads[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("rounds,subtype,executions", [(1, "tool_round_limit", 1), (10, "loop_detected", 2)])
async def test_stopped_tool_loop_keeps_salvage_but_reports_failure(monkeypatch, rounds, subtype, executions):
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", rounds)
    calls = []
    b = backend([_tool("read_file", calls=calls)])
    b._client = DelegatedClient(
        stream_scripts=[_tool_round("read_file", '{"path":"fixture"}')],
        post_scripts=[{"choices": [{"message": {"content": "Partial evidence found", "tool_calls": []},
                                    "finish_reason": "stop"}]}],
    )
    events = [event async for event in b.ask("Keep investigating")]
    result = next(event.data for event in events if event.kind == "result")
    assert result["is_error"] is True
    assert result["subtype"] == subtype
    assert next(event.data for event in events if event.kind == "assistant_done") == "Partial evidence found"
    assert b.messages[-1]["content"] == "Partial evidence found"
    assert len(calls) == executions
    assert len(b._client.posted) == 1
    assert not b._client.posted[0].get("tools")  # Salvage cannot repeat tool side effects.


@pytest.mark.asyncio
async def test_round_limit_identifies_rounds_not_individual_calls(monkeypatch):
    monkeypatch.setattr(openai_compat, "_MAX_TOOL_ROUNDS", 1)
    calls = []
    b = backend([_tool("read_file", calls=calls)])
    script = [_sse({"choices": [{"delta": {"tool_calls": [
        {"index": index, "id": f"call{index}", "type": "function",
         "function": {"name": "read_file", "arguments": '{"path":"file%d"}' % index}}
        for index in range(2)
    ]}, "finish_reason": "tool_calls"}]}), "data: [DONE]"]
    b._client = DelegatedClient(stream_scripts=[script], post_scripts=[{
        "choices": [{"message": {"content": "Partial evidence", "tool_calls": []},
                     "finish_reason": "stop"}]}])
    events = [event async for event in b.ask("Inspect both files")]
    result = next(event.data for event in events if event.kind == "result")
    assert len(calls) == 2
    assert result["tool_round_limit"] == 1
    assert result["is_error"] is True
    assert any("tool-round limit (1)" in str(event.data) for event in events if event.kind == "system")
    assert "1-tool-call limit" not in str(b._client.posted)
