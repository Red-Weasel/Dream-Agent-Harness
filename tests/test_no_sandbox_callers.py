"""No shell sandbox (the owner, 2026-10-11): the places besides the main prompt that still decided a shell command with
the sandbox probe's evidence decide it as run_bash itself does. With the evidence, a read-only command (`cat
~/.ssh/...` included) counted as contained and was allowed in Ask and Accept edits, though it runs on the host now.
Auto is unchanged: it allows every shell command either way."""
from types import SimpleNamespace

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from dream.core.backends.anthropic import consult_permission
from dream.core.engine import Engine
from dream.core.execution import ExecutionScope, SandboxCapability

READS = ['ls', 'cat notes.txt', 'cat ~/.ssh/id_rsa', 'grep -r token /etc']


def evidence(workspace):
    scope = ExecutionScope(workspace)
    return scope, SandboxCapability(True, 'fixture', scope, '/fixture/bwrap')   # the probe says bubblewrap works


@pytest.mark.parametrize('mode', ['ask', 'accept-edits'])
@pytest.mark.parametrize('command', READS)
async def test_a_consult_refuses_a_shell_command_the_owner_would_be_asked_about(tmp_path, mode, command):
    decide = consult_permission(mode, str(tmp_path), (), lambda: evidence(tmp_path))
    result = await decide('run_bash', {'command': command}, None)
    assert isinstance(result, PermissionResultDeny) and 'cannot ask the owner' in result.message, result


@pytest.mark.parametrize('command', READS + ['rm -rf build', 'git push origin main'])
async def test_a_consult_in_auto_still_runs_every_shell_command(tmp_path, command):
    decide = consult_permission('auto', str(tmp_path), (), lambda: evidence(tmp_path))
    assert isinstance(await decide('run_bash', {'command': command}, None), PermissionResultAllow)


@pytest.mark.parametrize('command', READS)
async def test_an_engine_without_an_owner_callback_does_not_count_a_read_as_contained(tmp_path, command):
    engine = object.__new__(Engine)
    scope, capability = evidence(tmp_path)
    engine._user_can_use_tool, engine.workspace = None, tmp_path
    engine.execution_scope, engine.execution_capability = scope, capability
    assert await engine._authorize_tool('run_bash', {'command': command}) is False
