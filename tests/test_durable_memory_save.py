"""Authoritative saves must not wait for optional semantic indexing."""

import asyncio
import threading

import pytest

from dream import config
from dream.memory import longterm
from dream.memory.store import MemoryStore
from dream.tools.context import ToolContext, bind_context
from dream.tools.memory_tools import remember


@pytest.fixture
def store(tmp_path):
    value = MemoryStore(tmp_path / 'fixture.db')
    # The Engine scopes its store to the workspace's project (DREAM-108); so does this one.
    from dream.memory.project import project_key
    value.project = project_key(tmp_path)
    yield value
    value.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_remember_does_not_enter_slow_or_failing_embedder(store, tmp_path, failure):
    release = threading.Event()

    class Embedder:
        def available(self):
            if failure:
                raise RuntimeError('embedding unavailable')
            release.wait(2)
            return False

    store.embedder = Embedder()
    try:
        with bind_context(ToolContext(store, None, None, 'fixture', workspace=tmp_path)):
            result = await asyncio.wait_for(remember.handler({
                'title': 'Durable fixture', 'body': 'violet spacecraft',
            }), timeout=0.5)
        assert not result.get('is_error')
        assert 'deferred' in result['content'][0]['text'].lower()
        assert longterm.read_file(config.MEMORY_DIR / 'durable-fixture.md')['body'] == '[stated] violet spacecraft'
        store.embedder = None
        assert store.search_memories('violet')[0]['slug'] == 'durable-fixture'
    finally:
        release.set()


@pytest.mark.parametrize('existing', [False, True])
def test_failed_atomic_replace_preserves_previous_file_and_database(store, monkeypatch, existing):
    if existing:
        store.upsert_memory('semantic', 'Fixture', 'original', persist_markdown=True, embed=False)
    original = store.get_memory('fixture')
    path = config.MEMORY_DIR / 'fixture.md'
    before = path.read_bytes() if existing else None

    def fail(*args):
        raise OSError('disk write failed')

    monkeypatch.setattr('os.replace', fail)
    with pytest.raises(OSError, match='disk write failed'):
        store.upsert_memory('semantic', 'Fixture', 'replacement', persist_markdown=True, embed=False)
    assert store.get_memory('fixture') == original
    assert (path.read_bytes() if path.exists() else None) == before
    assert not list(config.MEMORY_DIR.glob('*.tmp'))


def test_deferred_edit_invalidates_vectors_chunks_and_auto_links(store):
    import numpy as np

    class Embedder:
        def available(self):
            return True

        def embed(self, text):
            return np.array([1, 0], dtype='float32')

    store.embedder = Embedder()
    store.upsert_memory('semantic', 'Neighbor', 'neighbor')
    mem = store.upsert_memory('semantic', 'Fixture', 'original')
    store._conn.execute('INSERT INTO memory_chunks(memory_id, chunk_idx, embedding) VALUES (?,0,?)',
                        (mem['id'], b'old'))
    store._conn.commit()
    for _ in range(2):
        store.upsert_memory('semantic', 'Changed title', 'replacement [[manual]]', slug='fixture',
                            persist_markdown=True, embed=False)
        assert store._conn.execute('SELECT embedding FROM memories WHERE slug="fixture"').fetchone()[0] is None
        assert store._conn.execute('SELECT COUNT(*) FROM memory_chunks WHERE memory_id=?', (mem['id'],)).fetchone()[0] == 0
        assert store.linked_slugs('fixture') == ['manual']


@pytest.mark.asyncio
async def test_index_file_failure_reports_saved_memory(store, tmp_path, monkeypatch):
    def fail():
        raise OSError('index unavailable')

    monkeypatch.setattr(longterm, 'write_index', fail)
    with bind_context(ToolContext(store, None, None, 'fixture', workspace=tmp_path)):
        result = await remember.handler({'title': 'Fixture', 'body': 'violet'})
    message = result['content'][0]['text']
    assert 'Remembered' in message and 'index unavailable' in message
    assert longterm.read_file(config.MEMORY_DIR / 'fixture.md')['body'] == '[stated] violet'
    assert store.get_memory('fixture')['body'] == '[stated] violet'


@pytest.mark.parametrize('existing', [False, True])
def test_commit_failure_keeps_recoverable_file_and_reports_partial_save(store, monkeypatch, existing):
    import sqlite3
    from dream.memory.store import MemoryIndexError

    if existing:
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
            raise sqlite3.OperationalError('commit unavailable')

    monkeypatch.setattr(store, '_conn', FailedCommit())
    with pytest.raises(MemoryIndexError, match='Memory saved.*search index failed'):
        store.upsert_memory('semantic', 'Fixture', 'replacement', persist_markdown=True, embed=False)
    monkeypatch.setattr(store, '_conn', connection)
    assert longterm.read_file(config.MEMORY_DIR / 'fixture.md')['body'] == 'replacement'
    row = store.get_memory('fixture')
    assert (row['body'] if row else None) == ('original' if existing else None)
    longterm.sync(store)
    assert store.get_memory('fixture')['body'] == 'replacement'


@pytest.mark.parametrize('change', ['edit', 'delete', 'recreate'])
def test_inflight_backfill_cannot_restore_stale_embeddings_or_links(store, change):
    import numpy as np

    store.upsert_memory('semantic', 'Fixture', 'original', embed=False)

    class RacingEmbedder:
        def available(self):
            return True

        def embed(self, text):
            if change != 'edit':
                store.delete_memory('fixture')
            if change != 'delete':
                store.upsert_memory('semantic', 'Fixture', 'replacement [[new]]',
                                    slug='fixture', embed=False)
            return np.array([1, 0], dtype='float32')

    store.embedder = RacingEmbedder()
    assert store.backfill_embeddings() == 0
    row = store._conn.execute('SELECT embedding FROM memories WHERE slug="fixture"').fetchone()
    assert row is None if change == 'delete' else row[0] is None
    assert store.linked_slugs('fixture') == ([] if change == 'delete' else ['new'])


@pytest.mark.parametrize('has_vector', [False, True])
def test_backfill_count_reports_installed_vectors(store, has_vector):
    import numpy as np

    store.upsert_memory('semantic', 'Fixture', 'original', embed=False)

    class Embedder:
        def available(self):
            return True

        def embed(self, text):
            return np.array([1, 0], dtype='float32') if has_vector else None

    store.embedder = Embedder()
    assert store.backfill_embeddings() == int(has_vector)
    vector = store._conn.execute(
        'SELECT embedding FROM memories WHERE slug="fixture"'
    ).fetchone()[0]
    assert (vector is not None) == has_vector
    assert store.backfill_embeddings() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['memory_write', 'memory_append', 'memory_str_replace'])
@pytest.mark.parametrize('disk_failure', [False, True])
async def test_file_tools_defer_embeddings_and_preserve_failed_version(
    store, tmp_path, monkeypatch, operation, disk_failure,
):
    from dream.tools import memory_file_tools

    original = store.upsert_memory('semantic', 'Fixture', '[stated] original', embed=False)
    original['created_at'] = '2020-01-02T03:04:05+00:00'
    path = longterm.write_markdown(original)
    before = path.read_text()
    version = memory_file_tools.version_of(before)
    original_row = store.get_memory('fixture')
    release = threading.Event()

    class SlowEmbedder:
        def available(self):
            release.wait(2)
            raise RuntimeError('optional embedder must not block file tools')

    store.embedder = SlowEmbedder()
    args = {'name': 'fixture', 'if_version': version}
    args.update({
        'memory_write': {'content': 'replacement'},
        'memory_append': {'text': 'replacement'},
        'memory_str_replace': {'old_string': 'original', 'new_string': 'replacement'},
    }[operation])
    if disk_failure:
        def fail(*args):
            raise OSError('disk write failed')
        monkeypatch.setattr('os.replace', fail)
    try:
        with bind_context(ToolContext(store, None, None, 'fixture', workspace=tmp_path)):
            result = await asyncio.wait_for(getattr(memory_file_tools, operation).handler(args), 0.5)
        if disk_failure:
            assert result.get('is_error')
            assert 'disk write failed' in result['content'][0]['text']
            assert path.read_text() == before
            assert store.get_memory('fixture') == original_row
        else:
            assert not result.get('is_error')
            text = path.read_text()
            assert 'replacement' in text and memory_file_tools.version_of(text) != version
            assert memory_file_tools.version_of(text) in result['content'][0]['text']
            assert longterm.read_file(path)['created_at'] == original['created_at']
            assert store.get_memory('fixture')['created_at'] == original['created_at']
            assert 'deferred' in result['content'][0]['text']
    finally:
        release.set()
