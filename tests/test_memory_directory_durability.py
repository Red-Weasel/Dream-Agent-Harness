"""Directory durability boundaries use disposable files and injected I/O faults."""
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from dream import config
from dream.memory import longterm
from dream.memory.store import MemoryIndexError, MemoryStore


@pytest.fixture
def store(tmp_path):
    value = MemoryStore(tmp_path / 'fixture.db')
    # The Engine scopes its store to the workspace's project (DREAM-108); so does this one.
    from dream.memory.project import project_key
    value.project = project_key(tmp_path)
    yield value
    value.close()


def _fault_after_replace(monkeypatch):
    original_replace, original_fsync = os.replace, os.fsync
    replaced = False

    def replace(*args):
        nonlocal replaced
        original_replace(*args)
        replaced = True

    def fsync(fd):
        if replaced and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError('injected directory sync failure')
        return original_fsync(fd)

    monkeypatch.setattr(os, 'replace', replace)
    monkeypatch.setattr(os, 'fsync', fsync)


@pytest.mark.parametrize('index', [False, True])
def test_write_syncs_file_then_replacement_then_directory(monkeypatch, index):
    events = []
    original_fsync, original_replace = os.fsync, os.replace

    def fsync(fd):
        events.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
        return original_fsync(fd)

    def replace(*args):
        events.append('replace')
        return original_replace(*args)

    monkeypatch.setattr(os, 'fsync', fsync)
    monkeypatch.setattr(os, 'replace', replace)
    if index:
        longterm.write_index()
    else:
        longterm._atomic_write(config.MEMORY_DIR / 'fixture.md', 'fixture')
    assert events[-3:] == ['file', 'replace', 'directory']


@pytest.mark.parametrize('existing', [False, True])
def test_directory_failure_commits_current_search_state_then_reports_partial(store, monkeypatch, existing):
    if existing:
        row = store.upsert_memory('semantic', 'Fixture', 'oldkeyword', persist_markdown=True, embed=False)
        store._conn.execute('UPDATE memories SET embedding=? WHERE id=?', (b'old-vector', row['id']))
        store._conn.execute('INSERT INTO memory_chunks(memory_id,chunk_idx,embedding) VALUES (?,0,?)', (row['id'], b'old-chunk'))
        store._conn.commit()
    _fault_after_replace(monkeypatch)
    with pytest.raises(OSError, match='replaced.*durability.*unconfirmed'):
        store.upsert_memory('semantic', 'Fixture', 'newkeyword [[manual]]', persist_markdown=True, embed=False)
    assert longterm.read_file(config.MEMORY_DIR / 'fixture.md')['body'] == 'newkeyword [[manual]]'
    row = store.get_memory('fixture')
    assert row['body'] == 'newkeyword [[manual]]'
    assert store._conn.execute('SELECT embedding FROM memories WHERE id=?', (row['id'],)).fetchone()[0] is None
    assert store._conn.execute('SELECT count(*) FROM memory_chunks WHERE memory_id=?', (row['id'],)).fetchone()[0] == 0
    assert store._conn.execute("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'newkeyword'").fetchone()[0] == 1
    assert store._conn.execute("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'oldkeyword'").fetchone()[0] == 0
    assert store.linked_slugs('fixture') == ['manual']
    assert not list(config.MEMORY_DIR.glob('.*.tmp'))


def test_directory_and_commit_failure_reports_both_states(store, monkeypatch):
    store.upsert_memory('semantic', 'Fixture', 'original', persist_markdown=True, embed=False)
    connection = store._conn

    class FailedCommit:
        def __getattr__(self, name):
            return getattr(connection, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return connection.__exit__(*args)

        def commit(self):
            raise sqlite3.OperationalError('injected index commit failure')

    monkeypatch.setattr(store, '_conn', FailedCommit())
    _fault_after_replace(monkeypatch)
    with pytest.raises(MemoryIndexError) as raised:
        store.upsert_memory('semantic', 'Fixture', 'replacement', persist_markdown=True, embed=False)
    message = str(raised.value)
    assert all(fragment in message for fragment in ['replaced', 'durability', 'unconfirmed', 'search index failed', 'Restart'])
    assert longterm.read_file(config.MEMORY_DIR / 'fixture.md')['body'] == 'replacement'
    assert store.get_memory('fixture')['body'] == 'original'


@pytest.mark.parametrize('phase', ['open', 'directory_sync', 'file_sync', 'replace'])
def test_pre_replace_failure_preserves_file_and_database(store, monkeypatch, phase):
    store.upsert_memory('semantic', 'Fixture', 'original', persist_markdown=True, embed=False)
    path = config.MEMORY_DIR / 'fixture.md'
    before = path.read_bytes()
    original_open, original_fsync = os.open, os.fsync

    def open_directory(*args, **kwargs):
        if args[1] & getattr(os, 'O_DIRECTORY', 0):
            raise OSError('injected open failure')
        return original_open(*args, **kwargs)

    def fsync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if directory == (phase == 'directory_sync'):
            raise OSError('injected sync failure')
        return original_fsync(fd)

    def replace(*args):
        raise OSError('injected replace failure')

    if phase == 'open':
        monkeypatch.setattr(os, 'open', open_directory)
    elif phase == 'replace':
        monkeypatch.setattr(os, 'replace', replace)
    else:
        monkeypatch.setattr(os, 'fsync', fsync)
    with pytest.raises(OSError, match='injected'):
        store.upsert_memory('semantic', 'Fixture', 'replacement', persist_markdown=True, embed=False)
    assert path.read_bytes() == before
    assert store.get_memory('fixture')['body'] == 'original'
    assert not list(config.MEMORY_DIR.glob('.*.tmp'))


def test_nested_creation_retries_synchronize_all_ancestors(tmp_path, monkeypatch):
    path = tmp_path / 'new' / 'nested' / 'fixture.md'
    original_fsync = os.fsync
    seen = []
    fail = True

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory = Path(os.readlink(f'/proc/self/fd/{fd}'))
            seen.append(directory)
            if fail and directory == tmp_path:
                raise OSError('injected ancestor sync failure')
        return original_fsync(fd)

    monkeypatch.setattr(os, 'fsync', fsync)
    with pytest.raises(OSError, match='ancestor'):
        longterm._atomic_write(path, 'first')
    assert not path.exists()
    fail = False
    seen.clear()
    longterm._atomic_write(path, 'second')
    assert path.read_text() == 'second'
    assert set(path.parent.resolve().parents) <= set(seen)
    assert seen[-1] == path.parent


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['remember', 'memory_write', 'memory_append', 'memory_str_replace'])
async def test_tool_reports_replacement_without_claiming_durable_success(store, tmp_path, monkeypatch, operation):
    from dream.tools import memory_file_tools, memory_tools
    from dream.tools.context import ToolContext, bind_context

    store.upsert_memory('semantic', 'Fixture', '[stated] original', persist_markdown=True, embed=False)
    path = config.MEMORY_DIR / 'fixture.md'
    version = memory_file_tools.version_of(path.read_text())

    class ForbiddenEmbedder:
        def available(self):
            raise AssertionError('optional embedder must not run')

    store.embedder = ForbiddenEmbedder()
    args = {
        'remember': {'title': 'Fixture', 'slug': 'fixture', 'body': 'replacement'},
        'memory_write': {'name': 'fixture', 'content': 'replacement', 'if_version': version},
        'memory_append': {'name': 'fixture', 'text': 'replacement', 'if_version': version},
        'memory_str_replace': {'name': 'fixture', 'old_string': 'original', 'new_string': 'replacement', 'if_version': version},
    }[operation]
    tool = getattr(memory_tools if operation == 'remember' else memory_file_tools, operation)
    _fault_after_replace(monkeypatch)
    with bind_context(ToolContext(store, None, None, 'fixture', workspace=tmp_path)):
        result = await tool.handler(args)
    assert result.get('is_error')
    text = result['content'][0]['text']
    assert all(fragment in text for fragment in ['replaced', 'durability', 'unconfirmed', 'Read the current file'])
    assert 'replacement' in longterm.read_file(path)['body']
    assert store.get_memory('fixture')['body'] == longterm.read_file(path)['body']
    assert memory_file_tools.version_of(path.read_text()) != version
