"""Regressions for streamed tool calls and installed skill reads, without models."""

import json
from types import SimpleNamespace

import httpx
import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.skills import loader
from dream.tools import installed_skill_tools


def _backend(tools=()):
    return OpenAICompatBackend(
        provider=SimpleNamespace(key="test", label="Test", multimodal=False),
        model="test", system_prompt="test", tools=list(tools), permission_cb=None,
    )


def _stream(*chunks):
    return "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"


async def test_fragmented_function_name_executes_the_complete_tool():
    calls = []

    async def handler(args):
        calls.append(args)
        return {"content": [{"type": "text", "text": "found"}]}

    tool = SimpleNamespace(name="skill_open", description="Read skill", handler=handler,
                           input_schema={"type": "object", "properties": {"name": {"type": "string"}}})
    backend = _backend([tool])
    responses = iter([
        _stream(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "skill_", "arguments": '{"name":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "open", "arguments": '"alpha"}'}}]}, "finish_reason": "tool_calls"}]},
        ),
        _stream({"choices": [{"delta": {"content": "Read alpha."}, "finish_reason": "stop"}]}),
    ])
    async with httpx.AsyncClient(base_url="http://test/v1/", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=next(responses), headers={"content-type": "text/event-stream"})
    )) as client:
        backend._client = client
        events = [event async for event in backend.ask("Read alpha")]
    assert calls == [{"name": "alpha"}]
    assert next(e.data for e in events if e.kind == "tool_result")["is_error"] is False


async def test_standard_mcp_error_flag_is_preserved():
    async def handler(args):
        return {"content": [{"type": "text", "text": "Could not read skill"}], "isError": True}

    tool = SimpleNamespace(name="skill_open", description="Read skill", handler=handler,
                           input_schema={"type": "object", "properties": {}})
    text, failed = await _backend([tool])._exec_tool("skill_open", {})
    assert failed is True
    assert "Could not read" in text


@pytest.mark.parametrize("content", ['["alpha", "beta"]', '[Read the instructions](references/guide.md)'])
async def test_bracket_wrapped_bundled_content_is_success(tmp_path, monkeypatch, content):
    manifest = tmp_path / "SKILL.md"
    manifest.write_text("---\nname: alpha\ndescription: Test skill\n---\nRead the data.")
    (tmp_path / "data.txt").write_text(content)
    skills, _ = loader.discover([tmp_path])
    monkeypatch.setattr(installed_skill_tools, "_CACHE", skills)
    result = await installed_skill_tools.skill_file.handler({"name": "alpha", "path": "data.txt"})
    assert not result.get("is_error")
    assert content in result["content"][0]["text"]


async def test_bracket_wrapped_manifest_is_success(tmp_path, monkeypatch):
    content = "[Read references/guide.md before continuing]"
    (tmp_path / "SKILL.md").write_text(content)
    skills, _ = loader.discover([tmp_path])
    monkeypatch.setattr(installed_skill_tools, "_CACHE", skills)
    result = await installed_skill_tools.skill_open.handler({"name": skills[0].name})
    assert not result.get("is_error")
    assert content in result["content"][0]["text"]


async def test_missing_skill_file_remains_an_error(tmp_path, monkeypatch):
    (tmp_path / "SKILL.md").write_text("---\nname: alpha\ndescription: Test skill\n---\nRead the data.")
    skills, _ = loader.discover([tmp_path])
    monkeypatch.setattr(installed_skill_tools, "_CACHE", skills)
    result = await installed_skill_tools.skill_file.handler({"name": "alpha", "path": "missing.txt"})
    assert result["is_error"] is True
    assert "no file" in result["content"][0]["text"]


async def test_large_opted_in_catalog_is_searchable_without_expanding_wake_index(tmp_path, monkeypatch):
    skills = [loader.FileSkill(name=f"skill-{i:03d}", description="A useful workflow " * 30,
                               manifest=tmp_path / "SKILL.md", root=tmp_path, source="test")
              for i in range(250)]
    curated = loader.FileSkill(name="coding", description="Make focused code changes.",
                               manifest=tmp_path / "SKILL.md", root=tmp_path,
                               source="bundled", curated=True)
    monkeypatch.setattr(installed_skill_tools, "_CACHE", [curated, *skills])
    lines = installed_skill_tools.index_lines()
    assert len("\n".join(lines)) <= 1600
    assert {line.split(" — ", 1)[0] for line in lines} == {"coding"}
    found = await installed_skill_tools.skill_find.handler({"query": "skill-249"})
    assert "skill-249" in found["content"][0]["text"]
    assert "1 installed skill(s)" in found["content"][0]["text"]
    broad = await installed_skill_tools.skill_find.handler({"query": ""})
    assert broad["content"][0]["text"].count("• ") == 25
    assert "narrow the query" in broad["content"][0]["text"]
