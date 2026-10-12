"""Integrity evidence must bind central dispatch, ledger and task authorities."""
import hashlib
import json

import pytest

from test_qualification_managed_gates import qualifier, checkout


AUTHORITIES = ('dream/core/loop.py', 'dream/core/run_state.py',
               'dream/memory/tasks.py', 'scripts/report_endurance.py')


@pytest.mark.parametrize('source', AUTHORITIES)
@pytest.mark.parametrize('mutation', ['drift', 'missing'])
def test_authority_drift_or_missing_source_refuses_qualification(
        qualifier, checkout, tmp_path, monkeypatch, source, mutation):
    target = checkout/source
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('synthetic authority source\n')
    original = hashlib.sha256(target.read_bytes()).hexdigest()
    if mutation == 'missing': target.unlink()
    def gate(*args, **kwargs):
        if mutation == 'drift': target.write_text('changed authority during fixture execution\n')
        return dict(status='verified', exit_code=0, tests=1, failures=[], errors=[], skips=[])
    monkeypatch.setattr(qualifier, 'run_gate', gate)
    output = tmp_path/'report.json'
    assert qualifier.main(['--gate', 'managed', '--output', str(output)]) == 1
    report = json.loads(output.read_text())
    assert report['source_sha256'][source] == (original if mutation == 'drift' else None)
    assert report['source_consistency']['status'] == 'failed'
    if mutation == 'drift':
        assert source in report['source_consistency']['changed_paths']
    else:
        # A file absent at both snapshots is missing, rather than changed.
        assert report['source_consistency']['changed_paths'] == []
    assert report['live_provider_inference']['status'] == 'not_run'
