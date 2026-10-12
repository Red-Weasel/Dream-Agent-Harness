"""DREAM-263: generic synthetic facts only; no user corpus or models."""
import hashlib
import json

import pytest

from dream import config
from dream.memory import curation, longterm
from dream.memory.store import MemoryStore


def put(store, slug='cedar', **kwargs):
    return store.upsert_memory('semantic', 'Cedar endpoint', '[stated] Cedar endpoint is port 8080.',
                               slug=slug, project='alpha', embed=False, **kwargs)


def test_explicit_lifecycle_roundtrip_and_abstention(tmp_path):
    s = MemoryStore(tmp_path / 'a.db')
    lifecycle = {'version': 1, 'expires_at': '2020-01-01T00:00:00Z'}
    m = put(s, lifecycle=lifecycle, source_session='synthetic-session',
            created_at='2019-01-01T00:00:00+00:00', persist_markdown=True)
    f = longterm.path_for(m)
    parsed = longterm.read_file(f)
    assert json.loads(parsed['lifecycle']) == lifecycle
    assert parsed['source_session'] == 'synthetic-session'
    fresh = MemoryStore(tmp_path / 'b.db')
    assert longterm.sync(fresh)['imported'] == 1
    row = fresh.get_memory('cedar')
    for k in ['lifecycle', 'created_at', 'updated_at', 'source_session', 'project']:
        assert row[k] == m[k]
    assert fresh.search_active_memories('cedar', scope=('alpha',)) == []
    assert fresh.get_memory('cedar')  # visible, retained original
    assert longterm.sync(fresh)['updated'] == 0


def test_cas_preserves_custom_file_and_rejects_stale_version(tmp_path):
    s = MemoryStore(tmp_path / 'a.db')
    m = put(s, persist_markdown=True)
    p = longterm.path_for(m)
    p.write_text(p.read_text().replace('title:', 'custom: keep exactly\ntitle:'))
    original = p.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    changed = longterm.update_lifecycle(s, 'cedar', {'version': 1, 'invalidated_at': '2026-01-01T00:00:00Z'},
                                       expected_version=digest, scope=('alpha',))
    assert 'custom: keep exactly\n' in p.read_text()
    assert changed['project'] == 'alpha'
    with pytest.raises(longterm.MemoryVersionError):
        longterm.update_lifecycle(s, 'cedar', {}, expected_version=digest, scope=('alpha',))
    assert s.search_active_memories('cedar', scope=('alpha',)) == []


def test_supersession_same_scope_only(tmp_path):
    s = MemoryStore(tmp_path / 'a.db')
    m = put(s, persist_markdown=True)
    s.upsert_memory('semantic','Cedar','other',slug='other',project='beta',embed=False,persist_markdown=True)
    p = longterm.path_for(m)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='scope'):
        longterm.update_lifecycle(s,'cedar',{'version':1,'superseded_by':'other'},expected_version=digest,scope=('alpha','beta'))
    put(s, 'replacement', persist_markdown=True)
    longterm.update_lifecycle(s,'cedar',{'version':1,'superseded_by':'replacement'},expected_version=digest,scope=('alpha',))
    assert [r['slug'] for r in s.search_active_memories('cedar',scope=('alpha',))] == ['replacement']


def test_malformed_metadata_quarantined_visible_and_recoverable(tmp_path):
    s = MemoryStore(tmp_path/'a.db')
    m = put(s,persist_markdown=True)
    p = longterm.path_for(m)
    p.write_text(p.read_text().replace('---\n','---\nlifecycle: {bad}\n',1))
    counts = longterm.sync(s)
    assert counts['quarantined'] == 1
    assert s.get_memory('cedar')['lifecycle_error']
    assert s.search_active_memories('cedar',scope=('alpha',)) == []
    assert '{bad}' in p.read_text()
    p.write_text(p.read_text().replace('lifecycle: {bad}\n',''))
    longterm.sync(s)
    assert s.search_active_memories('cedar',scope=('alpha',))


def test_bounded_curation_duplicates_advisory_no_models(tmp_path):
    s = MemoryStore(tmp_path/'a.db')
    put(s,'a',persist_markdown=True)
    put(s,'b',persist_markdown=True)
    report = curation.curate_active(s,scope=('alpha',),limit=2)
    assert report['duplicates'] == [['a','b']]
    assert report['examined'] == 2
    assert len(s.all_memories(scope=None)) == 2
    assert len(list(config.MEMORY_DIR.glob('*.md'))) == 2
    assert s.search_active_memories('unknownword',scope=('alpha',)) == []
    assert s.search_active_memories('cedar',scope=('beta',)) == []


@pytest.mark.parametrize('value', [[], {'version':2}, {'version':True}, {'version':1,'expires_at':'2026-01-01'},
                                   {'version':1,'surprise':1}, {'version':1,'superseded_by':'../private'}])
def test_invalid_metadata_rejected_before_any_write(tmp_path,value):
    s=MemoryStore(tmp_path/'a.db')
    with pytest.raises(ValueError):
        put(s,lifecycle=value,persist_markdown=True)
    assert s.all_memories(scope=None) == []
    assert list(config.MEMORY_DIR.glob('*.md')) == []


def test_ttl_inclusive_and_timezone_aware():
    from datetime import datetime, timezone
    m={'lifecycle':{'version':1,'expires_at':'2026-01-01T01:00:00+01:00'}}
    assert curation.active_status(m,now=datetime(2025,12,31,23,59,59,tzinfo=timezone.utc)) == 'eligible'
    assert curation.active_status(m,now=datetime(2026,1,1,tzinfo=timezone.utc)) == 'expired'


def test_ambiguous_dangling_cross_scope_and_cycle_visible(tmp_path):
    s=MemoryStore(tmp_path/'a.db')
    put(s,'a',lifecycle={'version':1,'superseded_by':'b'})
    assert s.search_active_memories('cedar',scope=('alpha',))[0]['lifecycle_conflict']
    s.upsert_memory('semantic','Cedar','cedar',slug='b',project='beta',embed=False)
    assert s.search_active_memories('cedar',scope=('alpha',))[0]['lifecycle_conflict']
    put(s,'c',lifecycle={'version':1,'superseded_by':'a'})
    put(s,'a',lifecycle={'version':1,'superseded_by':'c'})
    assert {r['slug'] for r in s.search_active_memories('cedar',scope=('alpha',))} == {'a','c'}


def test_bounded_page_and_no_optional_models(tmp_path):
    class Forbidden:
        def available(self):
            raise AssertionError('no models allowed')
    s=MemoryStore(tmp_path/'a.db',embedder=Forbidden(),reranker=Forbidden())
    for i in range(5):
        put(s,f'cedar-{i}')
    page=curation.curate_active(s,scope=('alpha',),limit=2)
    next_page=curation.curate_active(s,scope=('alpha',),limit=2,after_id=page['next_after_id'])
    assert page['examined'] == next_page['examined'] == 2
    assert page['next_after_id'] < next_page['next_after_id']
    assert len(s.search_active_memories('cedar',scope=('alpha',),limit=2)) == 2
    assert s.search_active_memories('cedar',scope=('alpha',),max_bytes=1) == []
    with pytest.raises(ValueError):
        curation.curate_active(s,limit=1000)
    with pytest.raises(ValueError):
        s.search_active_memories('x'*257)


def test_partial_file_and_duplicate_frontmatter_quarantine_recover(tmp_path):
    s=MemoryStore(tmp_path/'a.db')
    m=put(s,persist_markdown=True)
    p=longterm.path_for(m)
    original=p.read_text()
    p.write_text('---\ntitle: partial\n---\n')
    assert longterm.sync(s)['unreadable'] == 1
    assert s.get_memory('cedar')['body'] == m['body']
    assert s.search_active_memories('cedar',scope=('alpha',)) == []
    p.write_text(original.replace('---\n','---\nlifecycle: {}\nlifecycle: {}\n',1))
    assert longterm.sync(s)['quarantined'] == 1
    p.write_text(original)
    longterm.sync(s)
    assert len(s.search_active_memories('cedar',scope=('alpha',))) == 1


def test_cas_failure_before_replace_preserves_and_post_replace_recovers(tmp_path,monkeypatch):
    from dream.memory.store import MemoryIndexError
    s=MemoryStore(tmp_path/'a.db')
    m=put(s,persist_markdown=True)
    p=longterm.path_for(m)
    original=p.read_bytes()
    digest=hashlib.sha256(original).hexdigest()
    lifecycle={'version':1,'invalidated_at':'2026-01-01T00:00:00Z'}
    with monkeypatch.context() as patch:
        def fail(*a,**kw):
            raise OSError('injected replace fault')
        patch.setattr(longterm.os,'replace',fail)
        with pytest.raises(OSError):
            longterm.update_lifecycle(s,'cedar',lifecycle,expected_version=digest,scope=('alpha',))
    assert p.read_bytes() == original
    assert s.get_memory('cedar')['lifecycle'] == '{}'
    with monkeypatch.context() as patch:
        def fail_index(*a,**kw):
            raise RuntimeError('injected index fault')
        patch.setattr(s,'upsert_memory',fail_index)
        with pytest.raises(MemoryIndexError):
            longterm.update_lifecycle(s,'cedar',lifecycle,expected_version=digest,scope=('alpha',))
    assert json.loads(longterm.read_file(p)['lifecycle']) == lifecycle
    assert longterm.sync(s)['updated'] == 1
    assert s.search_active_memories('cedar',scope=('alpha',)) == []


def test_two_stores_cas_only_one_writer_wins(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    s=MemoryStore(tmp_path/'a.db'); other=MemoryStore(tmp_path/'a.db')
    m=put(s,persist_markdown=True)
    digest=hashlib.sha256(longterm.path_for(m).read_bytes()).hexdigest()
    def update(store):
        try:
            longterm.update_lifecycle(store,'cedar',{'version':1,'reason':'explicit update'},
                                      expected_version=digest,scope=('alpha',))
            return 'saved'
        except longterm.MemoryVersionError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(update,[s,other]))
    assert sorted(results) == ['conflict','saved']


def test_additive_migration_preserves_existing_rows_and_sessions(tmp_path):
    s=MemoryStore(tmp_path/'a.db')
    put(s)
    session='synthetic'
    s.start_session(session, 'synthetic fixture')
    s._conn.execute('ALTER TABLE memories DROP COLUMN lifecycle')
    s._conn.execute('ALTER TABLE memories DROP COLUMN lifecycle_error')
    s._conn.commit(); s.close()
    reopened=MemoryStore(tmp_path/'a.db')
    assert reopened.get_memory('cedar')['lifecycle'] == '{}'
    assert reopened.get_session(session)
    assert len(reopened.search_active_memories('cedar',scope=('alpha',))) == 1


def test_shadowed_file_identity_count_and_file_preservation(tmp_path):
    s=MemoryStore(tmp_path/'a.db')
    m=put(s,persist_markdown=True)
    canonical=longterm.path_for(m)
    alias=config.MEMORY_DIR/'Cedar.md'
    alias.write_text(canonical.read_text().replace('8080','9090'))
    before=alias.read_bytes()
    assert longterm.sync(s)['shadowed'] == 1
    assert alias.read_bytes() == before
    assert '8080' in s.get_memory('cedar')['body']


def test_normal_persisted_write_retains_lifecycle(tmp_path):
    s=MemoryStore(tmp_path/'a.db')
    m=put(s,lifecycle={'version':1,'expires_at':'2020-01-01T00:00:00Z'},persist_markdown=True)
    put(s,persist_markdown=True)
    assert longterm.read_file(longterm.path_for(m))['lifecycle'] == m['lifecycle']
    assert s.search_active_memories('cedar',scope=('alpha',)) == []


def test_fts_rebuild_keeps_lifecycle_exclusions_and_original_count(tmp_path):
    s=MemoryStore(tmp_path/'a.db')
    put(s,'fresh')
    put(s,'old',lifecycle={'version':1,'expires_at':'2020-01-01T00:00:00Z'})
    s._conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
    s._conn.commit()
    assert [r['slug'] for r in s.search_active_memories('cedar',scope=('alpha',))] == ['fresh']
    assert len(s.all_memories(scope=None)) == 2


def test_unterminated_frontmatter_never_revives_excluded_memory(tmp_path):
    store = MemoryStore(tmp_path / 'unterminated.db')
    row = put(store, persist_markdown=True,
              lifecycle={'version': 1, 'invalidated_at': '2026-01-01T00:00:00Z'})
    path = longterm.path_for(row)
    original = path.read_text()
    path.write_text('---\nlifecycle: {}\ncedar endpoint partially saved\n')
    counts = longterm.sync(store)
    assert counts.get('quarantined', 0) + counts.get('unreadable', 0) == 1
    assert store.search_active_memories('cedar', scope=('alpha',)) == []
    assert store.get_memory('cedar')['lifecycle_error']
    path.write_text(original)
    longterm.sync(store)
    assert store.get_memory('cedar')['lifecycle'] == row['lifecycle']
    assert store.get_memory('cedar')['lifecycle_error'] == ''
    assert longterm.sync(store)['updated'] == 0


def test_sync_cannot_restore_snapshot_before_concurrent_invalidation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    sync_store = MemoryStore(tmp_path / 'race.db')
    edit_store = MemoryStore(tmp_path / 'race.db')
    row = put(sync_store, persist_markdown=True)
    path = longterm.path_for(row)
    path.write_text(path.read_text().replace('8080', '8081'))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    read_taken, release_read, edit_started, edit_finished = (threading.Event() for _ in range(4))
    original_read = longterm.read_file

    def pause_after_snapshot(file, **kwargs):
        result = original_read(file, **kwargs)
        if threading.current_thread().name.startswith('sync-race') and not read_taken.is_set():
            read_taken.set()
            assert release_read.wait(5)
        return result

    monkeypatch.setattr(longterm, 'read_file', pause_after_snapshot)

    def invalidate():
        edit_started.set()
        result = longterm.update_lifecycle(
            edit_store, 'cedar', {'version': 1, 'invalidated_at': '2026-01-01T00:00:00Z'},
            expected_version=digest, scope=('alpha',))
        edit_finished.set()
        return result

    with ThreadPoolExecutor(1, thread_name_prefix='sync-race') as sync_pool, \
            ThreadPoolExecutor(1, thread_name_prefix='edit-race') as edit_pool:
        sync_future = sync_pool.submit(longterm.sync, sync_store)
        assert read_taken.wait(5)
        edit_future = edit_pool.submit(invalidate)
        assert edit_started.wait(5)
        # The old unlocked sync lets invalidation complete before reindexing its
        # stale snapshot. The corrected sync holds the shared file-edit lock.
        edit_finished.wait(.3)
        release_read.set()
        sync_future.result(timeout=5)
        edit_future.result(timeout=5)
    assert sync_store.search_active_memories('cedar', scope=('alpha',)) == []
    assert sync_store.get_memory('cedar')['lifecycle'] == longterm.read_file(path)['lifecycle']


def test_active_sql_preflight_excludes_large_provenance_before_materialization(tmp_path):
    import sqlite3

    store = MemoryStore(tmp_path / 'oversized.db')
    put(store, 'small')
    put(store, 'large', source_session='s' * 300_000)
    # Guard the database/Python boundary, not merely the final returned list.
    def reject_large_materialized_row(cursor, values):
        assert all(not isinstance(v, str) or len(v.encode('utf-8')) <= 65536 for v in values)
        return sqlite3.Row(cursor, values)
    store._conn.row_factory = reject_large_materialized_row
    assert [r['slug'] for r in store.active_memory_page(scope=('alpha',))] == ['small']
    assert [r['slug'] for r in store.search_active_memories('cedar', scope=('alpha',))] == ['small']
    # Direct target metadata must have its own preflight too.
    store._conn.execute('UPDATE memories SET project=? WHERE slug=?', ('p' * 300_000, 'large'))
    store._conn.commit()
    assert store.lifecycle_target('large') is None


def test_source_session_removed_from_authoritative_file_clears_idempotently(tmp_path):
    store = MemoryStore(tmp_path / 'source.db')
    row = put(store, source_session='synthetic-source', persist_markdown=True)
    # Ordinary omission retains provenance, explicit None deliberately clears it.
    put(store)
    assert store.get_memory('cedar')['source_session'] == 'synthetic-source'
    path = longterm.path_for(row)
    path.write_text(path.read_text().replace('source_session: synthetic-source\n', ''))
    assert longterm.sync(store)['updated'] == 1
    assert store.get_memory('cedar')['source_session'] is None
    assert longterm.sync(store)['updated'] == 0
    put(store, source_session='second-source')
    put(store, source_session=None)
    assert store.get_memory('cedar')['source_session'] is None


def test_observed_handwritten_source_can_be_removed_but_legacy_source_survives(tmp_path):
    store = MemoryStore(tmp_path / 'legacy-provenance.db')
    put(store, source_session='legacy-db-only')
    path = config.MEMORY_DIR / 'cedar.md'
    path.write_text('---\nproject: alpha\ntitle: Cedar endpoint\n---\ncedar endpoint\n')
    longterm.sync(store)
    assert store.get_memory('cedar')['source_session'] == 'legacy-db-only'
    path.write_text(path.read_text().replace('project: alpha', 'project: alpha\nsource_session: file-source'))
    longterm.sync(store)
    assert store.get_memory('cedar')['source_session'] == 'file-source'
    path.write_text(path.read_text().replace('source_session: file-source\n', ''))
    longterm.sync(store)
    assert store.get_memory('cedar')['source_session'] is None
    assert longterm.sync(store)['updated'] == 0
