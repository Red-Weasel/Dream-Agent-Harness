"""Native Auto shell approval refreshes stale evidence without widening policy."""
import asyncio
from types import SimpleNamespace

import pytest

from dream import config
from dream.core import execution
from dream.core.execution import ExecutionScope, SandboxCapability
from dream.tui.app import App


@pytest.fixture
def app(tmp_path):
    app = object.__new__(App)
    app.workspace = tmp_path
    app.mode = 'auto'
    app._active_loop = None
    app._perm_lock = asyncio.Lock()
    app._always_allow = set()
    app.studio = None
    app.prompts, app.messages, app.approvals = [], [], []
    app.answer = 'n'
    app.renderer = SimpleNamespace(system=app.messages.append, live_pause=lambda: None,
        live_resume=lambda: None, permission_request=lambda *args: app.prompts.append(args))
    async def answer(choices):
        return app.answer
    app._read_answer = answer
    app.engine = SimpleNamespace(workspace=tmp_path, execution_scope=ExecutionScope(tmp_path),
        execution_capability=None, turn_timing=None,
        approve_command=lambda command, **kwargs: app.approvals.append((command, kwargs)))
    return app


@pytest.mark.parametrize('name', ['run_bash', f'mcp__{config.MCP_SERVER_NAME}__run_bash'])
@pytest.mark.parametrize('saved', ['missing', 'failed', 'other_scope', 'no_executable'])
async def test_auto_recovers_from_missing_or_stale_native_evidence(app, monkeypatch, name, saved):
    scope = app.engine.execution_scope
    if saved != 'missing':
        old_scope = ExecutionScope(app.workspace / 'other') if saved == 'other_scope' else scope
        app.engine.execution_capability = SandboxCapability(saved != 'failed', 'old', old_scope,
            None if saved == 'no_executable' else '/fixture/bwrap')
    calls = []
    refreshed = SandboxCapability(True, 'recovered', scope, '/fixture/bwrap')
    async def probe(scope):
        calls.append(scope)
        return refreshed
    monkeypatch.setattr(execution, 'probe_sandbox', probe)
    assert await app._decide_permission(name, {'command': 'python fixture.py'})
    assert calls == [scope]
    assert app.engine.execution_capability is refreshed
    assert app.prompts == app.approvals == []


@pytest.mark.parametrize('mode', ['plan', 'auto'])
async def test_plan_and_healthy_cached_auto_do_not_probe(app, monkeypatch, mode):
    app.mode = mode
    scope = app.engine.execution_scope
    app.engine.execution_capability = SandboxCapability(mode == 'auto', 'cached', scope, '/fixture/bwrap')
    def forbidden(scope):
        raise AssertionError('Unnecessary probe')
    monkeypatch.setattr(execution, 'probe_sandbox', forbidden)
    assert await app._decide_permission('run_bash', {'command': 'python fixture.py'}) is (mode == 'auto')
    assert not app.prompts


@pytest.mark.parametrize('name', ['Bash', 'evil__run_bash', 'mcp__evil__run_bash', 'mcp__dream__evil__run_bash'])
async def test_other_executor_and_spoofed_names_never_inherit_native_evidence(app, monkeypatch, name):
    app.engine.execution_capability = SandboxCapability(True, 'cached', app.engine.execution_scope, '/fixture/bwrap')
    def forbidden(scope):
        raise AssertionError('Other tool must not probe native containment')
    monkeypatch.setattr(execution, 'probe_sandbox', forbidden)
    assert not await app._decide_permission(name, {'command': 'python fixture.py'})
    assert len(app.prompts) == 1
    assert not app.approvals


@pytest.mark.parametrize('command', ['rm fixture.txt', 'git push origin main'])
async def test_recovery_keeps_deletion_and_publish_prompts(app, monkeypatch, command):
    async def probe(scope):
        return SandboxCapability(True, 'recovered', scope, '/fixture/bwrap')
    monkeypatch.setattr(execution, 'probe_sandbox', probe)
    assert not await app._decide_permission('run_bash', {'command': command})
    assert len(app.prompts) == 1
    assert not app.approvals


@pytest.mark.parametrize('answer,allowed', [('n', False), ('y', True), ('a', False)])
async def test_unavailable_requires_exact_host_once_and_never_session_grant(app, monkeypatch, answer, allowed):
    app.answer = answer
    async def probe(scope):
        return SandboxCapability(False, 'still unavailable', scope)
    monkeypatch.setattr(execution, 'probe_sandbox', probe)
    command = 'python fixture.py'
    assert await app._decide_permission('run_bash', {'command': command}) is allowed
    assert len(app.prompts) == 1
    assert 'host access' in app.prompts[0][1]
    assert app.approvals == ([(command, {'uncontained': True})] if allowed else [])
    assert not app._always_allow


@pytest.mark.parametrize('change', ['engine', 'mode', 'workspace', 'engine_workspace', 'scope'])
async def test_scope_change_during_probe_discards_result_and_denies(app, monkeypatch, change):
    original = app.engine
    cached = original.execution_capability
    async def probe(scope):
        if change == 'engine':
            app.engine = SimpleNamespace(**vars(original))
        elif change == 'scope':
            original.execution_scope = ExecutionScope(app.workspace / 'other')
        elif change == 'engine_workspace':
            original.workspace = app.workspace / 'other'
        elif change == 'workspace':
            app.workspace = app.workspace / 'other'
        else:
            app.mode = 'plan'
        return SandboxCapability(True, 'old scope recovered', scope, '/fixture/bwrap')
    monkeypatch.setattr(execution, 'probe_sandbox', probe)
    assert not await app._decide_permission('run_bash', {'command': 'python fixture.py'})
    assert original.execution_capability is cached
    assert app.engine.execution_capability is cached
    assert not app.prompts and not app.approvals
    assert any('changed' in message for message in app.messages)


@pytest.mark.parametrize('mode', ['ask', 'accept-edits'])
async def test_recovered_evidence_does_not_change_non_auto_shell_mode(app, monkeypatch, mode):
    app.mode = mode
    async def probe(scope):
        return SandboxCapability(True, 'recovered', scope, '/fixture/bwrap')
    monkeypatch.setattr(execution, 'probe_sandbox', probe)
    assert not await app._decide_permission('run_bash', {'command': 'python fixture.py'})
    assert len(app.prompts) == 1
    assert not app.approvals


async def test_cancellation_during_refresh_preserves_cached_failure(app, monkeypatch):
    cached = SandboxCapability(False, 'old failure', app.engine.execution_scope)
    app.engine.execution_capability = cached
    entered = asyncio.Event()
    async def probe(scope):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(execution, 'probe_sandbox', probe)
    task = asyncio.create_task(app._decide_permission('run_bash', {'command': 'python fixture.py'}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.engine.execution_capability is cached
    assert app.prompts == app.approvals == []


@pytest.mark.parametrize('available', [True, False])
async def test_desktop_native_choices_name_boundary_and_show_reason(app, monkeypatch, available):
    async def probe(scope):
        return SandboxCapability(available, 'fixture', scope, '/fixture/bwrap')
    monkeypatch.setattr(execution, 'probe_sandbox', probe)
    app.engine.execution_capability = await probe(app.engine.execution_scope)
    shown = []
    async def request(tool, args, reason, choices):
        shown.append((reason, choices))
        return 'n'
    app.studio = SimpleNamespace(client_count=1, request_permission=request)
    assert not await app._decide_permission('run_bash', {'command': 'rm frames/old.png'})
    reason, choices = shown[0]
    assert choices['y'] == ('Run in sandbox once' if available else 'Run outside sandbox once')
    assert choices.get('h') == ('Run outside sandbox once' if available else None)
    assert 'does not remember approval' in reason
    assert ('deletes files' if available else 'protection unavailable') in reason
    assert not app.approvals


async def test_provider_bash_prompt_does_not_claim_dream_sandbox(app):
    shown = []
    async def request(tool, args, reason, choices):
        shown.append((reason, choices))
        return 'n'
    app.studio = SimpleNamespace(client_count=1, request_permission=request)
    assert not await app._decide_permission('Bash', {'command': 'python fixture.py'})
    reason, choices = shown[0]
    assert choices['y'] == 'Allow once' and 'h' not in choices
    assert 'verified containment' in reason
