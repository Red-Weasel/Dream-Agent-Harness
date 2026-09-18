"""Malformed configuration must fail before runtime startup or private export."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PRIVATE_VALUE = 'private-value-DO-NOT-ECHO'
GUARD = '''
import sys, socket, subprocess, ssl
def forbidden(*args, **kwargs):
    raise AssertionError('external operation attempted')
socket.socket = forbidden
subprocess.Popen = forbidden
class RuntimeGuard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('dream.core.backends', 'dream.tui', 'dream.desktop', 'dream.local.launcher', 'gi')):
            raise AssertionError('runtime import attempted')
sys.meta_path.insert(0, RuntimeGuard())
'''


def probe(tmp_path, code, values):
    env = dict(os.environ, DREAM_ROOT=str(tmp_path / 'must-not-exist'), **values)
    result = subprocess.run([sys.executable, '-c', GUARD + code],
                            cwd=Path(__file__).parents[1], env=env,
                            capture_output=True, text=True, timeout=4)
    assert not (tmp_path / 'must-not-exist').exists()
    assert 'external operation attempted' not in result.stderr
    assert 'runtime import attempted' not in result.stderr
    return result


@pytest.mark.parametrize('args', [[], ['local'], ['desktop'], ['--provider', 'openai']],
                         ids=['picker', 'local', 'desktop', 'provider'])
def test_bad_numeric_setting_stops_before_runtime_imports_without_echo(tmp_path, args):
    result = probe(tmp_path, '\nfrom dream.__main__ import main\nsys.argv=' + repr(['dream', *args]) + '\nmain()\n',
                   {'DREAM_MEMORY_FILE_MAX': PRIVATE_VALUE})
    assert result.returncode == 1
    assert 'DREAM_MEMORY_FILE_MAX' in result.stderr
    assert 'unset' in result.stderr.lower()
    assert 'Traceback' not in result.stderr
    assert PRIVATE_VALUE not in result.stdout + result.stderr


@pytest.mark.parametrize('args', [
    ['desktop', '-h'], ['desktop', '--help'],
    ['desktop', '--provider', 'openai', '--help'],
    ['desktop', '--', '--help'], ['local', '--help'],
    ['--provider', 'openai', '--help'],
])
def test_help_token_cannot_bypass_preflight_for_runtime_routes(tmp_path, args):
    result = probe(tmp_path, '\nfrom dream.__main__ import main\nsys.argv=' + repr(['dream', *args]) + '\nmain()\n',
                   {'DREAM_MEMORY_FILE_MAX': PRIVATE_VALUE})
    assert result.returncode == 1
    assert 'DREAM_MEMORY_FILE_MAX' in result.stderr and 'Unset' in result.stderr
    assert 'Traceback' not in result.stderr
    assert PRIVATE_VALUE not in result.stdout + result.stderr


@pytest.mark.parametrize('flag', ['-h', '--help'])
def test_help_remains_available_with_malformed_numeric_environment(tmp_path, flag):
    result = probe(tmp_path, '\nfrom dream.__main__ import main\nsys.argv=' + repr(['dream', flag]) + '\nmain()\n',
                   {'DREAM_MEMORY_FILE_MAX': PRIVATE_VALUE})
    assert result.returncode == 0 and '--provider' in result.stdout
    assert PRIVATE_VALUE not in result.stdout + result.stderr


def test_diagnostics_reports_bad_environment_without_config_import_or_value(tmp_path):
    code = '''
class ConfigGuard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'dream.config':
            raise AssertionError('config imported')
sys.meta_path.insert(0, ConfigGuard())
from dream.diagnostics import main
raise SystemExit(main(['--json']))
'''
    result = probe(tmp_path, code, {'DREAM_MEMORY_FILE_MAX': PRIVATE_VALUE})
    assert result.returncode == 1
    report = json.loads(result.stdout)
    rows = [row for row in report['checks'] if row['id'] == 'environment:DREAM_MEMORY_FILE_MAX']
    assert len(rows) == 1 and rows[0]['status'] == 'fail'
    assert PRIVATE_VALUE not in result.stdout + result.stderr
    assert 'config imported' not in result.stderr


@pytest.mark.parametrize('name,raw,expected', [
    ('DREAM_MEMORY_FILE_MAX', '9000', 9000),
    ('DREAM_CTX_WINDOW', '1_024', 1024),
    ('DREAM_HTTP_TIMEOUT_S', '2.5', 2.5),
    ('DREAM_FREQUENCY_PENALTY', '-0.2', -0.2),
    ('DREAM_TOOL_BUDGET', 'NONE', None),
    ('DREAM_TOOL_BUDGET', 'unlimited', None),
    ('DREAM_TOOL_BUDGET', '', None),
    ('DREAM_TOOL_BUDGET', '0', None),
    ('DREAM_LLM_READ_TIMEOUT_S', 'None', None),
    ('DREAM_LLM_READ_TIMEOUT_S', '', None),
    ('DREAM_LLM_READ_TIMEOUT_S', '0', None),
    # Preserve existing spelling semantics rather than silently retuning values.
    ('DREAM_LLM_READ_TIMEOUT_S', ' 0 ', 0.0),
    ('DREAM_TOOL_BUDGET', '+0', 0),
])
def test_numeric_syntax_types_and_legacy_unlimited_aliases(name, raw, expected):
    from dream.environment import read_numeric
    actual = read_numeric(name, environ={name: raw})
    assert actual == expected and type(actual) is type(expected)


@pytest.mark.parametrize('name,raw', [
    ('DREAM_MEMORY_FILE_MAX', PRIVATE_VALUE),
    ('DREAM_HTTP_TIMEOUT_S', 'nan'),
    ('DREAM_FREQUENCY_PENALTY', 'inf'),
    ('DREAM_LLM_READ_TIMEOUT_S', '1e999'),
    ('DREAM_TOOL_BUDGET', 'none '),
    ('DREAM_MEMORY_FILE_MAX', '1' * 1025),
], ids=['malformed', 'nan', 'infinity', 'overflow', 'legacy_alias_spacing', 'oversized'])
def test_invalid_numbers_have_bounded_value_free_errors(name, raw):
    from dream.environment import NumericEnvironmentError, read_numeric
    with pytest.raises(NumericEnvironmentError) as error:
        read_numeric(name, environ={name: raw})
    assert name in str(error.value) and len(str(error.value)) < 256
    assert error.value.name == name
    assert PRIVATE_VALUE not in str(error.value)


def test_registry_covers_every_central_declaration_and_retains_defaults():
    import ast
    from dream.environment import NUMERIC_SETTINGS, numeric_environment_errors, read_numeric
    source = ast.parse((Path(__file__).parents[1] / 'dream/config.py').read_text())
    declarations = [node.args[0].value for node in ast.walk(source)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'read_numeric']
    assert len(declarations) == 28 and set(declarations) == set(NUMERIC_SETTINGS)
    assert numeric_environment_errors(environ={}) == []
    expected = {'DREAM_MEMORY_FILE_MAX': 8000, 'DREAM_CTX_WINDOW': 200000,
                'DREAM_MAX_TOKENS': 131072, 'DREAM_HTTP_TIMEOUT_S': 20.0,
                'DREAM_REPETITION_PENALTY': 1.05,
                'DREAM_TOOL_BUDGET': None, 'DREAM_LLM_READ_TIMEOUT_S': None}
    for name, value in expected.items():
        actual = read_numeric(name, environ={})
        assert actual == value and type(actual) is type(value)


def test_numeric_diagnostic_export_allows_only_known_setting_names(tmp_path, monkeypatch):
    from dream.diagnostics import collect_report, export_report
    monkeypatch.setenv('DREAM_MEMORY_FILE_MAX', PRIVATE_VALUE)
    monkeypatch.setenv('DREAM_FREQUENCY_PENALTY', 'nan')
    monkeypatch.setenv('DREAM_PRIVATE_UNKNOWN', PRIVATE_VALUE)
    report = collect_report()
    errors = [row for row in report['checks'] if row['id'].startswith('environment:')]
    assert {row['id'] for row in errors} == {
        'environment:DREAM_MEMORY_FILE_MAX', 'environment:DREAM_FREQUENCY_PENALTY'}
    report['checks'].append({'id': 'environment:DREAM_PRIVATE_UNKNOWN', 'status': 'fail',
                             'code': 'environment_integer_invalid', 'value': PRIVATE_VALUE})
    target = tmp_path / 'report.json'
    export_report(report, target)
    raw = target.read_text()
    assert PRIVATE_VALUE not in raw and 'DREAM_PRIVATE_UNKNOWN' not in raw
    assert target.stat().st_mode & 0o777 == 0o600


def test_wheel_requires_the_shared_numeric_parser(tmp_path):
    from dream.diagnostics import inspect_wheel
    from test_diagnostics import _wheel
    path = tmp_path / 'missing-environment.whl'
    _wheel(path, omitted=['dream/environment.py'])
    row = inspect_wheel(path)
    assert row['code'] == 'wheel_assets_missing' and row['counts']['missing'] == 1
