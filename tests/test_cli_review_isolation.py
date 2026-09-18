"""Real fixture children, synthetic sign-ins only: never invoke a live provider."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core import moe
from dream.core.cli_review import CLIConsultation, CLIIsolationError, _answer
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
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'XAI_API_KEY',
                'DREAM_PARENT_BRIDGE_FILE', 'NODE_OPTIONS', 'PYTHONPATH', 'HTTP_PROXY', 'HTTPS_PROXY',
                'DBUS_SESSION_BUS_ADDRESS', 'SSH_AUTH_SOCK', 'GROK_CONFIG'):
        monkeypatch.setenv(key, 'must-not-reach-advisor')
    return host


def fixture_cli(tmp_path, monkeypatch, provider, behavior='answer'):
    executable = tmp_path / ('fixture-' + provider)
    audit = tmp_path / (provider + '-audit.json')
    # The child asserts the launch contract, consumes synthetic auth, and emits
    # real wire-format examples. It never reports credential values or env dumps.
    executable.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys, time, subprocess
provider = PROVIDER
behavior = BEHAVIOR
audit = pathlib.Path(AUDIT)
home = pathlib.Path(os.environ['HOME'])
assert home != pathlib.Path.cwd() and pathlib.Path.cwd().name == 'work'
assert all(k not in os.environ for k in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY','XAI_API_KEY','DREAM_PARENT_BRIDGE_FILE','NODE_OPTIONS','PYTHONPATH','HTTP_PROXY','HTTPS_PROXY','DBUS_SESSION_BUS_ADDRESS','SSH_AUTH_SOCK','GROK_CONFIG'))
assert (home.stat().st_mode & 0o777) == 0o700
argv = sys.argv[1:]
if provider == 'codex':
    state = pathlib.Path(os.environ['CODEX_HOME'])
    assert '--ignore-user-config' in argv and '--ignore-rules' in argv and '--ephemeral' in argv
    assert argv[argv.index('--sandbox')+1] == 'read-only'
    assert 'shell_tool' in argv and 'apps' in argv and 'hooks' in argv and 'plugins' in argv
    assert 'tools.view_image=false' in argv and 'mcp_servers={}' in argv
    assert not (state / 'config.toml').exists()
    name = 'auth.json'
    prompt = sys.stdin.read()
elif provider == 'grok':
    state = pathlib.Path(os.environ['GROK_HOME'])
    assert argv[argv.index('--tools')+1] == 'read_file'
    assert 'read_file,search_tool,use_tool,Agent' in argv and 'MCPTool' in argv
    assert '--no-subagents' in argv and '--disable-web-search' in argv
    assert 'auto_update = false' in (state / 'config.toml').read_text()
    assert '[mcp_servers.hostile]' not in (state / 'config.toml').read_text()
    name = 'auth.json'
    prompt = pathlib.Path(argv[argv.index('--prompt-file')+1]).read_text()
else:
    state = pathlib.Path(os.environ['GEMINI_CLI_HOME']) / '.gemini'
    settings = json.loads((state / 'settings.json').read_text())
    # Gemini 0.52's headless startup rejects a fresh, untrusted cwd with exit 55.
    trust_file = state / 'trustedFolders.json'
    if not trust_file.exists():
        print('fixture-secret-do-not-return', file=sys.stderr)
        sys.exit(55)
    assert json.loads(trust_file.read_text()) == {str(pathlib.Path.cwd().resolve()): 'TRUST_FOLDER'}
    assert trust_file.stat().st_mode & 0o777 == 0o600
    assert '--skip-trust' not in argv and 'GEMINI_CLI_TRUST_WORKSPACE' not in os.environ
    assert argv[argv.index('--approval-mode')+1] == 'plan'
    assert settings['tools']['core'] == [] and settings['hooksConfig']['enabled'] is False
    assert settings['mcpServers'] == {} and settings['admin']['mcp']['enabled'] is False
    assert settings['admin']['extensions']['enabled'] is False
    policy = pathlib.Path(argv[argv.index('--policy')+1]).read_text()
    assert 'toolName = "*"' in policy and 'decision = "deny"' in policy
    name = 'oauth_creds.json'
    prompt = sys.stdin.read()
assert json.loads((state / name).read_text())['synthetic_credential'] == 'fixture-only'
assert (state / name).stat().st_mode & 0o777 == 0o600
info = {'private_root': str(home.parent), 'pid': os.getpid(), 'model': argv[argv.index('-m')+1] if '-m' in argv else argv[argv.index('--model')+1]}
if behavior == 'hang':
    child = subprocess.Popen(['/usr/bin/python3','-c','import time; time.sleep(60)'])
    info['child'] = child.pid
audit.write_text(json.dumps(info))
if behavior == 'hang':
    time.sleep(60)
if behavior in ('error', 'untrusted'):
    print('fixture-secret-do-not-return', file=sys.stderr)
    sys.exit(55 if behavior == 'untrusted' else 3)
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
'''.replace('PROVIDER', repr(provider)).replace('BEHAVIOR', repr(behavior)).replace('AUDIT', repr(str(audit))))
    executable.chmod(0o700)
    monkeypatch.setattr('dream.core.cli_review.shutil.which', lambda _: str(executable))
    return audit


@pytest.mark.parametrize('provider', ['codex', 'grok', 'gemini'])
async def test_signed_in_advisors_survive_with_isolated_configuration(provider, tmp_path, monkeypatch, signed_in):
    audit = fixture_cli(tmp_path, monkeypatch, provider)
    original = {p: p.read_bytes() for p in signed_in.rglob('*') if p.is_file()}
    answer = await moe.consult_advisor(provider, 'Give independent advice', cwd=str(tmp_path), model='fixture-model')
    assert answer == 'Independent fixture dissent'
    observed = json.loads(audit.read_text())
    assert observed['model'] == 'fixture-model'
    assert not Path(observed['private_root']).exists()
    assert all(p.read_bytes() == data for p, data in original.items())


@pytest.mark.parametrize('provider', ['codex', 'grok', 'gemini'])
async def test_cli_reviewer_uses_only_bound_reader_and_preserves_global_context(provider, tmp_path, monkeypatch, signed_in):
    from dream.tools import context
    sentinel = object()
    monkeypatch.setattr(context, '_CTX', sentinel)
    fixture_cli(tmp_path, monkeypatch, provider, 'read')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'artifact.txt').write_text('inspectable acceptance evidence')
    reader = ScopedReader(workspace)
    backend = review_backend(ReviewSettings(get_provider(provider), 'fixture-model'), reader.tools(), 'Review', workspace)
    assert await collect_review(backend, 'Check the artifact', 3) == 'VERDICT: PASS\nGAPS: none'
    assert str(workspace / 'artifact.txt') in reader.inspected
    assert context._CTX is sentinel


async def test_timeout_kills_fixture_descendants_and_removes_credentials(tmp_path, monkeypatch, signed_in):
    audit = fixture_cli(tmp_path, monkeypatch, 'grok', 'hang')
    task = asyncio.create_task(moe.consult_advisor('grok', 'q', model='fixture-model', timeout=.5))
    for _ in range(100):
        if audit.exists():
            break
        await asyncio.sleep(.01)
    info = json.loads(audit.read_text())
    assert 'unavailable' in await task
    assert not Path(info['private_root']).exists()
    for pid in (info['pid'], info['child']):
        status = Path(f'/proc/{pid}/stat')
        assert not status.exists() or status.read_text().split()[2] == 'Z'


async def test_cli_failure_does_not_echo_auth_diagnostics(tmp_path, monkeypatch, signed_in):
    fixture_cli(tmp_path, monkeypatch, 'codex', 'error')
    answer = await moe.consult_advisor('codex', 'q', model='fixture-model')
    assert 'exited 3' in answer and 'fixture-secret' not in answer


async def test_gemini_trust_failure_is_classified_without_private_diagnostics(tmp_path, monkeypatch, signed_in):
    fixture_cli(tmp_path, monkeypatch, 'gemini', 'untrusted')
    answer = await moe.consult_advisor('gemini', 'q', model='fixture-model')
    assert 'exited 55' in answer and 'FatalUntrustedWorkspaceError' in answer
    assert 'fixture-secret' not in answer and 'check sign-in' not in answer


async def test_gemini_private_trust_does_not_copy_or_modify_host_trust(tmp_path, monkeypatch, signed_in):
    host_trust = signed_in / '.gemini' / 'trustedFolders.json'
    original = json.dumps({str(signed_in): 'DO_NOT_TRUST'}).encode()
    host_trust.write_bytes(original)
    fixture_cli(tmp_path, monkeypatch, 'gemini')
    answer = await moe.consult_advisor('gemini', 'q', model='fixture-model')
    assert answer == 'Independent fixture dissent'
    assert host_trust.read_bytes() == original


def test_credential_symlink_rejected_and_temp_scope_removed(tmp_path, monkeypatch, signed_in):
    fixture_cli(tmp_path, monkeypatch, 'codex')
    auth = signed_in / '.codex' / 'auth.json'
    auth.unlink()
    auth.symlink_to(tmp_path / 'missing')
    call = CLIConsultation(get_provider('codex'))
    with pytest.raises(CLIIsolationError, match='safely'):
        call.prepare()
    assert call._temp is None


@pytest.mark.parametrize('provider,events', [
    ('codex', [{'type':'item.started','item':{'type':'mcp_tool_call'}}]),
    ('grok', [{'type':'tool_call','toolCallId':'unexpected'}]),
    ('gemini', [{'type':'tool_use','tool_name':'run_shell_command'}]),
    ('codex', [{'type':'item.completed','item':{'type':'agent_message','text':'VERDICT: PASS\nGAPS: none'}}]),
    ('gemini', [{'type':'result','status':'error'}]),
])
def test_native_tool_or_incomplete_result_cannot_be_accepted(provider, events):
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
