"""Independent-review findings reproduced with actual public handlers/storage."""
import time
from types import SimpleNamespace

import pytest

from dream.core.worker_checkpoints import WorkerCheckpointError, WorkerJournal
from dream.tools import context, files, native


def authority(root, namespace):
    folder = root / '.dream' / namespace
    folder.mkdir(parents=True)
    target = folder / 'ledger.jsonl'
    target.write_text('original authority')
    return folder, target


@pytest.mark.parametrize('namespace', ['worker-checkpoints', 'tool-artifacts'])
@pytest.mark.parametrize('operation', ['write', 'edit', 'copy'])
async def test_symlink_parent_traversal_cannot_hide_authority(tmp_path, namespace, operation):
    folder, target = authority(tmp_path, namespace)
    (folder / 'sub').mkdir()
    (tmp_path / 'alias').symlink_to(folder / 'sub', target_is_directory=True)
    source = tmp_path / 'source.txt'; source.write_text('replacement')
    with context.bind_context(SimpleNamespace(workspace=tmp_path)):
        path = 'alias/../ledger.jsonl'
        if operation == 'write':
            result = await native.write_file.handler({'path': path, 'content': 'replacement'})
        elif operation == 'edit':
            result = await files.str_replace_edit.handler({'path': path, 'old_string': 'original authority',
                                                           'new_string': 'replacement'})
        else:
            result = await files.copy_files.handler({'files': [{'src': str(source), 'dest': path}]})
    assert result.get('is_error') or result.get('isError'), result
    assert target.read_text() == 'original authority'


@pytest.mark.parametrize('namespace', ['worker-checkpoints', 'tool-artifacts'])
@pytest.mark.parametrize('alias_kind', ['destination_directory', 'destination_basename', 'source_directory'])
async def test_recursive_copy_validates_effective_descendants(tmp_path, namespace, alias_kind):
    folder, target = authority(tmp_path, namespace)
    source = tmp_path / 'input'; source.mkdir()
    destination = tmp_path / 'output'; destination.mkdir()
    if alias_kind == 'destination_directory':
        (source / 'sub').mkdir(); (source / 'sub/ledger.jsonl').write_text('replacement')
        (destination / 'sub').symlink_to(folder, target_is_directory=True)
    elif alias_kind == 'source_directory':
        (source / 'sub').symlink_to(folder, target_is_directory=True)
    else:
        (source / 'ledger.jsonl').write_text('replacement')
        source = source / 'ledger.jsonl'
        (destination / 'ledger.jsonl').symlink_to(target)
    with context.bind_context(SimpleNamespace(workspace=tmp_path)):
        result = await files.copy_files.handler({'files': [{'src': str(source), 'dest': str(destination)}]})
    assert result.get('is_error') or result.get('isError'), result
    assert target.read_text() == 'original authority'
    assert not (destination / 'sub/ledger.jsonl').is_file() or alias_kind == 'destination_directory'


async def test_recursive_delete_preserves_nested_project_journal(tmp_path):
    root = tmp_path / 'container/project'; root.mkdir(parents=True)
    with WorkerJournal(root, 'nested-project', 'a' * 32, create=True) as journal:
        journal.create({'task': 'nested'}, {'rules': 'original'},
                       deadline_ns=time.time_ns() + 60_000_000_000, max_rounds=3)
    with context.bind_context(SimpleNamespace(workspace=tmp_path)):
        result = await files.delete_file.handler({'paths': ['container']})
    assert result.get('is_error') or result.get('isError'), result
    with WorkerJournal(root, 'nested-project', 'a' * 32) as journal:
        assert journal.inspect()['snapshot'] == {'rules': 'original'}


def test_late_tool_outcome_cannot_mutate_terminal_receipt(tmp_path):
    with WorkerJournal(tmp_path, 'late-outcome', 'e' * 32, create=True) as journal:
        journal.create({'task': 'original'}, {'rules': 'original'},
                       deadline_ns=time.time_ns() + 60_000_000_000, max_rounds=3)
        journal.request_started(now_ns=time.time_ns())
        journal.tool_started('call')
        journal.control('stopped')
        seq = journal.finish('incomplete', {'result': 'stopped'})
        before = (journal.path / 'ledger.jsonl').read_bytes()
        with pytest.raises(WorkerCheckpointError) as caught:
            journal.tool_finished('call', 'a' * 64)
        assert caught.value.code == 'terminal'
        assert (journal.path / 'ledger.jsonl').read_bytes() == before
    with WorkerJournal(tmp_path, 'late-outcome', 'e' * 32) as journal:
        assert journal.inspect()['completion_seq'] == seq


@pytest.mark.parametrize('namespace', ['worker-checkpoints', 'tool-artifacts'])
async def test_batch_move_cannot_retarget_later_copy_into_authority(tmp_path, namespace):
    folder, target = authority(tmp_path, namespace)
    (tmp_path / 'target').symlink_to(folder, target_is_directory=True)
    source = tmp_path / 'stage/src'; source.mkdir(parents=True)
    (source / 'sub').symlink_to('../target', target_is_directory=True)
    replacement = tmp_path / 'replacement.txt'; replacement.write_text('replacement')
    with context.bind_context(SimpleNamespace(workspace=tmp_path)):
        result = await files.copy_files.handler({'files': [
            {'src': str(source), 'dest': 'out', 'move': True},
            {'src': str(replacement), 'dest': 'out/sub/ledger.jsonl'}]})
    assert result.get('is_error') or result.get('isError'), result
    assert target.read_text() == 'original authority'
    # This known invalid plan is rejected before the otherwise ordinary move.
    assert source.is_dir() and not (tmp_path / 'out').exists()


@pytest.mark.parametrize('move', [False, True])
async def test_ordinary_file_to_workspace_directory_preserves_unrelated_journal(tmp_path, move):
    _, target = authority(tmp_path, 'worker-checkpoints')
    source = tmp_path / 'input/source.txt'; source.parent.mkdir(); source.write_text('ordinary file')
    with context.bind_context(SimpleNamespace(workspace=tmp_path)):
        result = await files.copy_files.handler({'files': [
            {'src': str(source), 'dest': str(tmp_path), 'move': move}]})
    assert not result.get('is_error') and not result.get('isError'), result
    assert (tmp_path / 'source.txt').read_text() == 'ordinary file'
    assert target.read_text() == 'original authority'
