"""Model-free saved-context recovery under noisy and long conversations."""
import pytest

from dream import config
from dream.memory.store import MemoryStore
from dream.projects.library import ProjectLibrary
from dream.projects.documents import ProjectDocuments, build_context
from dream.projects.workspace import ProjectError


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'DB_PATH', tmp_path / 'archive.sqlite3')
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'private')
    store = MemoryStore(config.DB_PATH)
    store.start_session('selected', 'Synthetic recovery')
    store.start_session('elsewhere', 'Unrelated')
    library = ProjectLibrary()
    project = library.create('Selected', str(tmp_path), session_id='selected')
    yield library, project['id'], store
    store.close()


def test_tool_flood_does_not_erase_latest_correction_or_saved_report(archive):
    library, project_id, store = archive
    store.add_turn('selected', 'user', 'Correction: preserve the saved model.')
    store.add_turn('selected', 'assistant', 'Saved draft.blend. Checks remain unrun.')
    for _ in range(35):
        store.add_turn('selected', 'tool', 'tool flood must not become recovered text')
    context = library.restored_context(project_id, 'selected')
    assert 'Correction: preserve the saved model.' in context
    assert 'Saved draft.blend. Checks remain unrun.' in context
    assert 'tool flood' not in context
    assert 'selected' in context and project_id in context
    assert 'turn ' in context and 'do not replay' in context


def test_assistant_flood_cannot_consume_user_correction_budget(archive):
    library, project_id, store = archive
    store.add_turn('selected', 'user', 'Latest direction: inspect before editing.')
    for index in range(25):
        store.add_turn('selected', 'assistant', f'Report {index}: ' + 'x' * 4000)
    context = library.restored_context(project_id, 'selected')
    assert 'Latest direction: inspect before editing.' in context
    assert 'Report 24:' in context
    assert context.index('Latest direction') < context.index('Report 24:')
    assert len(context) <= 14000


def test_long_latest_message_keeps_ending_correction_and_labels_excerpt(archive):
    library, project_id, store = archive
    store.add_turn('selected', 'user', 'Opening intent. ' + 'x' * 12000 + ' Final correction: no publish.')
    store.end_session('selected', 'Saved old summary. ' + 'y' * 16000)
    context = library.restored_context(project_id, 'selected')
    assert 'Opening intent.' in context and 'Final correction: no publish.' in context
    assert 'truncated' in context.lower()
    assert 'may predate' in context.lower()
    assert len(context) <= 14000


def test_context_is_scoped_and_missing_archive_fails(archive):
    library, project_id, store = archive
    store.add_turn('elsewhere', 'user', 'Private unrelated marker')
    context = library.restored_context(project_id, 'selected')
    assert 'Private unrelated marker' not in context
    with pytest.raises(ProjectError):
        library.restored_context(project_id, 'elsewhere')
    library.associate(project_id, 'missing')
    with pytest.raises(ProjectError):
        library.restored_context(project_id, 'missing')


def test_selected_handoff_remains_complete_and_unselected_notes_stay_out(archive):
    _, project_id, _ = archive
    documents = ProjectDocuments(project_id)
    content = '## Source\nselected\n## Next action\nInspect saved output.\n' + 'a' * 4000
    documents.save({'title': 'Handoff', 'content': content, 'kind': 'memory', 'include': True})
    documents.save({'title': 'Unselected', 'content': 'Excluded marker', 'include': False})
    context = build_context(project_id)
    assert content in context and 'Excluded marker' not in context


def test_full_role_budgets_and_maximum_session_id_stay_bounded(archive):
    library, project_id, store = archive
    session_id = 's' * 200
    store.start_session(session_id, 'Long context')
    library.associate(project_id, session_id)
    for index in range(30):
        store.add_turn(session_id, 'user', f'User {index}: ' + 'u' * 20000 + f' user-end-{index}')
        store.add_turn(session_id, 'assistant', f'Assistant {index}: ' + 'a' * 20000 + f' report-end-{index}')
    store.end_session(session_id, 'Summary ' * 3000)
    context = library.restored_context(project_id, session_id)
    assert len(context) <= 14000
    assert 'User 29:' in context and 'user-end-29' in context
    assert 'Assistant 29:' in context and 'report-end-29' in context
    assert context.index('User 29:') < context.index('Assistant 29:')
    assert 'User 0:' not in context


def test_small_interleaved_turns_restore_in_original_chronology(archive):
    library, project_id, store = archive
    for role, content in [('user', 'Initial request'), ('assistant', 'First saved report'),
                          ('user', 'Later correction'), ('assistant', 'Latest saved report')]:
        store.add_turn('selected', role, content)
    context = library.restored_context(project_id, 'selected')
    positions = [context.index(text) for text in
                 ['Initial request', 'First saved report', 'Later correction', 'Latest saved report']]
    assert positions == sorted(positions)
    assert 'excerpt truncated' not in context
