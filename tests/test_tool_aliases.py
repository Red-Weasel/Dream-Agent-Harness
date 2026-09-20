"""A model reaching for `bash` means `run_bash` (live 2026-09-20: it used run_bash
correctly 19 times, then drifted and burned a round trip on "unknown tool")."""
import pytest

from test_schema_deferral import _backend, _tool

pytestmark = pytest.mark.asyncio


def _bash_backend():
    ran = []

    async def handler(args):
        ran.append(args)
        return {"content": "ok"}

    tool = _tool("run_bash")
    tool.handler = handler
    return _backend([tool]), ran


async def test_bash_reaches_run_bash():
    b, ran = _bash_backend()
    content, failed = await b._exec_tool("bash", {"command": "ls"})
    assert not failed and ran == [{"command": "ls"}]


async def test_an_alias_cannot_widen_a_subagent_scope():
    """The scope check must see the real name, so an alias is never a way in."""
    b, ran = _bash_backend()
    content, failed = await b._exec_tool("bash", {"command": "ls"}, allowed={"read_file"})
    assert failed and "not available to this subagent" in content and ran == []


async def test_a_real_tool_named_like_an_alias_wins():
    """If a tool genuinely is called `bash`, the alias must not hijack it."""
    ran = []

    async def handler(args):
        ran.append("real")
        return {"content": "ok"}

    real = _tool("bash")
    real.handler = handler
    b = _backend([real])
    content, failed = await b._exec_tool("bash", {})
    assert not failed and ran == ["real"]


async def test_an_unknown_name_still_errors():
    b, _ = _bash_backend()
    content, failed = await b._exec_tool("teleport", {})
    assert failed and "unknown tool" in content
