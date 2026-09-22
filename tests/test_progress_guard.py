"""Progress guard: 55 minutes of reads and no product edit on 2026-09-21 until the
owner typed 'Dont just inspect'. The harness says it first."""
from __future__ import annotations
import pytest
from dream.core.progress_guard import ProgressGuard


@pytest.fixture
def guard(tmp_path):
    return ProgressGuard(after=3, workspace=tmp_path)


def test_fires_on_the_nth_consecutive_read_only_step(guard):
    assert guard.observe("read_file", {"path": "a.js"}) is None
    assert guard.observe("list_dir", {"path": "."}) is None
    note = guard.observe("grep", {"pattern": "x"})
    assert note and "3 consecutive read-only" in note and "no change to a project file" in note


def test_rearms_after_another_n_steps(guard):
    for _ in range(3):
        guard.observe("read_file", {"path": "a.js"})
    assert guard.observe("read_file", {"path": "a.js"}) is None
    assert guard.observe("read_file", {"path": "a.js"}) is None
    assert guard.observe("read_file", {"path": "a.js"})  # 6th


def test_a_write_resets_the_count(guard):
    guard.observe("read_file", {"path": "a.js"})
    guard.observe("read_file", {"path": "a.js"})
    assert guard.observe("write_file", {"path": "a.js", "content": "x"}) is None
    assert guard.observe("read_file", {"path": "a.js"}) is None
    assert guard.observe("read_file", {"path": "a.js"}) is None
    assert guard.observe("str_replace_edit", {"path": "a.js", "old_string": "a", "new_string": "b"}) is None
    assert guard.observe("read_file", {"path": "a.js"}) is None


def test_read_only_shell_counts_but_a_shell_write_resets(guard):
    guard.observe("run_bash", {"command": 'cd "/p" && ls -la src | head -40'})
    guard.observe("run_bash", {"command": "grep -n foo src/a.js | head"})
    assert guard.observe("run_bash", {"command": "sed -n '1,40p' src/a.js"})
    guard.observe("run_bash", {"command": "sed -i 's/a/b/' src/a.js"})
    assert guard.observe("read_file", {"path": "a.js"}) is None
    assert guard.observe("read_file", {"path": "a.js"}) is None
    assert guard.observe("read_file", {"path": "a.js"})


def test_zero_disables(tmp_path):
    g = ProgressGuard(after=0, workspace=tmp_path)
    assert all(g.observe("read_file", {"path": "a"}) is None for _ in range(50))


def test_default_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAM_PROGRESS_GUARD_AFTER", raising=False)
    assert ProgressGuard.from_env(tmp_path).after == 12
    monkeypatch.setenv("DREAM_PROGRESS_GUARD_AFTER", "5")
    assert ProgressGuard.from_env(tmp_path).after == 5


@pytest.mark.parametrize("command,read_only", [
    ('cd "/p" && ls -la src | head -40', True),
    ("grep -n foo src/a.js | head", True),
    ("sed -n '1,40p' src/a.js", True),
    ("awk 'NR>=10 && NR<=20 {print}' a.js", True),
    ("git status && git diff --stat", True),
    ("sed -i 's/a/b/' src/a.js", False),
    ("perl -pi -e 's/a/b/' a.js", False),
    ("echo x > out.txt", False),
    ("python3 scripts/render.py", False),
    ("python3 - <<'EOF'\nprint(1)\nEOF", False),
    ("rm -rf build", False),
    ("git commit -am x", False),
])
def test_shell_read_only_classifier(command, read_only):
    from dream.core.policy import shell_read_only
    assert shell_read_only(command) is read_only


def test_the_second_note_comes_sooner(tmp_path):
    g = ProgressGuard(after=12, workspace=tmp_path)
    fired = [i for i in range(1, 31) if g.observe("read_file", {"path": "a"})]
    assert fired == [12, 18, 24, 30]
