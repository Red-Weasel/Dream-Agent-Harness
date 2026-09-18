"""Only owned CPU fixture children; never launch or probe an inference engine."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import pytest
from dream.local.load_lock import load_lock


def test_nested_launch_holds_until_inherited_child_exits(tmp_path):
    with load_lock(19435, root=tmp_path) as fd:
        with load_lock(19435, root=tmp_path) as nested:
            assert fd == nested
            child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'],
                                     stdin=subprocess.PIPE, pass_fds=(fd,))
    try:
        with pytest.raises(ValueError, match='Another Dream'):
            with load_lock(19435, root=tmp_path):
                pytest.fail('double load')
    finally:
        child.communicate(timeout=3)
    with load_lock(19435, root=tmp_path):
        pass


async def test_another_task_cannot_borrow_outer_task_lock(tmp_path):
    async def contender():
        with pytest.raises(ValueError, match='Another Dream'):
            with load_lock(19435, root=tmp_path):
                pytest.fail('overlap')
    with load_lock(19435, root=tmp_path):
        await asyncio.create_task(contender())


def test_unsafe_lock_rejected(tmp_path):
    target = tmp_path / 'target'
    target.write_text('untouched')
    (tmp_path / f'dream-machx-load-{os.getuid()}-19435.lock').symlink_to(target)
    with pytest.raises(ValueError, match='safely'):
        with load_lock(19435, root=tmp_path):
            pass
    assert target.read_text() == 'untouched'


def test_machx_serve_inherits_launch_lock(tmp_path, monkeypatch):
    from dream.local import machx
    from contextlib import contextmanager
    calls = []
    @contextmanager
    def fixture_lock(port):
        with load_lock(port, root=tmp_path) as fd:
            yield fd
    class FakeProcess:
        pid = 123456789
    def spawn(*args, **kwargs):
        fd, = kwargs['pass_fds']
        assert os.fstat(fd)
        calls.append(kwargs)
        return FakeProcess()
    monkeypatch.setattr('dream.local.load_lock.load_lock', fixture_lock)
    monkeypatch.setattr(machx.subprocess, 'Popen', spawn)
    monkeypatch.setattr(machx.config, 'LOG_DIR', tmp_path)
    monkeypatch.setattr(machx, '_log_file', lambda: tmp_path / 'model.log')
    monkeypatch.setattr(machx, '_pid_file', lambda: tmp_path / 'model.pid')
    monkeypatch.setattr(machx, 'PORT', 19435)
    machx.serve(Path('fixture.gguf'), gpus=1, ctx=8192)
    assert len(calls) == 1
