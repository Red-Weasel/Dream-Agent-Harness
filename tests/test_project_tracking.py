"""Tracking must reject silent changes without requiring Dream or a model."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_project_tracking.py"
SPEC = importlib.util.spec_from_file_location("project_tracking", SCRIPT)
tracking = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tracking)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.when = datetime.now(timezone.utc) - timedelta(minutes=2)
        self.old = tracking.UPDATES + self.when.date().isoformat() + "-baseline.md"
        for name in ("START_HERE.md", "AGENTS.md", "README.md", "CLAUDE.md"):
            self.write(name, "# Entry\n\n[Current](docs/project/CURRENT.md)\n")
        for name in ("README.md", "WORKFLOW.md", "DECISIONS.md", "HISTORY.md", "templates/update.md"):
            self.write(tracking.HUB + name, "# Project documentation\n")
        self.write(tracking.MASTER, "# Plan\n\n| DREAM-001 | Test task | verified | next | None |\n")
        self.write(self.old, self.record(self.when, [self.old, tracking.CURRENT]))
        self.current(self.when, self.old)
        self.write("dream/example.txt", "old content\n")

    def write(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)

    def current(self, when, record, active="none"):
        self.write(tracking.CURRENT, f"# Current\n\nUpdated: `{when.isoformat()}`\n"
                   f"Current work: `{active}`\nLatest handoff: [Update]({Path(record).relative_to(tracking.HUB)})\n")

    def record(self, when, files):
        text = (f"# Test handoff\n\nRecorded: `{when.isoformat()}`\n"
                "Work items: `DREAM-001`\nOutcome: `verified`\nActor: Test contributor\n")
        for heading in tracking.SECTIONS:
            body = "\n".join(f"- `{p}`" for p in files) if heading == "Files changed" else "Observed fixture detail."
            text += f"\n## {heading}\n\n{body}\n"
        return text

    def init_git(self):
        def run(*args):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        run("init", "-q")
        run("add", ".")
        run("-c", "user.name=Tracking fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline")

    def test_valid_portable_handoff(self):
        errors, records = tracking.validate(self.root)
        self.assertEqual(errors, [])
        self.assertIn(self.old, records)

    def test_broken_local_link_is_rejected(self):
        self.write("START_HERE.md", "[Missing](docs/project/missing.md)\n")
        errors, _ = tracking.validate(self.root)
        self.assertTrue(any("broken/nonportable local link" in e for e in errors))

    def test_timezone_is_required(self):
        p = self.root / self.old
        p.write_text(p.read_text().replace(self.when.isoformat(), self.when.replace(tzinfo=None).isoformat()))
        errors, _ = tracking.validate(self.root)
        self.assertTrue(any("explicit timezone" in e for e in errors))

    def test_unknown_work_item_and_missing_validation_are_rejected(self):
        p = self.root / self.old
        original = p.read_text()
        p.write_text(original.replace("DREAM-001", "DREAM-999"))
        errors, _ = tracking.validate(self.root)
        self.assertTrue(any("unknown work IDs" in e for e in errors))
        p.write_text(original.replace("## Validation", "## Claimed success"))
        errors, _ = tracking.validate(self.root)
        self.assertTrue(any("section: Validation" in e for e in errors))

    def test_active_state_must_agree_with_master(self):
        self.current(self.when, self.old, active="DREAM-001")
        errors, _ = tracking.validate(self.root)
        self.assertTrue(any("exactly match active items" in e for e in errors))

    def test_latest_handoff_cannot_stay_stale(self):
        when = self.when + timedelta(seconds=30)
        name = tracking.UPDATES + when.date().isoformat() + "-next.md"
        self.write(name, self.record(when, [name]))
        errors, _ = tracking.validate(self.root)
        self.assertTrue(any("newest recorded update" in e for e in errors))

    def test_undocumented_change_and_reused_record_fail(self):
        _, records = tracking.validate(self.root)
        errors = tracking.check_diff({"dream/example.txt", self.old}, {self.old}, records)
        self.assertTrue(any("append-only" in e for e in errors))
        self.assertTrue(any("NEW dated update" in e for e in errors))
        self.assertTrue(any("must be updated" in e for e in errors))
        self.assertTrue(any("undocumented changed path: dream/example.txt" in e for e in errors))

    def test_deleted_history_cannot_be_hidden_by_a_new_record(self):
        new = tracking.UPDATES + "2026-09-05-new.md"
        changed = {self.old, new, tracking.CURRENT}
        errors = tracking.check_diff(changed, {self.old, tracking.CURRENT}, {new: changed})
        self.assertTrue(any("append-only" in e for e in errors))

    def test_documented_changes_pass_and_omitted_file_fails(self):
        new = tracking.UPDATES + "2026-09-05-new.md"
        changed = {new, tracking.CURRENT, "dream/new.py", "dream/removed.py"}
        previous = {self.old, tracking.CURRENT, "dream/removed.py"}
        self.assertEqual(tracking.check_diff(changed, previous, {new: changed}), [])
        errors = tracking.check_diff(changed, previous, {new: changed - {"dream/new.py"}})
        self.assertEqual(errors, ["undocumented changed path: dream/new.py"])

    def test_snapshot_tracks_untracked_deletions_but_excludes_runtime(self):
        self.init_git()
        before = tracking.snapshot(self.root)
        (self.root / "dream/example.txt").unlink()
        self.write("dream/new.txt", "new source\n")
        self.write("data/private.json", "private fixture\n")
        after = tracking.snapshot(self.root)
        changed = {n for n in before["files"].keys() | after["files"].keys()
                   if before["files"].get(n) != after["files"].get(n)}
        self.assertEqual(changed, {"dream/example.txt", "dream/new.txt"})
        self.assertNotIn("data/private.json", after["files"])

    def test_cli_commit_comparison_rejects_missing_handoff(self):
        self.write("scripts/check_project_tracking.py", SCRIPT.read_text())
        self.init_git()
        self.write("dream/example.txt", "undocumented change\n")
        result = subprocess.run([os.sys.executable, str(self.root / "scripts/check_project_tracking.py"),
                                 "check", "--base", "HEAD"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("undocumented changed path: dream/example.txt", result.stderr)

    def test_ci_base_uses_event_shas_and_handles_initial_push(self):
        event = self.root / "event.json"
        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)}):
            event.write_text(json.dumps({"pull_request": {"base": {"sha": "a" * 40}}}))
            self.assertEqual(tracking.ci_base(), "a" * 40)
            event.write_text(json.dumps({"before": "b" * 40}))
            self.assertEqual(tracking.ci_base(), "b" * 40)
            event.write_text(json.dumps({"before": "0" * 40}))
            self.assertIsNone(tracking.ci_base())
            event.write_text(json.dumps({"before": "--not-a-revision"}))
            with self.assertRaises(ValueError):
                tracking.ci_base()


if __name__ == "__main__":
    unittest.main()
