"""Checkpoint tools: the agent's hands on its own undo.

Every file Dream is about to write gets snapshotted first (``core.checkpoints``),
in Dream's own ``var/`` rather than the user's git. These let Dream offer the rollback
itself after a bad edit instead of leaving them to reconstruct what changed.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ..core import checkpoints
from .context import err, in_thread, ok


def _store() -> checkpoints.CheckpointStore:
    return checkpoints.default_store()


_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "description": "How many recent checkpoints (default 8)."}
    },
    "required": [],
}


@tool(
    "checkpoint_list",
    "List recent file checkpoints — the snapshot taken before each set of edits you "
    "made, newest first. Read this before offering a rollback so you can name the "
    "right checkpoint id and say which files it covers.",
    _LIST_SCHEMA,
)
async def checkpoint_list(args: dict[str, Any]) -> dict[str, Any]:
    rows = await in_thread(_store().list, int(args.get("limit", 8)))
    if not rows:
        return ok("No checkpoints recorded.")
    lines = ["Recent checkpoints (newest first):"]
    for r in rows:
        sealed = "" if r["sealed"] else " [unsealed — turn didn't finish]"
        lines.append(f"\n• {r['id']} — {r['label'] or '(no label)'} ({r['created_at']}){sealed}")
        names = [p.rsplit("/", 1)[-1] for p in r["paths"]]
        shown = ", ".join(names[:6]) + (f" +{len(names) - 6} more" if len(names) > 6 else "")
        lines.append(f"  {r['files']} file(s): {shown}")
    return ok("\n".join(lines))


_RESTORE_SCHEMA = {
    "type": "object",
    "properties": {
        "checkpoint_id": {
            "type": "string",
            "description": "Checkpoint id from checkpoint_list, e.g. '0007'.",
        },
        "force": {
            "type": "boolean",
            "description": "Overwrite files that changed after the snapshot, losing "
            "those changes. They may be the user's own edits — ask before using this.",
        },
    },
    "required": ["checkpoint_id"],
}


@tool(
    "checkpoint_restore",
    "Roll files back to the state they were in before a checkpoint — the undo for an "
    "edit that went wrong. Reports exactly what was restored, deleted, and skipped: a "
    "file that changed after the snapshot is skipped rather than clobbered, since the "
    "change may be the user's.",
    _RESTORE_SCHEMA,
)
async def checkpoint_restore(args: dict[str, Any]) -> dict[str, Any]:
    try:
        report = await in_thread(
            _store().restore, args["checkpoint_id"], bool(args.get("force", False))
        )
    except KeyError as e:
        return err(str(e))
    return ok(report.summary())
