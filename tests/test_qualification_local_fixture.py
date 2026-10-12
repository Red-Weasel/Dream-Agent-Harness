"""Fixed local-fixture qualification over a synthetic checkout, never inference."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_qualification_managed_gates import qualifier, checkout, assert_offline


FIXTURES = (
    'tests/test_local_harness_eval_config.py',
    'tests/test_local_harness_eval_http.py',
    'tests/test_local_harness_eval_cli.py',
    'tests/test_local_harness_eval_identity.py',
    'tests/test_harness_eval_offline.py',
    'tests/test_local_eval_catalog.py',
    'tests/test_local_eval_skills_api.py',
    'tests/test_local_eval_skills_storage.py',
    'tests/test_local_eval_skills_races.py',
    'tests/test_local_eval_skill_order.py',
)
SOURCES = ('dream/local_harness_eval.py', 'dream/harness_eval.py',
           'dream/eval_fixtures/harness_cases.json', 'dream/local_eval_catalog.py',
           'dream/tools/registry.py', 'dream/tools/demonstration_tools.py', 'dream/tools/capability_tools.py',
           'dream/local_eval_skills.py', 'dream/tools/installed_skill_tools.py', 'dream/tools/artifact_tools.py')


@pytest.fixture
def local_checkout(checkout):
    for name in (*SOURCES, *FIXTURES):
        path = checkout/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('generic synthetic qualification input: ' + name + '\n')
    skill = checkout / 'skills/data-analysis/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: data-analysis\ndescription: Inspect CSV fixtures.\n---\nGeneric steps.\n')
    return checkout


def require_gate(qualifier):
    assert 'local_fixture' in qualifier.GATES, 'The named offline local_fixture gate is missing.'


def test_named_local_fixture_gate_executes_exact_fixed_tests_and_pins_inputs(qualifier, local_checkout, monkeypatch, tmp_path):
    require_gate(qualifier)
    calls = []
    def fake_child(argv, **kwargs):
        calls.append((argv, kwargs))
        assert {arg for arg in argv if arg.startswith('tests/')} == set(FIXTURES)
        assert kwargs['cwd'] == local_checkout and kwargs['timeout'] == 31
        assert kwargs['check'] is False
        report = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--junitxml=')))
        report.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0">'
                         '<testcase classname="synthetic" name="local_contract"/></testsuite>')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(qualifier.subprocess, 'run', fake_child)
    output = tmp_path/'report.json'
    assert qualifier.main(['--gate', 'local_fixture', '--gate', 'local_fixture',
        '--timeout', '31', '--output', str(output)]) == 0
    value = json.loads(output.read_text())
    assert len(calls) == 1 and set(value['gates']) == {'local_fixture'}
    assert value['gates']['local_fixture']['status'] == 'verified'
    assert value['source_consistency']['status'] == 'verified'
    for name in (*SOURCES, *FIXTURES):
        assert value['source_sha256'][name] == hashlib.sha256((local_checkout/name).read_bytes()).hexdigest()
    assert value['source_sha256']['skills/data-analysis/SKILL.md'] == hashlib.sha256(
        (local_checkout/'skills/data-analysis/SKILL.md').read_bytes()).hexdigest()
    assert_offline(value)


@pytest.mark.parametrize('damage', ['missing', 'drift', 'added', 'symlink'])
def test_packaged_skill_snapshot_drift_or_missing_prevents_qualification(qualifier, local_checkout, monkeypatch, tmp_path, damage):
    skill = local_checkout / 'skills/data-analysis/SKILL.md'
    def fake_gate(*args, **kwargs):
        if damage == 'missing':
            skill.unlink()
        elif damage == 'drift':
            skill.write_text(skill.read_text() + 'Changed.\n')
        elif damage == 'added':
            added = local_checkout / 'skills/other/SKILL.md'
            added.parent.mkdir()
            added.write_text('---\nname: other\ndescription: Generic.\n---\nBody.\n')
        else:
            outside = tmp_path/'outside.md'
            outside.write_text('Private bytes must not enter snapshot.\n')
            skill.unlink()
            skill.symlink_to(outside)
        return {'status': 'verified'}
    monkeypatch.setattr(qualifier, 'run_gate', fake_gate)
    output = tmp_path / 'skills-qualification.json'
    assert qualifier.main(['--gate', 'local_fixture', '--output', str(output)]) == 1
    value = json.loads(output.read_text())
    assert value['source_consistency']['status'] == 'failed'
    assert_offline(value)


@pytest.mark.parametrize('path', SOURCES)
@pytest.mark.parametrize('damage', ['missing', 'drift'])
def test_runtime_and_corpus_damage_invalidates_otherwise_successful_qualifier(qualifier, local_checkout, monkeypatch, tmp_path, path, damage):
    # Existing http gate deliberately used here: runtime SOURCES must bind even
    # before the new gate exists, producing a behavioral source-consistency red.
    target = local_checkout/path
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    if damage == 'missing': target.unlink()
    calls = []
    def successful_gate(name, *, timeout):
        calls.append(name)
        if damage == 'drift': target.write_text('changed during successful synthetic child\n')
        return {'status': 'verified', 'exit_code': 0, 'tests': 1, 'failures': [], 'errors': [], 'skips': []}
    monkeypatch.setattr(qualifier, 'run_gate', successful_gate)
    output = tmp_path/'report.json'
    assert qualifier.main(['--gate', 'http', '--output', str(output)]) == 1, path + ' escaped source auditing'
    value = json.loads(output.read_text())
    assert calls == ['http'] and value['gates']['http']['status'] == 'verified'
    assert value['source_sha256'][path] == (None if damage == 'missing' else before)
    assert value['source_consistency']['status'] == 'failed'
    assert value['source_consistency']['changed_paths'] == ([] if damage == 'missing' else [path])
    assert_offline(value)


@pytest.mark.parametrize('path', FIXTURES)
@pytest.mark.parametrize('damage', ['missing', 'drift'])
def test_selected_local_test_missing_or_changed_cannot_be_verified(qualifier, local_checkout, monkeypatch, tmp_path, path, damage):
    require_gate(qualifier)
    target = local_checkout/path
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    if damage == 'missing': target.unlink()
    calls = []
    def successful_gate(name, *, timeout):
        calls.append(name)
        if damage == 'drift': target.write_text('changed synthetic test during gate\n')
        return {'status': 'verified', 'exit_code': 0, 'tests': 1, 'failures': [], 'errors': [], 'skips': []}
    monkeypatch.setattr(qualifier, 'run_gate', successful_gate)
    output = tmp_path/'report.json'
    assert qualifier.main(['--gate', 'local_fixture', '--output', str(output)]) == 1
    value = json.loads(output.read_text())
    assert calls == ['local_fixture']
    assert value['source_sha256'][path] == (None if damage == 'missing' else before)
    assert value['source_consistency']['status'] == 'failed'
    assert value['source_consistency']['changed_paths'] == ([] if damage == 'missing' else [path])
    assert_offline(value)


def test_skipped_local_fixture_is_not_verified_or_live_and_does_not_publish_raw_output(qualifier, local_checkout, monkeypatch, tmp_path):
    require_gate(qualifier)
    marker = 'GENERIC_PRIVATE_FIXTURE_DETAIL'
    def fake_child(argv, **kwargs):
        report = Path(next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--junitxml=')))
        report.write_text('<testsuite tests="1" failures="0" errors="0" skipped="1">'
            '<testcase classname="synthetic" name="required"><skipped>' + marker + '</skipped>'
            '<system-out>' + marker + '</system-out></testcase></testsuite>')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(qualifier.subprocess, 'run', fake_child)
    output = tmp_path/'report.json'
    assert qualifier.main(['--gate', 'local_fixture', '--output', str(output)]) == 1
    value = json.loads(output.read_text())
    assert value['gates']['local_fixture']['status'] == 'not_run'
    assert marker not in output.read_text()
    assert_offline(value)
