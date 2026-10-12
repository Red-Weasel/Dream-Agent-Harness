"""Browser executable discovery must survive isolated fixture settings."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('binding', ['default', 'explicit', 'hermetic', 'npm'])
def test_qualifier_shares_only_browser_binary_binding_with_isolated_child(tmp_path, monkeypatch, binding):
    spec = importlib.util.spec_from_file_location('browser_qualifier',
        Path(__file__).resolve().parents[1] / 'scripts/qualify_production.py')
    qualifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qualifier)
    host_cache = tmp_path / 'synthetic-host-cache'
    (host_cache / 'ms-playwright').mkdir(parents=True)
    monkeypatch.setenv('XDG_CACHE_HOME', str(host_cache))
    keys = ('PLAYWRIGHT_BROWSERS_PATH', 'npm_config_playwright_browsers_path',
            'npm_package_config_playwright_browsers_path')
    for key in keys: monkeypatch.delenv(key, raising=False)
    selected = tmp_path / 'selected-binaries'
    if binding == 'explicit': monkeypatch.setenv(keys[0], str(selected))
    elif binding == 'hermetic': monkeypatch.setenv(keys[0], '0')
    elif binding == 'npm': monkeypatch.setenv(keys[1], str(selected))
    marker = 'SYNTHETIC_CREDENTIAL_MUST_NOT_ENTER_CHILD'
    monkeypatch.setenv('OPENAI_API_KEY', marker)
    monkeypatch.setenv('DREAM_CONTEXT_COUNTER', 'a-user-selected-helper')
    calls = []
    def fixture(argv, **kwargs):
        env = kwargs['env']; calls.append(env)
        assert env['XDG_CACHE_HOME'] != str(host_cache)
        assert env['DREAM_ROOT'] != str(Path.cwd())
        assert 'OPENAI_API_KEY' not in env and 'DREAM_CONTEXT_COUNTER' not in env
        if binding == 'default':
            assert env.get(keys[0]) == str(host_cache / 'ms-playwright')
        elif binding == 'explicit': assert env[keys[0]] == str(selected)
        elif binding == 'hermetic': assert env[keys[0]] == '0'
        else:
            assert env[keys[1]] == str(selected)
            assert keys[0] not in env
        report = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--junitxml=')))
        report.write_text('<testsuite tests="1"><testcase name="fixture"/></testsuite>')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(qualifier.subprocess, 'run', fixture)
    assert qualifier.run_gate('context', timeout=23)['status'] == 'verified'
    assert len(calls) == 1
