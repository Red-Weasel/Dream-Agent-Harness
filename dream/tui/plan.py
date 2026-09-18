"""The Claude-Code-style tasks panel — the checklist the user sparked on from their
screenshot. Fed by ``TodoWrite`` tool calls, it holds the current plan and renders
three surfaces: a full ``panel`` (activity line + checklist) shown above the prompt
on ``ctrl+t``, a collapsed ``strip`` for the footer, and a ``tree`` for ``/plan``.

Pure data + self-contained ``rich`` renderables (imports ``rich`` directly, never
``dream.tui.render``), so it's testable in isolation and the orchestrator just wires
events in and drops the renderables into the cockpit.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text
from rich.tree import Tree

# Brand palette — the atom logo's violet→cyan orbit, mirrored from render.py so this
# module stays decoupled (imports rich directly, per the plan).
_VIOLET = "#a78bfa"
_PURPLE = "#8b5cf6"
_BLUE = "#60a5fa"
_CYAN = "#22d3ee"

_DONE, _DOING, _PENDING = "completed", "in_progress", "pending"

# Glyphs, matching the screenshot: a filled square marks the live task, an open one
# the work still ahead, a check the work behind.
_GLYPH = {_DONE: "✔", _DOING: "■", _PENDING: "□"}


def _ellipsize(s: str, limit: int) -> str:
    s = " ".join((s or "").split())
    if limit <= 1:
        return s[:limit]
    return s if len(s) <= limit else s[: limit - 1] + "…"


class PlanTracker:
    """Holds the agent's current todo list and renders it three ways."""

    def __init__(self) -> None:
        self._tasks: list[dict[str, str]] = []

    # --- ingest --------------------------------------------------------------

    def ingest(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Absorb a ``TodoWrite`` (Claude path) call into the current plan. Reads
        ``tool_input["todos"]`` — a list of ``{content, status, activeForm?}`` — and
        tolerates the future local ``plan``-tool shape (same fields, ``plan``/``tasks``
        key). Silently ignores anything that isn't a todo list."""
        name = (tool_name or "").lower()
        payload = tool_input or {}
        raw: Any = None
        for key in ("todos", "plan", "tasks"):
            if isinstance(payload.get(key), list):
                raw = payload[key]
                break
        if raw is None:
            # Not a recognised shape — only act on a plan/todo-flavoured tool so a
            # stray call can never wipe the board.
            if "todo" not in name and "plan" not in name:
                return
            return

        tasks: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            # TodoWrite says {content, status}; the local update_todos tool says
            # {name, completed}. Same checklist, two spellings.
            content = str(item.get("content") or item.get("name") or "").strip()
            if not content:
                continue
            if "status" in item or "completed" not in item:
                status = str(item.get("status") or _PENDING).strip().lower()
            else:
                status = "completed" if item.get("completed") is True else _PENDING
            if status not in _GLYPH:
                status = _PENDING
            tasks.append(
                {
                    "content": content,
                    "status": status,
                    "activeForm": str(item.get("activeForm") or "").strip(),
                }
            )
        self._tasks = tasks

    def clear(self) -> None:
        """Drop the plan — the orchestrator calls this once every task is done."""
        self._tasks = []

    # --- queries -------------------------------------------------------------

    def has_tasks(self) -> bool:
        return bool(self._tasks)

    def _active_task(self) -> dict[str, str] | None:
        for t in self._tasks:
            if t["status"] == _DOING:
                return t
        return None

    def active(self) -> str:
        """The in-progress task's content, or ``""`` when nothing is live."""
        t = self._active_task()
        return t["content"] if t else ""

    def _counts(self) -> tuple[int, int]:
        done = sum(1 for t in self._tasks if t["status"] == _DONE)
        return done, len(self._tasks)

    # --- renderables ---------------------------------------------------------

    def _item_line(self, task: dict[str, str], width: int) -> Text:
        """One checklist row: glyph + content, styled by status."""
        status = task["status"]
        glyph = _GLYPH[status]
        content = _ellipsize(task["content"], max(width - 2, 8))
        line = Text()
        if status == _DONE:
            line.append(f"{glyph} ", style="dim green")
            line.append(content, style="strike dim")
        elif status == _DOING:
            line.append(f"{glyph} ", style=f"bold {_VIOLET}")
            line.append(content, style=f"bold {_VIOLET}")
        else:
            line.append(f"{glyph} ", style="dim")
            line.append(content, style="dim")
        return line

    def panel(self, width: int) -> Group:
        """The checklist: an activity line for the current task, then every item —
        ``✔`` done (dim, struck-through), ``■`` in-progress (bold violet), ``□``
        pending (muted). Returns an empty group when there's no plan."""
        rows: list[Any] = []
        if not self._tasks:
            return Group(*rows)
        active = self._active_task()
        if active is not None:
            label = active["activeForm"] or active["content"]
            head = Text()
            head.append("⏵ ", style=f"bold {_VIOLET}")
            head.append(_ellipsize(label, max(width - 2, 8)), style=f"italic {_CYAN}")
            rows.append(head)
        for task in self._tasks:
            rows.append(self._item_line(task, width))
        return Group(*rows)

    def strip(self) -> Text:
        """The collapsed one-liner for the footer: ``plan ▸ 2/5 <active>``. Empty
        when there's no plan."""
        t = Text()
        if not self._tasks:
            return t
        done, total = self._counts()
        t.append("plan", style="dim")
        t.append(" ▸ ", style=_VIOLET)
        t.append(f"{done}/{total}", style=f"bold {_CYAN}")
        active = self.active()
        if active:
            t.append(" ")
            t.append(_ellipsize(active, 40), style="dim")
        return t

    def tree(self) -> Tree:
        """The full plan for ``/plan``: a rooted tree with a per-task glyph leaf."""
        done, total = self._counts()
        root = Tree(
            Text.assemble(
                ("plan", f"bold {_VIOLET}"),
                ("  ", ""),
                (f"{done}/{total}", f"bold {_CYAN}"),
            ),
            guide_style=_PURPLE,
        )
        if not self._tasks:
            root.add(Text("no tasks", style="dim italic"))
            return root
        for task in self._tasks:
            root.add(self._item_line(task, 10_000))
        return root
