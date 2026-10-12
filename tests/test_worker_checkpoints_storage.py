"""Private worker journal storage, using real temporary files and host-only faults."""
import errno
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import time

import pytest


SESSION = 'generic-checkpoint-fixture'
RUN = 'a' * 32
BINDING = {'parent': 'generic-fixture'}
SNAPSHOT = {'messages': [{'role': 'user', 'content': 'Generic task.'}]}


def api():
    name = 'dream.core.worker_checkpoints'
    assert importlib.util.find_spec(name) is not None, 'Worker checkpoint module is absent.'
    module = importlib.import_module(name)
    assert callable(getattr(module, 'WorkerJournal', None)), 'WorkerJournal API is absent.'
    assert isinstance(getattr(module, 'WorkerCheckpointError', None), type), 'Typed checkpoint error is absent.'
    return module.WorkerJournal, module.WorkerCheckpointError


def folder(root):
    return root / '.dream/worker-checkpoints/v1' / hashlib.sha256(SESSION.encode()).hexdigest() / RUN


def create(root):
    Journal, _ = api()
    with Journal(root, SESSION, RUN, create=True) as journal:
        journal.create(BINDING, SNAPSHOT, deadline_ns=time.time_ns() + 60_000_000_000, max_rounds=3)
        return journal.inspect()


def refusal(error, operation):
    with pytest.raises(error) as caught:
        operation()
    assert isinstance(caught.value.code, str) and caught.value.code
    return caught.value


def test_storage_api_absence_is_explicit():
    api()


def test_reopen_restores_exact_snapshot_and_exclusive_owner(tmp_path):
    Journal, Error = api()
    original = create(tmp_path)
    with Journal(tmp_path, SESSION, RUN) as first:
        assert first.inspect()['snapshot'] == SNAPSHOT
        assert first.inspect()['binding'] == BINDING
        def second_owner():
            with Journal(tmp_path, SESSION, RUN):
                pytest.fail('A second journal acquired the same run.')
        assert refusal(Error, second_owner).code == 'locked'
    with Journal(tmp_path, SESSION, RUN) as reopened:
        assert reopened.inspect()['snapshot'] == original['snapshot']


@pytest.mark.parametrize('position', ['ancestor', 'workspace', '.dream', 'worker-checkpoints', 'v1', 'session', 'run', 'ledger', 'lock'])
def test_symlink_components_refuse_without_modifying_target(tmp_path, position):
    Journal, Error = api()
    root = tmp_path / 'owner/workspace'
    root.mkdir(parents=True, mode=0o700)
    create(root)
    run = folder(root)
    paths = {'ancestor': root.parent, 'workspace': root, '.dream': root / '.dream',
             'worker-checkpoints': root / '.dream/worker-checkpoints', 'v1': root / '.dream/worker-checkpoints/v1',
             'session': run.parent, 'run': run, 'ledger': run / 'ledger.jsonl', 'lock': run / '.lock'}
    target = paths[position]
    is_directory = target.is_dir()
    moved = tmp_path / 'moved'
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=is_directory)
    before = (moved.read_bytes() if not is_directory else None)
    def reopen():
        with Journal(root, SESSION, RUN):
            pytest.fail('Symlink authority was accepted.')
    refusal(Error, reopen)
    if before is not None:
        assert moved.read_bytes() == before


@pytest.mark.parametrize('name', ['ledger.jsonl', '.lock'])
def test_hardlinked_journal_files_refuse_without_repair(tmp_path, name):
    Journal, Error = api()
    create(tmp_path)
    target = folder(tmp_path) / name
    alias = tmp_path / 'external-alias'
    os.link(target, alias)
    before = target.read_bytes()
    def reopen():
        with Journal(tmp_path, SESSION, RUN):
            pytest.fail('Hardlinked authority file was accepted.')
    refusal(Error, reopen)
    assert target.read_bytes() == alias.read_bytes() == before
    assert target.stat().st_nlink == 2


@pytest.mark.parametrize('part,mode', [('ledger.jsonl', 0o644), ('.lock', 0o644), ('run', 0o755), ('session', 0o755)])
def test_nonprivate_storage_refuses_without_chmod(tmp_path, part, mode):
    Journal, Error = api()
    create(tmp_path)
    run = folder(tmp_path)
    target = run if part == 'run' else run.parent if part == 'session' else run / part
    target.chmod(mode)
    def reopen():
        with Journal(tmp_path, SESSION, RUN):
            pytest.fail('Nonprivate storage was accepted.')
    refusal(Error, reopen)
    assert target.stat().st_mode & 0o777 == mode


@pytest.mark.parametrize('corruption', ['truncated', 'duplicate', 'nan', 'nonobject', 'utf8', 'oversize'])
def test_corrupt_ledgers_refuse_without_rewriting(tmp_path, corruption):
    Journal, Error = api()
    create(tmp_path)
    ledger = folder(tmp_path) / 'ledger.jsonl'
    original = ledger.read_bytes()
    if corruption == 'truncated':
        damaged = original[:-2]
    elif corruption == 'duplicate':
        line = json.loads(original.splitlines()[0])
        key = next(iter(line))
        damaged = ('{' + json.dumps(key) + ':' + json.dumps(line[key]) + ',' + original.decode()[1:]).encode()
    elif corruption == 'nan':
        damaged = b'{"unexpected":NaN}\n'
    elif corruption == 'nonobject':
        damaged = b'[]\n'
    elif corruption == 'utf8':
        damaged = b'\xff\n'
    else:
        damaged = b'x' * (16 * 1024 * 1024 + 1)
    ledger.write_bytes(damaged)
    def reopen():
        with Journal(tmp_path, SESSION, RUN):
            pytest.fail('Invalid ledger was accepted.')
    refusal(Error, reopen)
    assert ledger.read_bytes() == damaged


def test_missing_snapshot_original_refuses_reopen(tmp_path):
    Journal, Error = api()
    state = create(tmp_path)
    assert isinstance(state.get('snapshot_receipt'), dict)
    originals = list((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))
    assert originals, 'Checkpoint creation must persist its snapshot original.'
    for path in originals:
        path.unlink()
    def reopen():
        with Journal(tmp_path, SESSION, RUN) as journal:
            journal.inspect()
    refusal(Error, reopen)


def test_host_snapshot_scope_refusal_propagates_without_ledger_repair(tmp_path, monkeypatch):
    Journal, Error = api()
    from dream.core.tool_artifacts import ArtifactError, ArtifactStore
    create(tmp_path)
    ledger = folder(tmp_path) / 'ledger.jsonl'
    before = ledger.read_bytes()
    calls = []
    def refuse_scope(*args, **kwargs):
        calls.append(True)
        raise ArtifactError('scope')
    with monkeypatch.context() as patch:
        patch.setattr(ArtifactStore, 'load_original', refuse_scope, raising=False)
        def reopen():
            with Journal(tmp_path, SESSION, RUN) as journal:
                journal.inspect()
        assert refusal(Error, reopen).code == 'scope'
    assert calls and ledger.read_bytes() == before


@pytest.mark.parametrize('failure_kind', ['write', 'fsync'])
def test_append_failure_latches_admission_even_after_io_recovers(tmp_path, monkeypatch, failure_kind):
    Journal, Error = api()
    create(tmp_path)
    with Journal(tmp_path, SESSION, RUN) as journal:
        ledger_info = (folder(tmp_path) / 'ledger.jsonl').stat()
        real = getattr(os, failure_kind)
        fired = False
        def fail(fd, *args):
            nonlocal fired
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (ledger_info.st_dev, ledger_info.st_ino):
                fired = True
                raise OSError(errno.ENOSPC, 'Synthetic checkpoint storage fault')
            return real(fd, *args)
        with monkeypatch.context() as patch:
            patch.setattr(os, failure_kind, fail)
            refusal(Error, lambda: journal.request_started(now_ns=time.time_ns()))
        assert fired
        assert refusal(Error, lambda: journal.request_started(now_ns=time.time_ns())).code == 'storage_failed'


def test_snapshot_save_failure_does_not_reopen_admission(tmp_path, monkeypatch):
    Journal, Error = api()
    from dream.core.tool_artifacts import ArtifactError, ArtifactStore
    create(tmp_path)
    with Journal(tmp_path, SESSION, RUN) as journal:
        journal.request_started(now_ns=time.time_ns())
        def fail_save(*args, **kwargs):
            raise ArtifactError('storage_io')
        with monkeypatch.context() as patch:
            patch.setattr(ArtifactStore, 'save', fail_save)
            refusal(Error, lambda: journal.settle({'messages': ['New observation.']}))
        assert refusal(Error, lambda: journal.request_started(now_ns=time.time_ns())).code == 'storage_failed'
