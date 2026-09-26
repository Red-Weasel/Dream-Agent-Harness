"""DREAM-136: Council advisors, CLI reviewers and the prompt optimizer run each CLI
like the main-model CLI path. Fixture executables only; no live provider is called."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core import moe
from dream.core.backends.cli_agent import adapter_for, sandbox_for_mode
from dream.core.cli_review import CLIConsultation, CLIIsolationError, _answer
from dream.core.evaluator import ReviewSettings, ScopedReader, collect_review, review_backend
from dream.core.providers import get_provider

_ISOLATION_FLAGS = (
    '--ignore-user-config', '--ignore-rules', '--ephemeral', '--disable', 'mcp_servers={}',
    'web_search="disabled"', 'tools.view_image=false', 'approval_policy="never"',
    '--deny', '--tools', '--disallowed-tools', '--max-turns', '--disable-web-search', '--no-subagents',
    '--policy',
)


def _which(monkeypatch, path):
    monkeypatch.setattr('dream.core.cli_review.shutil.which', lambda _: str(path))


def _prepared(tmp_path, monkeypatch, key, mode, model='fixture-model'):
    executable = tmp_path / ('fixture-' + key)
    executable.write_text('#!/bin/sh\n')
    executable.chmod(0o700)
    _which(monkeypatch, executable)
    workspace = tmp_path / 'workspace'
    workspace.mkdir(exist_ok=True)
    call = CLIConsultation(get_provider(key), model, cwd=str(workspace), mode=mode)
    call.prepare()
    return call, workspace, str(executable.resolve())


def _flag(argv, name):
    return argv[argv.index(name) + 1]


@pytest.mark.parametrize('key', ['codex', 'grok', 'gemini'])
@pytest.mark.parametrize('mode', ['accept-edits', 'auto'])
def test_edit_modes_run_workspace_write_with_no_isolation_flags(key, mode, tmp_path, monkeypatch):
    call, workspace, executable = _prepared(tmp_path, monkeypatch, key, mode)
    try:
        argv = call.argv
        assert argv[0] == executable
        assert not any(flag in argv for flag in _ISOLATION_FLAGS)
        assert _flag(argv, '--model') == 'fixture-model'
        if key == 'codex':
            assert argv[1] == 'exec' and _flag(argv, '--sandbox') == 'workspace-write'
            assert _flag(argv, '-C') == str(workspace) and argv[-1] == '-'
        elif key == 'grok':
            assert _flag(argv, '--permission-mode') == 'bypassPermissions'
            assert _flag(argv, '--cwd') == str(workspace) and '--prompt-file' in argv and '-p' not in argv
        else:
            assert _flag(argv, '--approval-mode') == 'yolo'
            assert _flag(argv, '--include-directories') == str(workspace)
        # The owner's own environment (HOME, CLI homes, sign-in) is inherited as is.
        assert call.env is None
    finally:
        call.close()


@pytest.mark.parametrize('key,flag,value', [
    ('codex', '--sandbox', 'read-only'),
    ('grok', '--permission-mode', 'plan'),
    ('gemini', '--approval-mode', 'plan'),
])
@pytest.mark.parametrize('mode', ['plan', 'ask', None])
def test_plan_ask_and_unknown_mode_stay_read_only(key, flag, value, mode, tmp_path, monkeypatch):
    call, _, _ = _prepared(tmp_path, monkeypatch, key, mode)
    try:
        assert _flag(call.argv, flag) == value
        assert call.provenance['native_sandbox'] == 'read-only'
        assert call.provenance['permission_mode'] == (mode or 'ask')
    finally:
        call.close()


@pytest.mark.parametrize('key', ['codex', 'grok', 'gemini'])
@pytest.mark.parametrize('mode', ['plan', 'auto'])
def test_posture_flags_are_the_main_path_adapters_own(key, mode, tmp_path, monkeypatch):
    call, workspace, _ = _prepared(tmp_path, monkeypatch, key, mode, model=None)
    try:
        main = adapter_for(key).argv('PROMPT', cwd=str(workspace), resume_id=None, sandbox=sandbox_for_mode(mode))
        # Only the prompt transport differs: stdin/prompt file instead of an argv cell.
        if key == 'codex':
            assert call.argv[1:-1] == main[1:-1]
        elif key == 'grok':
            index = main.index('-p')
            assert [a for a in call.argv[1:] if a not in ('--prompt-file', str(call.root / 'prompt.txt'))] \
                == main[1:index] + main[index + 2:]
        else:
            assert call.argv[1:-1] == main[1:-1]
    finally:
        call.close()


def test_provenance_reports_parity_not_isolation(tmp_path):
    call = CLIConsultation(get_provider('grok'), 'fixture-model', cwd=str(tmp_path), mode='plan')
    record = call.provenance
    assert record['transport'] == 'subscription_cli' and record['provider'] == 'grok'
    assert record['isolation'].startswith('none')
    assert record['cwd'] == str(tmp_path) and record['permission_mode'] == 'plan'
    assert record['native_sandbox'] == 'read-only' and record['native_posture'] == '--permission-mode plan'
    assert 'private' not in json.dumps(record)
    edit = CLIConsultation(get_provider('codex'), None, cwd=str(tmp_path), mode='accept-edits').provenance
    assert edit['native_sandbox'] == 'workspace-write' and edit['native_posture'] == '--sandbox workspace-write'


def test_missing_workspace_is_refused(tmp_path, monkeypatch):
    _which(monkeypatch, tmp_path / 'fixture')
    call = CLIConsultation(get_provider('codex'), None, mode='auto')
    with pytest.raises(CLIIsolationError, match='workspace'):
        call.prepare()
    assert call._temp is None


def test_advisor_prompt_no_longer_claims_read_only():
    text = moe.ADVISOR_SYSTEM
    assert 'read-only' not in text and 'do not act' not in text and 'run tools' not in text
    assert 'permission mode' in text
    assert 'images' in text and 'web' in text
    # The independent-advice framing is kept.
    assert 'independent advisor' in text and 'Agreement is never proof' in text


@pytest.mark.parametrize('provider,events,answer', [
    ('codex', [{'type': 'item.started', 'item': {'type': 'command_execution', 'command': 'ls'}},
               {'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 0}},
               {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Looked.'}},
               {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Advice.'}},
               {'type': 'turn.completed'}], 'Looked.\n\nAdvice.'),
    ('gemini', [{'type': 'message', 'role': 'assistant', 'content': 'Checking.'},
                {'type': 'tool_use', 'tool_name': 'read_file', 'tool_id': 't1'},
                {'type': 'tool_result', 'tool_id': 't1', 'status': 'success'},
                {'type': 'error', 'severity': 'warning', 'message': 'loop detected'},
                {'type': 'message', 'role': 'assistant', 'content': 'Advice.'},
                {'type': 'result', 'status': 'success'}], 'Checking.\n\nAdvice.'),
])
def test_native_tool_use_is_accepted(provider, events, answer):
    assert _answer(provider, '\n'.join(json.dumps(e) for e in events).encode()) == answer


def _fake_cli(tmp_path, key):
    executable = tmp_path / ('fixture-' + key)
    audit = tmp_path / (key + '-audit.json')
    executable.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys
key, audit = KEY, pathlib.Path(AUDIT)
argv = sys.argv[1:]
prompt = pathlib.Path(argv[argv.index('--prompt-file') + 1]).read_text() if key == 'grok' else sys.stdin.read()
audit.write_text(json.dumps({'cwd': os.getcwd(), 'home': os.environ.get('HOME'),
    'cli_home': os.environ.get('CODEX_HOME') or os.environ.get('GROK_HOME') or os.environ.get('GEMINI_CLI_HOME'),
    'argv': argv, 'prompt': prompt}))
(pathlib.Path.cwd() / ('advisor-' + key + '.txt')).write_text('written in the real workspace')
if key == 'codex':
    print(json.dumps({'type': 'item.started', 'item': {'type': 'web_search'}}))
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Parity advice'}}))
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 3, 'output_tokens': 2}}))
elif key == 'grok':
    print(json.dumps({'type': 'text', 'data': 'Parity advice'}))
    print(json.dumps({'type': 'end', 'stopReason': 'EndTurn'}))
else:
    print(json.dumps({'type': 'tool_use', 'tool_name': 'google_web_search', 'tool_id': 'w'}))
    print(json.dumps({'type': 'message', 'role': 'assistant', 'content': 'Parity advice'}))
    print(json.dumps({'type': 'result', 'status': 'success'}))
'''.replace('KEY', repr(key)).replace('AUDIT', repr(str(audit))))
    executable.chmod(0o700)
    return executable, audit


@pytest.mark.parametrize('key', ['codex', 'grok', 'gemini'])
async def test_fake_cli_consult_runs_in_the_real_workspace_with_the_owners_home(key, tmp_path, monkeypatch):
    home = tmp_path / 'owner-home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    for name in ('CODEX_HOME', 'GROK_HOME', 'GEMINI_CLI_HOME'):
        monkeypatch.setenv(name, str(home / 'cli-home'))
    executable, audit = _fake_cli(tmp_path, key)
    _which(monkeypatch, executable)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    answer = await moe.consult_advisor(key, 'Give independent advice', cwd=str(workspace),
                                       model='fixture-model', mode='accept-edits')
    assert answer == 'Parity advice'
    seen = json.loads(audit.read_text())
    assert Path(seen['cwd']).resolve() == workspace.resolve()
    assert seen['home'] == str(home) and seen['cli_home'] == str(home / 'cli-home')
    assert moe.ADVISOR_SYSTEM in seen['prompt'] and 'Give independent advice' in seen['prompt']
    assert (workspace / ('advisor-' + key + '.txt')).exists()


async def test_council_threads_mode_to_cli_advisors_and_provenance(monkeypatch, tmp_path):
    seen = {}

    async def cli(provider, prompt, *, cwd=None, mode=None, **kwargs):
        seen[provider.key] = (cwd, mode)
        return 'fine'

    async def other(provider, prompt, *, cwd=None, **kwargs):
        assert 'mode' not in kwargs
        return 'fine'

    monkeypatch.setattr(moe, '_consult_cli', cli)
    monkeypatch.setattr(moe, '_consult_openai', other)
    rows = await moe.council(['codex', 'machx'], 'q', cwd=str(tmp_path), mode='plan')
    assert seen == {'codex': (str(tmp_path), 'plan')}
    record = rows[0]['isolation']
    assert record['native_sandbox'] == 'read-only' and record['cwd'] == str(tmp_path)
    assert 'isolation' not in rows[1]


async def test_consult_tool_passes_the_sessions_mode(monkeypatch, tmp_path):
    from dream.tools import context as tool_context
    from dream.tools.context import ToolContext, set_context
    from dream.tools.moe_tools import consult
    seen = {}

    async def consult_advisor(key, question, context='', **kwargs):
        seen.update(kwargs)
        return 'fine'

    monkeypatch.setattr(moe, 'consult_advisor', consult_advisor)
    cfg = moe.MoeConfig('machx', ['codex'])
    set_context(ToolContext(store=SimpleNamespace(), working=SimpleNamespace(), browser=SimpleNamespace(),
                            session_id='fixture', workspace=tmp_path, moe_config=cfg,
                            mode_getter=lambda: 'plan'))
    try:
        result = await consult.handler({'advisor': 'codex', 'question': 'q'})
    finally:
        tool_context._CTX = None
    assert seen['mode'] == 'plan' and seen['cwd'] == str(tmp_path)
    text = result['content'][0]['text']
    assert 'private sign-in' not in text and 'permission mode' in text


def test_review_settings_take_the_engines_mode():
    engine = SimpleNamespace(provider=get_provider('codex'), model='m', profile=None,
                             _mode_getter=lambda: 'plan')
    assert ReviewSettings.resolve(engine).mode == 'plan'
    assert ReviewSettings.resolve(SimpleNamespace(provider=get_provider('codex'), model='m')).mode is None


async def test_cli_reviewer_runs_in_the_worker_workspace_by_mode(tmp_path, monkeypatch):
    executable, audit = _fake_cli(tmp_path, 'codex')
    _which(monkeypatch, executable)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    settings = ReviewSettings(get_provider('codex'), 'fixture-model', mode='ask')
    backend = review_backend(settings, ScopedReader(workspace).tools(), 'Review', workspace)
    assert backend.provenance['native_sandbox'] == 'read-only'
    assert backend.provenance['cwd'] == str(workspace)
    assert await collect_review(backend, 'Check', 5) == 'Parity advice'
    seen = json.loads(audit.read_text())
    assert Path(seen['cwd']).resolve() == workspace.resolve()
    assert _flag(seen['argv'], '--sandbox') == 'read-only'
    assert 'Native tools are restricted' not in seen['prompt']


async def test_prompt_optimizer_is_pinned_read_only_in_every_mode(monkeypatch, tmp_path):
    from dream import prompt_optimizer as po
    seen = {}

    class CLI:
        def __init__(self, provider, model, **kwargs):
            seen.update(kwargs)
        def prepare(self):
            pass
        async def run(self, prompt):
            return json.dumps({'prompt': 'GOAL\nDo it', 'questions': [], 'notes': []})
        def close(self):
            pass

    monkeypatch.setattr(po, 'CLIConsultation', CLI)
    await po.optimize({'draft': 'Write a plan'}, [], tmp_path,
                      ReviewSettings(get_provider('codex'), 'pinned', mode='auto'))
    # It drafts prompts only: read-only even when the session is in an edit mode.
    assert seen['cwd'] == str(tmp_path) and seen['mode'] == 'plan'
    assert sandbox_for_mode(seen['mode']) == 'read-only'


def _detaching_cli(tmp_path):
    """A fake Codex that leaves a detached, re-sessioned grandchild, then hangs."""
    executable = tmp_path / 'fixture-codex'
    record = tmp_path / 'grandchild.pid'
    executable.write_text('''#!/usr/bin/python3
import subprocess, sys, time
sys.stdin.read()
# The intermediate exits at once, so the sleeper is orphaned in its own session.
subprocess.run(['/usr/bin/python3', '-c',
    'import pathlib, subprocess, sys; p = subprocess.Popen(["/usr/bin/setsid", "/usr/bin/sleep", "307"]); '
    'pathlib.Path(sys.argv[1]).write_text(str(p.pid))', RECORD])
time.sleep(60)
'''.replace('RECORD', repr(str(record))))
    executable.chmod(0o700)
    return executable, record


def _gone(pid):
    status = Path(f'/proc/{pid}/stat')
    return not status.exists() or status.read_text().split()[2] == 'Z'


async def _wait_for(path):
    for _ in range(300):
        if path.exists() and path.read_text():
            return int(path.read_text())
        await asyncio.sleep(.01)
    raise AssertionError('fixture grandchild never started')


async def _settled(pid):
    for _ in range(100):
        if _gone(pid):
            return True
        await asyncio.sleep(.05)
    return False


@pytest.mark.parametrize('ending', ['timeout', 'cancel'])
async def test_detached_grandchild_is_gone_after_timeout_or_cancel(ending, tmp_path, monkeypatch):
    executable, record = _detaching_cli(tmp_path)
    _which(monkeypatch, executable)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    if ending == 'timeout':
        task = asyncio.create_task(moe.consult_advisor('codex', 'q', cwd=str(workspace), mode='auto', timeout=1))
    else:
        call = CLIConsultation(get_provider('codex'), None, cwd=str(workspace), mode='auto')
        call.prepare()
        task = asyncio.create_task(call.run('q'))
    pid = await _wait_for(record)
    try:
        if ending == 'timeout':
            assert 'unavailable' in await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            call.close()
        assert await _settled(pid), 'a detached grandchild outlived the consultation'
    finally:
        if not _gone(pid):
            os.kill(pid, 9)


def test_codex_top_level_error_is_a_note_as_on_the_main_path():
    # The main path's CodexAdapter renders nothing for a top-level `error` event
    # (e.g. a reconnect notice); `turn.failed` and a missing turn.completed still fail.
    events = [{'type': 'error', 'message': 'Reconnecting... 1/5'},
              {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Advice.'}},
              {'type': 'turn.completed'}]
    assert adapter_for('codex').translate(events[0], {}) == []
    assert _answer('codex', '\n'.join(json.dumps(e) for e in events).encode()) == 'Advice.'
    with pytest.raises(CLIIsolationError):
        _answer('codex', '\n'.join(json.dumps(e) for e in events[:2]).encode())
    with pytest.raises(CLIIsolationError):
        _answer('grok', b'{"type":"error","message":"x"}\n{"type":"text","data":"a"}\n{"type":"end","stopReason":"EndTurn"}')
