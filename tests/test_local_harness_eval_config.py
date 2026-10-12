"""Local evaluator admission and child isolation; subprocess execution is mocked."""
from dataclasses import asdict
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dream import local_harness_eval as local


def config(**changes):
    return local.LocalEvalConfig(**({'endpoint': 'http://127.0.0.1:11435/v1',
                                   'model': 'generic-local-model'} | changes))


@pytest.mark.parametrize('endpoint', ['http://127.0.0.1:11435/v1',
    'http://127.0.0.2:8080/v1/', 'http://[::1]:8080/v1'])
def test_numeric_loopback_endpoints_are_explicit(endpoint):
    assert config(endpoint=endpoint).endpoint == endpoint


@pytest.mark.parametrize('endpoint', ['http://127.0.0.1:0/v1', ' http://127.0.0.1:11435/v1',
                                     'http://127.0.0.1:11435/v1\n'])
def test_zero_port_or_hidden_endpoint_whitespace_refuses(endpoint):
    with pytest.raises(ValueError):
        config(endpoint=endpoint)


@pytest.mark.parametrize('endpoint', [None, True, '', 'http://localhost:11435/v1',
    'http://engine.localhost:11435/v1', 'http://192.0.2.1:11435/v1',
    'http://0.0.0.0:11435/v1', 'https://127.0.0.1:11435/v1',
    'http://127.0.0.1/v1', 'http://127.0.0.1:65536/v1',
    'http://127.0.0.1:11435/', 'http://127.0.0.1:11435/v1/chat/completions',
    'http://user@127.0.0.1:11435/v1', 'http://user:secret@127.0.0.1:11435/v1',
    'http://127.0.0.1:11435/v1?model=other', 'http://127.0.0.1:11435/v1#fragment',
    'http://api.example.test:11435/v1', 'http://2130706433:11435/v1',
    'http://127.1:11435/v1'])
def test_nonlocal_implicit_or_authenticated_endpoints_are_refused(endpoint):
    with pytest.raises(ValueError): config(endpoint=endpoint)


@pytest.mark.parametrize('changes', [
    {'model': None}, {'model': True}, {'model': ''}, {'model': ' \n'},
    {'model': 'x'*257}, {'model': 'name\x00suffix'}, {'model': 'name\nsuffix'},
    {'discovery_mode': None}, {'discovery_mode': True}, {'discovery_mode': []},
    {'discovery_mode': 'full'}, {'discovery_mode': 'Descriptions'},
    {'catalog_mode': None}, {'catalog_mode': True}, {'catalog_mode': []},
    {'catalog_mode': 'all'},
    {'max_requests': True}, {'max_requests': 0}, {'max_requests': 65},
    {'max_requests': 1.0}, {'max_requests': '2'},
    {'max_wall_seconds': False}, {'max_wall_seconds': 0}, {'max_wall_seconds': 601},
    {'max_wall_seconds': 1.0}, {'max_wall_seconds': '300'},
])
def test_invalid_identity_mode_or_budget_is_refused(changes):
    with pytest.raises(ValueError): config(**changes)


def test_packaged_skills_catalog_is_an_explicit_config_mode():
    assert config(catalog_mode='builtin_tools_skills').catalog_mode == 'builtin_tools_skills'


@pytest.mark.parametrize('mode', ['default', 'descriptions'])
def test_environment_is_an_allowlist_with_pinned_mode_and_isolated_paths(monkeypatch, tmp_path, mode):
    hostile = ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'AWS_ACCESS_KEY_ID', 'GOOGLE_API_KEY',
        'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy',
        'DREAM_EXTENSION_SETTINGS', 'DREAM_EVALUATOR_PROVIDER', 'DREAM_EVALUATOR_MODEL',
        'DREAM_DB', 'DREAM_MCP_CONFIG', 'DREAM_PLUGINS_DIR', 'DREAM_EXTERNAL_SKILLS',
        'DREAM_MODEL_PRESETS', 'DREAM_CONTEXT_COUNTER_HELPER', 'DREAM_CONTEXT_COUNTER_GGUF',
        'DREAM_CONTEXT_COUNTER_MODEL', 'DREAM_TOKENIZER_PATH', 'DREAM_VISION_HELPER',
        'CODEX_HOME', 'GROK_HOME', 'GEMINI_CLI_HOME', 'PYTHONSTARTUP',
        'ONEAPI_DEVICE_SELECTOR', 'SYCL_DEVICE_FILTER', 'ZE_AFFINITY_MASK', 'PYTEST_VERSION']
    for name in hostile: monkeypatch.setenv(name, 'operator-value-must-not-survive')
    for name in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'PYTHONPATH', 'PATH'):
        monkeypatch.setenv(name, '/operator-private-fixture')
    monkeypatch.setenv('DREAM_DISCOVERY_MODE', 'opposite-mode')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0,1')
    monkeypatch.setenv('DREAM_CONTEXT_COUNTER', 'gguf_helper')
    env = local.child_environment(tmp_path, config(discovery_mode=mode))
    assert not set(hostile) & set(env)
    assert env['DREAM_DISCOVERY_MODE'] == mode
    assert env['PYTHONPATH'] == str(Path(local.__file__).resolve().parents[1])
    assert env['PATH'] == '/usr/bin:/bin' and env['PYTHONNOUSERSITE'] == '1'
    assert env['DREAM_ROOT'] == str(tmp_path.resolve())
    for name, suffix in [('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                         ('XDG_DATA_HOME', 'data-home'), ('XDG_CACHE_HOME', 'cache'),
                         ('XDG_RUNTIME_DIR', 'runtime')]:
        assert env[name] == str(tmp_path.resolve()/suffix)
    for name in ('CUDA_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES', 'ROCR_VISIBLE_DEVICES', 'DREAM_SKILL_DIRS'):
        assert env[name] == ''
    for name in ('DREAM_SEMANTIC_MEMORY', 'DREAM_RERANK', 'DREAM_CONSOLIDATE'):
        assert env[name] == '0'
    assert env['DREAM_CONTEXT_COUNTER'] == 'off'
    assert '/operator-private-fixture' not in json.dumps(env)


@pytest.mark.parametrize('pytest_flag', [None, 'PYTEST_CURRENT_TEST', 'PYTEST_VERSION'])
def test_only_real_shared_lease_override_is_preserved(monkeypatch, tmp_path, pytest_flag):
    for key in ('PYTEST_CURRENT_TEST', 'PYTEST_VERSION'):
        monkeypatch.delenv(key, raising=False)
    lease = str(tmp_path/'shared-endpoint-leases')
    monkeypatch.setenv('DREAM_INFERENCE_LEASE_DIR', lease)
    if pytest_flag: monkeypatch.setenv(pytest_flag, 'synthetic-test-runtime')
    env = local.child_environment(tmp_path/'run', config())
    if pytest_flag: assert 'DREAM_INFERENCE_LEASE_DIR' not in env
    else: assert env['DREAM_INFERENCE_LEASE_DIR'] == lease
    assert 'PYTEST_CURRENT_TEST' not in env and 'PYTEST_VERSION' not in env


def test_relative_shared_lease_override_refuses(monkeypatch, tmp_path):
    for key in ('PYTEST_CURRENT_TEST', 'PYTEST_VERSION'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('DREAM_INFERENCE_LEASE_DIR', 'relative-lease')
    with pytest.raises(ValueError): local.child_environment(tmp_path, config())


@pytest.mark.parametrize('kind', ['directory', 'file', 'symlink', 'dangling_symlink'])
def test_existing_run_path_refuses_before_any_process(monkeypatch, tmp_path, kind):
    run = tmp_path/'run'
    if kind == 'directory': run.mkdir()
    elif kind == 'file': run.write_text('preserve')
    else:
        target = tmp_path/'target'
        if kind == 'symlink': target.mkdir()
        run.symlink_to(target, target_is_directory=True)
    def forbidden(*args, **kwargs): raise AssertionError('Existing run must not spawn.')
    monkeypatch.setattr(local.subprocess, 'run', forbidden)
    with pytest.raises(FileExistsError): local.launch(config(), run)
    if kind == 'file': assert run.read_text() == 'preserve'
    if 'symlink' in kind: assert run.is_symlink()


@pytest.mark.parametrize('exit_code', [0, 1, -15])
def test_launch_passes_exact_isolated_request_and_nonzero_exit_cannot_pass(monkeypatch, tmp_path, exit_code):
    chosen = config(discovery_mode='descriptions', max_requests=3, max_wall_seconds=17)
    run = tmp_path/'new-run'; calls = []
    def synthetic(argv, **kwargs):
        calls.append(argv)
        assert argv == [sys.executable, '-c', 'from dream.local_harness_eval import child_main; child_main()']
        assert kwargs['cwd'] == run/'workspace'
        assert json.loads(kwargs['input']) == {'config': asdict(chosen), 'root': str(run)}
        assert kwargs['env'] == local.child_environment(run, chosen)
        assert kwargs['timeout'] == 47 and kwargs['check'] is False
        assert kwargs['stdout'] is kwargs['stderr']
        assert Path(kwargs['stdout'].name) == run/'process.log'
        assert stat.S_IMODE(run.stat().st_mode) == 0o700
        for name in ('workspace', 'home', 'config', 'cache', 'data-home', 'runtime'):
            assert (run/name).is_dir()
        (run/'report.json').write_text(json.dumps({'passed': True, 'live_endurance_qualified': False}))
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr(local.subprocess, 'run', synthetic)
    value = local.launch(chosen, run)
    assert len(calls) == 1 and value['process_exit'] == exit_code
    assert value['passed'] is (exit_code == 0)
    assert value['live_endurance_qualified'] is False


@pytest.mark.parametrize('kind', ['missing', 'oversized', 'timeout'])
def test_absent_bounded_result_or_watchdog_never_passes(monkeypatch, tmp_path, kind):
    run = tmp_path/'new-run'; calls = []
    def synthetic(argv, **kwargs):
        calls.append(argv)
        if kind == 'timeout': raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        if kind == 'oversized': (run/'report.json').write_bytes(b' ' * 2_000_001)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(local.subprocess, 'run', synthetic)
    value = local.launch(config(), run)
    assert len(calls) == 1 and value['passed'] is False
    assert value['live_endurance_qualified'] is False
    assert value['status'] == ('watchdog_expired' if kind == 'timeout' else 'unavailable')


def test_source_drift_refuses_an_otherwise_successful_fixture(monkeypatch, tmp_path):
    snapshots = iter([{'listed-code': 'before'}, {'listed-code': 'after'}])
    monkeypatch.setattr(local, '_source_hashes', lambda: next(snapshots))
    run = tmp_path / 'drift-run'
    def synthetic(*args, **kwargs):
        (run / 'report.json').write_text(json.dumps({'passed': True}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(local.subprocess, 'run', synthetic)
    value = local.launch(config(), run)
    assert value['passed'] is False and value['process_exit'] == 0
    assert value['source_consistency']['unchanged'] is False
    assert value['source_sha256'] == {'listed-code': 'before'}
