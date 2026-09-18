"""Synthetic qualification controls; these tests never contact a hosted model."""
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from dream.core.backends.base import Event


@pytest.mark.parametrize('field', ['subtype', 'terminal_reason'])
@pytest.mark.parametrize('value', [[], {}])
def test_malformed_terminal_metadata_is_unknown_without_losing_outcome(field, value):
    terminal = {'is_error': False, 'subtype': 'success', field: value}
    result = qualifier().assess_events([Event('result', terminal)], expected=None)
    assert result['terminal_subtypes' if field == 'subtype' else 'terminal_reasons'] == ['unknown']
    assert result['status'] == ('failed' if field == 'subtype' else 'passed')
from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter


def qualifier():
    path = Path(__file__).parents[1] / 'scripts/check_hosted_reliability.py'
    assert path.exists(), 'The opt-in hosted qualifier has not been implemented'
    spec = importlib.util.spec_from_file_location('hosted_qualification', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('events,category', [
    ([Event('assistant_done', 'READY')], 'missing_terminal'),
    ([Event('assistant_done', 'READY'), Event('error', '429 rate limit secret')], 'rate_limit'),
    ([Event('assistant_done', 'READY'), Event('error', 'connection reset /private/file')], 'connection'),
    ([Event('assistant_done', 'READY'), Event('result', {'is_error': True})], 'provider_error'),
    ([Event('assistant_done', 'READY'), Event('result', {'is_error': False, 'subtype': 'success'}),
      Event('result', {'is_error': False, 'subtype': 'success'})], 'duplicate_terminal'),
    ([Event('assistant_done', 'wrong'), Event('result', {'is_error': False, 'subtype': 'success'})], 'response_mismatch'),
    ([Event('assistant_done', 'READY'), Event('result', {})], 'invalid_terminal'),
    ([Event('assistant_done', 'READY'), Event('result', {'is_error': False})], 'invalid_terminal'),
    ([Event('assistant_done', 'READY'), Event('result', {'is_error': False, 'subtype': 'error_max_turns'})], 'invalid_terminal'),
])
def test_qualification_rejects_false_success_and_does_not_export_content(events, category):
    result = qualifier().assess_events(events, expected='READY')
    assert result['status'] == 'failed'
    assert result['category'] == category
    assert not any(s in json.dumps(result) for s in ('READY', 'secret', '/private', 'wrong'))


def test_qualification_requires_exact_answer_and_one_clean_terminal():
    result = qualifier().assess_events([
        Event('text_delta', 'READY'), Event('assistant_done', 'READY'),
        Event('result', {'is_error': False, 'subtype': 'success', 'usage': {'input_tokens': 3, 'output_tokens': 1}}),
    ], expected='READY')
    assert result['status'] == 'passed'
    assert result['terminal_count'] == 1


def test_private_environment_drops_owner_config_secrets_and_gpu_settings(tmp_path):
    env = qualifier().private_environment(tmp_path, Path('/synthetic/repo'), {
        'PATH': '/usr/bin', 'HOME': '/owner', 'CODEX_HOME': '/owner/config',
        'ANTHROPIC_API_KEY': 'private', 'DREAM_SYSTEM_PROMPT': 'private',
        'CUDA_VISIBLE_DEVICES': '0',
    })
    assert env['HOME'].startswith(str(tmp_path))
    assert env['DREAM_ROOT'].startswith(str(tmp_path))
    assert env['CUDA_VISIBLE_DEVICES'] == ''
    assert 'CODEX_HOME' not in env and 'ANTHROPIC_API_KEY' not in env
    assert 'DREAM_SYSTEM_PROMPT' not in env


def test_codex_isolation_flags_survive_explicit_resume(tmp_path):
    adapter = qualifier().isolated_codex_adapter()
    args = adapter.argv('synthetic', cwd=str(tmp_path), resume_id='session', sandbox='read-only')
    assert args.index('--ignore-user-config') < args.index('resume')
    assert args.index('--ignore-rules') < args.index('resume')


def fake_backend(tmp_path, body):
    fake = tmp_path / 'fixture-cli'
    fake.write_text('#!/usr/bin/env python3\n' + body)
    fake.chmod(0o700)
    adapter = CodexAdapter()
    adapter.cmd = str(fake)
    return CliAgentBackend(adapter, system_prompt='Synthetic test only', cwd=str(tmp_path), idle_timeout=2)


async def test_actual_cli_drop_keeps_partial_and_never_replays(tmp_path):
    backend = fake_backend(tmp_path,
        'import json\n'
        f'with open({str(tmp_path / "calls")!r}, "a") as f: f.write("1")\n'
        'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"READY"}}))\n')
    result = await qualifier().observe_turn(backend, 'synthetic', expected='READY', timeout=3)
    assert result['status'] == 'failed'
    assert result['response_observed'] is True
    assert (tmp_path / 'calls').read_text() == '1'
    assert backend._proc is None


async def test_owned_stop_settles_real_cli_and_explicit_next_turn_is_separate(tmp_path):
    backend = fake_backend(tmp_path,
        'import json, sys, time\n'
        'if "STOP-PROBE" in sys.argv[-1]:\n'
        ' print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"partial"}}), flush=True)\n'
        ' time.sleep(30)\n'
        'else:\n'
        ' print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"READY"}}))\n'
        ' print(json.dumps({"type":"turn.completed","usage":{}}))\n')
    result = await qualifier().observe_turn(backend, 'STOP-PROBE', expected=None, timeout=3, stop_after=.1)
    assert result['stop_requested'] is True
    assert result['status'] == 'interrupted'
    assert result['local_cleanup_complete'] is True
    assert backend._proc is None
    resumed = await qualifier().observe_turn(backend, 'explicit next', expected='READY', timeout=3)
    assert resumed['status'] == 'passed'


async def test_observation_timeout_cannot_be_reported_as_stop_success(tmp_path):
    backend = fake_backend(tmp_path, 'import time\ntime.sleep(30)\n')
    result = await qualifier().observe_turn(backend, 'synthetic', expected='READY', timeout=.1)
    assert result['status'] == 'failed'
    assert result['category'] == 'deadline'
    assert backend._proc is None


def test_private_worker_deadline_reaps_detached_descendant(tmp_path):
    child = tmp_path / 'child.py'
    pidfile = tmp_path / 'pid'
    child.write_text('import subprocess, sys, time\n'
        'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)\n'
        f'open({str(pidfile)!r}, "w").write(str(p.pid))\n'
        'time.sleep(30)\n')
    result = qualifier().run_private_worker([sys.executable, str(child)],
        cwd=tmp_path, env=dict(os.environ), timeout=.3)
    assert result.timed_out is True
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_anthropic_probe_disables_tools_and_external_customizations(tmp_path):
    backend = qualifier().make_backend('anthropic', 'sonnet', tmp_path)
    options = backend._build_options()
    assert options.tools == [] and options.allowed_tools == []
    assert options.setting_sources == [] and options.strict_mcp_config is True
    assert 'safe-mode' in options.extra_args
    assert 'disable-slash-commands' in options.extra_args


def test_safe_terminal_diagnostics_keep_budget_but_drop_unknown_provider_text():
    result = qualifier().assess_events([Event('result', {
        'is_error': True, 'subtype': 'error_max_budget_usd',
        'terminal_reason': '/owner/private/secret'})], expected=None)
    assert result['terminal_subtypes'] == ['error_max_budget_usd']
    assert result['terminal_reasons'] == ['unknown']
    assert '/owner' not in json.dumps(result)


def test_live_execution_requires_opt_in(monkeypatch):
    module = qualifier()
    monkeypatch.setattr(sys, 'argv', ['check_hosted_reliability.py'])
    def forbidden(*args, **kwargs):
        pytest.fail('Attempted hosted work without --live')
    monkeypatch.setattr(module, 'run_live', forbidden)
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
