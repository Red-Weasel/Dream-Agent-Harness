"""DREAM-089: a plugin agent that declares no tools inherits the working set, not nothing.

2026-09-23 09:50: the understand skill dispatched the plugin's `project-scanner` agent through the local `task` tool.
Upstream's agent files declare no `tools:` frontmatter (Claude Code gives agents its default toolset), and Dream gave
the subagent exactly the declared list -- empty -- so it probed tool names for eleven rounds and reported "none of the
required tools are available". An undeclared list now means every tool the parent has except delegation itself,
filtered by the same extension settings; a declared list is still honoured as written.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

import dream.config as config
from dream import plugins
from dream.core import subagents
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.subagents import LocalSubagentSpec


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    d = root / "mapper"
    (d / "agents").mkdir(parents=True)
    (d / "plugin.yaml").write_text("name: mapper\ndescription: maps things\nversion: 1.0\n")
    (d / "agents" / "scanner.md").write_text("---\nname: scanner\ndescription: Scans\n---\nYou scan projects.\n")
    (d / "agents" / "greeter.md").write_text("---\nname: greeter\ndescription: Greets\ntools: read_file, recall\n---\nYou greet.\n")
    monkeypatch.setattr(config, "PLUGINS_DIR", root)
    plugins.load(root)
    yield root
    plugins.load(tmp_path / "none")


def test_an_undeclared_tool_list_means_the_working_set_and_a_declared_one_is_kept(tree):
    local = subagents.local_subagents()
    assert local["scanner"].tool_names == ("*",)
    assert local["greeter"].tool_names == ("read_file", "recall")
    sdk = subagents.subagents()
    assert sdk["scanner"].tools is None                       # the SDK's "inherit the parent's tools"
    assert sdk["greeter"].tools == [config.tool_id("read_file"), config.tool_id("recall")]


class _Tool:
    def __init__(self, name):
        self.name, self.description, self.input_schema = name, "", {"type": "object", "properties": {}}

    async def handler(self, args):
        return {"content": "ok", "is_error": False}


def _backend(names):
    p = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=True, api_key=lambda: "n")
    async def cb(name, args):
        return True
    return OpenAICompatBackend(provider=p, model="m", system_prompt="s", tools=[_Tool(n) for n in names], permission_cb=cb)


def test_the_backend_resolves_the_working_set_without_delegation_tools():
    b = _backend(["read_file", "run_bash", "ua_run", "task", "fork_verifier_agent"])
    everything = LocalSubagentSpec("scanner", "Scans", "You scan.", ("*",))
    assert b._subagent_tool_names(everything) == ["read_file", "run_bash", "ua_run"]
    declared = LocalSubagentSpec("greeter", "Greets", "You greet.", ("read_file", "recall", "nope"))
    assert b._subagent_tool_names(declared) == ["read_file"]     # a missing tool is skipped, never invented
