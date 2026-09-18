"""Private project metadata and scoped, bounded historical conversations."""
import json
import os

import pytest

from dream import config
from dream.memory.store import MemoryStore
from dream.projects.library import ProjectLibrary
from dream.projects import ProjectError, StaleRevision


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'memory.db')
    return ProjectLibrary(tmp_path / 'catalog.json')


def test_project_roundtrip_and_compare_and_swap(library, tmp_path):
    project = library.create('A project', str(tmp_path), 'Use concise prose.')
    assert ProjectLibrary(library.path).get(project['id']) == project
    assert library.list()[0]['session_count'] == 0
    changed = library.update(project['id'], expected_revision=project['revision'], instructions='New instructions')
    assert changed['instructions'] == 'New instructions'
    with pytest.raises(StaleRevision):
        library.update(project['id'], expected_revision=project['revision'], name='stale')
    assert library.get(project['id']) == changed
    assert os.stat(library.path).st_mode & 0o777 == 0o600


def test_workspace_identity_cannot_relabel_history(library, tmp_path):
    one = library.create('One', str(tmp_path), '')
    with pytest.raises(ProjectError):
        library.create('Duplicate', str(tmp_path), '')
    other = tmp_path / 'other'
    other.mkdir()
    with pytest.raises(ProjectError):
        library.update(one['id'], expected_revision=one['revision'], workspace=str(other))
    assert library.find_workspace(tmp_path)['id'] == one['id']


@pytest.mark.parametrize('name,instructions', [('', ''), ('x' * 201, ''), ('ok', 'x' * 16001), (True, '')])
def test_invalid_records_do_not_persist(library, tmp_path, name, instructions):
    with pytest.raises(ProjectError):
        library.create(name, str(tmp_path), instructions)
    assert library.list() == []


def test_corrupt_and_symlink_catalog_not_replaced(library, tmp_path):
    library.path.write_text('{bad')
    with pytest.raises(ProjectError):
        library.create('one', str(tmp_path), '')
    assert library.path.read_text() == '{bad'
    library.path.unlink()
    target = tmp_path / 'target'
    target.write_text('{}')
    library.path.symlink_to(target)
    with pytest.raises((ProjectError, OSError)):
        library.list()
    assert target.read_text() == '{}'


def test_history_requires_explicit_association_and_returns_recent_bounded_text(library, tmp_path):
    project = library.create('One', str(tmp_path), '')
    store = MemoryStore(config.DB_PATH)
    try:
        store.start_session('known', 'Conversation')
        store.start_session('unrelated', 'Other workspace')
        for i in range(205):
            store.add_turn('known', 'assistant', str(i) + 'x' * 20000)
        store.add_turn('unrelated', 'user', 'private other context')
        assert library.sessions(project['id']) == []
        library.associate(project['id'], 'known')
        assert [s['id'] for s in library.sessions(project['id'])] == ['known']
        detail = library.transcript(project['id'], 'known', limit=2)
        assert detail['total'] == 205 and detail['partial']
        assert detail['turns'][-1]['content'].startswith('204')
        assert len(detail['turns'][-1]['content']) <= 8000
        with pytest.raises(ProjectError):
            library.transcript(project['id'], 'unrelated')
        context = library.restored_context(project['id'], 'known')
        assert len(context) <= 14000 and '204' in context
        assert 'private other context' not in context
    finally:
        store.close()
