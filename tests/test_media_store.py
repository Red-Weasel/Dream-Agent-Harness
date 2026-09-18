import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from dream.media.store import MediaError, MediaStore


def test_persistence_and_immutable_revisions(tmp_path):
    s = MediaStore(tmp_path)
    p = s.create_project('Demo', {'scenes': []})
    assert p['revision'] == 1
    updated = s.update_project(p['id'], {'scenes': [1]}, 1)
    assert updated['revision'] == 2
    with pytest.raises(MediaError, match='revision'):
        s.update_project(p['id'], {}, 1)
    reopened = MediaStore(tmp_path)
    assert reopened.get_project(p['id']) == updated
    assert reopened.list_projects() == [updated]
    with sqlite3.connect(s.root / 'media.sqlite3') as db:
        assert json.loads(db.execute('SELECT composition FROM revisions WHERE revision=1').fetchone()[0]) == {'scenes': []}


def test_streamed_import_and_project_identity(tmp_path, monkeypatch):
    s = MediaStore(tmp_path)
    p, q = s.create_project('P'), s.create_project('Q')
    source = tmp_path / 'input.png'
    source.write_bytes(b'abc' * 900000)
    monkeypatch.setattr(Path, 'read_bytes', lambda _: pytest.fail('whole-file buffering'))
    a = s.import_asset(p['id'], source, provenance={'provider': 'manual'})
    b = s.import_asset(q['id'], source)
    assert a['id'] != b['id']
    assert a['sha256'] == hashlib.sha256(b'abc' * 900000).hexdigest()
    assert a['size'] == 2700000 and a['mime'] == 'image/png'
    assert a['provenance']['provider'] == 'manual'
    assert s.asset_path(a['id']).is_file()
    assert s.list_assets(p['id']) == [a]
    assert MediaStore(tmp_path).get_asset(a['id']) == a
    assert s.root.stat().st_mode & 0o077 == 0


def test_rejects_paths_and_symlinks(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    s = MediaStore(workspace)
    p = s.create_project('Demo')
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'outside')
    with pytest.raises(MediaError):
        s.import_asset(p['id'], outside)
    link = workspace / 'link.png'
    link.symlink_to(outside)
    with pytest.raises(MediaError):
        s.import_asset(p['id'], link)
    source = workspace / 'ok.png'
    source.write_bytes(b'ok')
    a = s.import_asset(p['id'], source)
    target = s.asset_path(a['id'])
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(MediaError):
        s.asset_path(a['id'])
    with pytest.raises(MediaError):
        s.get_project('../outside')


def test_rejects_symlink_storage(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / '.dream').symlink_to(outside, target_is_directory=True)
    with pytest.raises(MediaError):
        MediaStore(workspace)


def test_idempotency_and_transition_constraints(tmp_path):
    s = MediaStore(tmp_path)
    p = s.create_project('Demo')
    j = s.create_job(p['id'], 'render', {'x': 1}, idempotency_key='one')
    assert s.create_job(p['id'], 'render', {'x': 1}, idempotency_key='one') == j
    with pytest.raises(MediaError, match='idempotency'):
        s.create_job(p['id'], 'render', {'x': 2}, idempotency_key='one')
    with pytest.raises(MediaError):
        s.update_job(j['id'], 'succeeded')
    s.update_job(j['id'], 'running', progress=0.5)
    with pytest.raises(MediaError):
        s.update_job(j['id'], 'running', progress=float('nan'))
    s.update_job(j['id'], 'failed', error='encoder failed')
    with pytest.raises(MediaError):
        s.update_job(j['id'], 'running')
    assert MediaStore(tmp_path).get_job(j['id'])['error'] == 'encoder failed'


def test_worker_lock_and_recovery(tmp_path):
    s, other = MediaStore(tmp_path), MediaStore(tmp_path)
    p = s.create_project('Demo')
    j = s.create_job(p['id'], 'render', {})
    s.update_job(j['id'], 'running')
    unknown = s.create_job(p['id'], 'generate', {})
    s.update_job(unknown['id'], 'running')
    s.update_job(unknown['id'], 'unknown')
    with s.worker_lock():
        with pytest.raises(MediaError, match='worker'):
            with other.worker_lock():
                pass
        with pytest.raises(MediaError, match='worker'):
            other.recover_jobs()
        assert other.get_job(j['id'])['status'] == 'running'
    recovered = other.recover_jobs()
    assert [x['id'] for x in recovered] == [j['id']]
    assert s.get_job(j['id'])['status'] == 'interrupted'
    assert s.get_job(unknown['id'])['status'] == 'unknown'


def test_result_assets_must_belong_to_job_project(tmp_path):
    s = MediaStore(tmp_path)
    p, q = s.create_project('P'), s.create_project('Q')
    src = tmp_path / 'a.png'
    src.write_bytes(b'a')
    a = s.import_asset(q['id'], src)
    j = s.create_job(p['id'], 'render', {})
    s.update_job(j['id'], 'running')
    with pytest.raises(MediaError, match='project'):
        s.update_job(j['id'], 'succeeded', result={'asset_ids': [a['id']]})


def test_dedup_rejects_tampered_content(tmp_path):
    s = MediaStore(tmp_path)
    p = s.create_project('P')
    src = tmp_path / 'a.png'
    src.write_bytes(b'good')
    a = s.import_asset(p['id'], src)
    s.asset_path(a['id']).write_bytes(b'evil')
    with pytest.raises(MediaError, match='content'):
        s.import_asset(p['id'], src)


def test_import_bound_cleans_partial_file(tmp_path, monkeypatch):
    s = MediaStore(tmp_path)
    p = s.create_project('P')
    src = tmp_path / 'big.png'
    src.write_bytes(b'12345')
    monkeypatch.setattr(s, 'MAX_ASSET_BYTES', 4)
    with pytest.raises(MediaError, match='size limit'):
        s.import_asset(p['id'], src)
    assert not list((s.root / 'tmp').iterdir())
    assert not s.list_assets(p['id'])


def test_concurrent_revision_and_idempotency(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    s = MediaStore(tmp_path)
    p = s.create_project('P')

    def save(index):
        try:
            return MediaStore(tmp_path).update_project(p['id'], {'index': index}, 1)
        except MediaError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(save, range(4)))
        jobs = list(pool.map(lambda _: MediaStore(tmp_path).create_job(p['id'], 'render', {}, idempotency_key='same'), range(4)))
    assert sum(x is not None for x in results) == 1
    assert len({j['id'] for j in jobs}) == 1


def test_remote_recovery_without_backend_id_is_unknown(tmp_path):
    s = MediaStore(tmp_path)
    p = s.create_project('P')
    j = s.create_job(p['id'], 'generate', {'submission_intent': True})
    s.update_job(j['id'], 'running')
    assert s.recover_jobs()[0]['status'] == 'unknown'


def test_cross_project_composition_and_success(tmp_path):
    s = MediaStore(tmp_path)
    p, q = s.create_project('P'), s.create_project('Q')
    src = tmp_path / 'a.png'
    src.write_bytes(b'a')
    a = s.import_asset(p['id'], src)
    with pytest.raises(MediaError, match='project'):
        s.update_project(q['id'], {'scenes': [{'asset_id': a['id']}]}, 1)
    j = s.create_job(p['id'], 'render', {})
    s.update_job(j['id'], 'running')
    done = s.update_job(j['id'], 'succeeded', result={'asset_ids': [a['id']]}, progress=1)
    assert done['status'] == 'succeeded'
    assert s.list_jobs(p['id']) == [done]


def test_success_rejects_corrupted_asset(tmp_path):
    s = MediaStore(tmp_path)
    p = s.create_project('P')
    src = tmp_path / 'a.png'
    src.write_bytes(b'good')
    a = s.import_asset(p['id'], src)
    j = s.create_job(p['id'], 'render', {})
    s.update_job(j['id'], 'running')
    s.asset_path(a['id']).write_bytes(b'evil')
    with pytest.raises(MediaError, match='invalid'):
        s.update_job(j['id'], 'succeeded', result={'asset_ids': [a['id']]})


def test_audio_scope_and_revision_retrieval(tmp_path):
    s = MediaStore(tmp_path)
    p, q = s.create_project('P', {'scenes': []}), s.create_project('Q')
    src = tmp_path / 'a.wav'
    src.write_bytes(b'audio')
    a = s.import_asset(q['id'], src)
    with pytest.raises(MediaError, match='project'):
        s.update_project(p['id'], {'audio_asset_id': a['id']}, 1)
    s.update_project(p['id'], {'scenes': [1]}, 1)
    old = s.get_revision(p['id'], 1)
    assert old['project_id'] == p['id']
    assert old['revision'] == 1 and old['composition'] == {'scenes': []}
    with pytest.raises(MediaError):
        s.get_revision(p['id'], 99)


def test_import_provenance_preserves_actual_source(tmp_path):
    s = MediaStore(tmp_path)
    p = s.create_project('P')
    source = tmp_path / 'actual.png'
    source.write_bytes(b'image')
    a = s.import_asset(p['id'], source, provenance={'source': 'different.png', 'provider': 'manual'})
    assert a['provenance'] == {'source': 'actual.png', 'provider': 'manual'}
    assert MediaStore(tmp_path).get_asset(a['id'])['provenance'] == a['provenance']


def test_terminal_noop_preserves_entire_record(tmp_path):
    s = MediaStore(tmp_path)
    p = s.create_project('P')
    j = s.create_job(p['id'], 'render', {})
    terminal = s.update_job(j['id'], 'cancelled')
    assert s.update_job(j['id'], 'cancelled') == terminal
    assert MediaStore(tmp_path).get_job(j['id']) == terminal


@pytest.mark.parametrize('kind', ['render', 'generate'])
def test_explicit_recovery_interrupts_unclaimed_queued_jobs(tmp_path, kind):
    store = MediaStore(tmp_path)
    project = store.create_project('Unclaimed')
    job = store.create_job(project['id'], kind, {})
    with store.worker_lock():
        with pytest.raises(MediaError, match='worker'):
            MediaStore(tmp_path).recover_jobs()
        assert store.get_job(job['id'])['status'] == 'queued'
    recovered = MediaStore(tmp_path).recover_jobs()
    assert recovered[0]['id'] == job['id']
    assert recovered[0]['status'] == 'interrupted'
    assert 'before worker claim' in recovered[0]['error']
    with pytest.raises(MediaError, match='transition'):
        store.update_job(job['id'], 'running')
