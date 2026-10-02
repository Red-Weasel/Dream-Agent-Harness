"""DREAM-205: tests keep their inference leases in a private directory, never in the owner's live one.

Dream's local request leases live in `/tmp/dream-inference-<uid>` (dream/core/inference_coordination.py). A test that
lets a backend take a lease there -- a fake engine on a loopback port and no `_coordination_override` -- reads and
rewrites the records of the Dream that is running on this computer, and can make it think a lane is free or taken.

Two rails, both in the product's resolver `inference_coordination.lease_root`:

- `DREAM_INFERENCE_LEASE_DIR` names the directory (an absolute path, opened with the same private-folder rules).
- Under pytest (`PYTEST_CURRENT_TEST` or `PYTEST_VERSION` in the environment) with nothing set, the default is a
  private per-process directory under the system temp folder, never the live one, removed on a normal exit.

This module adds the per-test rail: import its fixture into a test module

    from lease_isolation import isolated_lease_dir  # noqa: F401

and every EndpointCoordinator made without an explicit root during that module's tests lives under the test's own
`tmp_path` (a fresh, empty folder per test, so no record of one test reaches another). `tests/test_lease_isolation.py`
guards the real folder itself.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# The env var's name as a literal: the fixture must load on a tree whose product does not know it yet, so that the
# guard test fails on the product's behaviour rather than on an import.
LEASE_DIR_ENV = "DREAM_INFERENCE_LEASE_DIR"
REAL_LEASE_DIR = Path("/tmp") / f"dream-inference-{os.getuid()}"


def real_lease_listing() -> tuple[int, dict[str, tuple[int, int]]] | None:
    """The live folder as it is now -- its mtime and every entry's (mtime_ns, size) -- or None when it does not exist.
    Two equal listings mean nothing in it was created, rewritten or removed in between."""
    if not REAL_LEASE_DIR.exists():
        return None
    entries = {}
    for path in REAL_LEASE_DIR.iterdir():
        info = path.stat()
        entries[path.name] = (info.st_mtime_ns, info.st_size)
    return REAL_LEASE_DIR.stat().st_mtime_ns, entries


def process_start(pid="self") -> str:
    """A process's start time, /proc/<pid>/stat field 22 (clock ticks after boot), read here independently of Dream."""
    with open(f"/proc/{pid}/stat") as stat_file:
        return stat_file.read().rsplit(")", 1)[1].split()[19]


def pid_namespace(pid="self") -> int:
    """A process's PID namespace, the inode of /proc/<pid>/ns/pid, read here independently of Dream."""
    return os.stat(f"/proc/{pid}/ns/pid").st_ino


def per_process_name(prefix: str) -> str:
    """The private folder name of this test process (DREAM-206 gate, rounds 2 and 3):
    <prefix>-<uid>-pytest-<pid>-<start>-<pid namespace>. The start time makes it unique beyond the pid, which later
    processes reuse; the namespace, beyond processes started together in other PID namespaces, which share both."""
    return f"{prefix}-{os.getuid()}-pytest-{os.getpid()}-{process_start()}-{pid_namespace()}"


def assert_private_lease_root(root: Path, inside: Path | None = None) -> None:
    """Called before a guard takes any lease (DREAM-205 gate, round 2): `root`, resolved so that no `..` or link can
    alias it, is neither the live folder nor inside it, and lies under `inside` when given. A broken rail -- or a tree
    without the resolver -- then fails the test before a byte reaches the live folder."""
    real, live = Path(os.path.realpath(root)), Path(os.path.realpath(REAL_LEASE_DIR))
    assert real != live and live not in real.parents, f"a lease would use the live folder: {root} ({real})"
    if inside is not None:
        base = Path(os.path.realpath(inside))
        assert real == base or base in real.parents, f"a lease would use {root} ({real}), outside {inside}"


@pytest.fixture(autouse=True)
def isolated_lease_dir(tmp_path, monkeypatch) -> Path:
    """Every lease this test takes without an explicit root goes under tmp_path."""
    root = tmp_path / "inference-leases"
    monkeypatch.setenv(LEASE_DIR_ENV, str(root))
    return root


# DREAM-206: the local model launch lock (dream/local/load_lock.py), resolved the same way. Under pytest with nothing
# set it is already a private folder of each process; a module whose test and its child processes must share one lock
# (a second launcher refused, say) imports this fixture: the folder is under the test's tmp_path, and a child started
# with this environment inherits it.
LOAD_LOCK_DIR_ENV = "DREAM_MACHX_LOAD_LOCK_DIR"


@pytest.fixture(autouse=True)
def isolated_load_lock_dir(tmp_path, monkeypatch) -> Path:
    """The launch lock this test and its children take lives under tmp_path, never in the owner's /tmp."""
    root = tmp_path / "load-lock"
    monkeypatch.setenv(LOAD_LOCK_DIR_ENV, str(root))
    return root
