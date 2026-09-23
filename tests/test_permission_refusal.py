"""DREAM-085: a refused tool call names its real reason.

2026-09-23: the owner approved a shell command with "Always allow in sandbox", yet the model was told "Declined by the
user." -- every refusal of the permission check read that way: Dream's own (a policy block, shell authorization that
changed while the prompt was open) and a check that raised. The owner's No still reads "Declined by the user."; a
refusal by Dream reads "Not run: <reason>"; a check that fails reads as that failure.
"""

import asyncio
from types import SimpleNamespace

import pytest

from dream.core.backends.anthropic import AnthropicBackend
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.execution import ExecutionScope, SandboxCapability
from dream.core.permission_refusal import PermissionRefused
from dream.tui.app import App


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
    p = SimpleNamespace(key="machx", label="MachX", base_url="http://x/v1", multimodal=True, api_key=lambda: "n")
    return OpenAICompatBackend(provider=p, model="m", system_prompt="s", tools=tools, permission_cb=cb)


async def test_owner_decline_still_reads_declined():
    async def no(name, args):
        return False
    tool = _RecTool("preview_tui")
    text, is_error = await _openai_backend([tool], no)._exec_tool("preview_tui", {})
    assert is_error and text == "Declined by the user."
    assert tool.ran == 0


async def test_dream_refusal_names_its_reason_and_is_not_blamed_on_the_owner():
    async def refuse(name, args):
        raise PermissionRefused("shell authorization changed during approval; retry the command")
    tool = _RecTool("preview_tui")
    text, is_error = await _openai_backend([tool], refuse)._exec_tool("preview_tui", {})
    assert is_error and text == "Not run: shell authorization changed during approval; retry the command"
    assert "Declined" not in text and tool.ran == 0


async def test_a_failing_permission_check_reads_as_a_failure():
    async def broken(name, args):
        raise RuntimeError("boom")
    tool = _RecTool("preview_tui")
    text, is_error = await _openai_backend([tool], broken)._exec_tool("preview_tui", {})
    assert is_error and text == "Not run: the permission check failed (RuntimeError: boom)"
    assert tool.ran == 0


async def test_anthropic_backend_reports_the_same_reasons():
    b = object.__new__(AnthropicBackend)
    b._budget_failures = None
    async def refuse(name, args):
        raise PermissionRefused("blocked by Dream's policy: plan mode — no commands")
    async def no(name, args):
        return False
    b.permission_cb = refuse
    assert (await b._permission_handler("Bash", {}, None)).message == "Not run: blocked by Dream's policy: plan mode — no commands"
    b.permission_cb = no
    assert (await b._permission_handler("Bash", {}, None)).message == "Declined by the user."


# ---- the TUI's permission check: its own refusals raise PermissionRefused at the callback boundary --------------------

@pytest.fixture
def app(tmp_path):
    app = object.__new__(App)
    app.workspace = tmp_path
    app.mode = "accept-edits"   # the desktop's interactive default: a shell command asks (auto would run it contained)
    app._active_loop = None
    app._perm_lock = asyncio.Lock()
    app._always_allow = set()
    app.studio = None
    app.prompts, app.messages = [], []
    app.renderer = SimpleNamespace(system=app.messages.append, live_pause=lambda: None, live_resume=lambda: None,
                                   permission_request=lambda *args: app.prompts.append(args))
    scope = ExecutionScope(tmp_path)
    app.engine = SimpleNamespace(workspace=tmp_path, execution_scope=scope,
                                 execution_capability=SandboxCapability(True, "ok", scope, executable="/usr/bin/bwrap"),
                                 turn_timing=None, approve_command=lambda command, **kwargs: None)
    async def no_snapshot(tool_name, tool_input):
        return None
    app._snapshot = no_snapshot
    return app


OUTSIDE = "ls /dev/dri; blender --version"   # the owner's probe of 2026-09-23 (accept-edits: a prompt)


async def test_owner_no_at_the_prompt_is_a_plain_decline(app):
    async def answer(choices):
        return "n"
    app._read_answer = answer
    assert await app._permission("run_bash", {"command": OUTSIDE}) is False
    assert len(app.prompts) == 1


async def test_always_in_sandbox_grants_this_exact_command_for_the_session(app):
    async def answer(choices):
        assert "always in sandbox" in choices
        return "a"
    app._read_answer = answer
    assert await app._permission("run_bash", {"command": OUTSIDE}) is True
    async def must_not_ask(choices):
        raise AssertionError("the same command asked again after 'always'")
    app._read_answer = must_not_ask
    assert await app._permission("run_bash", {"command": OUTSIDE}) is True
    assert len(app.prompts) == 1


async def test_authorization_change_during_the_prompt_is_a_refusal_with_its_reason(app):
    async def answer(choices):   # the owner approves, but the shell scope was replaced while the prompt was open
        app.engine.execution_scope = ExecutionScope(app.workspace / "elsewhere")
        return "a"
    app._read_answer = answer
    with pytest.raises(PermissionRefused, match="changed during approval"):
        await app._permission("run_bash", {"command": OUTSIDE})
    # the check itself keeps its boolean contract
    app.engine.execution_scope = ExecutionScope(app.workspace)
    app.engine.execution_capability = SandboxCapability(True, "ok", app.engine.execution_scope, executable="/usr/bin/bwrap")
    async def answer2(choices):
        app.engine.execution_scope = ExecutionScope(app.workspace / "elsewhere")
        return "y"
    app._read_answer = answer2
    assert await app._decide_permission("run_bash", {"command": OUTSIDE}) is False


async def test_a_policy_block_is_a_refusal_with_its_reason(app):
    app.mode = "plan"
    async def must_not_ask(choices):
        raise AssertionError("a blocked command must not prompt")
    app._read_answer = must_not_ask
    with pytest.raises(PermissionRefused, match="plan mode"):
        await app._permission("run_bash", {"command": "ls"})


# ---- DREAM-088: a re-probe of the same sandbox during the prompt is not a change of authorization ---------------------

async def test_an_equal_capability_reprobed_during_approval_still_grants(app):
    """2026-09-23 08:21 and 09:46: the first shell command of a session was refused right after the owner approved it
    ("shell authorization changed during approval"). Dream re-probes the sandbox and stores a NEW SandboxCapability
    object for the same scope while the prompt is open; the post-answer check compared objects by identity."""
    async def answer(choices):
        cap = app.engine.execution_capability
        app.engine.execution_capability = SandboxCapability(cap.available, cap.reason, cap.scope, executable=cap.executable)
        return "a"
    app._read_answer = answer
    assert await app._permission("run_bash", {"command": OUTSIDE}) is True


async def test_a_capability_that_really_changed_during_approval_is_still_refused(app):
    async def answer(choices):   # the sandbox became unavailable while the owner was reading the prompt
        app.engine.execution_capability = SandboxCapability(False, "bwrap gone", app.engine.execution_scope, executable=None)
        return "y"
    app._read_answer = answer
    with pytest.raises(PermissionRefused, match="changed during approval"):
        await app._permission("run_bash", {"command": OUTSIDE})
