"""CPU fixtures for project scope, context curation and durable pin state."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path

import pytest

from dream.projects import ProjectError, ProjectWorkspace, StaleRevision, build_context
from dream.projects.recovery import list_recoveries


def test_search_excludes_hidden_secrets_and_symlinks_with_provenance(tmp_path):
    (tmp_path / 'example.py').write_text('def greeting():\n    return "hello needle"\n')
    (tmp_path / '.env').write_text('needle secret')
    (tmp_path / 'credentials.json').write_text('needle secret')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git/config').write_text('needle hidden')
    (tmp_path / 'outside').symlink_to('/etc', target_is_directory=True)
    result = ProjectWorkspace(tmp_path).search('needle')
    assert {hit['path'] for hit in result['hits']} == {'example.py'}
    hit = result['hits'][0]
    source = (tmp_path / hit['path']).read_text()
    assert source[hit['offset']:hit['end']] == hit['snippet']
    assert hit['line'] == 2


def test_search_marks_bounds_and_rejects_empty_query(tmp_path):
    for i in range(5):
        (tmp_path / f'{i}.txt').write_text('keyword')
    svc = ProjectWorkspace(tmp_path)
    result = svc.search('keyword', max_files=2)
    assert result['files_checked'] == 2 and result['partial']
    with pytest.raises(ProjectError):
        svc.search('!!!')


def test_manifest_persists_and_cas_serializes_writers(tmp_path):
    svc = ProjectWorkspace(tmp_path)
    assert svc.manifest()['revision'] == 0
    assert not svc.root.exists()
    def write(i):
        try:
            svc.pin(kind='fact', text=f'Fact {i}', expected_revision=0)
            return 'saved'
        except StaleRevision:
            return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, [1, 2])) == ['saved', 'stale']
    assert ProjectWorkspace(tmp_path).manifest()['revision'] == 1
    assert os.stat(svc.root / 'manifest.json').st_mode & 0o777 == 0o600


def test_pin_change_requires_revalidation_and_missing_warns(tmp_path):
    path = tmp_path / 'notes.md'
    path.write_text('old fact')
    svc = ProjectWorkspace(tmp_path)
    manifest = svc.pin(kind='file', path='notes.md', expected_revision=0)
    pin = manifest['pins'][0]
    assert 'old fact' in svc.context()['text']
    path.write_text('new fact')
    context = svc.context()
    assert not context['text'] and 'Changed file excluded' in context['warnings'][0]
    svc.refresh_pin(pin['id'], 1)
    assert 'new fact' in svc.context()['text']
    path.unlink()
    assert 'Unavailable pin' in svc.context()['warnings'][0]
    svc.remove_pin(pin['id'], 2)
    assert svc.manifest()['pins'] == []


def test_constraints_survive_budget_and_context_lists_omissions(tmp_path):
    svc = ProjectWorkspace(tmp_path)
    svc.pin(kind='fact', text='x' * 1000, expected_revision=0)
    svc.pin(kind='fact', text='y' * 1000, expected_revision=1)
    svc.pin(kind='constraint', text='Never load models.', expected_revision=2)
    svc.pin(kind='decision', text='z' * 1000, expected_revision=3)
    context = svc.context('x')
    assert len(context['text']) <= 2600 and 'Never load models.' in context['text']
    assert context['warnings']
    assert context['text'].index('constraint:') < context['text'].index('fact:')
    assert build_context(None)['text'] == ''


@pytest.mark.parametrize('path', ['../outside', '/etc/passwd', '.env', '.git/config', 'keys/private-key.txt', 'cert.pem'])
def test_refuses_unsafe_explicit_pins(tmp_path, path):
    with pytest.raises((OSError, ProjectError)):
        ProjectWorkspace(tmp_path).pin(kind='file', path=path, expected_revision=0)


def test_symlink_state_and_nested_source_are_rejected(tmp_path):
    workspace, outside = tmp_path / 'workspace', tmp_path / 'outside'
    workspace.mkdir(); outside.mkdir()
    (outside / 'note.txt').write_text('private')
    (workspace / 'link').symlink_to(outside, target_is_directory=True)
    svc = ProjectWorkspace(workspace)
    with pytest.raises(OSError):
        svc.read_source('link/note.txt')
    (workspace / '.dream').symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        svc.pin(kind='fact', text='test', expected_revision=0)
    assert list(outside.iterdir()) == [outside / 'note.txt']


def test_manifest_schema_and_identity_fail_closed(tmp_path):
    svc = ProjectWorkspace(tmp_path)
    svc.pin(kind='fact', text='valid', expected_revision=0)
    target = svc.root / 'manifest.json'
    record = json.loads(target.read_text()); record['schema_version'] = 2
    target.write_text(json.dumps(record))
    assert 'unavailable' in build_context(tmp_path)['warnings'][0]
    with pytest.raises(ProjectError):
        svc.pin(kind='fact', text='overwrite', expected_revision=1)
    assert json.loads(target.read_text())['schema_version'] == 2


def test_readonly_recovery_validates_ledger_workspace_and_uncertainty(tmp_path):
    root = tmp_path / 'runs'; root.mkdir()
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    for name, wanted, broken in [('own', str(workspace), False), ('other', '/elsewhere', False), ('broken', str(workspace), True)]:
        run = root / name; run.mkdir()
        record = {'seq': 1, 'at': '2026-09-10', 'event': 'fixture', 'data': {}, 'state': {'run_id': name, 'worker_workspace': wanted,
                  'goal': 'A saved task', 'status': 'running', 'uncertain': True}}
        (run / 'ledger.jsonl').write_text(json.dumps(record) + ('garbage' if broken else '\n'))
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    found = list_recoveries(workspace, root)
    assert [item['run_id'] for item in found] == ['own']
    assert found[0]['requires_reconciliation']
    assert before == {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert not list(root.rglob('.lock'))


def test_office_search_extracts_checked_copy_with_markers(tmp_path):
    from zipfile import ZipFile
    with ZipFile(tmp_path / 'notes.docx', 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>document needle</w:t></w:r></w:p></w:body></w:document>')
    result = ProjectWorkspace(tmp_path).search('needle')
    assert result['hits'], result['warnings']
    assert result['hits'][0]['path'] == 'notes.docx'
    assert 'document needle' in result['hits'][0]['snippet']
    assert result['partial']
