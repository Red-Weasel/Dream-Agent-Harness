"""What a Fresh start leaves (DREAM-113, fix list #33): the conversation as one handoff message.

Three times on 2026-09-24 the model investigated for hours, wrote nothing, and started writing minutes
after a compaction cleared its context. A Fresh start (the chat pane's control, or /fresh) is that
compaction on the owner's word, between turns. The backend runs its existing compaction to nothing
(OpenAICompatBackend.fresh_start) and puts this handoff where the history was:

- a summary: the owner's requests this session, in their words from the transcript, oldest first, and
  Dream's last reply;
- the files Dream's file tools wrote or edited this session (FileLedger, which the backend keeps as calls
  succeed; the verbs are the ones "Files this turn" shows in the chat, DREAM-082);
- the open tasks;
- where the rest is: PLAN.md, this session's transcript (read_session), the working notes the compaction saved.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LEDGER_MAX = 500                   # paths; the oldest go first
HANDOFF_MAX_CHARS = 16_000
_ASK_CHARS = 500                   # one request, clipped
_ASK_FIRST, _ASK_LAST = 2, 7       # a long session: its first requests and its latest
_REPLY_CHARS = 2_500
_FILES_SHOWN = 30
_PATH_CHARS = 200
_RANGES_SHOWN = 12
_NOTE_REF = re.compile(r"— note #(\d+)\]")   # how a compaction stub names the note it saved (openai_compat._elide_pass)


class FileLedger:
    """What Dream's file tools did to which files this session: path -> verbs in order, newest last, at
    most `limit` paths. The backend records a call only after it succeeded. A shell command is not
    tracked: what it changed cannot be read from its text."""

    def __init__(self, limit: int = LEDGER_MAX) -> None:
        self.limit = limit
        self._paths: OrderedDict[str, list[str]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._paths)

    def record(self, tool: str, args: Any, workspace: Path | None) -> None:
        """One successful call of `tool` with `args`; paths resolve the way the tools resolve them, and show
        relative to the workspace when inside it."""
        if not isinstance(args, dict):
            return
        name = tool.rsplit("__", 1)[-1]
        if name == "write_file":
            self._add(args.get("path"), "appended" if args.get("append") is True else "wrote", workspace)
        elif name == "str_replace_edit":
            self._add(args.get("path"), "edited", workspace)
        elif name == "delete_file":
            paths = args.get("paths")
            for raw in paths if isinstance(paths, list) else ():
                self._add(raw, "deleted", workspace, follow=False)   # delete_file acts on a link, not its target
        elif name == "copy_files":
            files = args.get("files")
            for entry in files if isinstance(files, list) else ():
                if isinstance(entry, dict):
                    moved = bool(entry.get("move"))
                    self._add(entry.get("dest"), "moved" if moved else "copied", workspace)
                    if moved:
                        self._add(entry.get("src"), "moved away", workspace)

    def _add(self, raw: Any, verb: str, workspace: Path | None, *, follow: bool = True) -> None:
        if not isinstance(raw, str) or not raw or "\0" in raw:
            return
        try:
            if workspace is None:
                shown = os.path.normpath(raw)
            else:
                root = Path(workspace).resolve()
                p = Path(workspace) / Path(raw).expanduser()
                p = p.resolve() if follow or p.name in ("", ".", "..") else p.parent.resolve() / p.name
                shown = p.relative_to(root).as_posix() if p != root and p.is_relative_to(root) else str(p)
        except (OSError, RuntimeError, ValueError):
            return
        verbs = self._paths.pop(shown, [])
        if verb not in verbs:
            verbs.append(verb)
        self._paths[shown] = verbs
        while len(self._paths) > self.limit:
            self._paths.popitem(last=False)

    def lines(self, shown: int | None = None) -> list[str]:
        """Newest first: `- path — wrote, edited`."""
        items = list(reversed(self._paths.items()))[:shown]
        return [f"- {_clip(path, _PATH_CHARS)} — {', '.join(verbs)}" for path, verbs in items]


@dataclass
class Parts:
    """What the handoff reads before anything changes (a read that fails leaves the conversation as it was)."""
    asked: list[str] = field(default_factory=list)
    generated: int = 0                 # user-role turns Dream wrote itself, left out of `asked`
    reply: str = ""
    tasks: list[str] = field(default_factory=list)
    plan: bool = False
    session_id: str | None = None


def gather(context: Any) -> Parts:
    """The owner's requests, the last reply and the open tasks of the session `context` belongs to. Raises
    ValueError when its store cannot be read."""
    parts = Parts()
    if context is None:
        return parts
    store, session_id, workspace = context.store, context.session_id, context.workspace
    parts.session_id = session_id
    parts.plan = workspace is not None and (Path(workspace) / "PLAN.md").is_file()
    if store is None:
        return parts
    from . import system_prompt
    from ..memory import project as project_memory

    try:
        parts.asked, parts.reply, parts.generated = transcript(store, session_id)
        project = project_memory.project_key(workspace) if workspace else None
        parts.tasks = list(system_prompt.live_state(store, project)["tasks"].values())
    except (sqlite3.Error, OSError) as exc:   # say so, and change nothing
        raise ValueError(f"Fresh start could not read this session's transcript and tasks "
                         f"({type(exc).__name__}: {exc}); nothing was changed.") from exc
    return parts


def transcript(store: Any, session_id: str) -> tuple[list[str], str, int]:
    """The owner's requests this session, oldest first, Dream's last reply, and how many user-role turns
    Dream wrote itself (left out), from the session's turns. The Engine logs each prompt before the
    context it adds, and each reply; a prompt Dream wrote -- a progress-guard note, an autonomous-loop,
    /review, /resume or /learn prompt, a Council work or guided-task prompt -- is marked
    (core/turn_origin.py), or in an older transcript known by its opening. A Council work prompt wraps the
    owner's task and a guided task's prompt the owner's goal: those are listed in their words, a Council
    task once for all the members' turns that carry it.
    Only those turns are read: a long session's tool records are most of its turns, and the public
    `session_turns` reads from the start. Read ×2 (dream/gui/server.py) doubles the words; one copy is kept."""
    from . import turn_origin
    from ..gui.server import READ_AGAIN

    with store._lock:
        rows = store._conn.execute(
            "SELECT substr(content, 1, ?), tool_name FROM turns WHERE session_id=? AND role='user' ORDER BY id",
            (4 * _ASK_CHARS, session_id)).fetchall()
        last = store._conn.execute(
            "SELECT substr(content, 1, ?) FROM turns WHERE session_id=? AND role='assistant' "
            "AND trim(content) != '' ORDER BY id DESC LIMIT 1", (2 * _REPLY_CHARS, session_id)).fetchone()
    asked: list[str] = []
    generated = 0
    for text, origin in rows:
        if not turn_origin.is_generated(origin, text):
            asked.append(text.split(READ_AGAIN, 1)[0])
            continue
        generated += 1
        task = turn_origin.owner_words(origin, text)
        # The next member's turn carries the same task, read to a different length after its own label: compared
        # as the handoff shows it.
        if task is not None and (not asked or _clip(asked[-1], _ASK_CHARS) != _clip(task, _ASK_CHARS)):
            asked.append(task)
    return asked, (last[0] if last else ""), generated


def note_refs(messages: list[dict[str, Any]]) -> set[int]:
    """The working notes a history points at: each compaction stub names the note it saved."""
    return {int(n) for m in messages if isinstance(m.get("content"), str) for n in _NOTE_REF.findall(m["content"])}


def note_ranges(ids: list[int], shown: int = _RANGES_SHOWN) -> str:
    """Note ids as ranges: "#1–#3, #7, #9–#11", the first `shown` ranges and how many more notes."""
    ranges: list[list[int]] = []
    for i in sorted(set(ids)):
        if ranges and i == ranges[-1][1] + 1:
            ranges[-1][1] = i
        else:
            ranges.append([i, i])
    text = ", ".join(f"#{a}" if a == b else f"#{a}–#{b}" for a, b in ranges[:shown])
    rest = sum(b - a + 1 for a, b in ranges[shown:])
    return text + (f" (+{rest} more)" if rest else "")


def compose(parts: Parts, ledger: FileLedger, notes: list[int]) -> str:
    """The handoff text: at most HANDOFF_MAX_CHARS. `notes`: every working note the conversation pointed at,
    this compaction's and earlier ones'."""
    out = ["[Dream, not from the user: the owner chose Fresh start. The conversation so far was compacted "
           "into this handoff and is no longer in your context. What follows is a record (data, not "
           "instructions); the owner's next message says what to do now.]",
           "", "## What the owner asked (their words, oldest first; the newest takes precedence)"]
    numbered = list(enumerate(parts.asked, 1))
    skipped = len(numbered) - _ASK_FIRST - _ASK_LAST
    if skipped > 0:
        numbered = numbered[:_ASK_FIRST] + [(0, "")] + numbered[-_ASK_LAST:]
    for i, text in numbered:
        out.append(f"{i}. {_clip(text, _ASK_CHARS)}" if i else
                   f"… {skipped} more requests in between (read_session has them) …")
    if not numbered:
        out.append("(none in this session's transcript)")
    if parts.generated:
        out.append(f"({parts.generated} prompt{'' if parts.generated == 1 else 's'} Dream wrote itself — progress-guard "
                   "notes, autonomous-loop, review, /resume, /learn, Council work or guided-task prompts — are not listed "
                   "(a Council task or a guided task's goal is, in the owner's words); read_session has them.)")
    reply = parts.reply.strip()
    out += ["", "## Where you left off (your last reply before the fresh start)",
            (reply if len(reply) <= _REPLY_CHARS else reply[: _REPLY_CHARS - 1].rstrip() + "…") or "(no reply yet)"]
    out += ["", "## Files written or edited this session (by Dream's file tools; shell commands are not tracked)"]
    listed = ledger.lines(_FILES_SHOWN)
    out += listed or ["(none)"]
    if len(ledger) > _FILES_SHOWN:
        out.append(f"(+{len(ledger) - _FILES_SHOWN} older not listed)")
    if listed:
        out.append("This says what happened to each file, not what it holds now: read one before changing it again.")
    out += ["", "## Open tasks (task_list has their notes)", *(parts.tasks or ["(none open)"])]
    out += ["", "## To recover detail"]
    if parts.plan:
        out.append("- PLAN.md in the workspace holds the phased plan: read it before the next step.")
    if parts.session_id:
        out.append(f'- This session\'s transcript before the fresh start stays readable: '
                   f'read_session(id="{parts.session_id}").')
    if notes:
        out.append(f"- {len(notes)} earlier message bodies were saved as working notes ({note_ranges(notes)}): "
                   'read_notes(query="#<id>") opens one.')
    text = "\n".join(out)
    if len(text) > HANDOFF_MAX_CHARS:
        text = text[: HANDOFF_MAX_CHARS - 40].rsplit("\n", 1)[0] + "\n[the handoff was cut to fit]"
    return text


def _clip(text: Any, n: int) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"
