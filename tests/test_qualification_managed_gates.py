"""Qualifier integration over a synthetic checkout; no nested fixture execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


REQUIRED_FIXTURES = {
    'managed': {
        'tests/test_phase_runner.py', 'tests/test_phase_http_workflow.py',
        'tests/test_phase_plan.py', 'tests/test_phase_entrypoint.py',
        'tests/test_task_checkpoints.py', 'tests/test_run_resume.py',
        'tests/test_recovery_runtime.py', 'tests/test_generation_cut_http.py',
        'tests/test_frontier_help.py', 'tests/test_responses_phase_workflow.py',
        'tests/test_endurance_report.py',
    },
    'context': {
        'tests/test_context_inspection.py', 'tests/test_context_inspection_http.py',
        'tests/test_responses_context_inspection.py', 'tests/test_description_discovery.py',
        'tests/test_description_discovery_http.py',
    },
}
REQUIRED_SOURCES = (
    'dream/core/phase_runner.py', 'dream/core/phase_plan.py',
    'dream/core/task_checkpoints.py', 'dream/core/run_resume.py',
    'dream/core/recovery.py', 'dream/core/recovery_runtime.py',
    'dream/core/help_continuation.py', 'dream/core/frontier_help.py',
    'dream/core/responses_help.py', 'dream/core/context_inspection.py',
    'dream/core/context_counter.py', 'dream/core/discovery.py',
    'dream/core/stream_repetition.py', 'dream/core/stream_text_repetition.py',
    'dream/core/tool_artifacts.py', 'dream/core/endurance_report.py',
    'dream/gui/lucid_routes.py', 'dream/gui/static/library.js',
)


@pytest.fixture
def qualifier():
    source = Path(__file__).resolve().parents[1] / 'scripts' / 'qualify_production.py'
    spec = importlib.util.spec_from_file_location('managed_qualification_fixture', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checkout(qualifier, tmp_path, monkeypatch):
    root = tmp_path / 'synthetic-checkout'; root.mkdir()
    paths = set(qualifier.SOURCES) | set(REQUIRED_SOURCES)
    for tests in (*qualifier.GATES.values(), *REQUIRED_FIXTURES.values()): paths.update(tests)
    for name in paths:
        target = root / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('synthetic fixture bytes for ' + name + '\n')
    monkeypatch.setattr(qualifier, 'ROOT', root)
    return root


def report_path(tmp_path): return tmp_path / 'qualification.json'


def assert_offline(report):
    assert report['live_provider_inference'] == {'status': 'not_run'}
    assert report['owner_desktop_acceptance'] == {'status': 'not_run'}
    assert 'not live model qualification' in report['scope']


@pytest.mark.parametrize('gate', ['managed', 'context'])
def test_new_gate_runs_fixed_required_fixture_paths_via_existing_qualifier(qualifier, checkout, tmp_path, monkeypatch, gate):
    assert gate in qualifier.GATES, 'Required offline qualification gate is absent: ' + gate
    invocations = []
    def fixture_process(argv, **kwargs):
        invocations.append((argv, kwargs))
        actual = {value for value in argv if value.startswith('tests/')}
        assert REQUIRED_FIXTURES[gate] <= actual
        assert actual == set(qualifier.GATES[gate])
        assert kwargs['cwd'] == checkout and kwargs['timeout'] == 23
        junit = Path(next(value.split('=', 1)[1] for value in argv if value.startswith('--junitxml=')))
        junit.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="synthetic" name="contract"/></testsuite>')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(qualifier.subprocess, 'run', fixture_process)
    output = report_path(tmp_path)
    assert qualifier.main(['--gate', gate, '--gate', gate, '--timeout', '23', '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    assert len(invocations) == 1 and set(report['gates']) == {gate}
    assert report['gates'][gate]['status'] == 'verified' and report['gates'][gate]['tests'] == 1
    assert REQUIRED_FIXTURES[gate] <= report['source_sha256'].keys()
    assert set(REQUIRED_SOURCES) <= report['source_sha256'].keys()
    assert report['source_consistency']['status'] == 'verified'
    assert_offline(report)


@pytest.mark.parametrize('source', REQUIRED_SOURCES)
def test_each_new_runtime_source_drift_invalidates_otherwise_successful_report(qualifier, checkout, tmp_path, monkeypatch, source):
    target = checkout / source
    original = hashlib.sha256(target.read_bytes()).hexdigest()
    called = []
    def successful_gate(name, *, timeout):
        called.append(name)
        target.write_text('changed runtime bytes during the otherwise successful fixture gate\n')
        return {'status': 'verified', 'exit_code': 0, 'tests': 1, 'failures': [], 'errors': [], 'skips': []}
    monkeypatch.setattr(qualifier, 'run_gate', successful_gate)
    output = report_path(tmp_path)
    code = qualifier.main(['--gate', 'http', '--output', str(output)])
    assert code == 1, 'Unpinned runtime drift must not produce a verified report: ' + source
    report = json.loads(output.read_text())
    assert called == ['http'] and report['gates']['http']['status'] == 'verified'
    assert report['source_consistency'] == {
        'status': 'failed', 'changed_paths': [source],
        'scope': 'Listed files only; not a complete dependency or installed-wheel audit.',
    }
    assert report['source_sha256'][source] == original
    assert_offline(report)


def test_missing_managed_runtime_source_cannot_be_verified(qualifier, checkout, tmp_path, monkeypatch):
    (checkout / 'dream/core/responses_help.py').unlink()
    monkeypatch.setattr(qualifier, 'run_gate', lambda *a, **kw: {'status': 'verified', 'exit_code': 0})
    output = report_path(tmp_path)
    assert qualifier.main(['--gate', 'http', '--output', str(output)]) == 1
    report = json.loads(output.read_text())
    assert report['source_sha256']['dream/core/responses_help.py'] is None
    assert report['source_consistency']['status'] == 'failed'
    assert_offline(report)


@pytest.mark.parametrize('gate', ['managed', 'context'])
def test_skipped_new_gate_is_unverified_and_drops_private_fixture_output(qualifier, checkout, tmp_path, monkeypatch, gate):
    assert gate in qualifier.GATES, 'Required offline qualification gate is absent: ' + gate
    marker = 'SYNTHETIC_PRIVATE_DIAGNOSTIC_NOT_FOR_REPORT'
    def fixture_process(argv, **kwargs):
        junit = Path(next(value.split('=', 1)[1] for value in argv if value.startswith('--junitxml=')))
        junit.write_text('<testsuite tests="1" failures="0" errors="0" skipped="1">'
            '<testcase classname="synthetic" name="required_case"><skipped>' + marker + '</skipped>'
            '<system-out>' + marker + '</system-out></testcase></testsuite>')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(qualifier.subprocess, 'run', fixture_process)
    output = report_path(tmp_path)
    assert qualifier.main(['--gate', gate, '--output', str(output)]) == 1
    report = json.loads(output.read_text())
    assert report['gates'][gate]['status'] == 'not_run'
    assert report['gates'][gate]['skips'] == ['synthetic::required_case']
    assert marker not in output.read_text()
    assert_offline(report)
