"""Loaded-process identity stays anchored while selected disk source changes."""
import os

import pytest

from dream import runtime_identity as identity


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, '_ROOT', tmp_path)
    monkeypatch.setattr(identity, '_FILES', ('module.py',))
    path = tmp_path / 'module.py'
    path.write_text('value = 1\n')
    monkeypatch.setattr(identity, '_INITIAL', identity._snapshot(tmp_path))
    return path


def test_disk_edit_does_not_replace_process_startup_identity(source):
    before = identity.runtime_identity()
    assert before['source_changed'] is False
    source.write_text('value = 2\n')
    after = identity.runtime_identity()
    assert after['startup_source_id'] == before['startup_source_id']
    assert after['disk_source_id'] != before['disk_source_id']
    assert after['imported_at'] == before['imported_at']
    assert after['source_changed'] is True
    assert after['changed_files'] == ['module.py']
    assert str(source.parent) not in str(after)


@pytest.mark.parametrize('kind', ['missing', 'symlink', 'fifo', 'oversize'])
def test_unreadable_identity_stays_unknown_and_never_blocks(source, tmp_path, kind):
    source.unlink()
    if kind == 'symlink':
        target = tmp_path / 'other'
        target.write_text('private fixture')
        source.symlink_to(target)
    elif kind == 'fifo':
        os.mkfifo(source)
    elif kind == 'oversize':
        source.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
    report = identity.runtime_identity()
    assert report['source_changed'] is None
    assert report['disk_source_id'] is None
    assert 'unavailable' in report['restart_guidance']


def test_management_status_reports_process_identity(monkeypatch):
    from dream import management, extensions
    monkeypatch.setattr(extensions, 'status', lambda **kwargs: {})
    report = management.runtime_status()
    assert report['runtime_identity']['pid'] == os.getpid()
    assert report['model_loaded_by_this_command'] is False
