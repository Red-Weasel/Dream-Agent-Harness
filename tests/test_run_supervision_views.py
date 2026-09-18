"""Public run listings report observed ownership, not a stale saved snapshot."""
import json

import pytest

from dream import config, management
from dream.core.run_state import RunState
from dream.projects.recovery import list_recoveries


@pytest.fixture
def saved_run(tmp_path, monkeypatch):
    root = tmp_path / 'runs'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    journal = RunState(root)
    journal.record('fixture', goal='Continue the saved project',
                   worker_workspace=str(workspace), status='running',
                   phase='worker_running', iterations=1, uncertain=True,
                   next='Verify the saved output before continuing')
    monkeypatch.setattr(config, 'LOOP_DIR', root)
    yield root, workspace, journal
    journal.close()


def both_rows(root, workspace):
    return management.list_runs()[0], list_recoveries(workspace, root)[0]


def test_released_writer_is_not_reported_as_running(saved_run):
    root, workspace, journal = saved_run
    for row in both_rows(root, workspace):
        assert row['status'] == 'running'
        assert row['ownership'] == 'held'
        assert row['continuation'] == 'owned'
    journal.close()
    before = {p.name: p.read_bytes() for p in journal.path.iterdir()}
    for row in both_rows(root, workspace):
        assert row['status'] == 'stopped'
        assert row['recorded_status'] == 'running'
        assert row['ownership'] == 'free'
        assert row['continuation'] == 'needs_reconciliation'
        assert row['requires_reconciliation'] is True
    assert before == {p.name: p.read_bytes() for p in journal.path.iterdir()}


def test_canonical_ledger_overrules_completed_snapshot(saved_run):
    root, workspace, journal = saved_run
    journal.close()
    (journal.path / 'state.json').write_text(json.dumps({'status': 'done', 'goal': 'wrong'}))
    for row in both_rows(root, workspace):
        assert row['recorded_status'] == 'running'
        assert row['goal'] == 'Continue the saved project'
        assert row['status'] != 'done'


def test_missing_lock_is_unknown_and_is_not_created(saved_run):
    root, workspace, journal = saved_run
    journal.close()
    (journal.path / '.lock').unlink()
    for row in both_rows(root, workspace):
        assert row['ownership'] == 'unknown'
        assert row['status'] == 'unknown'
        assert row['requires_reconciliation'] is True
    assert not (journal.path / '.lock').exists()


def test_clean_checkpoint_is_available_without_claiming_it_is_scheduled(saved_run):
    root, workspace, journal = saved_run
    journal.record('worker_finished', phase='ready', uncertain=False)
    journal.close()
    for row in both_rows(root, workspace):
        assert row['continuation'] == 'resume_available'
        assert not row['requires_reconciliation']
        assert row['status'] != 'running'


def test_projects_do_not_reveal_other_workspace(saved_run, tmp_path):
    root, _, journal = saved_run
    assert list_recoveries(tmp_path / 'different', root) == []
    assert management.list_runs()[0]['id'] == journal.run_id


@pytest.mark.parametrize('kind', ['ledger', 'run', 'root'])
def test_symlinks_cannot_supply_run_authority(saved_run, tmp_path, monkeypatch, kind):
    root, workspace, journal = saved_run
    journal.close()
    if kind == 'ledger':
        source = journal.path / 'ledger.jsonl'
        target = tmp_path / 'original-ledger'
        source.rename(target)
        source.symlink_to(target)
    elif kind == 'run':
        target = tmp_path / 'original-run'
        journal.path.rename(target)
        journal.path.symlink_to(target, target_is_directory=True)
    else:
        target = tmp_path / 'linked-root'
        target.symlink_to(root, target_is_directory=True)
        root = target
        monkeypatch.setattr(config, 'LOOP_DIR', root)
    assert management.list_runs() == []
    assert list_recoveries(workspace, root) == []


def test_snapshot_without_canonical_ledger_is_not_a_run(saved_run):
    root, workspace, journal = saved_run
    journal.close()
    (journal.path / 'ledger.jsonl').unlink()
    assert management.list_runs() == []
    assert list_recoveries(workspace, root) == []


def test_deeply_nested_ledger_is_skipped_without_crashing(saved_run):
    root, workspace, journal = saved_run
    journal.close()
    path = journal.path / 'ledger.jsonl'
    path.write_text('{"opaque":' + '[' * 20000 + '0' + ']' * 20000 + ',' + path.read_text()[1:])
    assert management.list_runs() == []
    assert list_recoveries(workspace, root) == []


def test_inspection_rejects_symlink_in_root_ancestors(saved_run, tmp_path):
    from dream.core.run_state import inspect_run
    root, _, journal = saved_run
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        inspect_run(alias / root.name, journal.run_id)


def test_replaced_lock_does_not_claim_free_ownership(saved_run, monkeypatch):
    import os
    import fcntl
    from dream.core import run_state
    root, _, journal = saved_run
    journal.close()
    original = os.open
    held = []
    def replace_lock(path, flags, *args, **kwargs):
        if path == 'ledger.jsonl':
            lock = journal.path / '.lock'
            lock.rename(journal.path / '.old-lock')
            fd = original(lock, os.O_CREAT | os.O_RDWR, 0o600)
            held.append(fd)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(run_state.os, 'open', replace_lock)
    try:
        observed = run_state.inspect_run(root, journal.run_id)
        assert observed['ownership'] == 'unknown'
        assert observed['status'] == 'unknown'
    finally:
        for fd in held:
            os.close(fd)
