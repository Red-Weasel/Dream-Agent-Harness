"""Offline framework checks, never model-quality measurements."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dream import harness_eval


@pytest.mark.asyncio
async def test_self_test_executes_native_tools_and_grades_all_cases():
    records = await harness_eval.run_self_test()
    assert records['origin'] == 'offline-self-test'
    report = harness_eval.grade(records)
    assert report['passed'] == report['total'] == 5
    assert report['measurement'] == 'offline framework checks'
    assert {case['id'] for case in records['cases']} == {case['id'] for case in harness_eval.load_cases()}
    assert all(case['tools'] for case in records['cases'])
    recovery = next(case for case in records['cases'] if case['id'] == 'recovery-ambiguous-edit')
    assert recovery['tools'][1]['result']['is_error'] is True
    assert 'matches 2 time(s)' in recovery['tools'][1]['result']['content'][0]['text']


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id,key,bad', [
    ('coding-boundary', 'source', 'def shipping(total):\n    return 0\n'),
    ('documents-facts', 'facts', {'project': 'Orion', 'budget_usd': 9000, 'deadline': '2026-10-31'}),
    ('data-totals', 'totals', {'North, Central': '30.00', 'South': '20.00'}),
    ('recovery-ambiguous-edit', 'source', 'primary=60\nbackup=60\n'),
    ('tool-use-scope', 'matches', 'decoy.py:1: API_VERSION = 99'),
])
async def test_wrong_outcome_fails_even_with_self_reported_pass(case_id, key, bad):
    records = await harness_eval.run_self_test()
    case = next(c for c in records['cases'] if c['id'] == case_id)
    case['outputs'][key] = bad
    case['passed'] = True
    report = harness_eval.grade(records)
    assert report['passed'] == 4
    assert next(c for c in report['cases'] if c['id'] == case_id)['failures']


@pytest.mark.asyncio
async def test_missing_case_and_missing_tool_evidence_fail():
    records = await harness_eval.run_self_test()
    records['cases'].pop()
    records['cases'][0]['tools'] = []
    assert harness_eval.grade(records)['passed'] == 3


@pytest.mark.asyncio
async def test_duplicate_unknown_and_malformed_records_rejected():
    records = await harness_eval.run_self_test()
    for mutate in (
        lambda r: r['cases'].append(copy.deepcopy(r['cases'][0])),
        lambda r: r['cases'][0].update(id='unknown'),
        lambda r: r.update(schema_version=2),
        lambda r: r['cases'][0].update(tools='pass'),
    ):
        bad = copy.deepcopy(records)
        mutate(bad)
        with pytest.raises(ValueError):
            harness_eval.grade(bad)


def test_cli_default_and_export_grade_without_network_or_model_imports(tmp_path):
    target = tmp_path/'records.json'
    # The audit hook rejects process launches and networking within the evaluated
    # process; the import hook rejects model libraries and Dream inference paths.
    script = '''
import importlib.abc, runpy, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ('torch', 'fastembed', 'onnxruntime', 'transformers', 'dream.core.engine', 'dream.core.backends', 'dream.local')
        if any(fullname == x or fullname.startswith(x + '.') for x in blocked):
            raise AssertionError('Forbidden import: ' + fullname)
sys.meta_path.insert(0, Guard())
def audit(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'):
        raise AssertionError('Forbidden side effect: ' + event)
sys.addaudithook(audit)
sys.argv = ['dream.harness_eval', *sys.argv[1:]]
runpy.run_module('dream.harness_eval', run_name='__main__')
'''
    def run(*args):
        return subprocess.run([sys.executable, '-c', script, *args], capture_output=True, text=True, timeout=30)
    result = run('--export', str(target))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['passed'] == 5
    assert target.is_file()
    assert run('--grade', str(target)).returncode == 0
    records = json.loads(target.read_text())
    records['origin'] = 'external-recording'
    records['cases'] = []
    target.write_text(json.dumps(records))
    result = run('--grade', str(target))
    assert result.returncode == 1
    assert json.loads(result.stdout)['passed'] == 0
    assert 'externally supplied' in json.loads(result.stdout)['measurement']


@pytest.mark.asyncio
async def test_recovery_cannot_pass_by_repeating_unchanged_refused_call():
    records = await harness_eval.run_self_test()
    recovery = next(case for case in records['cases'] if case['id'] == 'recovery-ambiguous-edit')
    recovery['tools'][3] = copy.deepcopy(recovery['tools'][1])
    assert harness_eval.grade(records)['passed'] == 4


@pytest.mark.asyncio
async def test_grading_never_executes_recorded_python_or_tools(tmp_path):
    records = await harness_eval.run_self_test()
    marker = tmp_path/'must-not-exist'
    records['cases'][0]['outputs']['source'] = f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'
    records['cases'][0]['tools'][0]['name'] = 'run_bash'
    records['cases'][0]['tools'][0]['arguments'] = {'command': f'touch {marker}'}
    assert harness_eval.grade(records)['passed'] == 4
    assert not marker.exists()


def test_cli_list_invalid_records_and_existing_export(tmp_path, capsys):
    assert harness_eval.main(['--list']) == 0
    assert len(json.loads(capsys.readouterr().out)) == 5
    target = tmp_path/'record.json'
    target.write_text('original')
    assert harness_eval.main(['--export', str(target)]) == 2
    assert target.read_text() == 'original'
    assert 'already exists' in capsys.readouterr().out
    assert harness_eval.main(['--grade', str(target)]) == 2
    assert 'error' in json.loads(capsys.readouterr().out)
    target.write_bytes(b' ' * 2_000_001)
    assert harness_eval.main(['--grade', str(target)]) == 2
    assert 'exceeds 2 MB' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_recovery_rejects_editing_wrong_path_or_value():
    for change in ({'path':'different.txt'}, {'new_string':'primary=99'}):
        records = await harness_eval.run_self_test()
        recovery = next(case for case in records['cases'] if case['id'] == 'recovery-ambiguous-edit')
        recovery['tools'][3]['arguments'].update(change)
        assert harness_eval.grade(records)['passed'] == 4


@pytest.mark.asyncio
async def test_artifact_boolean_is_not_an_integer_count():
    records = await harness_eval.run_self_test()
    data = next(case for case in records['cases'] if case['id'] == 'data-totals')
    artifact = json.loads(data['outputs']['artifact'])
    artifact['excluded'] = True
    data['outputs']['artifact'] = json.dumps(artifact)
    assert harness_eval.grade(records)['passed'] == 4
