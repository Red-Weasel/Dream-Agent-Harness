"""Real fixture children, synthetic sign-ins only: never invoke a live provider.

DREAM-136 replaced the isolated contract these tests pinned with main-path parity."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core import moe
from dream.core.cli_review import CLIIsolationError, _answer
from dream.core.evaluator import ReviewSettings, ScopedReader, collect_review, review_backend
from dream.core.providers import get_provider


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    host = tmp_path / 'host'
    for key, name in (('codex', 'auth.json'), ('grok', 'auth.json'), ('gemini', 'oauth_creds.json')):
        folder = host / ('.' + key)
        folder.mkdir(parents=True)
        (folder / name).write_text('{"synthetic_credential":"fixture-only"}')
        (folder / 'config.toml').write_text('[mcp_servers.hostile]\ncommand = "touch"\n')
        (folder / 'settings.json').write_text('{"hooksConfig":{"enabled":true},"mcpServers":{"hostile":{}}}')
    monkeypatch.setenv('HOME', str(host))
    monkeypatch.setenv('CODEX_HOME', str(host / '.codex'))
    monkeypatch.setenv('GROK_HOME', str(host / '.grok'))
    monkeypatch.setenv('GEMINI_CLI_HOME', str(host))
    # DREAM-136: the advisor inherits the owner's environment, as the main path does.
    monkeypatch.setenv('OPENAI_API_KEY', 'inherited-like-the-main-path')
    return host


def fixture_cli(tmp_path, monkeypatch, provider, behavior='answer'):
    executable = tmp_path / ('fixture-' + provider)
    audit = tmp_path / (provider + '-audit.json')
    # DREAM-136: the child asserts the main-path launch contract (the owner's own
    # HOME and CLI homes, the Dream workspace as cwd, no isolation flags) and emits
    # real wire-format examples. It never reports credential values or env dumps.
    executable.write_text("""#!/usr/bin/python3
import json, os, pathlib, sys, time, subprocess
provider = PROVIDER
behavior = BEHAVIOR
audit = pathlib.Path(AUDIT)
host = pathlib.Path(HOST)
assert pathlib.Path(os.environ['HOME']) == host
assert os.environ['OPENAI_API_KEY'] == 'inherited-like-the-main-path'
argv = sys.argv[1:]
for flag in ('--ignore-user-config', '--ignore-rules', '--ephemeral', '--disable', '--deny', '--tools',
             '--max-turns', '--disable-web-search', '--policy', 'mcp_servers={}', 'tools.view_image=false'):
    assert flag not in argv, flag
if provider == 'codex':
    assert pathlib.Path(os.environ['CODEX_HOME']) == host / '.codex'
    assert argv[argv.index('--sandbox')+1] == 'workspace-write'
    assert pathlib.Path(argv[argv.index('-C')+1]) == pathlib.Path.cwd()
    prompt = sys.stdin.read()
elif provider == 'grok':
    assert pathlib.Path(os.environ['GROK_HOME']) == host / '.grok'
    assert argv[argv.index('--permission-mode')+1] == 'bypassPermissions'
    prompt_file = pathlib.Path(argv[argv.index('--prompt-file')+1])
    prompt = prompt_file.read_text()
else:
    assert pathlib.Path(os.environ['GEMINI_CLI_HOME']) == host
    if behavior == 'untrusted':
        print('fixture-secret-do-not-return', file=sys.stderr)
        sys.exit(55)
    assert argv[argv.index('--approval-mode')+1] == 'yolo'
    assert pathlib.Path(argv[argv.index('--include-directories')+1]) == pathlib.Path.cwd()
    prompt = sys.stdin.read()
info = {'cwd': os.getcwd(), 'pid': os.getpid(), 'model': argv[argv.index('--model')+1],
        'prompt_dir': str(prompt_file.parent) if provider == 'grok' else None}
if behavior == 'hang':
    child = subprocess.Popen(['/usr/bin/python3','-c','import time; time.sleep(60)'])
    info['child'] = child.pid
audit.write_text(json.dumps(info))
if behavior == 'hang':
    time.sleep(60)
if behavior == 'error':
    print('fixture-secret-do-not-return', file=sys.stderr)
    sys.exit(3)
answer = 'Independent fixture dissent'
if behavior == 'read':
    answer = json.dumps({'review_read':{'name':'read_file','arguments':{'path':'artifact.txt'}}}) if 'READ RESULT:' not in prompt else 'VERDICT: PASS\\nGAPS: none'
if provider == 'codex':
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':answer}}))
    print(json.dumps({'type':'turn.completed'}))
elif provider == 'grok':
    print(json.dumps({'type':'text','data':answer}))
    print(json.dumps({'type':'end','stopReason':'EndTurn'}))
else:
    print(json.dumps({'type':'message','role':'assistant','content':answer}))
    print(json.dumps({'type':'result','status':'success'}))
""".replace('PROVIDER', repr(provider)).replace('BEHAVIOR', repr(behavior)).replace('AUDIT', repr(str(audit)))
     .replace('HOST', repr(os.environ['HOME'])))
    executable.chmod(0o700)
    monkeypatch.setattr('dream.core.cli_review.shutil.which', lambda _: str(executable))
    return audit


@pytest.mark.parametrize('provider', ['codex', 'grok', 'gemini'])
async def test_signed_in_advisors_use_owner_configuration_in_place(provider, tmp_path, monkeypatch, signed_in):
    audit = fixture_cli(tmp_path, monkeypatch, provider)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    original = {p: p.read_bytes() for p in signed_in.rglob('*') if p.is_file()}
    answer = await moe.consult_advisor(provider, 'Give independent advice', cwd=str(workspace),
                                       model='fixture-model', mode='auto')
    assert answer == 'Independent fixture dissent'
    observed = json.loads(audit.read_text())
    assert observed['model'] == 'fixture-model'
    assert Path(observed['cwd']).resolve() == workspace.resolve()
    if observed['prompt_dir']:
        assert not Path(observed['prompt_dir']).exists()
    # Dream copies, rewrites and deletes nothing of the owner's CLI configuration.
    assert {p: p.read_bytes() for p in signed_in.rglob('*') if p.is_file()} == original


@pytest.mark.parametrize('provider', ['codex', 'grok', 'gemini'])
async def test_cli_reviewer_bound_reader_still_works_and_preserves_global_context(provider, tmp_path, monkeypatch, signed_in):
    from dream.tools import context
    sentinel = object()
    monkeypatch.setattr(context, '_CTX', sentinel)
    fixture_cli(tmp_path, monkeypatch, provider, 'read')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'artifact.txt').write_text('inspectable acceptance evidence')
    reader = ScopedReader(workspace)
    settings = ReviewSettings(get_provider(provider), 'fixture-model', mode='auto')
    backend = review_backend(settings, reader.tools(), 'Review', workspace)
    assert await collect_review(backend, 'Check the artifact', 3) == 'VERDICT: PASS\nGAPS: none'
    assert str(workspace / 'artifact.txt') in reader.inspected
    assert context._CTX is sentinel


async def test_timeout_kills_fixture_descendants_and_removes_prompt_scope(tmp_path, monkeypatch, signed_in):
    audit = fixture_cli(tmp_path, monkeypatch, 'grok', 'hang')
    task = asyncio.create_task(moe.consult_advisor('grok', 'q', cwd=str(tmp_path), model='fixture-model',
                                                   mode='auto', timeout=.5))
    for _ in range(100):
        if audit.exists():
            break
        await asyncio.sleep(.01)
    info = json.loads(audit.read_text())
    assert 'unavailable' in await task
    assert not Path(info['prompt_dir']).exists()
    for pid in (info['pid'], info['child']):
        status = Path(f'/proc/{pid}/stat')
        assert not status.exists() or status.read_text().split()[2] == 'Z'


async def test_cli_failure_does_not_echo_auth_diagnostics(tmp_path, monkeypatch, signed_in):
    fixture_cli(tmp_path, monkeypatch, 'codex', 'error')
    answer = await moe.consult_advisor('codex', 'q', cwd=str(tmp_path), model='fixture-model', mode='auto')
    assert 'exited 3' in answer and 'fixture-secret' not in answer


async def test_gemini_trust_failure_is_classified_without_private_diagnostics(tmp_path, monkeypatch, signed_in):
    fixture_cli(tmp_path, monkeypatch, 'gemini', 'untrusted')
    answer = await moe.consult_advisor('gemini', 'q', cwd=str(tmp_path), model='fixture-model', mode='auto')
    assert 'exited 55' in answer and 'FatalUntrustedWorkspaceError' in answer
    assert 'fixture-secret' not in answer and 'check sign-in' not in answer


async def test_gemini_consult_does_not_modify_host_trust(tmp_path, monkeypatch, signed_in):
    host_trust = signed_in / '.gemini' / 'trustedFolders.json'
    original = json.dumps({str(signed_in): 'DO_NOT_TRUST'}).encode()
    host_trust.write_bytes(original)
    fixture_cli(tmp_path, monkeypatch, 'gemini')
    answer = await moe.consult_advisor('gemini', 'q', cwd=str(tmp_path), model='fixture-model', mode='auto')
    assert answer == 'Independent fixture dissent'
    assert host_trust.read_bytes() == original


@pytest.mark.parametrize('provider,events', [
    ('codex', [{'type':'item.completed','item':{'type':'agent_message','text':'VERDICT: PASS\nGAPS: none'}}]),
    ('codex', [{'type':'turn.failed'}]),
    ('grok', [{'type':'text','data':'partial'}, {'type':'end','stopReason':'MaxTurns'}]),
    ('gemini', [{'type':'result','status':'error'}]),
])
def test_incomplete_result_cannot_be_accepted(provider, events):
    with pytest.raises(CLIIsolationError):
        _answer(provider, '\n'.join(json.dumps(e) for e in events).encode())


def test_review_profile_follows_same_provider_and_resolves_independent_provider(tmp_path):
    from dream.core.profiles import resolve_profile
    worker = SimpleNamespace(provider=get_provider('machx'), model='local-model', profile=resolve_profile(get_provider('machx')))
    assert ReviewSettings.resolve(worker).profile is worker.profile
    other = ReviewSettings.resolve(worker, provider='openai', model='explicit-model')
    assert other.provider.key == 'openai' and other.profile is not worker.profile
    backend = review_backend(ReviewSettings(worker.provider, worker.model), [], 'review', tmp_path)
    assert backend.profile is not None


def test_explicit_reviewer_model_resolves_its_own_context_profile(monkeypatch):
    from dataclasses import replace
    from dream.core.profiles import resolve_profile
    from dream.core import evaluator
    provider = get_provider('machx')
    worker = SimpleNamespace(provider=provider, model='worker-model', profile=resolve_profile(provider))
    seen = []
    def resolve(selected, *, model=None):
        seen.append((selected.key, model))
        return replace(worker.profile, context_limit=8192)
    monkeypatch.setattr(evaluator, 'resolve_profile', resolve)
    settings = ReviewSettings.resolve(worker, model='small-reviewer')
    assert seen == [('machx', 'small-reviewer')]
    assert settings.profile.context_limit == 8192 and settings.profile is not worker.profile
