"""Private root memory must not hide Python source from the Git-backed snapshot."""

import importlib.util
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "source_inventory_tracking", ROOT / "scripts/check_project_tracking.py",
)
tracking = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tracking)

PRIVATE = (
    "memory/semantic/private.md", "memory/procedural/private.md",
    "memory/private.json", "data/dream.db", "var/private.json",
    ".dream/private.json", ".remember/private.json",
)


@pytest.fixture
def source_tree(tmp_path, monkeypatch):
    subprocess.run(
        ["git", "init", "-q", "--template=", str(tmp_path)],
        check=True, capture_output=True, timeout=10,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "core.excludesFile", "/dev/null"],
        check=True, capture_output=True, timeout=10,
    )
    (tmp_path / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    for name in ("dream/memory/tasks.py", "dream/memory/future_module.py", *PRIVATE):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic fixture only\n")

    real_git = tracking.git

    def git_without_commit(root, *args):
        # Inventory and hashing are real. No stage or commit is needed for HEAD.
        if args == ("rev-parse", "HEAD"):
            return b"synthetic-uncommitted-head\n"
        return real_git(root, *args)

    monkeypatch.setattr(tracking, "git", git_without_commit)
    return tmp_path


def test_current_and_future_memory_modules_are_inventoried(source_tree):
    files = tracking.snapshot(source_tree)["files"]
    assert "dream/memory/tasks.py" in files
    assert "dream/memory/future_module.py" in files


def test_memory_source_mutation_changes_snapshot(source_tree):
    name = "dream/memory/tasks.py"
    before = tracking.snapshot(source_tree)["files"]
    (source_tree / name).write_text("changed synthetic source\n")
    after = tracking.snapshot(source_tree)["files"]
    assert name in before and name in after
    assert before[name] != after[name]
    assert {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)} == {name}


def test_private_root_files_remain_ignored_and_out_of_snapshot(source_tree):
    files = tracking.snapshot(source_tree)["files"]
    assert set(PRIVATE).isdisjoint(files)
    ignored = subprocess.run(
        ["git", "-C", str(source_tree), "check-ignore", "-z", "--stdin"],
        input=b"\0".join(name.encode() for name in PRIVATE) + b"\0",
        check=True, capture_output=True, timeout=10,
    ).stdout
    assert {name.decode() for name in ignored.split(b"\0") if name} == set(PRIVATE)
