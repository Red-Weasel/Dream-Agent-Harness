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


# --- fix list #89 (DREAM-119): PLAN.md unwritten for 2.5 hours while the model built phase 3 -------------
# With a PLAN.md in the workspace, every PLAN_NUDGE_AFTER tool calls that leave it unwritten add one line to
# the guard's note asking for an update; a write of PLAN.md (seen by its mtime, or the update_plan call
# itself) resets the count; no PLAN.md, no line.


@pytest.fixture
def plan(tmp_path):
    path = tmp_path / "PLAN.md"
    path.write_text("# Plan: Model car\n\n## ◐ 1. Body\n- ○ greenhouse\n")
    return path


PLAN_LINE = "Update PLAN.md with update_plan"   # the nudge's one line, counted


def _writes(guard, n):
    return [guard.observe("write_file", {"path": f"f{i}.js", "content": "x"}) for i in range(n)]


def test_the_twelfth_call_without_a_plan_write_asks_for_the_plan_once(tmp_path, plan):
    from dream.core.progress_guard import PLAN_NUDGE_AFTER
    assert PLAN_NUDGE_AFTER == 12
    g = ProgressGuard(after=3, workspace=tmp_path)
    assert _writes(g, 11) == [None] * 11                     # 11: not yet
    note = g.observe("write_file", {"path": "f.js", "content": "x"})
    assert note and note.startswith("[Dream progress guard]") and note.count(PLAN_LINE) == 1
    # The gate's wording (#93 twice refused a steps-less update_plan tonight): the line keeps the steps lists.
    assert "mark finished steps done" in note and "keep each phase's steps list" in note
    assert "give a done phase its summary" in note and "one line per phase" not in note
    assert "read-only" not in note                            # the writes were productive; only the plan is stale
    assert _writes(g, 11) == [None] * 11                     # at most once per 12 calls
    again = g.observe("write_file", {"path": "f.js", "content": "x"})
    assert again and again.count(PLAN_LINE) == 1
    assert plan.read_text().startswith("# Plan: Model car")  # the guard only asks; it never writes the plan


def test_a_write_of_plan_md_resets_the_count(tmp_path, plan):
    import os
    g = ProgressGuard(after=3, workspace=tmp_path)
    _writes(g, 11)
    plan.write_text("# Plan: Model car\n\n## ● 1. Body\n- ● greenhouse\n")
    stamp = plan.stat().st_mtime_ns + 5_000_000               # a later mtime even on a coarse-clock filesystem
    os.utime(plan, ns=(stamp, stamp))
    assert _writes(g, 11) == [None] * 11                     # the write is seen; 11 calls after it are not 12
    note = g.observe("write_file", {"path": "f.js", "content": "x"})
    assert note and "PLAN.md" in note


def test_the_update_plan_call_itself_is_the_write(tmp_path, plan):
    g = ProgressGuard(after=3, workspace=tmp_path)
    _writes(g, 11)
    assert g.observe("update_plan", {"phases": [{"name": "Body", "status": "done", "steps": []}]}) is None
    assert _writes(g, 11) == [None] * 11
    assert g.observe("write_file", {"path": "f.js", "content": "x"})


def test_no_plan_file_never_asks(tmp_path):
    g = ProgressGuard(after=100, workspace=tmp_path)
    assert _writes(g, 40) == [None] * 40
    (tmp_path / "PLAN.md").write_text("# Plan\n")           # created mid-turn: the count starts there
    assert _writes(g, 11) == [None] * 11
    assert g.observe("write_file", {"path": "f.js", "content": "x"})


def test_the_plan_line_is_one_line_of_a_read_only_note_when_both_are_due(tmp_path, plan):
    g = ProgressGuard(after=12, workspace=tmp_path)
    notes = [g.observe("read_file", {"path": "a"}) for _ in range(12)]
    assert notes[:11] == [None] * 11
    note = notes[11]
    assert "12 consecutive read-only steps" in note and note.count(PLAN_LINE) == 1
    assert note.count("[Dream progress guard]") == 1 and len(note.splitlines()) == 2
    assert g.fired == 1


def test_a_plan_only_nudge_is_counted_as_a_note_and_leaves_the_read_count_alone(tmp_path, plan):
    g = ProgressGuard(after=12, workspace=tmp_path)
    _writes(g, 12)
    assert g.fired == 1 and g.reads == 0


def test_zero_still_disables_everything(tmp_path, plan):
    g = ProgressGuard(after=0, workspace=tmp_path)
    assert _writes(g, 30) == [None] * 30
