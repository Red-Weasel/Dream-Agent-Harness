"""A subagent that calls a tool outside its scope is told which tools it has.

2026-09-23, live session 20260923-094144-f5ed: two plugin subagents with no tools (DREAM-089) spent 8 and 5 requests
probing names (`Bash`, `bash`, `shell`, `read`, ...) because the refusal only said "Error: tool 'X' is not available
to this subagent." -- the last of those requests ran 452 s to the 8,192-token ceiling. The refusal now names the
tools the subagent can call, or says it has none.
"""
from types import SimpleNamespace

from dream.core.backends.openai_compat import OpenAICompatBackend


class _Tool:
    def __init__(self, name):
        self.name, self.description, self.input_schema = name, "", {"type": "object", "properties": {}}
        self.ran = 0

    async def handler(self, args):
        self.ran += 1
        return {"content": "ok", "is_error": False}


def _backend(names):
    p = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=True, api_key=lambda: "n")
    async def cb(name, args):
        return True
    tools = [_Tool(n) for n in names]
    return OpenAICompatBackend(provider=p, model="m", system_prompt="s", tools=tools, permission_cb=cb), tools


async def test_an_out_of_scope_call_names_the_tools_the_subagent_has():
    b, tools = _backend(["read_file", "list_dir", "run_bash"])
    text, is_error = await b._exec_tool("Bash", {"command": "ls"}, allowed={"read_file", "list_dir"})
    assert is_error and "not available to this subagent" in text
    assert text.endswith("Its tools: list_dir, read_file.")     # `Bash` is aliased to run_bash, which is named as refused
    assert all(t.ran == 0 for t in tools)


async def test_a_subagent_with_no_tools_is_told_so_instead_of_probing():
    b, tools = _backend(["read_file", "run_bash"])
    text, is_error = await b._exec_tool("run_bash", {"command": "ls"}, allowed=set())
    assert is_error and "not available to this subagent" in text
    assert "no tools" in text and "task prompt" in text
    assert all(t.ran == 0 for t in tools)
