"""Working memory: per-session short-term scratch + a durable transcript log.

Bound to one session. Notes are quick things the agent jots while thinking; the
transcript is the full episodic record, written both to the DB (for recall) and to a
``data/sessions/<id>.jsonl`` file (for grepping and replay).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

from .. import config
from .store import MemoryStore


class WorkingMemory:
    def __init__(self, store: MemoryStore, session_id: str):
        self.store = store
        self.session_id = session_id
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path = config.SESSIONS_DIR / f"{session_id}.jsonl"

    def note(self, text: str) -> int:
        """Jot a short-term working note (survives only as scratch until consolidation)."""
        return self.store.add_note(self.session_id, text)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        return self.store.search_notes(self.session_id, query, limit)

    def log_turn(
        self, role: str, content: str, tool_name: str | None = None, *,
        on_commit: Callable[[int], None] | None = None,
    ) -> None:
        """Record a turn; notify internal capture after DB commit, before JSONL."""
        turn_id = self.store.add_turn(self.session_id, role, content, tool_name)
        if on_commit is not None:
            if type(turn_id) is not int or turn_id <= 0:
                raise RuntimeError("Turn commit did not provide a valid receipt")
            on_commit(turn_id)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "role": role,
            "tool": tool_name,
            "content": content,
        }
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def notes(self, only_unconsolidated: bool = True) -> list[dict]:
        return self.store.session_notes(self.session_id, only_unconsolidated)
