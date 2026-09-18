"""Compare supplied task evidence without trusting a completion claim."""
import copy
import hashlib
import json

import pytest

from dream.calibration import compare_trials
from dream.harness_eval import load_cases


def trial(run_id, effort, elapsed, sample='sample-1'):
    case = next(row for row in load_cases() if row['id'] == 'data-totals')
    artifact = json.dumps(case['expected'])
    def manifest(text):
        raw = text.encode()
        return {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    before = {'sales.csv': manifest(case['input'])}
    calls = [{'name': name, 'arguments': {'path': path},
              'result': {'content': [{'type': 'text', 'text': 'observed'}], 'is_error': False}}
             for name, path in [('read_file', 'sales.csv'), ('write_file', 'totals.json')]]
    return {
        'run_id': run_id, 'sample_id': sample, 'case_id': case['id'],
        'harness_sha256': 'a' * 64, 'environment_sha256': 'b' * 64,
        'configuration': {'provider': 'test', 'model_sha256': 'c' * 64,
                          'profile': 'balanced', 'reasoning_effort': effort,
                          'context_window': 32768, 'output_ceiling': 4096,
                          'source': 'prepared_http'},
        'total_elapsed_s': elapsed,
        'duration_scope': 'task_including_repairs_and_verification',
        'final': True, 'protocol_outcome': 'completed',
        'records': {'schema_version': 1, 'origin': 'external-recording', 'run_id': run_id,
                    'cases': [{'id': case['id'], 'outputs': {**case['expected'], 'artifact': artifact},
                               'tools': calls}]},
        'workspace_before': before,
        'workspace_after': {**before, 'totals.json': manifest(artifact)},
        'allowed_outputs': ['totals.json'],
    }


def bundle(*trials):
    return {'schema_version': 1, 'trials': list(trials)}


def test_only_complete_passing_matched_trials_can_be_fastest():
    result = compare_trials(bundle(trial('one', 'low', 10), trial('two', 'high', 20)))
    assert result['comparison_status'] == 'comparable'
    assert result['observed_fastest_configuration'] == result['configurations'][0]['configuration_id']
    assert result['configurations'][0]['configuration']['reasoning_effort'] == 'low'
    assert result['settings_applied'] is False
    assert result['model_quality_measured_by_comparator'] is False


@pytest.mark.parametrize('failure', ['wrong_artifact', 'unrelated_write', 'protocol_error', 'unfinished'])
def test_fast_failure_cannot_win(failure):
    fast, slow = trial('fast', 'low', 1), trial('slow', 'high', 20)
    if failure == 'wrong_artifact':
        fast['records']['cases'][0]['outputs']['rows'] = 900
    elif failure == 'unrelated_write':
        fast['workspace_after']['unrelated.txt'] = {'size': 1, 'sha256': 'd' * 64}
    elif failure == 'protocol_error':
        fast['protocol_outcome'] = 'error'
    else:
        fast['final'] = False
    report = compare_trials(bundle(fast, slow))
    assert report['observed_fastest_configuration'] is None
    assert any(row['failed_or_unknown'] for row in report['configurations'])


def test_failed_attempt_is_not_hidden_by_a_later_success():
    rows = [trial('a1', 'low', 1), trial('a2', 'low', 2, 'sample-2'),
            trial('b1', 'high', 20), trial('b2', 'high', 30, 'sample-2')]
    rows[0]['protocol_outcome'] = 'error'
    report = compare_trials(bundle(*rows))
    assert report['observed_fastest_configuration'] is None


@pytest.mark.parametrize('key,value', [
    ('sample_id', 'different-sample'), ('harness_sha256', 'd' * 64),
    ('environment_sha256', 'e' * 64), ('duration_scope', 'first_token_only'),
])
def test_mismatched_evidence_has_no_winner(key, value):
    other = trial('two', 'high', 20)
    other[key] = value
    assert compare_trials(bundle(trial('one', 'low', 10), other))['observed_fastest_configuration'] is None


def test_unknown_native_effective_configuration_cannot_win():
    other = trial('two', 'high', 2)
    other['configuration']['source'] = 'native_configuration_only'
    assert compare_trials(bundle(trial('one', 'low', 10), other))['observed_fastest_configuration'] is None


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), '10'])
def test_invalid_durations_rejected(value):
    row = trial('one', 'low', value)
    with pytest.raises(ValueError):
        compare_trials(bundle(row))


def test_record_identity_and_duplicate_runs_are_rejected():
    row = trial('one', 'low', 10)
    with pytest.raises(ValueError):
        compare_trials(bundle(row, copy.deepcopy(row)))
    row['records']['run_id'] = 'unrelated-turn'
    with pytest.raises(ValueError):
        compare_trials(bundle(row))


def test_output_contains_no_raw_task_data_or_tool_results():
    row = trial('one', 'low', 10)
    row['records']['cases'][0]['tools'][0]['result']['content'][0]['text'] = 'PRIVATE_FIXTURE_SENTINEL'
    text = json.dumps(compare_trials(bundle(row)))
    assert 'PRIVATE_FIXTURE_SENTINEL' not in text
    assert 'North, Central' not in text
    assert 'workspace_after' not in text


def test_empty_or_excessive_bundle_is_rejected():
    for rows in ([], [trial(str(n), 'low', 1, str(n)) for n in range(201)]):
        with pytest.raises(ValueError):
            compare_trials(bundle(*rows))


def test_configuration_identity_uses_full_model_digest():
    other = trial('two', 'low', 20)
    other['configuration']['model_sha256'] = 'd' * 64
    report = compare_trials(bundle(trial('one', 'low', 10), other))
    assert len(report['configurations']) == 2
    assert report['comparison_scopes'] == [{'harness_sha256': 'a' * 64, 'environment_sha256': 'b' * 64}]


def test_ties_and_unpaired_repetitions_do_not_recommend():
    assert compare_trials(bundle(trial('one', 'low', 10), trial('two', 'high', 10)))['comparison_status'] == 'tied'
    rows = [trial('one', 'low', 10), trial('two', 'high', 20), trial('three', 'low', 8, 'repeat-2')]
    assert compare_trials(bundle(*rows))['comparison_status'] == 'incomparable_task_coverage'


def test_cli_reads_only_explicit_bundle_and_returns_summary(tmp_path, capsys):
    from dream.calibration import main

    path = tmp_path / 'trials.json'
    path.write_text(json.dumps(bundle(trial('one', 'low', 10), trial('two', 'high', 20))))
    before = set(tmp_path.rglob('*'))
    assert main(['--compare', str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['comparison_status'] == 'comparable'
    assert set(tmp_path.rglob('*')) == before


@pytest.mark.parametrize('raw', [
    '{"schema_version": 1, "schema_version": 1, "trials": []}',
    '{"schema_version": 1, "trials": NaN}',
    '[]', 'x' * 2_000_001,
])
def test_cli_rejects_bad_and_overbound_json(tmp_path, raw):
    from dream.calibration import main

    path = tmp_path / 'trials.json'
    path.write_text(raw)
    with pytest.raises(SystemExit) as error:
        main(['--compare', str(path)])
    assert error.value.code == 2
