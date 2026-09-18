"""Explicit remembered Bash choices do not turn into blanket host permission."""
from types import SimpleNamespace

import pytest

from dream.core import execution
from dream.core.execution import SandboxCapability, ExecutionScope
from test_auto_bash_recovery import app


async def setup(app, monkeypatch, available=True, answer='a'):
    app.mode = 'ask'
    async def probe(scope):
        return SandboxCapability(available, 'fixture', scope, '/fixture/bwrap' if available else None)
    monkeypatch.setattr(execution, 'probe_sandbox', probe)
    app.engine.execution_capability = await probe(app.engine.execution_scope)
    shown = []
    async def request(tool, args, reason, choices):
        shown.append((reason, choices))
        return answer
    app.studio = SimpleNamespace(client_count=1, request_permission=request)
    return shown


async def test_sandbox_always_choice_avoids_repeat_prompt(app, monkeypatch):
    shown = await setup(app, monkeypatch)
    assert await app._decide_permission('run_bash', {'command': 'python render.py'})
    assert shown[0][1]['a'] == 'Always allow in sandbox (session)'
    assert await app._decide_permission('run_bash', {'command': 'python render.py'})
    assert len(shown) == 1 and not app.approvals
    assert 'exact command' in shown[0][0]


@pytest.mark.parametrize('available', [True, False])
async def test_host_always_reissues_exact_host_grant(app, monkeypatch, available):
    shown = await setup(app, monkeypatch, available, 'ha')
    command = 'python render.py'
    assert await app._decide_permission('run_bash', {'command': command})
    assert shown[0][1]['ha'] == 'Always allow outside sandbox (session)'
    assert await app._decide_permission('run_bash', {'command': command})
    assert len(shown) == 1
    assert app.approvals == [(command, {'uncontained': True})] * 2


@pytest.mark.parametrize('change', ['command', 'workspace', 'engine', 'scope', 'plan', 'executor'])
async def test_host_grant_does_not_spread(app, monkeypatch, change):
    shown = await setup(app, monkeypatch, False, 'ha')
    command = 'python render.py'
    assert await app._decide_permission('run_bash', {'command': command})
    if change == 'command':
        command += ' --different'
    elif change == 'workspace':
        app.workspace /= 'other'
        app.engine.workspace = app.workspace
        app.engine.execution_scope = ExecutionScope(app.workspace)
    elif change == 'engine':
        app.engine = SimpleNamespace(**vars(app.engine))
    elif change == 'scope':
        app.engine.execution_scope = ExecutionScope(app.workspace, read_roots=(app.workspace / 'read-extra',))
    elif change == 'plan':
        app.mode = 'plan'
    async def deny(*args):
        shown.append(args)
        return 'n'
    app.studio.request_permission = deny
    assert not await app._decide_permission('Bash' if change == 'executor' else 'run_bash', {'command': command})
    assert len(app.approvals) == 1


async def test_sandbox_grant_never_becomes_host_grant(app, monkeypatch):
    shown = await setup(app, monkeypatch)
    assert await app._decide_permission('run_bash', {'command': 'python render.py'})
    await setup(app, monkeypatch, False, 'n')
    assert not await app._decide_permission('run_bash', {'command': 'python render.py'})
    assert not app.approvals


@pytest.mark.parametrize('command', ['rm important.txt', 'git push origin main', 'sudo apt upgrade'])
async def test_consequential_commands_keep_once_only_choices(app, monkeypatch, command):
    shown = await setup(app, monkeypatch, True, 'ha')
    assert not await app._decide_permission('run_bash', {'command': command})
    assert not {'a', 'ha'} & shown[0][1].keys()
    assert not app._always_allow and not app.approvals


async def test_mode_change_during_prompt_invalidates_approval(app, monkeypatch):
    await setup(app, monkeypatch)
    async def switch(*args):
        app.mode = 'plan'
        return 'a'
    app.studio.request_permission = switch
    assert not await app._decide_permission('run_bash', {'command': 'python render.py'})
    assert not app._always_allow


@pytest.mark.parametrize('available,choice,label', [
    (True, 'a', 'Always allow in sandbox (session)'),
    (False, 'ha', 'Always allow outside sandbox (session)'),
])
async def test_actual_desktop_always_choice_is_consumed_once(app, chat, monkeypatch, available, choice, label):
    import asyncio
    from playwright.async_api import expect
    srv, page, _, _ = chat
    await setup(app, monkeypatch, available, choice)
    app.studio = srv
    await page.set_viewport_size({'width': 390, 'height': 844})
    pending = asyncio.create_task(app._decide_permission('run_bash', {'command': 'python render.py'}))
    try:
        button = page.get_by_role('button', name=label, exact=True)
        await expect(button).to_be_visible()
        await button.click()
        assert await asyncio.wait_for(pending, 2)
        await expect(button).to_have_count(0)
        assert await app._decide_permission('run_bash', {'command': 'python render.py'})
        await expect(button).to_have_count(0)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


# Real UI fixture uses a temporary authenticated Studio server and fake providers.
from test_desktop_chat import chat
