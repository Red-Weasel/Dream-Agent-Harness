"""Dedicated file tools cannot overwrite the worker's checkpoint authority."""
from types import SimpleNamespace

import pytest

from dream.tools import context, files, native


@pytest.mark.parametrize('namespace', ['worker-checkpoints', 'tool-artifacts'])
@pytest.mark.parametrize('operation', ['write', 'append', 'edit', 'copy', 'delete', 'delete_parent'])
async def test_dedicated_file_tools_refuse_authority_mutations(tmp_path, namespace, operation):
    authority = tmp_path / '.dream' / namespace
    authority.mkdir(parents=True)
    target = authority / 'original.json'
    target.write_text('original authority')
    source = tmp_path / 'source.txt'
    source.write_text('replacement')
    with context.bind_context(SimpleNamespace(workspace=tmp_path)):
        if operation in ('write', 'append'):
            result = await native.write_file.handler({'path': str(target), 'content': 'replacement',
                                                      'append': operation == 'append'})
        elif operation == 'edit':
            result = await files.str_replace_edit.handler({'path': str(target),
                'old_string': 'original authority', 'new_string': 'replacement'})
        elif operation == 'copy':
            result = await files.copy_files.handler({'files': [{'src': str(source), 'dest': str(target)}]})
        else:
            result = await files.delete_file.handler({'paths': [str(tmp_path / '.dream') if operation == 'delete_parent' else str(target)]})
    assert result.get('is_error') or result.get('isError'), result
    assert target.read_text() == 'original authority'


@pytest.mark.parametrize('operation', ['write', 'edit', 'copy'])
async def test_alias_to_authority_does_not_allow_mutation(tmp_path, operation):
    authority = tmp_path / '.dream/worker-checkpoints'
    authority.mkdir(parents=True)
    target = authority / 'ledger.jsonl'
    target.write_text('original authority')
    alias = tmp_path / 'alias'
    alias.symlink_to(authority, target_is_directory=True)
    source = tmp_path / 'source.txt'
    source.write_text('replacement')
    with context.bind_context(SimpleNamespace(workspace=tmp_path)):
        if operation == 'write':
            result = await native.write_file.handler({'path': 'alias/ledger.jsonl', 'content': 'replacement'})
        elif operation == 'edit':
            result = await files.str_replace_edit.handler({'path': 'alias/ledger.jsonl',
                'old_string': 'original authority', 'new_string': 'replacement'})
        else:
            result = await files.copy_files.handler({'files': [{'src': str(source), 'dest': 'alias/ledger.jsonl'}]})
    assert result.get('is_error') or result.get('isError'), result
    assert target.read_text() == 'original authority'


async def test_regular_dream_report_is_still_writable(tmp_path, monkeypatch):
    monkeypatch.setattr(native.mirror, 'file_written', lambda *a: None)
    with context.bind_context(SimpleNamespace(workspace=tmp_path)):
        result = await native.write_file.handler({'path': '.dream/smoke-report.md', 'content': 'checked report'})
    assert not result.get('is_error') and (tmp_path / '.dream/smoke-report.md').read_text() == 'checked report'
