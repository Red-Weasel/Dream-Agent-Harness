"""2026-10-12, the MachX session's Flash-Next training run (harness-ab train1012, task 0032_442_32442771_qa_3): the
model wrote `<function=run_b= >` for run_bash. fd936ef recovers that text as a call to an unknown tool `run_b=`. The
error now names the closest tool this call may use, so a small model recovers in one round; the guessed tool never
runs in its place. Only a garbled name gets a guess (gate round 1): a well-formed one is a tool the model believes
in, and a near name (`task` -> task_add) would point it at a different tool."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend, _parse_text_tool_calls


class _RecTool:
    def __init__(self, name):
        self.name = name
        self.description = ""
        self.input_schema = {"type": "object", "properties": {"command": {"type": "string"}}}
        self.ran = 0

    async def handler(self, args):
        self.ran += 1
        return {"content": "ok", "is_error": False}


def _backend(tools):
    async def allow(name, args):
        return True
    provider = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=False,
                               api_key=lambda: "n")
    return OpenAICompatBackend(provider=provider, model="m", system_prompt="s", tools=tools, permission_cb=allow)


def test_the_garbled_call_is_recovered_as_an_unknown_tool():
    _, calls = _parse_text_tool_calls(
        "<tool_call>\n<function=run_b= >\n<parameter=command>\nls\n</parameter>\n</function>\n</tool_call>")
    assert [call["name"] for call in calls] == ["run_b="]


async def test_a_garbled_name_names_the_closest_tool_and_runs_nothing():
    bash, read = _RecTool("run_bash"), _RecTool("read_file")
    backend = _backend([bash, read])
    text, is_error = await backend._exec_tool("run_b=", {"command": "ls"})
    assert is_error and text == "Error: unknown tool 'run_b='. Did you mean 'run_bash'?"
    assert bash.ran == 0 and read.ran == 0


@pytest.mark.parametrize("name", ["teleport", "task", "Task", "todo_write", "forget_all", "Read", "create_file"])
async def test_a_well_formed_name_gets_no_guess(name):
    tools = [_RecTool(n) for n in ("run_bash", "read_file", "task_add", "memory_write", "forget", "ram_read")]
    backend = _backend(tools)
    text, is_error = await backend._exec_tool(name, {})
    assert is_error and text == f"Error: unknown tool '{name}'."
    assert not any(tool.ran for tool in tools)


async def test_the_guess_stays_inside_what_the_call_may_use():
    backend = _backend([_RecTool("run_bash"), _RecTool("read_file")])
    # a sub-agent allowed `run_b=` (an entry no tool has) and read_file: run_bash is not its to be told about
    text, is_error = await backend._exec_tool("run_b=", {}, allowed={"run_b=", "read_file"})
    assert is_error and "run_bash" not in text


async def test_the_guess_stays_inside_a_scoped_step():
    backend = _backend([_RecTool("run_bash"), _RecTool("read_file")])
    backend.tool_scope = frozenset({"run_b=", "read_file"})
    text, is_error = await backend._exec_tool("run_b=", {})
    assert is_error and "run_bash" not in text


async def test_a_disabled_tool_is_never_the_guess():
    from dream import extensions
    backend = _backend([_RecTool("run_bash"), _RecTool("read_file")])
    extensions.set_enabled("tool:run_bash", False)
    text, is_error = await backend._exec_tool("run_b=", {})
    assert is_error and text == "Error: unknown tool 'run_b='."
