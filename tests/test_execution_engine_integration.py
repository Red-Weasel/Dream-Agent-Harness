"""Real Engine lifecycle/wrapper, mocked provider startup, real native execution."""
from types import SimpleNamespace

import pytest

from dream.core import execution as ex, policy
from dream.core.engine import Engine
from dream.tools import native
from dream.tools.context import ToolContext
from dream import hooks


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    asked = []
    human = {"allow": False}
    mode = {"value": "auto"}

    async def permission(name, args):
        decision, _ = policy.decide(name, args, mode["value"], tmp_path,
            execution_scope=instance.execution_scope, execution_capability=instance.execution_capability)
        if decision == "ask":
            asked.append(args["command"])
            return human["allow"]
        return decision == "allow"

    async def no_hooks(*args, **kwargs):
        return SimpleNamespace(allowed=True, outcomes=[])
    monkeypatch.setattr(hooks, "run_hooks", no_hooks)
    instance = Engine(provider="openai", workspace=tmp_path, can_use_tool=permission,
                      mode_getter=lambda: mode["value"])

    async def startup():
        # No provider connection, model/store initialization, discovery, or GPU.
        # Engine.start itself, its context/wrapper and authorization are real.
        instance._tool_context = ToolContext(None, None, None, instance.session_id, workspace=tmp_path)
        instance.execution_capability = await ex.probe_sandbox(instance.execution_scope)
        instance._session_tools = {"run_bash": instance._wrap_tool(native.run_bash)}
        instance._started = True
    monkeypatch.setattr(instance, "_start", startup)
    await instance.start()
    if not instance.execution_capability.available:
        pytest.skip(instance.execution_capability.reason)
    return instance, asked, human, mode


async def dispatch(engine, command):
    if not await engine._authorize_tool("run_bash", {"command": command}):
        return None
    return await engine._session_tools["run_bash"].handler({"command": command})


async def test_engine_auto_runs_routine_shell_in_its_workspace_without_prompt(engine, tmp_path):
    instance, asked, _, _ = engine
    result = await dispatch(instance, "/usr/bin/python3 -c 'from pathlib import Path; Path(\"routine.txt\").write_text(\"contained\")'")
    assert not result.get("is_error"), result
    assert (tmp_path / "routine.txt").read_text() == "contained"
    assert asked == []
    assert not instance._command_approvals


async def test_engine_dangerous_commands_ask_and_each_approval_covers_one_attempt(engine, tmp_path):
    instance, asked, human, _ = engine
    path = tmp_path / "keep.txt"
    path.write_text("keep")
    assert await dispatch(instance, "rm keep.txt") is None
    assert await dispatch(instance, "git push origin main") is None
    assert asked == ["rm keep.txt", "git push origin main"] and path.exists()
    human["allow"] = True
    result = await dispatch(instance, "rm keep.txt")
    assert not result.get("is_error"), result
    assert not path.exists() and not instance._command_approvals
    path.write_text("new data requires a new approval")
    result = await instance._session_tools["run_bash"].handler({"command": "rm keep.txt"})
    assert result["is_error"] and path.exists()


async def test_engine_plan_and_context_mismatch_cannot_execute_native_shell(engine, tmp_path):
    instance, asked, _, mode = engine
    mode["value"] = "plan"
    assert await dispatch(instance, "echo should-not-run > marker") is None
    assert asked == [] and not (tmp_path / "marker").exists()
    mode["value"] = "auto"
    other = tmp_path / "other"
    other.mkdir()
    instance._tool_context.workspace = other
    result = await dispatch(instance, "echo should-not-run > marker")
    assert result["is_error"] and "another workspace" in result["content"][0]["text"]
    assert not (tmp_path / "marker").exists() and not (other / "marker").exists()
