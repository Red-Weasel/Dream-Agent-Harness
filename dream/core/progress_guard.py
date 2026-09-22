"""Progress guard: notice a turn that only looks and never changes anything.

2026-09-21 22:09-23:04: ~27 model calls, three diagnostic scripts, nine frame views,
twenty file reads, no edit to the project, until the owner typed "Dont just inspect".
The guard counts consecutive read-only steps (read-only tools, and run_bash commands
with no consequential effect) since the last write or edit; at every `after` of them it
returns a short steering note the engine hands to the model. `after` = 0 disables it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import policy

DEFAULT_AFTER = 12
ENV = "DREAM_PROGRESS_GUARD_AFTER"
_READ_CAPS = frozenset({policy.READONLY, policy.MEMORY})


class ProgressGuard:
    def __init__(self, after: int = DEFAULT_AFTER, workspace: Path | str | None = None) -> None:
        self.after = max(0, int(after))
        self.workspace = Path(workspace or ".").resolve()
        self.reads = 0          # consecutive read-only steps since the last write
        self.fired = 0          # how many notes this turn
        self._next = self.after  # the read count that fires the next note

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
        steps reaches `after` (and again at every further `after`)."""
        if not self.after:
            return None
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
        self.fired += 1
        return (f"[Dream progress guard] {self.reads} consecutive read-only steps and no change to a "
                "project file this turn. If the task calls for changes, make the next step an edit "
                "(str_replace_edit / write_file) or a check that produces one; if something blocks you, "
                "say what it is instead of reading more.")
