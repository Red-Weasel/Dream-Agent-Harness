"""DREAM-218, the Frontier review loop's model calls (F3; ADR-070): one call() per step on the lean postures -- Claude
without Claude Code's preset, tools or the owner's settings; Codex isolated, read-only, low effort, the prompt on
stdin; Grok read-only through a prompt file -- each in a new private folder outside any git repository, never
loading a model, metered on its own and against the run's RunMeter. Scripted fakes only: fake CLIs on PATH, a fake
SDK query, fake HTTP invokers. No model, no network, no GPU."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import time

import claude_agent_sdk as sdk
import pytest

from dream.core import moe
from dream.core.context_budget import estimate

SYSTEM = 'You are a judge in a Frontier review loop.'
PROMPT = 'Score this draft.'
SECRET = 'FRONTIER_TEST_API_TOKEN'


def fc():
    """The module under test, imported in each test so that each one fails on its own while the module is missing."""
    return importlib.import_module('dream.workflows.frontier_calls')


def persona(provider, model='', name='Judge'):
    return {'name': name, 'provider': provider, 'model': model, 'voice': 'A voice.'}


@pytest.fixture
def private_root(tmp_path):
    """The private root every call folder must be made under (conftest points Sleepwalk's roots here)."""
    return tmp_path / 'private-run-root'


@pytest.fixture
def workspace(tmp_path):
    folder = tmp_path / 'workspace'
    folder.mkdir()
    return folder


# --- fakes ----------------------------------------------------------------------------------------------------------

FAKE_CLI = r'''#!/usr/bin/python3
import os
argv = os.sys.argv[1:]
if argv[:2] != ['mcp', 'list']:
    with open(PIDS, 'w') as out:
        out.write(f'{os.getpid()} {os.getpgid(0)}')
import json, pathlib, sys, time
audit = pathlib.Path(AUDIT)
if argv[:2] == ['mcp', 'list']:
    audit.with_name('mcp-list.json').write_text(json.dumps({'argv': argv, 'env': sorted(os.environ)}))
    print(json.dumps([{'name': 'docs', 'enabled': False}]))
    sys.exit(0)
prompt = pathlib.Path(argv[argv.index('--prompt-file') + 1]).read_text() if KEY == 'grok' else sys.stdin.read()
here = pathlib.Path.cwd()
audit.write_text(json.dumps({'argv': argv, 'cwd': str(here), 'mode': oct(here.stat().st_mode & 0o777),
                             'entries': sorted(p.name for p in here.iterdir()), 'prompt': prompt,
                             'env': sorted(os.environ)}))
if MODE == 'sleep':
    time.sleep(600)
if KEY == 'codex':
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': ANSWER}}))
    print(json.dumps({'type': 'turn.completed',
                      'usage': {'input_tokens': 1200, 'cached_input_tokens': 800, 'output_tokens': 90}}))
else:
    print(json.dumps({'type': 'text', 'data': ANSWER}))
    print(json.dumps({'type': 'end', 'stopReason': 'EndTurn'}))
'''


def fake_cli(tmp_path, monkeypatch, key, mode='answer', answer='{"hook_3s": 8}'):
    """A fake `codex` or `grok` on PATH (shutil.which answers it) that records what it was given."""
    executable = tmp_path / ('fake-' + key)
    audit, pids = tmp_path / (key + '-audit.json'), tmp_path / (key + '-pids.txt')
    source = FAKE_CLI
    for name, value in (('PIDS', str(pids)), ('AUDIT', str(audit)), ('KEY', key), ('MODE', mode), ('ANSWER', answer)):
        source = source.replace(name, repr(value))
    executable.write_text(source)
    executable.chmod(0o700)
    monkeypatch.setattr('dream.core.cli_review.shutil.which', lambda _: str(executable))
    return audit, pids


def codex_home(tmp_path, monkeypatch):
    home = tmp_path / 'codex-home'
    home.mkdir()
    (home / 'config.toml').write_text('[mcp_servers.docs]\ncommand = "docs"\n')
    monkeypatch.setenv('CODEX_HOME', str(home))


def install_query(monkeypatch, usage):
    seen = {'queries': 0}

    async def query(*, prompt, options):
        seen['queries'] += 1
        seen['options'] = options
        seen['prompts'] = [message async for message in prompt]
        folder = Path(options.cwd)
        seen['cwd'] = (folder.is_dir(), oct(folder.stat().st_mode & 0o777), sorted(p.name for p in folder.iterdir()))
        yield sdk.AssistantMessage(content=[sdk.TextBlock(text='A lean answer')], model='fixture')
        yield sdk.ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                                session_id='fixture', usage=usage)
    monkeypatch.setattr(sdk, 'query', query)
    return seen


def inside_git(path: Path) -> bool:
    return any((p / '.git').exists() for p in (path, *path.parents))


def group_gone(pids: Path) -> bool:
    pgid = int(pids.read_text().split()[1])
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


# --- lean Claude ----------------------------------------------------------------------------------------------------

async def test_lean_claude_options_match_the_plans_list(monkeypatch, private_root, workspace):
    monkeypatch.setenv(SECRET, 'secret')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sign-in')
    seen = install_query(monkeypatch, {'input_tokens': 10, 'cache_read_input_tokens': 5, 'output_tokens': 7})
    out = await fc().call(persona('anthropic', 'claude-opus-5-5'), SYSTEM, PROMPT, timeout=30, effort='low')
    options = seen['options']
    assert options.tools == []                                              # no built-in tools at all
    assert options.system_prompt == SYSTEM                                  # a custom prompt, not Claude Code's preset
    assert options.setting_sources == []                                    # no user, project or local settings
    assert options.mcp_servers == {} and options.strict_mcp_config is True
    assert json.loads(options.settings) == {'disableAllHooks': True} and options.hooks is None
    assert options.max_turns == 1
    assert options.allowed_tools == [] and options.permission_mode is None
    denied = await options.can_use_tool('Bash', {'command': 'ls'}, None)
    assert isinstance(denied, sdk.PermissionResultDeny)
    assert options.env[SECRET] == '' and 'ANTHROPIC_API_KEY' not in options.env      # isolated like Sleepwalk's
    assert options.env['CLAUDE_AGENT_SDK_CLIENT_APP'].startswith('dream/')
    assert (options.model, options.effort) == ('claude-opus-5-5', 'low')
    folder = Path(options.cwd)
    assert seen['cwd'] == (True, '0o700', [])                               # a new, empty 0700 folder during the call
    assert folder.parent == private_root and folder.name.startswith('dream-frontier-')
    assert not folder.is_relative_to(workspace) and not inside_git(folder)
    assert not folder.exists()                                              # removed after the call
    assert seen['prompts'][0]['message']['content'] == PROMPT
    assert out['text'] == 'A lean answer' and out['outcome'] == 'ok'
    assert out['usage'] == {'prompt_tokens': 15, 'completion_tokens': 7} and out['usage_source'] == 'provider'
    assert type(out['duration_ms']) is int and out['duration_ms'] >= 0


async def test_a_claude_call_without_a_model_leaves_the_sdk_default(monkeypatch):
    seen = install_query(monkeypatch, {'input_tokens': 1, 'output_tokens': 1})
    await fc().call(persona('anthropic'), SYSTEM, PROMPT, timeout=30)
    assert seen['options'].model is None and seen['options'].effort is None


async def test_the_councils_claude_posture_is_unchanged_without_options(monkeypatch):
    seen = install_query(monkeypatch, {'input_tokens': 1, 'output_tokens': 1})
    from dream.core.providers import get_provider
    await moe._consult_anthropic(get_provider('anthropic'), 'q', cwd='/')
    assert seen['options'].tools == {'type': 'preset', 'preset': 'claude_code'}      # DREAM-140's posture


# --- Codex, isolated ------------------------------------------------------------------------------------------------

async def test_codex_runs_isolated_read_only_at_low_effort_with_the_prompt_on_stdin(tmp_path, monkeypatch,
                                                                                 private_root, workspace):
    monkeypatch.setenv(SECRET, 'secret')
    codex_home(tmp_path, monkeypatch)
    audit, _ = fake_cli(tmp_path, monkeypatch, 'codex', answer='Codex says 8')
    out = await fc().call(persona('codex', name='Motion designer'), SYSTEM, PROMPT, timeout=30)
    seen = json.loads(audit.read_text())
    argv = seen['argv']
    from dream.core.cli_review import CODEX_ISOLATION
    at = argv.index(CODEX_ISOLATION[0])
    assert tuple(argv[at:at + len(CODEX_ISOLATION)]) == CODEX_ISOLATION
    assert argv[argv.index('mcp_servers.docs.enabled=false') - 1] == '-c'           # each MCP server off
    assert argv[argv.index('--sandbox') + 1] == 'read-only'
    assert argv[argv.index('model_reasoning_effort="low"') - 1] == '-c'
    assert argv[-1] == '-' and seen['prompt'] == SYSTEM + '\n\n' + PROMPT              # the prompt on stdin
    assert '--model' not in argv                                                     # the CLI's default model
    folder = Path(seen['cwd'])
    assert seen['mode'] == '0o700' and seen['entries'] == []
    assert folder.parent == private_root and not folder.is_relative_to(workspace) and not inside_git(folder)
    assert argv[argv.index('-C') + 1] == seen['cwd']
    assert SECRET not in seen['env']                                          # the run's environment is allowlisted
    assert out['text'] == 'Codex says 8' and out['outcome'] == 'ok'
    assert out['usage'] == {'prompt_tokens': 1200, 'completion_tokens': 90} and out['usage_source'] == 'provider'


# --- Grok, read-only, estimated -------------------------------------------------------------------------------------

async def test_grok_runs_read_only_through_a_prompt_file_and_its_usage_is_estimated(tmp_path, monkeypatch,
                                                                                   private_root, workspace):
    audit, _ = fake_cli(tmp_path, monkeypatch, 'grok', answer='Grok says 7')
    out = await fc().call(persona('grok', name='Sceptical viewer'), SYSTEM, PROMPT, timeout=30)
    seen = json.loads(audit.read_text())
    argv = seen['argv']
    assert argv[argv.index('--permission-mode') + 1] == 'plan'
    assert '--prompt-file' in argv and seen['prompt'] == SYSTEM + '\n\n' + PROMPT
    assert argv[argv.index('--cwd') + 1] == seen['cwd']
    folder = Path(seen['cwd'])
    assert seen['mode'] == '0o700' and seen['entries'] == []
    assert folder.parent == private_root and not folder.is_relative_to(workspace) and not inside_git(folder)
    assert out['text'] == 'Grok says 7' and out['outcome'] == 'ok'
    assert out['usage_source'] == 'estimated'
    assert out['usage'] == {'prompt_tokens': estimate(seen['prompt']), 'completion_tokens': estimate('Grok says 7')}
    assert out['usage']['prompt_tokens'] > 0


# --- outcomes -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('reply', ['[unavailable — advisor returned no answer]',
                                   '[failed — MachX HTTP 500: boom]',
                                   'Half an answer\n\n[failed — xAI HTTP 429: slow down]',
                                   '[Judge: unavailable — RuntimeError: gone]',
                                   '', '   '])
async def test_an_unavailable_reply_is_unavailable(monkeypatch, reply):
    async def openai(provider, prompt, **kwargs):
        return reply
    monkeypatch.setattr(moe, '_consult_openai', openai)
    out = await fc().call(persona('openai', 'gpt-6-astra'), SYSTEM, PROMPT, timeout=30)
    assert out['outcome'] == 'unavailable' and out['usage_source'] == 'unknown'
    assert out['usage'] == {'prompt_tokens': 0, 'completion_tokens': 0}


async def test_a_raising_call_is_unavailable_and_names_why(monkeypatch):
    async def openai(provider, prompt, **kwargs):
        raise RuntimeError('boom')
    monkeypatch.setattr(moe, '_consult_openai', openai)
    out = await fc().call(persona('xai', name='Critic'), SYSTEM, PROMPT, timeout=30)
    assert out['outcome'] == 'unavailable' and out['text'] == '[Critic: unavailable — RuntimeError: boom]'


async def test_a_bracketed_answer_that_is_not_a_marker_is_an_answer(monkeypatch):
    async def openai(provider, prompt, **kwargs):
        return '[scores below] {"hook_3s": 8}'
    monkeypatch.setattr(moe, '_consult_openai', openai)
    assert (await fc().call(persona('openai'), SYSTEM, PROMPT, timeout=30))['outcome'] == 'ok'


async def test_a_timeout_ends_within_its_time_with_the_process_group_gone(tmp_path, monkeypatch):
    _, pids = fake_cli(tmp_path, monkeypatch, 'grok', mode='sleep')
    started = time.monotonic()
    out = await fc().call(persona('grok'), SYSTEM, PROMPT, timeout=2)
    took = time.monotonic() - started
    assert out['outcome'] == 'timeout' and out['text'] == 'No answer within 2 seconds'
    assert 2 <= took < 2 + 5
    assert pids.exists() and group_gone(pids)
    assert out['usage_source'] == 'unknown'


async def test_cancelling_raises_cancelled_error_with_the_process_group_gone(tmp_path, monkeypatch):
    _, pids = fake_cli(tmp_path, monkeypatch, 'grok', mode='sleep')
    task = asyncio.create_task(fc().call(persona('grok'), SYSTEM, PROMPT, timeout=120))
    for _ in range(400):
        if pids.exists() and pids.read_text():
            break
        await asyncio.sleep(0.05)
    assert pids.exists(), 'the fake CLI never started'
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert group_gone(pids)


async def test_a_machx_persona_without_a_loaded_model_makes_no_request(monkeypatch):
    from dream.local import machx
    checked, requested = [], []
    monkeypatch.setattr(machx, 'model_loaded', lambda timeout=2.0: checked.append(1) or False)

    async def openai(provider, prompt, **kwargs):
        requested.append(1)
        return 'never'
    monkeypatch.setattr(moe, '_consult_openai', openai)
    out = await fc().call(persona('machx', name='Brand guardian'), SYSTEM, PROMPT, timeout=30)
    assert checked and not requested
    assert out['outcome'] == 'unavailable'
    assert out['text'] == 'The local model is not loaded; a workflow never loads one.'


async def test_a_machx_persona_with_a_loaded_model_is_asked_without_tools_and_reports_exact_usage(monkeypatch):
    from dream.local import machx
    monkeypatch.setattr(machx, 'model_loaded', lambda timeout=2.0: True)
    seen = {}

    async def openai(provider, prompt, *, cwd=None, model=None, effort=None, system=None, meter=None):
        seen.update(provider=provider.key, prompt=prompt, model=model, system=system, effort=effort)
        meter.usage({'prompt_tokens': 50, 'completion_tokens': 5})
        return 'Local answer'
    monkeypatch.setattr(moe, '_consult_openai', openai)
    out = await fc().call(persona('machx', name='Brand guardian'), SYSTEM, PROMPT, timeout=30, effort='low')
    assert seen == {'provider': 'machx', 'prompt': PROMPT, 'model': None, 'system': SYSTEM, 'effort': None}
    assert out['outcome'] == 'ok' and out['usage'] == {'prompt_tokens': 50, 'completion_tokens': 5}
    assert out['usage_source'] == 'provider'


async def test_the_real_http_path_reports_exact_usage_to_the_call_and_the_run(monkeypatch):
    # nothing of moe or the backend is faked: an httpx MockTransport answers the streamed chat request
    import httpx
    requests = []

    def transport(request):
        requests.append(request)
        assert request.method == 'POST' and request.url.path.endswith('/chat/completions')
        chunks = [{'choices': [{'index': 0, 'delta': {'content': 'An HTTP answer'}}]},
                  {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
                  {'choices': [], 'usage': {'prompt_tokens': 321, 'completion_tokens': 12}}]
        body = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})
    real = httpx.AsyncClient

    class Mocked(real):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(httpx, 'AsyncClient', Mocked)
    run = fc().run_meter('run-http', cap=600_000)
    out = await fc().call(persona('openai', 'gpt-6-astra', name='Critic'), SYSTEM, PROMPT, timeout=30, run=run)
    assert (out['text'], out['outcome'], out['usage_source']) == ('An HTTP answer', 'ok', 'provider')
    assert out['usage'] == {'prompt_tokens': 321, 'completion_tokens': 12}
    assert (run.prompt_tokens, run.output_tokens) == (321, 12)
    [request] = requests
    payload = json.loads(request.content)
    assert not payload.get('tools') and payload['model'] == 'gpt-6-astra'
    assert SYSTEM in json.dumps(payload['messages'][0]) and PROMPT in json.dumps(payload['messages'][-1])


async def test_a_call_folder_inside_a_git_repository_is_refused_before_any_request(tmp_path, monkeypatch):
    from dream.sleepwalk import runner as sleepwalk
    repository = tmp_path / 'repository'
    (repository / '.git').mkdir(parents=True)
    root = repository / 'private'
    root.mkdir(mode=0o700)
    monkeypatch.setattr(sleepwalk, '_ROOTS', (lambda: (root, ''),))
    requested = []

    async def openai(provider, prompt, **kwargs):
        requested.append(1)
        return 'never'
    monkeypatch.setattr(moe, '_consult_openai', openai)
    out = await fc().call(persona('openai', name='Critic'), SYSTEM, PROMPT, timeout=30)
    assert out['outcome'] == 'unavailable' and 'is inside a git repository' in out['text'] and not requested
    assert list(root.iterdir()) == []                                       # the folder made there is gone again


# --- the run's meter ------------------------------------------------------------------------------------------------

async def test_every_call_feeds_the_runs_meter_including_estimates(tmp_path, monkeypatch):
    run = fc().run_meter('run-1', cap=600_000)
    install_query(monkeypatch, {'input_tokens': 10, 'output_tokens': 7})
    await fc().call(persona('anthropic'), SYSTEM, PROMPT, timeout=30, run=run)
    assert (run.prompt_tokens, run.output_tokens) == (10, 7)
    audit, _ = fake_cli(tmp_path, monkeypatch, 'grok', answer='Grok says 7')
    out = await fc().call(persona('grok'), SYSTEM, PROMPT, timeout=30, run=run)
    assert out['usage_source'] == 'estimated'
    assert (run.prompt_tokens, run.output_tokens) == (10 + out['usage']['prompt_tokens'],
                                                      7 + out['usage']['completion_tokens'])


async def test_a_run_at_its_cap_refuses_the_call_before_any_request(monkeypatch):
    run = fc().run_meter('run-2', cap=10_000)
    run.usage({'prompt_tokens': 9_000, 'completion_tokens': 1_000})
    seen = install_query(monkeypatch, {'input_tokens': 1, 'output_tokens': 1})
    out = await fc().call(persona('anthropic'), SYSTEM, PROMPT, timeout=30, run=run)
    assert seen['queries'] == 0
    assert out['outcome'] == 'unavailable' and 'Run token budget reached' in out['text']


def test_the_runs_meter_carries_the_cap_and_the_time_limit():
    run = fc().run_meter('run-3', cap=200_000)
    assert run.profile.max_run_tokens == 200_000 and run.profile.max_run_seconds == 30 * 60
    assert fc().run_meter('run-4', cap=10_000, seconds=60).profile.max_run_seconds == 60


# --- the catalog ----------------------------------------------------------------------------------------------------

def test_claude_opus_5_5_is_in_the_bundled_catalog():
    from dream.core.model_catalog import model_choices
    choices = {m['id']: m for m in model_choices('anthropic')}
    assert choices['claude-opus-5-5']['label'] == 'Claude Opus 5.5'
    assert choices['claude-opus-5-5']['efforts'] == ['low', 'medium', 'high', 'xhigh', 'max']
