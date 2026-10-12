"""Restore original output baselines without recapturing today's files."""
from copy import deepcopy
import json

import pytest

from dream.core.worker_result import ContractError, OutputCheck, parse_contract


def outputs(*paths):
    return parse_contract(outputs=[{'path': path, 'format': 'text'} for path in paths]).outputs


def roundtrip(check):
    return json.loads(json.dumps(check.snapshot(), allow_nan=False))


def invalid(root, contract, snapshot):
    with pytest.raises(ContractError) as caught:
        OutputCheck.from_snapshot(root, contract, snapshot)
    assert caught.value.reason == 'invalid_snapshot'


@pytest.mark.parametrize('before', ['missing', 'unchanged', 'modified', 'other'])
def test_restored_verdict_preserves_before_worker_baseline(tmp_path, before):
    target = tmp_path / 'result.txt'
    if before in ('unchanged', 'modified'):
        target.write_text('original bytes')
    elif before == 'other':
        target.mkdir()
    contract = outputs('result.txt')
    snapshot = roundtrip(OutputCheck(tmp_path, contract))
    if before == 'other':
        target.rmdir()
    if before != 'unchanged':
        target.write_text('worker output')
    restored = OutputCheck.from_snapshot(tmp_path, contract, snapshot)
    passed, report, _ = restored.finish()
    assert passed == (before != 'unchanged')
    if before == 'unchanged':
        assert 'output_unchanged' in report
    else:
        assert 'verified' in report


def test_restoration_never_observes_outputs_for_a_new_baseline(tmp_path, monkeypatch):
    contract = outputs('result.txt')
    snapshot = roundtrip(OutputCheck(tmp_path, contract))
    (tmp_path / 'result.txt').write_text('written before restart')
    def deny_observation(*args, **kwargs):
        pytest.fail('Restoration attempted to recapture a declared output')
    monkeypatch.setattr(OutputCheck, '_observe', deny_observation)
    restored = OutputCheck.from_snapshot(tmp_path, contract, snapshot)
    assert restored.finish()[0]


def test_snapshot_has_exact_json_format_and_detached_copies(tmp_path):
    (tmp_path / 'existing.txt').write_text('original')
    contract = outputs('missing.txt', 'existing.txt')
    original = OutputCheck(tmp_path, contract)
    snapshot = roundtrip(original)
    assert set(snapshot) == {'kind', 'version', 'root', 'root_id', 'outputs', 'before'}
    assert snapshot['kind'] == 'dream.output_check_snapshot' and snapshot['version'] == 1
    assert snapshot['outputs'] == [dict(path=p, format='text', required_keys=[])
                                  for p in ('missing.txt', 'existing.txt')]
    assert snapshot['before'][0] == {'kind': 'missing'}
    assert set(snapshot['before'][1]) == {'kind', 'identity', 'sha256'}
    assert len(snapshot['before'][1]['identity']) == 5
    restored = OutputCheck.from_snapshot(tmp_path, contract, snapshot)
    saved = deepcopy(snapshot)
    snapshot['before'][0]['kind'] = 'other'
    snapshot['outputs'][0]['path'] = 'replacement.txt'
    assert roundtrip(restored) == saved
    changed = original.snapshot()
    changed['root_id'][0] += 1
    changed['before'][0]['kind'] = 'other'
    assert roundtrip(original) == saved


def test_null_digest_original_identity_survives_restore(tmp_path):
    (tmp_path / 'result.txt').write_text('original')
    contract = outputs('result.txt')
    snapshot = roundtrip(OutputCheck(tmp_path, contract))
    snapshot['before'][0]['sha256'] = None
    restored = OutputCheck.from_snapshot(tmp_path, contract, snapshot)
    assert roundtrip(restored) == snapshot
    assert not restored.finish()[0]


@pytest.mark.parametrize('damage', [
    'extra', 'missing_key', 'version_bool', 'version_float', 'root_bool',
    'root_identity_bool', 'short', 'long', 'baseline_extra', 'baseline_kind',
    'identity_bool', 'identity_float', 'identity_nonfinite', 'identity_short',
    'invalid_sha', 'contract_extra', 'oversize',
])
def test_malformed_snapshot_has_typed_refusal(tmp_path, damage):
    (tmp_path / 'result.txt').write_text('original')
    contract = outputs('result.txt')
    snapshot = roundtrip(OutputCheck(tmp_path, contract))
    baseline = snapshot['before'][0]
    if damage == 'extra': snapshot['extra'] = 'unknown'
    elif damage == 'missing_key': del snapshot['kind']
    elif damage == 'version_bool': snapshot['version'] = True
    elif damage == 'version_float': snapshot['version'] = 1.0
    elif damage == 'root_bool': snapshot['root'] = True
    elif damage == 'root_identity_bool': snapshot['root_id'][0] = True
    elif damage == 'short': snapshot['before'] = []
    elif damage == 'long': snapshot['before'].append({'kind': 'missing'})
    elif damage == 'baseline_extra': baseline['extra'] = 'unknown'
    elif damage == 'baseline_kind': baseline['kind'] = 'unknown'
    elif damage == 'identity_bool': baseline['identity'][0] = True
    elif damage == 'identity_float': baseline['identity'][0] = 1.0
    elif damage == 'identity_nonfinite': baseline['identity'][0] = float('nan')
    elif damage == 'identity_short': baseline['identity'].pop()
    elif damage == 'invalid_sha': baseline['sha256'] = 'not a digest'
    elif damage == 'contract_extra': snapshot['outputs'][0]['extra'] = 'unknown'
    else: snapshot['root'] = 'x' * (512 * 1024 + 1)
    invalid(tmp_path, contract, snapshot)


@pytest.mark.parametrize('change', ['path', 'format', 'required_keys', 'order'])
def test_supplied_contract_must_match_original_exactly(tmp_path, change):
    contract = outputs('one.txt', 'two.txt')
    snapshot = roundtrip(OutputCheck(tmp_path, contract))
    raw = deepcopy(snapshot['outputs'])
    if change == 'path': raw[0]['path'] = 'different.txt'
    elif change == 'format': raw[0]['format'] = 'json'
    elif change == 'required_keys':
        raw[0].update(format='json', required_keys=['required'])
    else: raw.reverse()
    changed = parse_contract(outputs=raw).outputs
    invalid(tmp_path, changed, snapshot)


def test_workspace_inode_replacement_refuses_restore(tmp_path):
    root = tmp_path / 'workspace'; root.mkdir()
    contract = outputs('result.txt')
    snapshot = roundtrip(OutputCheck(root, contract))
    root.rename(tmp_path / 'original-workspace')
    root.mkdir()
    invalid(root, contract, snapshot)


def test_foreign_workspace_refuses_original_snapshot(tmp_path):
    contract = outputs('result.txt')
    snapshot = roundtrip(OutputCheck(tmp_path, contract))
    foreign = tmp_path / 'foreign'; foreign.mkdir()
    invalid(foreign, contract, snapshot)


def test_current_protected_config_admission_runs_before_output_observation(tmp_path, monkeypatch):
    from dream import config
    contract = outputs('settings.txt')
    snapshot = roundtrip(OutputCheck(tmp_path, contract))
    monkeypatch.setattr(config, 'MCP_CONFIG_PATH', tmp_path / 'settings.txt')
    def deny_observation(*args, **kwargs):
        pytest.fail('Protected output observed during restoration')
    monkeypatch.setattr(OutputCheck, '_observe', deny_observation)
    with pytest.raises(ContractError) as caught:
        OutputCheck.from_snapshot(tmp_path, contract, snapshot)
    assert caught.value.reason == 'invalid_contract'
