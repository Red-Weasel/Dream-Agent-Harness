"""Debian runtime alternatives are visible read-only inside containment."""
from pathlib import Path

import pytest

from dream.core import execution as ex


def test_runtime_alternatives_are_readonly_without_exposing_all_etc(tmp_path):
    alternatives = Path('/etc/alternatives')
    if not alternatives.is_dir():
        pytest.skip('host has no alternatives directory')
    scope = ex.ExecutionScope(tmp_path)
    argv = ex._bwrap_argv(scope, '/fixture/bwrap', ['/bin/true'],
                         seccomp_fd=20, mount_fds={scope.workspace: 21})
    mounts = [argv[i:i + 3] for i, value in enumerate(argv) if value in {'--ro-bind', '--bind'}]
    assert ['--ro-bind', str(alternatives), str(alternatives)] in mounts
    assert not any(mount[1] == '/etc' or mount[2] == '/etc' for mount in mounts)
    assert not any(mount[0] == '--bind' and mount[2].startswith('/etc') for mount in mounts)
    assert '--unshare-net' in argv and '--seccomp' in argv and '--remount-ro' in argv


def test_absent_alternatives_is_optional(tmp_path, monkeypatch):
    exists = Path.exists
    monkeypatch.setattr(Path, 'exists', lambda path: False if path == Path('/etc/alternatives') else exists(path))
    scope = ex.ExecutionScope(tmp_path)
    argv = ex._bwrap_argv(scope, '/fixture/bwrap', ['/bin/true'],
                         seccomp_fd=20, mount_fds={scope.workspace: 21})
    assert '/etc/alternatives' not in argv


@pytest.mark.parametrize('binary,args,expected', [
    ('/usr/bin/ffmpeg', ['-version'], b'ffmpeg version'),
    ('/usr/bin/ffprobe', ['-version'], b'ffprobe version'),
    ('/usr/bin/which', ['ffmpeg'], b'/usr/bin/ffmpeg'),
])
async def test_real_contained_runtime_link_resolution(tmp_path, binary, args, expected):
    if not Path(binary).exists():
        pytest.skip(f'{binary} is not installed')
    scope = ex.ExecutionScope(tmp_path)
    capability = await ex.probe_sandbox(scope)
    if not capability.available:
        pytest.skip(capability.reason)
    result = await ex.run_contained([binary, *args], scope, timeout=10, max_output=4096)
    assert result.returncode == 0, result.output.decode(errors='replace')
    assert expected in result.output


def test_runtime_directory_requires_trusted_real_directory(tmp_path):
    directory = tmp_path / 'runtime'
    directory.mkdir()
    link = tmp_path / 'link'
    link.symlink_to('/usr/lib', target_is_directory=True)
    file = tmp_path / 'file'
    file.write_text('fixture')
    # Even a link to a trusted directory cannot redirect the runtime mount.
    assert not ex._trusted_runtime_directory(link)
    assert not ex._trusted_runtime_directory(directory)
    assert not ex._trusted_runtime_directory(file)
    assert not ex._trusted_runtime_directory(tmp_path / 'missing')


def test_untrusted_existing_alternatives_refuses_mount(tmp_path, monkeypatch):
    if not Path('/etc/alternatives').is_dir():
        pytest.skip('host has no alternatives directory')
    monkeypatch.setattr(ex, '_trusted_runtime_directory', lambda path: False)
    scope = ex.ExecutionScope(tmp_path)
    with pytest.raises(ex.ExecutionRefused, match='untrusted runtime alternatives'):
        ex._bwrap_argv(scope, '/fixture/bwrap', ['/bin/true'],
                       seccomp_fd=20, mount_fds={scope.workspace: 21})


async def test_real_alternatives_mount_is_readonly_and_other_etc_stays_hidden(tmp_path):
    if not Path('/etc/alternatives').is_dir():
        pytest.skip('host has no alternatives directory')
    scope = ex.ExecutionScope(tmp_path)
    capability = await ex.probe_sandbox(scope)
    if not capability.available:
        pytest.skip(capability.reason)
    code = ("import os; "
            "assert os.statvfs('/etc/alternatives').f_flag & os.ST_RDONLY; "
            "assert not os.path.exists('/etc/shadow'); "
            "print('readonly-runtime-links')")
    result = await ex.run_contained(['/usr/bin/python3', '-I', '-c', code], scope,
                                    timeout=10, max_output=4096)
    assert result.returncode == 0, result.output.decode(errors='replace')
    assert b'readonly-runtime-links' in result.output
