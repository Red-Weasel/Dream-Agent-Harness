from concurrent.futures import ThreadPoolExecutor
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dream.core import instructions


@pytest.fixture
def set_access_acl():
    executable = shutil.which('setfacl')
    if executable is None:
        pytest.skip('real POSIX ACL fixture requires setfacl')
    def apply(path, specification):
        result = subprocess.run([executable, '-m', specification, str(path)], capture_output=True, text=True)
        if result.returncode and 'not supported' in result.stderr.lower():
            pytest.skip('fixture filesystem does not support POSIX ACLs')
        result.check_returncode()
    return apply


def test_new_workspace_gets_complete_canonical_and_pointers(tmp_path):
    from dream.core.project_bootstrap import TEMPLATE, POINTERS
    with ThreadPoolExecutor(max_workers=8) as pool:
        prompts = list(pool.map(lambda _: instructions.project_instructions(tmp_path), range(24)))
    assert (tmp_path / 'DREAM.md').read_text() == TEMPLATE
    for name, text in POINTERS.items():
        assert (tmp_path / name).read_text() == text
    assert all('### DREAM.md' in p and '### CLAUDE.md' not in p and '### AGENTS.md' not in p for p in prompts)
    assert not list(tmp_path.glob('.dream-instructions-*'))


def test_custom_files_preserved_and_priority_visible(tmp_path):
    for name in ('DREAM.md', 'CLAUDE.md', 'AGENTS.md'):
        (tmp_path / name).write_text('custom ' + name)
    prompt = instructions.project_instructions(tmp_path)
    for name in ('DREAM.md', 'CLAUDE.md', 'AGENTS.md'):
        assert (tmp_path / name).read_text() == 'custom ' + name
        assert 'custom ' + name in prompt
    assert 'DREAM.md takes precedence' in prompt
    assert "user's current instructions" in prompt


def test_case_variant_canonical_is_used_without_duplicate(tmp_path):
    (tmp_path / 'dream.md').write_text('case-specific rule')
    prompt = instructions.project_instructions(tmp_path)
    assert 'case-specific rule' in prompt
    assert not (tmp_path / 'DREAM.md').exists()
    assert not (tmp_path / 'CLAUDE.md').exists()  # @DREAM.md would be a broken import


def test_symlink_never_read_or_overwritten(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    secret = tmp_path / 'secret'; secret.write_text('PRIVATE_OUTSIDE')
    (root / 'DREAM.md').symlink_to(secret)
    prompt = instructions.project_instructions(root)
    assert 'PRIVATE_OUTSIDE' not in prompt
    assert 'symlink' in prompt
    assert (root / 'DREAM.md').is_symlink()
    assert not (root / 'CLAUDE.md').exists()


def test_bad_workspace_and_unsafe_directory_visible(tmp_path):
    file = tmp_path / 'file'; file.write_text('x')
    assert 'unavailable' in instructions.project_instructions(file)
    tmp_path.chmod(0o777)
    assert 'unsafe_directory' in instructions.project_instructions(tmp_path)
    assert not (tmp_path / 'DREAM.md').exists()


def test_readonly_directory_failure_visible(tmp_path):
    tmp_path.chmod(0o500)
    try:
        assert 'read_only_directory' in instructions.project_instructions(tmp_path)
    finally:
        tmp_path.chmod(0o700)


def test_directory_symlink_rejected(tmp_path):
    real = tmp_path / 'real'; real.mkdir()
    alias = tmp_path / 'alias'; alias.symlink_to(real, target_is_directory=True)
    assert 'unavailable' in instructions.project_instructions(alias)
    assert not (real / 'DREAM.md').exists()


def test_modified_managed_pointer_is_custom(tmp_path):
    instructions.project_instructions(tmp_path)
    with (tmp_path / 'CLAUDE.md').open('a') as f:
        f.write('Additional rule.\n')
    assert 'Additional rule.' in instructions.project_instructions(tmp_path)


def test_publication_is_complete_before_target_becomes_visible(tmp_path, monkeypatch):
    from dream.core import project_bootstrap as bootstrap
    original = os.link
    seen = []
    def observe(source, target, **kwargs):
        fd = kwargs['src_dir_fd']
        with os.fdopen(os.open(source, os.O_RDONLY, dir_fd=fd), 'rb') as f:
            raw = f.read()
        assert raw == (bootstrap.TEMPLATE if target == 'DREAM.md' else bootstrap.POINTERS[target]).encode()
        assert not (tmp_path / target).exists()
        seen.append(target)
        return original(source, target, **kwargs)
    monkeypatch.setattr(os, 'link', observe)
    instructions.project_instructions(tmp_path)
    assert seen == ['DREAM.md', 'CLAUDE.md', 'AGENTS.md']


def test_concurrent_existing_winner_is_preserved_and_read(tmp_path, monkeypatch):
    original = os.link
    def race(source, target, **kwargs):
        if target == 'DREAM.md':
            (tmp_path / target).write_text('THE OTHER WRITER WON')
        return original(source, target, **kwargs)
    monkeypatch.setattr(os, 'link', race)
    prompt = instructions.project_instructions(tmp_path)
    assert 'THE OTHER WRITER WON' in prompt
    assert (tmp_path / 'DREAM.md').read_text() == 'THE OTHER WRITER WON'


def test_failed_creation_visible_with_structured_provenance(tmp_path, monkeypatch):
    from dream.core import project_bootstrap as bootstrap
    def fail(*args):
        raise PermissionError('do not expose this arbitrary/private/path')
    monkeypatch.setattr(bootstrap, '_publish', fail)
    prompt = instructions.project_instructions(tmp_path)
    assert 'creation_failed' in prompt and 'arbitrary/private' not in prompt
    assert any(source == 'project_instruction_status' and detail['reason'] == 'creation_failed'
               for source, detail, _ in prompt.parts)


def test_foreign_owned_file_is_not_read(tmp_path, monkeypatch):
    from dream.core import project_bootstrap as bootstrap
    (tmp_path / 'DREAM.md').write_text('FOREIGN_SECRET')
    owned = bootstrap._owned
    monkeypatch.setattr(bootstrap, '_owned', lambda info: owned(info) and not __import__('stat').S_ISREG(info.st_mode))
    prompt = instructions.project_instructions(tmp_path)
    assert 'foreign_owned_file' in prompt and 'FOREIGN_SECRET' not in prompt
    assert (tmp_path / 'DREAM.md').read_text() == 'FOREIGN_SECRET'


def test_ambiguous_case_variants_are_visible_and_preserved(tmp_path):
    (tmp_path / 'DREAM.md').write_text('A')
    (tmp_path / 'dream.md').write_text('B')
    prompt = instructions.project_instructions(tmp_path)
    assert 'ambiguous_case_variants' in prompt
    assert '### DREAM.md' not in prompt
    assert not (tmp_path / 'CLAUDE.md').exists()


def test_non_regular_instruction_entry_never_blocks(tmp_path):
    os.mkfifo(tmp_path / 'DREAM.md')
    prompt = instructions.project_instructions(tmp_path)
    assert 'not_regular_file' in prompt


def test_pointer_symlink_is_preserved_without_reading_target(tmp_path):
    (tmp_path / 'DREAM.md').write_text('SAFE_PROJECT')
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.write_text('OUTSIDE_PRIVATE')
    (tmp_path / 'CLAUDE.md').symlink_to(outside)
    prompt = instructions.project_instructions(tmp_path)
    assert 'SAFE_PROJECT' in prompt and 'OUTSIDE_PRIVATE' not in prompt
    assert 'symlink' in prompt and (tmp_path / 'CLAUDE.md').is_symlink()


def test_workspace_replacement_does_not_attribute_detached_rules(tmp_path, monkeypatch):
    from dream.core import project_bootstrap as bootstrap
    root = tmp_path / 'workspace'; root.mkdir()
    (root / 'DREAM.md').write_text('DETACHED_RULE')
    original = bootstrap._read
    def renamed(fd, name, cap):
        result = original(fd, name, cap)
        if name == 'DREAM.md':
            root.rename(tmp_path / 'detached')
            root.mkdir()
        return result
    monkeypatch.setattr(bootstrap, '_read', renamed)
    prompt = instructions.project_instructions(root)
    assert 'DETACHED_RULE' not in prompt
    assert 'workspace_changed_during_capture' in prompt
    assert not (root / 'DREAM.md').exists()


def test_shared_nonsticky_ancestor_refused_before_any_publication(tmp_path, monkeypatch):
    from dream.core import project_bootstrap as bootstrap
    ancestor = tmp_path / 'shared'; ancestor.mkdir(mode=0o777); ancestor.chmod(0o777)
    root = ancestor / 'private'; root.mkdir(mode=0o700)
    published = []
    original = bootstrap._publish
    def observe(*args):
        published.append(args[1])
        return original(*args)
    monkeypatch.setattr(bootstrap, '_publish', observe)
    prompt = instructions.project_instructions(root)
    assert 'unsafe_ancestor_directory' in prompt
    assert published == [] and list(root.iterdir()) == []


def test_sticky_trusted_ancestor_with_owned_child_is_supported(tmp_path):
    ancestor = tmp_path / 'shared'; ancestor.mkdir(); ancestor.chmod(0o1777)
    root = ancestor / 'private'; root.mkdir(mode=0o700)
    prompt = instructions.project_instructions(root)
    assert '### DREAM.md' in prompt and (root / 'DREAM.md').is_file()


@pytest.mark.parametrize('mode', [0o755, 0o1777])
def test_foreign_ancestor_owner_is_unsafe_even_with_sticky_bit(tmp_path, monkeypatch, mode):
    from types import SimpleNamespace
    ancestor = tmp_path / 'foreign'; ancestor.mkdir(); ancestor.chmod(mode)
    root = ancestor / 'private'; root.mkdir(mode=0o700)
    foreign_inode = ancestor.stat().st_ino
    original = os.fstat
    def foreign_owner(fd):
        result = original(fd)
        if result.st_ino == foreign_inode:
            return SimpleNamespace(**{name: (os.geteuid() + 1234 if name == 'st_uid' else getattr(result, name))
                for name in dir(result) if name.startswith('st_')})
        return result
    monkeypatch.setattr(os, 'fstat', foreign_owner)
    prompt = instructions.project_instructions(root)
    assert 'unsafe_ancestor_directory' in prompt
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('principal', ['u:65534:rwx', 'g:65534:rwx'])
@pytest.mark.parametrize('location', ['leaf', 'ancestor', 'instruction'])
def test_extended_access_acl_refused_before_publication(tmp_path, principal, location, set_access_acl):
    import subprocess
    ancestor = tmp_path / 'ancestor'; ancestor.mkdir(mode=0o700)
    root = ancestor / 'workspace'; root.mkdir(mode=0o700)
    target = root
    if location == 'ancestor':
        target = ancestor
    elif location == 'instruction':
        target = root / 'DREAM.md'
        target.write_text('ACL_CONTROLLED_INSTRUCTIONS')
    set_access_acl(target, principal)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    prompt = instructions.project_instructions(root)
    assert 'extended_access_acl' in prompt
    assert 'ACL_CONTROLLED_INSTRUCTIONS' not in prompt
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


@pytest.mark.parametrize('error', ['EACCES', 'EIO', 'EINVAL'])
def test_acl_inspection_error_is_visible_and_prevents_publication(tmp_path, monkeypatch, error):
    import errno
    from dream.core import project_bootstrap as bootstrap
    def unavailable(fd, attribute):
        assert isinstance(fd, int) and attribute == 'system.posix_acl_access'
        raise OSError(getattr(errno, error), '/private/exception/text')
    root = tmp_path / 'workspace'; root.mkdir(mode=0o700)
    monkeypatch.setattr(os, 'getxattr', unavailable)
    prompt = instructions.project_instructions(root)
    assert 'access_acl_inspection_failed' in prompt
    assert '/private/exception/text' not in prompt
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('error', ['ENODATA', 'ENOTSUP'])
def test_known_absent_or_unsupported_acl_filesystem_uses_mode_checks(tmp_path, monkeypatch, error):
    import errno
    def no_acl(fd, attribute):
        assert isinstance(fd, int) and attribute == 'system.posix_acl_access'
        raise OSError(getattr(errno, error), 'no access ACL')
    monkeypatch.setattr(os, 'getxattr', no_acl)
    prompt = instructions.project_instructions(tmp_path)
    assert '### DREAM.md' in prompt and (tmp_path / 'DREAM.md').is_file()


def test_missing_acl_api_is_visible_without_publication(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'; root.mkdir(mode=0o700)
    monkeypatch.delattr(os, 'getxattr')
    prompt = instructions.project_instructions(root)
    assert 'access_acl_unsupported' in prompt
    assert list(root.iterdir()) == []


def test_instruction_acl_inspection_failure_never_reads_or_creates_pointers(tmp_path, monkeypatch):
    import errno
    import stat
    root = tmp_path / 'workspace'; root.mkdir(mode=0o700)
    (root / 'DREAM.md').write_text('UNREADABLE_RULES')
    original = os.getxattr
    def unavailable(fd, attribute):
        assert isinstance(fd, int)
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, 'private exception')
        return original(fd, attribute)
    monkeypatch.setattr(os, 'getxattr', unavailable)
    prompt = instructions.project_instructions(root)
    assert 'access_acl_inspection_failed' in prompt and 'UNREADABLE_RULES' not in prompt
    assert sorted(p.name for p in root.iterdir()) == ['DREAM.md']


def test_inherited_access_acl_on_private_temporary_file_is_not_published(tmp_path, set_access_acl):
    import subprocess
    root = tmp_path / 'workspace'; root.mkdir(mode=0o700)
    # Default ACL is inheritance policy, not an access grant on this directory.
    # Generated mode0600 masks inherited nonowner entries, but this implementation
    # conservatively refuses every extended access ACL, including that safe subset.
    set_access_acl(root, 'd:u:65534:rwx')
    prompt = instructions.project_instructions(root)
    assert 'extended_access_acl' in prompt
    assert list(root.iterdir()) == []


def test_readonly_extended_acl_is_conservatively_visible(tmp_path, set_access_acl):
    import subprocess
    root = tmp_path / 'workspace'; root.mkdir(mode=0o700)
    set_access_acl(root, 'u:65534:r-x')
    prompt = instructions.project_instructions(root)
    assert 'extended_access_acl' in prompt and list(root.iterdir()) == []
