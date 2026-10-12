"""Real descriptor reads with controlled path and byte changes; no runtime starts."""
import errno
import os

import pytest

from dream import local_eval_skills


def write_skill(path, body='Generic instructions.\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('---\nname: sample\ndescription: Generic fixture.\n---\n' + body)
    return path


class Descriptors:
    """Track real successful opens and closes during one loader invocation."""
    def __init__(self):
        self.open = os.open
        self.close = os.close
        self.read = os.read
        self.live = set()
        self.read_inodes = []

    def opened(self, path, flags, *args, **kwargs):
        fd = self.open(path, flags, *args, **kwargs)
        assert fd not in self.live
        self.live.add(fd)
        return fd

    def closed(self, fd):
        self.close(fd)
        self.live.remove(fd)

    def read_bytes(self, fd, count):
        info = os.fstat(fd)
        self.read_inodes.append((info.st_dev, info.st_ino))
        return self.read(fd, count)


@pytest.mark.parametrize('position', ['root', 'skill', 'domain_skill', 'file'])
def test_symlink_substitution_immediately_before_open_never_reads_outside(tmp_path, monkeypatch, position):
    root = tmp_path / 'packaged'
    path = write_skill(root / ('domains/sample/SKILL.md' if position == 'domain_skill' else 'sample/SKILL.md'))
    outside_root = tmp_path / 'outside'
    outside = write_skill(outside_root / 'sample/SKILL.md', body='Outside sentinel must not be read.\n')
    info = outside.stat()
    outside_inode = (info.st_dev, info.st_ino)
    target = root if position == 'root' else path if position == 'file' else path.parent
    replacement = outside_root if position == 'root' else outside if position == 'file' else outside.parent
    trigger = 'packaged' if position == 'root' else 'SKILL.md' if position == 'file' else 'sample'
    descriptors = Descriptors()
    swapped = False

    def race_open(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == trigger and not swapped:
            swapped = True
            target.rename(tmp_path / 'original')
            target.symlink_to(replacement, target_is_directory=position != 'file')
        return descriptors.opened(name, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', race_open)
        patch.setattr(os, 'close', descriptors.closed)
        patch.setattr(os, 'read', descriptors.read_bytes)
        with pytest.raises((ValueError, OSError)):
            local_eval_skills.load_packaged_skills(root)
    assert swapped
    assert outside_inode not in descriptors.read_inodes
    assert not descriptors.live


@pytest.mark.parametrize('growth', [32, 200_000])
def test_growth_during_real_read_refuses_and_retains_byte_bound(tmp_path, monkeypatch, growth):
    root = tmp_path / 'packaged'
    path = write_skill(root / 'sample/SKILL.md', body='x' * 10_000)
    descriptors = Descriptors()
    grown = False
    bytes_read = 0
    requests = []

    def grow_read(fd, count):
        nonlocal grown, bytes_read
        requests.append(count)
        chunk = descriptors.read_bytes(fd, count)
        bytes_read += len(chunk)
        if not grown:
            grown = True
            with path.open('ab') as stream:
                stream.write(b'y' * growth)
        return chunk

    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', descriptors.opened)
        patch.setattr(os, 'close', descriptors.closed)
        patch.setattr(os, 'read', grow_read)
        with pytest.raises(ValueError):
            local_eval_skills.load_packaged_skills(root)
    assert grown and bytes_read > 0
    assert bytes_read <= 128 * 1024 + 1
    assert all(0 < count <= 8192 for count in requests)
    assert not descriptors.live


def test_read_error_preserves_original_failure_and_closes_all_descriptors(tmp_path, monkeypatch):
    root = tmp_path / 'packaged'
    write_skill(root / 'domains/sample/SKILL.md', body='x' * 10_000)
    descriptors = Descriptors()
    failure = OSError(errno.EIO, 'Synthetic read failure')
    calls = 0

    def fail_read(fd, count):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure
        return descriptors.read_bytes(fd, count)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', descriptors.opened)
        patch.setattr(os, 'close', descriptors.closed)
        patch.setattr(os, 'read', fail_read)
        with pytest.raises(OSError) as caught:
            local_eval_skills.load_packaged_skills(root)
    assert caught.value is failure and calls == 2
    assert not descriptors.live


@pytest.mark.parametrize('header', [
    'name: sample\nname: another\ndescription: Generic.\n',
    'name: sample\ndescription: First.\ndescription: Second.\n',
    'name: sample\ndescription: Generic.\nextra: one\nextra: two\n',
    'name: !!python/name:builtins.str ""\ndescription: Generic.\n',
    'name: sample\ndescription: !!python/object/apply:builtins.str [123]\n',
    '!!python/object:builtins.object {}\n',
])
def test_duplicate_keys_and_python_yaml_tags_refuse_without_descriptor_leak(tmp_path, monkeypatch, header):
    root = tmp_path / 'packaged'
    path = write_skill(root / 'sample/SKILL.md')
    path.write_text('---\n' + header + '---\nGeneric body.\n')
    descriptors = Descriptors()
    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', descriptors.opened)
        patch.setattr(os, 'close', descriptors.closed)
        with pytest.raises(ValueError):
            local_eval_skills.load_packaged_skills(root)
    assert not descriptors.live
