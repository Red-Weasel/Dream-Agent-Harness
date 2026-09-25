"""Progress guard: notice a turn that only looks and never changes anything.

2026-09-21 22:09-23:04: ~27 model calls, three diagnostic scripts, nine frame views,
twenty file reads, no edit to the project, until the owner typed "Dont just inspect".
The guard counts consecutive read-only steps (read-only tools, and run_bash commands
with no consequential effect) since the last write or edit; at every `after` of them it
returns a short steering note the engine hands to the model. `after` = 0 disables it.

Fix list #89 (2026-09-25): PLAN.md went unwritten for 2.5 hours while the model built phase 3, so the
plan strip said "phase 1 of 6 · 0/15 steps"; the system prompt asks for a current PLAN.md and nothing
reminded the model. When the workspace has a PLAN.md, every PLAN_NUDGE_AFTER tool calls that leave it
unwritten (by its mtime, or the update_plan call itself) add one line to the guard's note asking for an
update -- on its own when no read-only note is due, so a productive build gets it too.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import policy

DEFAULT_AFTER = 12
ENV = "DREAM_PROGRESS_GUARD_AFTER"
PLAN_NUDGE_AFTER = 12   # tool calls since PLAN.md's last write before the note asks for an update
PLAN_FILE = "PLAN.md"
_READ_CAPS = frozenset({policy.READONLY, policy.MEMORY})


class ProgressGuard:
    def __init__(self, after: int = DEFAULT_AFTER, workspace: Path | str | None = None) -> None:
        self.after = max(0, int(after))
        self.workspace = Path(workspace or ".").resolve()
        self.reads = 0          # consecutive read-only steps since the last write
        self.fired = 0          # how many notes this turn
        self._next = self.after  # the read count that fires the next note
        self.plan_calls = 0     # tool calls since PLAN.md was last written (or first seen)
        self._plan_mtime: int | None = None

    @classmethod
    def from_env(cls, workspace: Path | str | None = None) -> "ProgressGuard":
        raw = os.environ.get(ENV, "")
        try:
            after = int(raw) if raw.strip() else DEFAULT_AFTER
        except ValueError:
            after = DEFAULT_AFTER
        return cls(after=after, workspace=workspace)

    def is_read_only(self, tool_name: str, tool_input: dict[str, Any] | None) -> bool:
        cap = policy.capability(tool_name)
        if cap in _READ_CAPS:
            return True
        if cap == policy.SHELL:
            return policy.shell_read_only(str((tool_input or {}).get("command") or ""))
        return False

    def observe(self, tool_name: str, tool_input: dict[str, Any] | None) -> str | None:
        """Count one tool call; return the steering note when the run of read-only
        steps reaches `after` (and again at every further `after`), or when PLAN.md
        has gone PLAN_NUDGE_AFTER calls unwritten (#89)."""
        if not self.after:
            return None
        plan = self._plan_line(tool_name)
        note = self._read_only_note(tool_name, tool_input)
        if plan:
            note = f"{note}\n{plan}" if note else f"[Dream progress guard] {plan}"
        if note:
            self.fired += 1
        return note

    def _read_only_note(self, tool_name: str, tool_input: dict[str, Any] | None) -> str | None:
        if not self.is_read_only(tool_name, tool_input):
            self.reads = 0
            self._next = self.after
            return None
        self.reads += 1
        if self.reads < self._next:
            return None
        # The first note waits `after` reads; later ones come every after//2 (2026-09-22: the reads
        # resumed right after the first note, with the diagnosis already written).
        self._next = self.reads + max(3, self.after // 2)
        return (f"[Dream progress guard] {self.reads} consecutive read-only steps and no change to a "
                "project file this turn. If the task calls for changes, make the next step an edit "
                "(str_replace_edit / write_file) or a check that produces one; if something blocks you, "
                "say what it is instead of reading more.")

    def _plan_line(self, tool_name: str) -> str | None:
        """One line asking for a PLAN.md update, every PLAN_NUDGE_AFTER calls that leave it unwritten."""
        try:
            mtime = (self.workspace / PLAN_FILE).stat().st_mtime_ns
        except OSError:
            self.plan_calls, self._plan_mtime = 0, None      # no plan (yet): nothing to keep current
            return None
        written = self._plan_mtime is not None and mtime != self._plan_mtime
        self._plan_mtime = mtime
        if tool_name == "update_plan":                       # this call writes the plan itself
            self.plan_calls = 0
            return None
        self.plan_calls = 1 if written else self.plan_calls + 1   # the first call after a write counts 1
        if self.plan_calls < PLAN_NUDGE_AFTER:
            return None
        self.plan_calls = 0
        # The wording keeps the steps lists: #93 twice refused a steps-less update_plan call the same night.
        return (f"PLAN.md has not changed in the last {PLAN_NUDGE_AFTER} tool calls. Update PLAN.md with "
                "update_plan: mark finished steps done, keep each phase's steps list, give a done phase its "
                "summary.")
