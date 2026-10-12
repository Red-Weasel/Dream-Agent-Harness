"""Owned fake CLI transport/lifecycle and session authorization contracts."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from dream.core.backends.antigravity_adapter import AntigravityAdapter
from dream.core.backends.cli_agent import CliAgentBackend
from dream.core.cli_review import CLIConsultation, CLIIsolationError, _answer
from dream.core.providers import get_provider


def executable(tmp_path, *, result=True, rc=0, denied=False, hang=False, read=True):
    target = tmp_path / 'agy-fixture'
    target.write_text('''#!/usr/bin/python3
import json,os,pathlib,signal,sys,time
root=pathlib.Path(__file__).parent
def stop(signum,frame):
    (root/'stopped').write_text('terminated')
    sys.exit(0)
signal.signal(signal.SIGTERM,stop)
payload=sys.stdin.read() if READ else ''
(root/'audit.json').write_text(json.dumps({'argv':sys.argv,'payload':payload,'cwd':os.getcwd(),'bridge':os.getenv('DREAM_PARENT_BRIDGE_FILE'),'session':os.getenv('DREAM_SESSION_ID')}))
print(json.dumps({'event':'init','conversation_id':'owned-conversation','init':{}}),flush=True)
print(json.dumps({'event':'step_update','step_update':{'conversation_id':'owned-conversation','step_index':1,'step_type':'agent_response','state':'DONE','text_delta':'fixture answer'}}),flush=True)
if HANG:
    while True:time.sleep(1)
if RESULT:print(json.dumps({'event':'result','result':{'status':'SUCCESS','response':'fixture answer','denied_actions':['fixture denial'] if DENIED else []}}),flush=True)
print('fixture sensitive diagnostic must stay private',file=sys.stderr)
sys.exit(RC)
'''.replace('READ', repr(read)).replace('HANG', repr(hang)).replace('RESULT', repr(result))
        .replace('DENIED', repr(denied)).replace('RC', repr(rc)))
    target.chmod(0o700)
    return target


def backend(tmp_path, target, *, mode='ask', bridge=None, timeout=5):
    adapter = AntigravityAdapter()
    adapter.cmd = str(target)
    return CliAgentBackend(adapter, system_prompt='standing rules', cwd=str(tmp_path),
        mode_getter=lambda: mode, sandbox_getter=lambda: 'read-only' if mode in ('ask', 'plan') else 'workspace-write',
        mcp_config={'env': bridge} if bridge else None, idle_timeout=timeout)


async def test_large_stdin_is_lossless_closes_and_resume_does_not_repeat_rules(tmp_path, monkeypatch):
    target = executable(tmp_path)
    monkeypatch.setenv('DREAM_PARENT_BRIDGE_FILE', 'ambient-wrong-bridge')
    monkeypatch.setenv('DREAM_SESSION_ID', 'ambient-wrong-session')
    owned = {'DREAM_PARENT_BRIDGE_FILE': 'owned-bridge', 'DREAM_SESSION_ID': 'owned-session'}
    model = backend(tmp_path, target, bridge=owned)
    await model.connect()
    prompt = 'long input ' * 18000
    events = [e async for e in model.ask(prompt)]
    receipt = json.loads((tmp_path/'audit.json').read_text())
    assert json.loads(receipt['payload'])['message']['content'] == 'standing rules\n\n'+prompt
    assert prompt not in receipt['argv']
    assert receipt['cwd'] == str(tmp_path)
    assert receipt['bridge'] == 'owned-bridge' and receipt['session'] == 'owned-session'
    assert [e.data for e in events if e.kind == 'assistant_done'] == ['fixture answer']
    assert events[-1].kind == 'result' and not events[-1].data['is_error']
    assert model._session_id == 'owned-conversation'
    events = [e async for e in model.ask('second')]
    receipt = json.loads((tmp_path/'audit.json').read_text())
    assert json.loads(receipt['payload'])['message']['content'] == 'second'
    assert receipt['argv'][receipt['argv'].index('--conversation')+1] == 'owned-conversation'
    assert model._proc is None and model._spawning is None


@pytest.mark.parametrize('rc,result,denied', [(3, True, False), (0, False, False), (0, True, True)])
async def test_failed_or_denied_terminal_never_reports_success(tmp_path, rc, result, denied):
    model = backend(tmp_path, executable(tmp_path, rc=rc, result=result, denied=denied))
    events = [e async for e in model.ask('bounded fixture')]
    assert events[-1].kind == 'error' or events[-1].data['is_error']
    assert 'fixture sensitive diagnostic' not in str(events)
    assert model._proc is None and model._spawning is None


async def test_stop_during_blocked_large_stdin_reaps_owned_process(tmp_path):
    model = backend(tmp_path, executable(tmp_path, hang=True, read=False))
    ready = asyncio.Event()
    async def consume():
        async for event in model.ask('large input '*50000):
            if event.kind == 'text_delta':
                ready.set()
    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(ready.wait(), 5)
        await model.interrupt()
        await asyncio.wait_for(task, 5)
    finally:
        await model.interrupt()
        if not task.done():task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert (tmp_path/'stopped').read_text() == 'terminated'
    assert model._proc is None and model._spawning is None


async def test_idle_timeout_stops_owned_child(tmp_path):
    model = backend(tmp_path, executable(tmp_path, hang=True), timeout=.1)
    events = [e async for e in model.ask('bounded fixture')]
    assert events[-1].kind == 'error' and 'idle timeout' in events[-1].data
    assert (tmp_path/'stopped').exists()
    assert model._proc is None


async def test_advisor_uses_same_framing_and_private_stderr(tmp_path, monkeypatch):
    monkeypatch.setenv('DREAM_PARENT_BRIDGE_FILE', 'foreign-parent')
    monkeypatch.setenv('DREAM_SESSION_ID', 'foreign-session')
    target = executable(tmp_path)
    monkeypatch.setattr('dream.core.cli_review.locate_command', lambda _: str(target))
    consult = CLIConsultation(get_provider('antigravity'), cwd=tmp_path)
    try:
        consult.prepare()
        assert await consult.run('advisor fixture') == 'fixture answer'
        receipt = json.loads((tmp_path/'audit.json').read_text())
        assert json.loads(receipt['payload'])['message']['content'] == 'advisor fixture'
        assert '--dangerously-skip-permissions' not in receipt['argv']
        assert receipt['bridge'] is None and receipt['session'] is None
    finally:
        await consult._stop()
        consult.close()


def test_registration_never_persists_a_session_capability():
    argv = AntigravityAdapter().mcp_register_argv('dream_test', {
        'DREAM_PARENT_BRIDGE_FILE': '/private/session-capability', 'DREAM_SESSION_ID': 'session-secret',
        'OTHER_SECRET': 'sensitive'}, ['/usr/bin/python3', '-m', 'dream.mcp'])
    assert 'DREAM_REQUIRE_PARENT_BRIDGE=1' in argv
    assert not any(x in ' '.join(argv) for x in ('session-capability','session-secret','sensitive'))


async def test_unrelated_native_session_cannot_fall_back_to_standalone_memory(monkeypatch):
    from dream.mcp import server
    from dream.mcp.bridge import ParentBridgeClient
    monkeypatch.setenv('DREAM_REQUIRE_PARENT_BRIDGE', '1')
    monkeypatch.setattr(ParentBridgeClient, 'from_environment', lambda: None)
    def unexpected():raise AssertionError('standalone memory must never open')
    monkeypatch.setattr(server, 'build_context_from_env', unexpected)
    with pytest.raises(RuntimeError, match='standalone memory access refused'):
        await server.serve()


def test_advisor_duplicate_terminal_and_denial_fail_closed():
    line = json.dumps({'event':'result','result':{'status':'SUCCESS','response':'answer'}}).encode()
    with pytest.raises(CLIIsolationError):_answer('antigravity', line+b'\n'+line)
    denied=json.dumps({'event':'result','result':{'status':'SUCCESS','response':'answer','denied_actions':[{}]}}).encode()
    with pytest.raises(CLIIsolationError):_answer('antigravity',denied)


async def test_node_env_repair_preserves_owned_main_bridge(tmp_path, monkeypatch):
    target=executable(tmp_path)
    monkeypatch.setenv('DREAM_PARENT_BRIDGE_FILE','foreign-parent')
    monkeypatch.setenv('DREAM_SESSION_ID','foreign-session')
    def repair(name,path,env=None):return dict(os.environ if env is None else env)
    monkeypatch.setattr('dream.core.backends.cli_agent.node_child_env',repair)
    model=backend(tmp_path,target,bridge={'DREAM_PARENT_BRIDGE_FILE':'owned-bridge','DREAM_SESSION_ID':'owned-session'})
    await model.connect()
    events=[e async for e in model.ask('fixture')]
    receipt=json.loads((tmp_path/'audit.json').read_text())
    assert receipt['bridge']=='owned-bridge' and receipt['session']=='owned-session'


async def test_optimizer_refuses_native_planning_before_preparation(tmp_path, monkeypatch):
    from dream import prompt_optimizer
    from dream.core.evaluator import ReviewSettings
    from dream.core.profiles import resolve_profile
    def unexpected(*args,**kwargs):raise AssertionError('Optimizer must refuse before native process setup')
    monkeypatch.setattr(CLIConsultation,'prepare',unexpected)
    provider=get_provider('antigravity')
    settings=ReviewSettings(provider=provider,model='gemini-3.8-flash-high',profile=resolve_profile(provider))
    with pytest.raises(ValueError,match='enforced read-only'):
        await prompt_optimizer.optimize({'draft':'draft a fixture prompt'},[],tmp_path,settings)


@pytest.mark.parametrize('override',[False,True])
async def test_optimizer_app_selection_paths_refuse_before_native_prepare(tmp_path,monkeypatch,override):
    from types import SimpleNamespace
    from dream.tui.app import App
    from dream.core.evaluator import ReviewSettings
    from dream.core.profiles import resolve_profile
    from dream.core import settings as runtime_settings
    anti=get_provider('antigravity');main=get_provider('codex') if override else anti
    profile=resolve_profile(main)
    app=App.__new__(App);app.workspace=tmp_path
    app.engine=SimpleNamespace(provider=main,model='fixture-model',profile=profile,session_id='fixture-session')
    monkeypatch.setattr(runtime_settings,'role',lambda _: {'provider':'antigravity'} if override else None)
    if override:
        monkeypatch.setattr(ReviewSettings,'resolve',lambda *a,**kw:ReviewSettings(provider=anti,model='fixture-model',profile=profile))
    def unexpected(*a,**kw):raise AssertionError('No native process preparation')
    monkeypatch.setattr(CLIConsultation,'prepare',unexpected)
    payload={'_source_session':'fixture-session','_source_generation':0,'_source_provider':main.key,
             '_source_model':'fixture-model','_source_profile':profile,
             'draft':{'workspace':str(tmp_path),'session_id':'fixture-session','draft':'fixture prompt'},'files':[]}
    with pytest.raises(ValueError,match='enforced read-only'):
        await app._prepare_optimized_prompt(payload)


async def test_success_with_stderr_permission_denial_is_incomplete(tmp_path,monkeypatch):
    target=executable(tmp_path)
    s=target.read_text().replace('fixture sensitive diagnostic must stay private','Permission denied for fixture private tool')
    target.write_text(s)
    model=backend(tmp_path,target)
    events=[e async for e in model.ask('fixture')]
    assert events[-1].kind=='result' and events[-1].data['is_error']
    assert 'fixture private tool' not in str(events)
    monkeypatch.setattr('dream.core.cli_review.locate_command',lambda _:str(target))
    consult=CLIConsultation(get_provider('antigravity'),cwd=tmp_path)
    try:
        consult.prepare()
        with pytest.raises(CLIIsolationError,match='denied required actions'):
            await consult.run('fixture')
    finally:
        await consult._stop();consult.close()
