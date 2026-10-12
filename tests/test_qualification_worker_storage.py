"""Worker storage qualification contracts; synthetic checkout, no inference."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_qualification_managed_gates import qualifier, checkout, assert_offline


FIXTURES = tuple('tests/' + name for name in (
    'test_worker_checkpoints.py', 'test_worker_checkpoints_storage.py',
    'test_worker_checkpoint_pause_budget.py',
    'test_worker_continuation.py', 'test_worker_journal_http.py', 'test_worker_journal_controls.py', 'test_worker_journal_crash.py',
    'test_worker_journal_review_regressions.py',
    'test_worker_journal_startup_crash.py',
    'test_worker_survival.py',
    'test_worker_checkpoint_originals.py', 'test_worker_checkpoint_process.py',
    'test_worker_checkpoint_write_guards.py', 'test_tool_artifacts.py',
    'test_worker_checkpoint_review_regressions.py',
    'test_worker_delegations.py', 'test_worker_delegation_boundaries.py', 'test_managed_loop.py',
    'test_worker_delegation_review_regressions.py',
    'test_worker_output_restore.py', 'test_worker_evidence_restore.py',
    'test_parent_continuation.py', 'test_parent_continuation_http.py',
    'test_parent_continuation_review_regressions.py',
    'test_parent_continuation_boundary_regressions.py',
    'test_worker_contracts.py', 'test_worker_evidence_durability.py', 'test_worker_partial_results.py',
    'test_artifact_tools.py', 'test_native_files.py', 'test_read_file_lines.py',
))
SOURCES = ('dream/core/worker_checkpoints.py', 'dream/tools/native.py',
           'dream/tools/files.py', 'dream/core/worker_result.py',
           'dream/core/worker_evidence.py', 'dream/core/parent_continuation.py',
           'dream/core/worker_continuation.py',
           'tests/test_local_subagents.py')


@pytest.fixture
def worker_checkout(checkout):
    for name in (*SOURCES, *FIXTURES):
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('generic synthetic qualification input: ' + name + '\n')
    return checkout


def require_gate(qualifier):
    assert 'worker_storage' in qualifier.GATES, 'Required offline worker_storage gate is absent.'


def test_worker_storage_executes_exact_fixed_tests_and_pins_inputs(qualifier, worker_checkout, monkeypatch, tmp_path):
    require_gate(qualifier)
    calls = []

    def fake_child(argv, **kwargs):
        calls.append(argv)
        assert {arg for arg in argv if arg.startswith('tests/')} == set(FIXTURES)
        assert kwargs['cwd'] == worker_checkout and kwargs['timeout'] == 31
        assert kwargs['check'] is False
        junit = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--junitxml=')))
        junit.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0">'
                         '<testcase classname="synthetic" name="worker_contract"/></testsuite>')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(qualifier.subprocess, 'run', fake_child)
    output = tmp_path / 'report.json'
    assert qualifier.main(['--gate', 'worker_storage', '--gate', 'worker_storage',
                           '--timeout', '31', '--output', str(output)]) == 0
    report = json.loads(output.read_text())
    assert len(calls) == 1 and set(report['gates']) == {'worker_storage'}
    assert report['gates']['worker_storage']['status'] == 'verified'
    assert report['source_consistency']['status'] == 'verified'
    for name in (*SOURCES, *FIXTURES):
        assert report['source_sha256'][name] == hashlib.sha256((worker_checkout / name).read_bytes()).hexdigest()
    assert_offline(report)


@pytest.mark.parametrize('path', SOURCES)
@pytest.mark.parametrize('damage', ['missing', 'drift'])
def test_required_runtime_damage_invalidates_success(qualifier, worker_checkout, monkeypatch, tmp_path, path, damage):
    # Use an existing gate so omitted global sources produce behavioral failures
    # independently of whether worker_storage has been registered yet.
    check_damage(qualifier, worker_checkout, monkeypatch, tmp_path, path, damage, 'http')


@pytest.mark.parametrize('path', FIXTURES)
@pytest.mark.parametrize('damage', ['missing', 'drift'])
def test_selected_worker_test_damage_invalidates_success(qualifier, worker_checkout, monkeypatch, tmp_path, path, damage):
    require_gate(qualifier)
    check_damage(qualifier, worker_checkout, monkeypatch, tmp_path, path, damage, 'worker_storage')


def check_damage(qualifier, root, monkeypatch, tmp_path, path, damage, gate):
    target = root / path
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    if damage == 'missing':
        target.unlink()
    calls = []

    def successful_gate(name, *, timeout):
        calls.append(name)
        if damage == 'drift':
            target.write_text('changed during successful synthetic gate\n')
        return {'status': 'verified', 'exit_code': 0, 'tests': 1,
                'failures': [], 'errors': [], 'skips': []}

    monkeypatch.setattr(qualifier, 'run_gate', successful_gate)
    output = tmp_path / 'report.json'
    assert qualifier.main(['--gate', gate, '--output', str(output)]) == 1, path + ' escaped auditing'
    report = json.loads(output.read_text())
    assert calls == [gate] and report['gates'][gate]['status'] == 'verified'
    assert report['source_sha256'][path] == (None if damage == 'missing' else before)
    assert report['source_consistency']['status'] == 'failed'
    assert report['source_consistency']['changed_paths'] == ([] if damage == 'missing' else [path])
    assert_offline(report)
