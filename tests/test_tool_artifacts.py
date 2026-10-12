import base64
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from dream.core.tool_artifacts import ArtifactError, ArtifactStore


def store(path, **kw):
    return ArtifactStore(path, 'session-a', 'run-a', **kw)


@pytest.mark.parametrize('original', ['<html>' + '世界\n' * 20000 + '</html>',
    'def f():\n    return "🚀"\n' * 15000, '{"value":"' + '界' * 200000 + '"}',
    'secret=sk-abcdefghijklmnop\r\n\x00🚀é\n'])
def test_lossless_restart_and_character_pages(tmp_path, original):
    receipt = store(tmp_path).save(original, 'call-1')
    assert receipt.version == 1
    assert receipt.full_bytes == len(original.encode())
    assert receipt.sha256 == hashlib.sha256(original.encode()).hexdigest()
    restarted = store(tmp_path)
    parts, offset = [], 0
    while True:
        page = restarted.read(receipt.handle, offset=offset, limit=17003)
        parts.append(page['text'])
        assert len(page['text'].encode()) <= restarted.max_read_bytes
        if page['eof']:
            break
        assert page['next_offset'] > offset
        offset = page['next_offset']
    assert ''.join(parts) == original


def test_byte_boundary_and_search_single_line(tmp_path):
    original = 'a' * 40000 + '🚀NEEDLE' + 'z' * 40000
    r = store(tmp_path).save(original, 'call')
    s = store(tmp_path)
    p = s.read(r.handle, offset=40001, limit=2, unit='bytes')
    assert base64.b64decode(p['base64']) == original.encode()[40001:40003]
    assert p['text'] is None
    p = s.read(r.handle, query='NEEDLE', limit=12)
    assert p['found'] and p['offset'] == 40001 and p['text'].startswith('NEEDLE')
    assert not s.read(r.handle, query='missing')['found']


def test_scope_and_malformed(tmp_path):
    r = store(tmp_path).save('private', 'call')
    for s in (ArtifactStore(tmp_path, 'other', 'run-a'), ArtifactStore(tmp_path, 'session-a', 'other')):
        with pytest.raises(ArtifactError, match='scope'):
            s.read(r.handle)
    other = tmp_path / 'other'; other.mkdir(mode=0o700)
    with pytest.raises(ArtifactError, match='scope'):
        store(other).read(r.handle)
    for handle in ('../secret', r.handle + '/../../secret', '', None):
        with pytest.raises(ArtifactError, match='invalid_handle'):
            store(tmp_path).read(handle)
    for kw in ({'offset': -1}, {'limit': True}, {'limit': 0}, {'unit': 'lines'}, {'query': ''}, {'offset': 10**30}):
        with pytest.raises(ArtifactError):
            store(tmp_path).read(r.handle, **kw)


def test_budget_and_no_false_receipt(tmp_path, monkeypatch):
    s = store(tmp_path, max_result_bytes=12, max_disk_bytes=15)
    s.save('a' * 10, '1')
    with pytest.raises(ArtifactError, match='result_limit'):
        s.save('a' * 13, '2')
    with pytest.raises(ArtifactError, match='disk_limit'):
        s.save('b' * 6, '2')
    def full(*args):
        raise OSError(28, 'private/path full disk')
    monkeypatch.setattr(os, 'write', full)
    with pytest.raises(ArtifactError, match='storage_io') as e:
        s.save('c', '3')
    assert 'private' not in str(e.value)
    assert not list((tmp_path / '.dream/tool-artifacts/v1').glob('*.tmp'))


@pytest.mark.parametrize('part', ['.dream', 'tool-artifacts', 'v1'])
def test_symlink_directory_refused(tmp_path, part):
    outside = tmp_path / 'outside'; outside.mkdir()
    parent = tmp_path
    for name in ('.dream', 'tool-artifacts', 'v1'):
        if name == part:
            (parent / name).symlink_to(outside, target_is_directory=True)
            break
        parent /= name; parent.mkdir(mode=0o700)
    with pytest.raises(ArtifactError):
        store(tmp_path).save('secret', 'call')
    assert not list(outside.iterdir())


def test_tamper_missing_link_and_private_permissions(tmp_path):
    s = store(tmp_path); r = s.save('original', 'call')
    p = next((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))
    assert p.stat().st_mode & 0o777 == 0o600
    assert p.parent.stat().st_mode & 0o777 == 0o700
    p.write_text('tampered')
    with pytest.raises(ArtifactError, match='integrity'):
        s.read(r.handle)
    p.unlink()
    with pytest.raises(ArtifactError, match='missing'):
        s.read(r.handle)
    outside = tmp_path / 'private'; outside.write_text('original')
    p.symlink_to(outside)
    with pytest.raises(ArtifactError):
        s.read(r.handle)
    assert outside.read_text() == 'original'


def test_concurrent_publication_is_idempotent_and_quota_serialized(tmp_path):
    def put(i):
        return store(tmp_path).save('same original', 'same call')
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(put, range(24)))
    assert len({r.handle for r in receipts}) == 1
    assert len(list((tmp_path / '.dream/tool-artifacts/v1').iterdir())) == 1
    def limited(i):
        try:
            store(tmp_path, max_disk_bytes=23).save('123456', str(i))
            return True
        except ArtifactError as e:
            assert e.code == 'disk_limit'
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(limited, range(8))) == 1


def test_fsync_and_read_failures_are_explicit(tmp_path, monkeypatch):
    s = store(tmp_path); r = s.save('original', 'call')
    with monkeypatch.context() as m:
        def failed(*args):
            raise OSError('private failure detail')
        m.setattr(os, 'read', failed)
        with pytest.raises(ArtifactError, match='storage_io'):
            s.read(r.handle)
    with monkeypatch.context() as m:
        m.setattr(os, 'fsync', lambda *_: (_ for _ in ()).throw(OSError('fsync')))
        with pytest.raises(ArtifactError, match='storage_io'):
            s.save('new', 'call2')
    assert s.read(r.handle)['text'] == 'original'


def test_unsafe_modes_and_workspace_ancestor_links(tmp_path):
    s = store(tmp_path); s.save('x', 'call')
    folder = tmp_path / '.dream/tool-artifacts'
    folder.chmod(0o755)
    with pytest.raises(ArtifactError, match='unsafe_path'):
        s.read(s.save('x', 'call').handle)
    assert folder.stat().st_mode & 0o777 == 0o755
    link = tmp_path / 'alias'; link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ArtifactError):
        store(link / 'child')


def test_real_process_restart(tmp_path):
    import json
    import subprocess
    import sys
    original = 'kept\x00\r\n界🚀'
    receipt = store(tmp_path).save(original, 'call')
    code = ('import json,sys; from dream.core.tool_artifacts import ArtifactStore; '
            'print(json.dumps(ArtifactStore(sys.argv[1], "session-a", "run-a").read(sys.argv[2])))')
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path), receipt.handle],
                            capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(result.stdout)['text'] == original


def test_concurrent_reader_never_observes_partial_publication(tmp_path, monkeypatch):
    from threading import Event
    written, release = Event(), Event()
    s = store(tmp_path)
    original_write = os.write
    def paused(fd, data):
        n = original_write(fd, data[:2])
        written.set()
        assert release.wait(5)
        return n
    monkeypatch.setattr(os, 'write', paused)
    # No final managed name is visible until all bytes have been flushed.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(s.save, 'abcdef', 'call')
        assert written.wait(5)
        assert not list((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))
        release.set()
        receipt = future.result(timeout=5)
    assert s.read(receipt.handle)['text'] == 'abcdef'


def test_hardlink_and_insecure_file_refused(tmp_path):
    s = store(tmp_path); r = s.save('original', 'call')
    p = next((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))
    alias = tmp_path / 'alias'; os.link(p, alias)
    with pytest.raises(ArtifactError, match='unsafe_path'):
        s.read(r.handle)
    alias.unlink()
    p.chmod(0o644)
    with pytest.raises(ArtifactError, match='unsafe_path'):
        s.read(r.handle)
    assert p.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize('original,code', [(b'bytes', 'invalid_text'), ('\ud800', 'invalid_utf8')])
def test_invalid_original_refused(tmp_path, original, code):
    with pytest.raises(ArtifactError, match=code):
        store(tmp_path).save(original, 'call')
    assert not (tmp_path / '.dream').exists()


def test_file_replacement_and_foreign_owner_checks(tmp_path, monkeypatch):
    s = store(tmp_path); r = s.save('original', 'call')
    p = next((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))
    original_read = os.read
    replaced = False
    def swap(fd, n):
        nonlocal replaced
        data = original_read(fd, n)
        if not replaced:
            replaced = True
            p.unlink(); p.write_text('original'); p.chmod(0o600)
        return data
    with monkeypatch.context() as m:
        m.setattr(os, 'read', swap)
        with pytest.raises(ArtifactError, match='integrity'):
            s.read(r.handle)
    with monkeypatch.context() as m:
        real_uid = os.geteuid()
        m.setattr(os, 'geteuid', lambda: real_uid + 1)
        # The added chain check now rejects this identity at an ancestor.
        with pytest.raises(ArtifactError, match='unsafe_ancestor'):
            s.read(r.handle)


def test_real_nonregular_artifact_cannot_block_reader(tmp_path):
    s = store(tmp_path); r = s.save('original', 'call')
    p = next((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))
    p.unlink(); os.mkfifo(p, 0o600)
    with pytest.raises(ArtifactError, match='unsafe_path'):
        s.read(r.handle)


def test_result_bytes_and_read_budget_caps(tmp_path):
    with pytest.raises(ArtifactError, match='result_limit'):
        store(tmp_path, max_result_bytes=10).save('界' * 4, 'call')
    with pytest.raises(ArtifactError, match='invalid_range'):
        store(tmp_path, max_read_bytes=1)
    s = store(tmp_path, max_read_bytes=4); r = s.save('界🚀', 'call')
    p = s.read(r.handle, limit=4)
    assert p['text'] == '界' and p['next_offset'] == 1
    assert s.read(r.handle, offset=1, limit=4)['text'] == '🚀'


def test_original_file_exact_and_receipt_metadata_contains_no_content(tmp_path):
    original = 'token=sk-supersecretprivate\r\n\x00界🚀'
    s = store(tmp_path); receipt = s.save(original, 'call')
    p = next((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))
    assert p.read_bytes() == original.encode('utf-8')
    assert 'supersecret' not in str(receipt.metadata())
    assert 'session-a' not in str(receipt.metadata())
    before = p.stat()
    assert s.save(original, 'call') == receipt
    after = p.stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    changed = s.save('different result', 'call')
    assert changed.handle != receipt.handle
    assert p.read_bytes() == original.encode('utf-8')


def test_failure_after_publish_can_be_safely_retried(tmp_path, monkeypatch):
    s = store(tmp_path)
    s.save('warmup', 'first')
    fsync = os.fsync
    count = 0
    def fail_directory(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError(28, 'synthetic directory fsync failure')
        return fsync(fd)
    with monkeypatch.context() as m:
        m.setattr(os, 'fsync', fail_directory)
        with pytest.raises(ArtifactError, match='storage_io'):
            s.save('published but unacknowledged', 'second')
    receipt = s.save('published but unacknowledged', 'second')
    assert s.read(receipt.handle)['text'] == 'published but unacknowledged'
    assert len(list((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))) == 2


def test_save_never_overwrites_existing_symlink(tmp_path):
    s = store(tmp_path); receipt = s.save('original', 'call')
    p = next((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'))
    p.unlink()
    outside = tmp_path / 'outside'; outside.write_text('untouched')
    p.symlink_to(outside)
    with pytest.raises(ArtifactError):
        s.save('original', 'call')
    assert p.is_symlink() and outside.read_text() == 'untouched'


def test_user_private_group_workspace_and_dream_supported(tmp_path):
    import grp
    import pwd
    owner = pwd.getpwuid(os.geteuid())
    group = grp.getgrgid(owner.pw_gid)
    assert not [name for name in group.gr_mem if name != owner.pw_name]
    assert not [user for user in pwd.getpwall() if user.pw_gid == owner.pw_gid and user.pw_uid != owner.pw_uid]
    assert tmp_path.stat().st_gid == owner.pw_gid
    tmp_path.chmod(0o775)
    state = tmp_path / '.dream'; state.mkdir(mode=0o775); state.chmod(0o775)
    receipt = store(tmp_path).save('private-group original', 'call')
    assert store(tmp_path).read(receipt.handle)['text'] == 'private-group original'
    assert tmp_path.stat().st_mode & 0o777 == 0o775
    assert state.stat().st_mode & 0o777 == 0o775
    assert (state / 'tool-artifacts').stat().st_mode & 0o777 == 0o700


def test_shared_group_and_unresolvable_group_refused(tmp_path, monkeypatch):
    import grp
    from types import SimpleNamespace
    tmp_path.chmod(0o775)
    with monkeypatch.context() as m:
        m.setattr(grp, 'getgrgid', lambda _: SimpleNamespace(gr_mem=['another-user']))
        with pytest.raises(ArtifactError, match='unsafe_path'):
            store(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(grp, 'getgrgid', lambda _: (_ for _ in ()).throw(KeyError('absent')))
        with pytest.raises(ArtifactError, match='unsafe_path'):
            store(tmp_path)


def test_unsafe_nonsticky_ancestor_refused_and_sticky_tmp_supported(tmp_path):
    ancestor = tmp_path / 'shared'; ancestor.mkdir(mode=0o777); ancestor.chmod(0o777)
    workspace = ancestor / 'workspace'; workspace.mkdir(mode=0o700)
    with pytest.raises(ArtifactError, match='unsafe_ancestor'):
        store(workspace)
    assert not (workspace / '.dream').exists()
    ancestor.chmod(0o1777)
    receipt = store(workspace).save('sticky child owned by me', 'call')
    assert store(workspace).read(receipt.handle)['text'] == 'sticky child owned by me'


def test_foreign_owned_nonwritable_ancestor_refused(tmp_path, monkeypatch):
    ancestor = tmp_path / 'foreign'; ancestor.mkdir(mode=0o700)
    workspace = ancestor / 'workspace'; workspace.mkdir(mode=0o700)
    inode = ancestor.stat().st_ino
    real_fstat = os.fstat
    def foreign(fd):
        info = real_fstat(fd)
        if info.st_ino == inode:
            fields = list(info); fields[4] = os.geteuid() + 1
            return os.stat_result(fields)
        return info
    monkeypatch.setattr(os, 'fstat', foreign)
    with pytest.raises(ArtifactError, match='unsafe_ancestor'):
        store(workspace)


@pytest.mark.parametrize('next_action', ['retry', 'different', 'read'])
def test_real_crash_after_link_recovers_without_losing_original(tmp_path, next_action):
    import subprocess
    import sys
    # Deterministic receipt without persisting it: save once, keep handle, remove
    # original, then the separate publisher dies between link and unlink.
    s = store(tmp_path); receipt = s.save('crash original\x00界', 'call')
    next((tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8')).unlink()
    code = ('import os,sys; from dream.core.tool_artifacts import ArtifactStore; '
            'os.unlink=lambda *a,**k: os._exit(91); '
            'ArtifactStore(sys.argv[1],"session-a","run-a").save("crash original\\x00界","call")')
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], timeout=10)
    assert result.returncode == 91
    folder = tmp_path / '.dream/tool-artifacts/v1'
    entries = list(folder.iterdir())
    assert len(entries) == 2 and all(p.stat().st_nlink == 2 for p in entries)
    if next_action == 'retry':
        assert store(tmp_path).save('crash original\x00界', 'call') == receipt
    elif next_action == 'different':
        store(tmp_path).save('unrelated', 'another-call')
    else:
        assert store(tmp_path).read(receipt.handle)['text'] == 'crash original\x00界'
    assert store(tmp_path).read(receipt.handle)['text'] == 'crash original\x00界'
    assert all(p.stat().st_nlink == 1 for p in folder.iterdir())
    assert not list(folder.glob('*.tmp'))


def test_ancestor_permission_change_refused_before_publication(tmp_path):
    ancestor = tmp_path / 'ancestor'; ancestor.mkdir(mode=0o700)
    workspace = ancestor / 'workspace'; workspace.mkdir(mode=0o700)
    s = store(workspace)
    ancestor.chmod(0o777)
    with pytest.raises(ArtifactError, match='unsafe_ancestor'):
        s.save('must not publish', 'call')
    assert not (workspace / '.dream').exists()


def _linked_temp(folder, original, receipt, *, digest=None):
    alias = folder / (f'.{original.stem}.{digest or receipt.sha256}.' + 'a' * 32 + '.tmp')
    os.link(original, alias)
    return alias


@pytest.mark.parametrize('corruption', ['wrong_digest', 'outside_link', 'arbitrary_alias', 'wrong_target'])
def test_crash_recovery_refuses_unverified_or_external_links(tmp_path, corruption):
    s = store(tmp_path); receipt = s.save('original', 'call')
    folder = tmp_path / '.dream/tool-artifacts/v1'
    original = next(folder.glob('*.utf8'))
    if corruption == 'wrong_digest':
        alias = _linked_temp(folder, original, receipt, digest='0' * 64)
    elif corruption == 'outside_link':
        alias = _linked_temp(folder, original, receipt)
        os.link(original, tmp_path / 'outside')
    elif corruption == 'arbitrary_alias':
        alias = folder / 'not-a-managed-temp'
        os.link(original, alias)
    else:
        alias = folder / ('.' + '0' * 64 + '.' + receipt.sha256 + '.' + 'a' * 32 + '.tmp')
        os.link(original, alias)
    for action in (lambda: s.read(receipt.handle), lambda: s.save('other', 'other')):
        with pytest.raises(ArtifactError):
            action()
    assert original.read_bytes() == b'original' and alias.exists()


def test_legacy_crash_pair_preserved_until_receipt_verified(tmp_path):
    s = store(tmp_path); receipt = s.save('original', 'call')
    folder = tmp_path / '.dream/tool-artifacts/v1'
    original = next(folder.glob('*.utf8'))
    alias = folder / ('.' + 'b' * 32 + '.tmp'); os.link(original, alias)
    # An unrelated save has no digest for this old naming format; it must not
    # guess or delete. Both aliases are conservatively counted against quota.
    with pytest.raises(ArtifactError, match='disk_limit'):
        store(tmp_path, max_disk_bytes=16).save('x', 'other')
    store(tmp_path, max_disk_bytes=17).save('x', 'other')
    assert alias.exists()
    assert s.read(receipt.handle)['text'] == 'original'
    assert not alias.exists() and original.stat().st_nlink == 1


def test_recovery_retains_incomplete_temp_and_counts_quota(tmp_path):
    s = store(tmp_path); receipt = s.save('original', 'call')
    folder = tmp_path / '.dream/tool-artifacts/v1'
    temp = folder / ('.' + 'c' * 32 + '.tmp')
    temp.write_bytes(b'partial'); temp.chmod(0o600)
    with pytest.raises(ArtifactError, match='disk_limit'):
        store(tmp_path, max_disk_bytes=15).save('x', 'other')
    assert temp.read_bytes() == b'partial'
    assert s.read(receipt.handle)['text'] == 'original'


def test_crash_repair_failure_is_visible_and_retryable(tmp_path, monkeypatch):
    s = store(tmp_path); receipt = s.save('original', 'call')
    folder = tmp_path / '.dream/tool-artifacts/v1'
    original = next(folder.glob('*.utf8'))
    alias = _linked_temp(folder, original, receipt)
    with monkeypatch.context() as m:
        m.setattr(os, 'unlink', lambda *a, **kw: (_ for _ in ()).throw(OSError(28, 'disk failure')))
        with pytest.raises(ArtifactError, match='storage_io'):
            s.read(receipt.handle)
    assert alias.exists() and original.read_bytes() == b'original'
    assert s.read(receipt.handle)['text'] == 'original'
    assert not alias.exists()


def test_primary_group_membership_by_another_account_refused(tmp_path, monkeypatch):
    import pwd
    from types import SimpleNamespace
    owner = pwd.getpwuid(os.geteuid())
    tmp_path.chmod(0o775)
    monkeypatch.setattr(pwd, 'getpwall', lambda: [owner, SimpleNamespace(pw_gid=owner.pw_gid, pw_uid=owner.pw_uid + 1)])
    with pytest.raises(ArtifactError, match='unsafe_path'):
        store(tmp_path)


@pytest.mark.parametrize('target', ['workspace', 'state', 'ancestor'])
def test_real_named_user_acl_writer_is_refused(tmp_path, target):
    import subprocess
    ancestor = tmp_path / 'ancestor'; ancestor.mkdir(mode=0o700)
    workspace = ancestor / 'workspace'; workspace.mkdir(mode=0o700)
    state = workspace / '.dream'; state.mkdir(mode=0o700)
    selected = {'workspace': workspace, 'state': state, 'ancestor': ancestor}[target]
    subprocess.run(['setfacl', '-m', 'u:65534:rwx', str(selected)], check=True, capture_output=True)
    assert os.getxattr(selected, 'system.posix_acl_access')
    with pytest.raises(ArtifactError, match='unsafe_(path|ancestor)'):
        store(workspace).save('private original', 'call')
    assert not (state / 'tool-artifacts').exists()


def test_inherited_default_acl_cannot_grant_artifact_access(tmp_path):
    import subprocess
    subprocess.run(['setfacl', '-m', 'd:u:65534:rwx', str(tmp_path)], check=True, capture_output=True)
    s = store(tmp_path); receipt = s.save('private original', 'call')
    folder = tmp_path / '.dream/tool-artifacts/v1'
    original = next(folder.glob('*.utf8'))
    assert original.stat().st_mode & 0o777 == 0o600
    assert folder.stat().st_mode & 0o777 == 0o700
    assert s.read(receipt.handle)['text'] == 'private original'
    output = subprocess.run(['getfacl', '-cpn', str(original)], check=True, capture_output=True, text=True).stdout
    assert 'user:65534:rwx' in output and '#effective:---' in output


@pytest.mark.parametrize('kind', ['unreadable', 'unknown_version', 'malformed_length', 'unknown_tag', 'missing_mask'])
def test_acl_metadata_unknown_or_unreadable_fails_closed(tmp_path, monkeypatch, kind):
    import errno
    import struct
    def unknown(fd, attribute):
        assert isinstance(fd, int) and attribute == 'system.posix_acl_access'
        if kind == 'unreadable':
            raise OSError(errno.EACCES, 'cannot inspect ACL')
        if kind == 'unknown_version':
            return struct.pack('<I', 99)
        if kind == 'malformed_length':
            return struct.pack('<I', 2) + b'bad'
        if kind == 'unknown_tag':
            return struct.pack('<IHHI', 2, 512, 7, 0xffffffff)
        return struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in
            [(1, 7, 0xffffffff), (2, 7, 65534), (4, 0, 0xffffffff), (32, 0, 0xffffffff)])
    monkeypatch.setattr(os, 'getxattr', unknown)
    with pytest.raises(ArtifactError, match='unsafe_acl'):
        store(tmp_path)


@pytest.mark.parametrize('missing_acl', ['ENODATA', 'ENOTSUP'])
def test_absent_or_unsupported_acl_has_explicit_safe_semantics(tmp_path, monkeypatch, missing_acl):
    import errno
    monkeypatch.setattr(os, 'getxattr', lambda *a: (_ for _ in ()).throw(OSError(getattr(errno, missing_acl), 'no ACL')))
    receipt = store(tmp_path).save('ordinary private directory', 'call')
    assert store(tmp_path).read(receipt.handle)['text'] == 'ordinary private directory'


@pytest.mark.parametrize('acl', ['u:65534:r-x', 'u:65534:rwx,m::r-x'])
def test_effectively_readonly_named_acl_does_not_block_safe_workspace(tmp_path, acl):
    import subprocess
    subprocess.run(['setfacl', '-m', acl, str(tmp_path)], check=True, capture_output=True)
    receipt = store(tmp_path).save('original', 'call')
    assert store(tmp_path).read(receipt.handle)['text'] == 'original'


def test_sticky_ancestor_with_named_acl_still_requires_owned_child(tmp_path, monkeypatch):
    import subprocess
    parent = tmp_path / 'sticky'; parent.mkdir(mode=0o1700)
    child = parent / 'child'; child.mkdir(mode=0o700)
    workspace = child / 'workspace'; workspace.mkdir(mode=0o700)
    subprocess.run(['setfacl', '-m', 'u:65534:rwx', str(parent)], check=True, capture_output=True)
    inode = child.stat().st_ino
    original_fstat = os.fstat
    def foreign(fd):
        info = original_fstat(fd)
        if info.st_ino == inode:
            fields = list(info); fields[4] = 65534
            return os.stat_result(fields)
        return info
    monkeypatch.setattr(os, 'fstat', foreign)
    with pytest.raises(ArtifactError, match='unsafe_ancestor'):
        store(workspace)


def test_file_count_limit_preserves_read_and_retry_at_capacity(tmp_path, monkeypatch):
    from dream.core import tool_artifacts
    monkeypatch.setattr(tool_artifacts, 'MAX_FILES', 3)
    s = store(tmp_path)
    receipts = [s.save(str(i), str(i)) for i in range(3)]
    assert [s.read(r.handle)['text'] for r in receipts] == ['0', '1', '2']
    assert s.save('2', '2') == receipts[2]
    folder = tmp_path / '.dream/tool-artifacts/v1'
    original = folder / s._name(receipts[2])
    alias = _linked_temp(folder, original, receipts[2])
    # Read the first artifact BEFORE recovering the unrelated final publication.
    # A valid original must remain readable while C has a verified temp alias.
    assert s.read(receipts[0].handle)['text'] == '0'
    assert original.read_bytes() == b'2' and alias.exists()
    assert s.read(receipts[2].handle)['text'] == '2'
    assert not alias.exists()
    with pytest.raises(ArtifactError, match='disk_limit'):
        s.save('3', '3')
