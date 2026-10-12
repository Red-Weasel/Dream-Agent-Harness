"""DREAM-252: the worker record's folders survive a power loss on first use.

`_Store._folder` makes `.dream` and `.dream/workers` when they are missing. A new folder's entry lives in its PARENT,
so the parent is flushed straight after the mkdir, while its descriptor is still open. These tests use a real
workspace under the test's own tmp directory and watch the real os calls (mkdir, fsync, open, close) only for the
length of one store operation; the one injected fault is an fsync that fails for one chosen folder.

No model, no adapter, no network. What they cannot show: that a flush really reaches the disk (that is the kernel's
and the filesystem's), only that Dream asks for it, in the right order, on the right folder.
"""
from __future__ import annotations

import contextlib
import errno
import os
import stat

import pytest

from dream.core import worker_evidence

RUN = "0123456789abcdef0123456789abcdef"

pytestmark = pytest.mark.skipif(not worker_evidence.SAFE_WRITES,
                                reason="this platform lacks the file operations a safe write needs")


def _identity(path) -> tuple[int, int]:
    found = os.stat(path, follow_symlinks=False)
    return found.st_dev, found.st_ino


def _workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    os.chmod(workspace, 0o755)
    return workspace


def _private_dir(path) -> None:
    os.mkdir(path)
    os.chmod(path, 0o700)          # whatever the umask is


class _Watch:
    """The os calls one store operation makes, in order. `fail_fsync_on` is the (device, inode) of the one thing
    whose flush fails."""

    def __init__(self, fail_fsync_on: tuple[int, int] | None = None) -> None:
        self.events: list[tuple] = []
        self.open_fds: set[int] = set()
        self.mode_changes: list[tuple] = []
        self.fail_fsync_on = fail_fsync_on

    @contextlib.contextmanager
    def watching(self):
        real = {name: getattr(os, name) for name in ("mkdir", "fsync", "open", "close")}

        def mkdir(path, mode=0o777, *, dir_fd=None):
            real["mkdir"](path, mode, dir_fd=dir_fd)          # only a mkdir that MADE the folder is an event
            self.events.append(("mkdir", os.fspath(path), dir_fd))

        def fsync(fd):
            found = os.fstat(fd)
            identity = (found.st_dev, found.st_ino)
            self.events.append(("fsync", fd, identity))
            if identity == self.fail_fsync_on:
                raise OSError(errno.EIO, "injected: the flush failed")
            real["fsync"](fd)

        def opened(*args, **kwargs):
            fd = real["open"](*args, **kwargs)
            self.open_fds.add(fd)
            return fd

        def close(fd):
            self.events.append(("close", fd))
            self.open_fds.discard(fd)
            real["close"](fd)

        def changed(name):
            def record(*args, **kwargs):
                self.mode_changes.append((name, args))
                raise AssertionError(f"os.{name} must not be called")
            return record

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(worker_evidence.os, "mkdir", mkdir)
            patch.setattr(worker_evidence.os, "fsync", fsync)
            patch.setattr(worker_evidence.os, "open", opened)
            patch.setattr(worker_evidence.os, "close", close)
            for name in ("chmod", "fchmod", "lchmod", "chown", "fchown"):
                if hasattr(os, name):
                    patch.setattr(worker_evidence.os, name, changed(name))
            yield self

    def flushed(self) -> list[tuple[int, int]]:
        return [event[2] for event in self.events if event[0] == "fsync"]

    def made(self) -> list[str]:
        return [event[1] for event in self.events if event[0] == "mkdir"]


def test_first_use_flushes_each_new_folders_parent_before_closing_it(tmp_path):
    workspace = _workspace(tmp_path)
    store = worker_evidence._Store(workspace, RUN)
    with _Watch().watching() as watch:
        store.write(b'{"first": true}')

    root, dream, workers = _identity(workspace), _identity(workspace / ".dream"), _identity(workspace / ".dream/workers")
    record = _identity(workspace / ".dream" / "workers" / f"{RUN}.json")
    assert watch.made() == [".dream", "workers"]

    # The very next call after each mkdir is the flush of the descriptor the mkdir was relative to -- nothing
    # between them, and in particular not that descriptor's close.
    parents = []
    for index, event in enumerate(watch.events):
        if event[0] != "mkdir":
            continue
        _, _part, parent_fd = event
        following = watch.events[index + 1]
        assert following[0] == "fsync" and following[1] == parent_fd, watch.events
        parents.append(following[2])
        assert ("close", parent_fd) in watch.events[index + 2:], "the parent is closed, and only after its flush"
    assert parents == [root, dream]          # .dream's entry is in the workspace, workers' entry is in .dream

    # The record's own flushes are still there, after the folders': the file, then the folder that holds it.
    assert watch.flushed() == [root, dream, record, workers]
    assert watch.open_fds == set() and watch.mode_changes == []
    assert stat.S_IMODE(os.stat(workspace / ".dream").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(workspace / ".dream" / "workers").st_mode) == 0o700
    assert (workspace / ".dream" / "workers" / f"{RUN}.json").read_bytes() == b'{"first": true}'


def test_existing_folders_get_no_ancestry_flush(tmp_path):
    workspace = _workspace(tmp_path)
    _private_dir(workspace / ".dream")
    _private_dir(workspace / ".dream" / "workers")
    root, dream, workers = _identity(workspace), _identity(workspace / ".dream"), _identity(workspace / ".dream/workers")
    store = worker_evidence._Store(workspace, RUN)
    with _Watch().watching() as watch:
        store.write(b"{}")

    assert watch.made() == []
    record = _identity(workspace / ".dream" / "workers" / f"{RUN}.json")
    assert watch.flushed() == [record, workers]          # the file and its folder, as before; nothing above them
    assert root not in watch.flushed() and dream not in watch.flushed()
    assert watch.open_fds == set()


def test_a_second_write_does_not_flush_the_ancestry_again(tmp_path):
    workspace = _workspace(tmp_path)
    store = worker_evidence._Store(workspace, RUN)
    store.write(b'{"n": 1}')
    root, dream, workers = _identity(workspace), _identity(workspace / ".dream"), _identity(workspace / ".dream/workers")
    with _Watch().watching() as watch:
        store.write(b'{"n": 2}')

    assert watch.made() == []
    assert watch.flushed() == [_identity(workspace / ".dream" / "workers" / f"{RUN}.json"), workers]
    assert root not in watch.flushed() and dream not in watch.flushed()


def test_only_the_missing_folders_parent_is_flushed(tmp_path):
    workspace = _workspace(tmp_path)
    _private_dir(workspace / ".dream")
    root, dream = _identity(workspace), _identity(workspace / ".dream")
    store = worker_evidence._Store(workspace, RUN)
    with _Watch().watching() as watch:
        store.write(b"{}")

    assert watch.made() == ["workers"]
    workers = _identity(workspace / ".dream" / "workers")
    assert watch.flushed() == [dream, _identity(workspace / ".dream" / "workers" / f"{RUN}.json"), workers]
    assert root not in watch.flushed()


def test_remove_makes_and_flushes_nothing_when_the_folders_exist(tmp_path):
    workspace = _workspace(tmp_path)
    store = worker_evidence._Store(workspace, RUN)
    store.write(b"{}")
    with _Watch().watching() as watch:
        store.remove()

    assert watch.made() == [] and watch.flushed() == [] and watch.open_fds == set()
    assert not (workspace / ".dream" / "workers" / f"{RUN}.json").exists()


@pytest.mark.parametrize("failing", ["workspace", ".dream"])
def test_a_failed_ancestry_flush_keeps_the_record_in_memory_and_claims_no_handle(tmp_path, failing):
    workspace = _workspace(tmp_path)
    if failing == ".dream":          # .dream is there already, so the first ancestry flush is .dream's own
        _private_dir(workspace / ".dream")
    target = workspace if failing == "workspace" else workspace / ".dream"
    modes = {path: stat.S_IMODE(os.stat(path).st_mode) for path in (workspace, target)}

    evidence = worker_evidence.Evidence(RUN, "worker")
    evidence.bind(workspace)
    with _Watch(fail_fsync_on=_identity(target)).watching() as watch:
        seq = evidence.tool_started("call_1", "read_file")          # must not raise into the worker
        evidence.tool_ended(seq, "completed", "ALPHA-1 the file's first line")
        evidence.close("timeout")

    # The flush that failed was the ancestry one, straight after the one mkdir that happened; nothing was retried.
    made = ".dream" if failing == "workspace" else "workers"
    assert watch.made() == [made]
    assert watch.flushed() == [_identity(target)]
    assert watch.open_fds == set(), "every descriptor is closed on the failure path"
    assert watch.mode_changes == []
    assert {path: stat.S_IMODE(os.stat(path).st_mode) for path in modes} == modes

    # No record, and nothing that says there is one.
    assert evidence.handle is None
    assert evidence.unsaved == "the record could not be written (OSError)"
    assert evidence.summary()["persisted"] is False
    leftovers = [name for _dir, _subdirs, names in os.walk(workspace) for name in names]
    assert leftovers == [], "no record and no temporary file is left behind"
    if failing == "workspace":
        assert not (workspace / ".dream" / "workers").exists()

    # The run's own account is whole: the outcome is still in memory and in the block, which says it was not saved.
    assert evidence.counts["started"] == 1 and evidence.counts["completed"] == 1
    block = evidence.block()
    assert block.startswith("[worker incomplete] reason=timeout")
    assert "ALPHA-1" in block
    assert "handle:" not in block and worker_evidence.STATE_DIR[0] not in block
    assert "record: not saved (the record could not be written (OSError))" in block

    evidence.discard()          # nothing was written, so nothing is removed and nothing raises
    assert evidence.handle is None


def test_the_store_itself_raises_oserror_for_a_failed_ancestry_flush(tmp_path):
    """`_Store` reports the failure as the OSError its callers already turn into "not saved"."""
    workspace = _workspace(tmp_path)
    store = worker_evidence._Store(workspace, RUN)
    with _Watch(fail_fsync_on=_identity(workspace)).watching() as watch:
        with pytest.raises(OSError) as raised:
            store.write(b"{}")
    assert raised.value.errno == errno.EIO
    assert watch.open_fds == set()
    assert not (workspace / ".dream" / "workers").exists()
