"""Finding 2 (relocated): both backends must route tool calls through the mode
policy, not pre-approve everything.

- The Claude backend pre-approved ALL mcp__dream__* via `allowed_tools`, so the
  permission callback never fired for Dream tools — plan mode / workspace
  isolation didn't constrain custom (arbitrary-Python) tools or `forget`.
- The OpenAI-compat backend only gated `run_bash`/`write_file`, so a custom tool
  ran unprompted in every mode.

Both now share one capability check: read-only + own-mind memory tools stay
free; everything else (every self-built custom tool included) is gated.
"""

from __future__ import annotations

from types import SimpleNamespace

from dream import config
from dream.core import policy
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.tools import registry


class _RecTool:
    def __init__(self, name):
        self.name = name
        self.description = ""
        self.input_schema = {"type": "object", "properties": {}}
        self.ran = 0

    async def handler(self, args):
        self.ran += 1
        return {"content": "ok", "is_error": False}


def _openai_backend(tools, cb):
    p = SimpleNamespace(
        key="machx", label="MachX", base_url="http://x/v1",
        multimodal=True, api_key=lambda: "n",
    )
    return OpenAICompatBackend(
        provider=p, model="m", system_prompt="s", tools=tools, permission_cb=cb
    )


async def test_openai_backend_gates_by_capability():
    seen: list[str] = []

    async def cb(name, args):
        seen.append(name)
        return True

    tools = [_RecTool("recall"), _RecTool("remember"), _RecTool("write_file"),
             _RecTool("run_bash"), _RecTool("preview_tui")]
    b = _openai_backend(tools, cb)

    # Read-only and own-mind memory tools run without a prompt.
    await b._exec_tool("recall", {"query": "x"})
    await b._exec_tool("remember", {"title": "t", "body": "b"})
    assert seen == []

    # A file write, a shell command, AND a self-built custom tool all prompt.
    await b._exec_tool("write_file", {"path": "x", "content": "y"})
    await b._exec_tool("run_bash", {"command": "ls"})
    await b._exec_tool("preview_tui", {})
    assert set(seen) == {"write_file", "run_bash", "preview_tui"}


async def test_openai_backend_denied_tool_reports_declined():
    async def deny(name, args):
        return False

    tool = _RecTool("preview_tui")
    b = _openai_backend([tool], deny)
    text, is_error = await b._exec_tool("preview_tui", {})
    assert is_error and "Declined" in text
    assert tool.ran == 0  # denial happens BEFORE the handler runs


def test_registry_exempts_only_readonly_and_memory_tools():
    built = registry.build()
    exempt = set(built["exempt_tool_ids"])
    for name in built["names"]:
        tid = config.tool_id(name)
        if policy.capability(name) in policy.AUTO_CAPS:
            assert tid in exempt, f"{name} should be pre-approved"
        else:
            assert tid not in exempt, f"{name} must NOT be pre-approved"
    # The bundled self-built tool writes files + spawns chrome — never pre-approved.
    assert config.tool_id("preview_tui") not in exempt


def test_registry_rejects_double_underscore_custom_tool(monkeypatch):
    """A custom tool whose name embeds '__' could spoof a built-in's capability
    class; the registry must drop it at load time."""
    from pathlib import Path

    class FakeTool:
        def __init__(self, name):
            self.name = name
            self.description = "d"
            self.input_schema = {"type": "object", "properties": {}}

            async def _h(a):
                return {"content": "x"}

            self.handler = _h

    monkeypatch.setattr(
        registry, "_load_custom_tools",
        lambda: ([(FakeTool("evil__recall"), Path("/tmp/x.py"))], []),
    )
    built = registry.build()
    assert "evil__recall" not in built["names"]
    assert config.tool_id("evil__recall") not in built["exempt_tool_ids"]
    assert any("reserved" in w for w in built["warnings"])


def test_anthropic_preapproves_only_exempt_tools():
    from dream.core.backends.anthropic import SAFE_BUILTINS, AnthropicBackend

    b = AnthropicBackend(
        system_prompt="s",
        mcp_server=object(),
        preapproved_tool_ids=["mcp__dream__recall", "mcp__dream__remember"],
        agents=None,
        permission_cb=None,
        model=None,
    )
    allowed = b._build_options().allowed_tools
    assert "mcp__dream__recall" in allowed and "mcp__dream__remember" in allowed
    for bi in SAFE_BUILTINS:
        assert bi in allowed
    # Mutating Dream tools are absent → they route through can_use_tool instead.
    assert "mcp__dream__write_file" not in allowed
    assert "mcp__dream__preview_tui" not in allowed
