"""The Dream REPL: a streaming terminal chat with slash-commands and a status bar."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import html as _html
import json
import select
import shutil
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.text import Text

import re

from .. import config
from ..core import effort as effort_mod
from ..core import checkpoints, instructions, policy
from ..core.engine import Engine
from ..core.backends.base import Event
from ..tools.widgets import CARD_TOOL_NAMES, end_requested, reset_end
from ..core.friction import FrictionMeter
from ..memory import longterm
from ..memory.store import slugify
from ..telemetry import GpuSampler, InferenceMeter
from . import monitor as monitor_mod
from .mindmap import MindMap
from .plan import PlanTracker
from .council import CouncilControls, CouncilRequest
from .render import (
    _BLUE,
    _CYAN,
    _VIOLET,
    Renderer,
    build_footer,
    pt_gradient_rule,
    summarize_tool_input,
)

# Slugs a recall tool-result reports (e.g. "(slug: user-loves-fast-local-inference, …)").
_RECALL_SLUG_RE = re.compile(r"slug:\s*([a-z0-9][a-z0-9-]*)")

# /review severity → color. critical/high wear the renderer's alarm colors; the
# rest stay in the brand's violet→cyan orbit so a low finding doesn't shout.
_SEVERITY_COLOR = {
    "critical": "bold red",
    "high": "yellow",
    "medium": _VIOLET,
    "low": _BLUE,
    "unknown": _CYAN,
}


def _first_line(text: str) -> str:
    """The first non-blank line, clipped — a checkpoint label you can recognise in
    a list without it wrapping the row."""
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:70]


def _effort_label(level: str) -> str:
    """Retain native labels such as low alongside the legacy terminal ladder."""
    return effort_mod.describe(level) if effort_mod.normalize(level) is not None else str(level)


def _age(iso: str) -> str:
    """How long ago a checkpoint was taken — its id says nothing about recency."""
    try:
        secs = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return iso or "?"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "just now"


# The prompt line, dressed in the wordmark's violet end of the gradient.
PROMPT = HTML('<style fg="#a78bfa"><b>you</b></style> <style fg="#8b5cf6">›</style> ')


def _own_cancel() -> bool:
    """Was this CancelledError the targeted Ctrl-C below (True), or is the task
    running this code itself being torn down (False)? A cancel requested ON the
    current task must keep propagating; only a child's is ours to absorb."""
    cur = asyncio.current_task()
    return cur is not None and not cur.cancelling()


HELP = """\
Commands:
  /help                 show this
  /status               effective profile, context, execution and extension state
  /profile [preset]     auto · lean · balanced · frontier; applies with /new
  /extensions [verb]    list · enable <kind:name> · disable <kind:name>
  /learn [verb]         record/import a demonstration, analyze and draft a skill
  /red-team <dir> [min] scoped local exercise; /red-team off restores the workspace
  /loop <goal>          work autonomously toward a goal until done / needs you
  /runs                 list durable autonomous runs
  /loop-resume <id> [note] resume a run; reconcile uncertain actions in the note
  /recall <query>       search long-term memory
  /remember <t> :: <b>  save a semantic memory (title :: body)
  /memory [kind]        list memories (kind: semantic|procedural)
  /sessions             list recent sessions
  /tasks [verb]         open work: list · done <id> · add <title> · all
  /agents               list available sub-agents
  /model [name]         show or switch model
  /effort [level]       reasoning effort: med|high|xhigh|max|ultra
  /toolcalls [n|off]    tool-call budget per prompt (off = unlimited)
  /maxtokens [n]        output-token ceiling for ONE generation (takes effect now)
  /export [path]        write this session to markdown (--json, --full, --no-tools)
  /export library       file this session into the Library instead
  /library [verb]       your Library: list · search · open · folders · trash · undelete
  /mcp                  external MCP servers connected this session, and their tools
  /plugins              plugins under plugins/: what loaded, what failed
  /verbose [on|off]     show full tool output live (bare = toggle)
  /errors               show this session's errors in full
  /instructions [...]   standing preferences: <text> | edit | clear
  /thoughts on|off      reasoning: persistent (on) or ephemeral (off, default)
  /mind                 print the memory map (kinds, links, what's lit)
  /monitor [on|off]     GPU + inference-speed pane on the right (bare = toggle)
  /plan                 print the current task plan
  /council <q>          ask configured advisors (choose them in Council)
  /rewind [id]          list file checkpoints · roll one back (asks first)
  /review [--staged]    code-review the diff (or /review <base ref>)
  /new                  consolidate this session and start fresh
  ctrl+t                toggle the tasks panel · shift+tab cycles mode
  /clear                clear the screen
  /quit                 consolidate and exit
Anything else is a message to Dream. Ctrl-C interrupts a reply; Ctrl-D quits.
"""


@dataclass(frozen=True)
class QueuedPrompt:
    """Internal queue envelope; task identity never depends on prompt text."""
    text: str
    workflow_task_id: str
    workflow_attempt: int
    workflow_version: int
    workspace: str


class App(CouncilControls):
    def __init__(
        self,
        *,
        provider: str = "anthropic",
        model: str | None = None,
        consolidate_on_exit: bool = True,
        workspace: str | Path | None = None,
        moe: Any = None,
        gui: bool | None = None,
    ):
        self.renderer = Renderer()
        # Dream Studio: a second VIEW of this session, in a browser pane. Off
        # unless asked for, and additive by construction — the bus drops events
        # rather than making the terminal wait on a browser (see gui/bus.py).
        self.gui_enabled = config.GUI if gui is None else gui
        self.studio: Any = None
        self._gui_prompts: asyncio.Queue[str | QueuedPrompt] = asyncio.Queue(maxsize=32)
        self._deferred_gui_prompt: str | QueuedPrompt | None = None
        self._council_pending: CouncilRequest | None = None
        self._accepting_input = False
        from ..gui.bus import EventBus

        self.bus = EventBus()
        # This session's errors, kept in FULL (the collapsed run line only shows a
        # short preview) so /errors can recall exactly what went wrong.
        self.session_errors: list[tuple[str, str]] = []
        self._verbose = False
        self.moe = moe  # a core.moe.MoeConfig for a council session, else None
        if moe is not None:
            provider = moe.orchestrator  # the council runs on the orchestrator engine
        self.provider = provider
        self.model = model
        self.provider_label = "Claude · Anthropic"
        self.provider_kind = provider
        self.consolidate_on_exit = consolidate_on_exit
        self.workspace = Path(workspace).expanduser().resolve() if workspace else config.ROOT
        self.mode = "accept-edits"  # shift+tab cycles ask → accept-edits → auto → plan
        self.engine: Engine | None = None
        self._perm_lock = asyncio.Lock()  # one permission prompt at a time
        self._always_allow: set[str] = set()  # tools approved for the session
        # Ctrl-C bookkeeping: the innermost task an interrupt may cancel, and
        # whether the last turn ended that way.
        self._interrupt_target: asyncio.Task | None = None
        # Studio Stop owns the full turn even when a terminal prompt owns SIGINT.
        self._turn_interrupt_target: asyncio.Task | None = None
        self.interrupted = False
        self._status_broken = False  # the status panel raised; reported once
        # Session accounting for the status panel under the prompt. The tally is
        # the count (it never stops growing); the list is a bounded tail of names.
        self.activity_counts: dict[str, int] = {"created": 0, "edited": 0, "deleted": 0}
        self.activity: dict[str, list[str]] = {"created": [], "edited": [], "deleted": []}
        self.tool_count = 0
        self._recent_files: list[tuple[str, str]] = []  # (marker, name)
        # File activity classified at tool_use, keyed by tool id, committed to the
        # footer only when the tool_result comes back successful (not declined/errored).
        self._pending_activity: dict[Any, tuple[str, str]] = {}
        # Dream's own undo. A turn's writes fold into ONE checkpoint — opened at the
        # first write (a turn that changes nothing needs no undo point), sealed when
        # the turn ends so a restore can tell Dream's edit from a later one of the user's.
        self.checkpoints = checkpoints.default_store()
        self._turn_checkpoint: str | None = None
        self._active_loop = None  # set while an autonomous loop runs
        self._turn_label = ""  # the prompt line this turn's checkpoint is listed under
        # The cockpit: the visible memory map, the tasks panel, and the uncertainty
        # meter — fed from the event stream, rendered in the status panel.
        self.mindmap = MindMap()
        self.plan = PlanTracker()
        self.friction = FrictionMeter()
        self.meter = InferenceMeter()  # TTFT / tokens-per-second, fed every event
        self.show_monitor = config.MONITOR  # the right-side GPU/speed pane
        self.gpu: GpuSampler | None = None  # built in start() — discovery must not block boot
        self.show_tasks = True  # ctrl+t toggles the full tasks panel vs. its one-liner
        self._turn_t0 = time.monotonic()  # start of the current turn, for the footer clock
        config.ensure_dirs()
        kb = KeyBindings()

        @kb.add("s-tab")  # Shift+Tab, Claude-Code style
        def _cycle_mode(event) -> None:
            self.mode = policy.next_mode(self.mode)
            event.app.invalidate()

        @kb.add("c-t")  # Ctrl+T toggles the full tasks panel vs. its one-liner
        def _toggle_tasks(event) -> None:
            self.show_tasks = not self.show_tasks
            event.app.invalidate()

        self.session = PromptSession(
            history=FileHistory(str(config.VAR_DIR / "history")),
            bottom_toolbar=self._toolbar,
            key_bindings=kb,
            # noreverse: the panel sits on the terminal's own background, so it
            # blends with the theme instead of rendering as an inverted bar.
            style=Style.from_dict({"bottom-toolbar": "noreverse"}),
            # The monitor pane keeps ticking while idle at the prompt.
            refresh_interval=1.0,
        )

    # --- status bar ----------------------------------------------------------

    _MODE_COLOR = {  # prompt_toolkit (toolbar) colors
        "ask": "ansicyan",
        "accept-edits": "ansigreen",
        "auto": "ansiyellow",
        "plan": "ansimagenta",
    }
    _MODE_RICH = {  # rich (live footer) twins
        "ask": "#22d3ee",
        "accept-edits": "#34d399",
        "auto": "#fbbf24",
        "plan": "#a78bfa",
    }
    _MUTED = "#6f6890"  # quiet violet-gray for toolbar hints and separators

    def _status_data(self) -> dict:
        """One source of truth for the status panel — rendered two ways: as the
        prompt_toolkit toolbar at the prompt, as the rich live footer while working."""
        eng = self.engine
        ws = str(self.workspace).replace(str(Path.home()), "~")

        ctx_part = ""
        if eng.last_context_tokens:
            win = getattr(eng.backend, "n_ctx", None) or (
                config.CONTEXT_WINDOW if self.provider_kind == "anthropic" else None
            )
            ctx_part = f" · ctx {eng.last_context_tokens / 1000:.1f}k"
            if win:
                ctx_part += f"/{win / 1000:.0f}k ({eng.last_context_tokens / win:.0%})"
        tokens = f"tokens Σ {eng.session_tokens / 1000:.1f}k{ctx_part}"
        if eng.total_cost_usd:
            tokens += f" · ${eng.total_cost_usd:.2f}"

        a = self.activity_counts
        if self._recent_files:
            recent = "   ".join(f"{m} {n}" for m, n in self._recent_files[-4:])
        else:
            recent = "no file activity yet"

        # Cockpit strips — rich Text for the live footer, plain twins for the toolbar.
        width = self.renderer.console.width
        mind_t = self.mindmap.strip()
        plan_t = self.plan.strip()
        mon = mon_pt = mon_compact = None
        if self.show_monitor:
            gsnap = self.gpu.snapshot() if self.gpu else None
            msnap = self.meter.snapshot()
            mon_rows = monitor_mod.build_rows(gsnap, msnap)
            mon = monitor_mod.rich_lines(mon_rows)
            mon_pt = monitor_mod.pt_lines(mon_rows)
            mon_compact = monitor_mod.compact_strip(gsnap, msnap)
        return {
            "mode_label": policy.MODE_LABEL[self.mode],
            "mode_color": self._MODE_RICH[self.mode],
            "ws": ws,
            "effort": _effort_label(eng.effort) if eng.effort else None,
            "created": a["created"],
            "edited": a["edited"],
            "deleted": a["deleted"],
            "tools": self.tool_count,
            "tokens": tokens,
            "recent": recent,
            "mind": mind_t,
            "mind_plain": mind_t.plain,
            "plan_strip": plan_t,
            "plan_plain": plan_t.plain,
            "plan_panel": (
                self.plan.panel(width)
                if self.show_tasks and self.plan.has_tasks() else None
            ),
            "friction_spark": self.friction.sparkline(),
            "friction_label": self.friction.label(),
            "friction_reason": self.friction.reason(),
            "monitor": mon,
            "monitor_pt": mon_pt,
            "monitor_compact": mon_compact,
        }

    @staticmethod
    def _pt_line(*frags: tuple) -> tuple[str, int]:
        """(text, style?, bold?) fragments → (prompt_toolkit HTML, plain width).
        A '#hex' style becomes fg color, an ansi name becomes a tag. The plain
        width is what lets the toolbar compose two aligned columns."""
        parts: list[str] = []
        plain = 0
        for f in frags:
            txt = f[0]
            style = f[1] if len(f) > 1 else None
            bold = len(f) > 2 and f[2]
            plain += len(txt)
            esc = _html.escape(txt)
            if bold:
                esc = f"<b>{esc}</b>"
            if style and style.startswith("#"):
                esc = f'<style fg="{style}">{esc}</style>'
            elif style:
                esc = f"<{style}>{esc}</{style}>"
            parts.append(esc)
        return "".join(parts), plain

    def _note_status_error(self, exc: BaseException) -> None:
        """Record a status-panel failure ONCE. This runs on every rendered frame
        (several a second), so a repeat would bury the session in duplicates."""
        if self._status_broken:
            return
        self._status_broken = True
        self._record_error("(status panel)", f"{type(exc).__name__}: {exc}")

    def _toolbar(self) -> HTML:
        """The session panel under the prompt. prompt_toolkit does NOT contain an
        exception raised by a bottom_toolbar — one bad frame kills the prompt — so
        this degrades to a bare line instead of taking the REPL with it."""
        if not self.engine or not self.engine.store:
            return HTML(" Dream — starting… ")
        try:
            return self._build_toolbar()
        except Exception as e:
            self._note_status_error(e)
            return HTML(" Dream ")

    def _build_toolbar(self) -> HTML:
        """The brand gradient rule, mode + workspace, the color-coded activity
        tallies, recent files — and the monitor pane on the right when the
        terminal is wide enough."""
        d = self._status_data()
        color = self._MODE_COLOR[self.mode]
        width = shutil.get_terminal_size().columns
        rule = pt_gradient_rule(width)
        left: list[tuple[str, int]] = []
        line1: list[tuple] = [
            (" ", None),
            (f"⏵⏵ {d['mode_label']}", color, True),
            (f" · shift+tab cycles · ws {d['ws']}", self._MUTED),
        ]
        if d.get("effort"):
            line1.append((f" · effort {d['effort']}", self._MUTED))
        left.append(self._pt_line(*line1))
        line2: list[tuple] = [(" ", None)]
        for i, (label, count, tcolor) in enumerate((
            ("created", d["created"], "ansigreen"),
            ("edited", d["edited"], "ansiyellow"),
            ("deleted", d["deleted"], "ansired"),
            ("tools", d["tools"], "ansicyan"),
        )):
            if i:
                line2.append((" │ ", self._MUTED))
            # Zero tallies stay muted; color arrives with the first count.
            line2.append((f"{label} {count}", tcolor if count else self._MUTED))
        line2.append((" │ ", self._MUTED))
        line2.append((d["tokens"], "#a78bfa"))
        left.append(self._pt_line(*line2))
        left.append(self._pt_line((f" {d['recent']}", self._MUTED)))
        # Cockpit strips (plain twins of the rich footer): plan · mind · friction.
        if d.get("plan_plain"):
            left.append(self._pt_line((f" {d['plan_plain']}", "#a78bfa")))
        if d.get("mind_plain"):
            left.append(self._pt_line((f" {d['mind_plain']}", self._MUTED)))
        if d.get("friction_spark") or d.get("friction_label"):
            fr = f'friction {d.get("friction_spark", "")} {d.get("friction_label", "")}'
            if d.get("friction_reason"):
                fr += f'  ⚠ {d["friction_reason"]}'
            left.append(self._pt_line((f" {fr}", self._MUTED)))
        mon_pt = d.get("monitor_pt")
        if mon_pt and width >= monitor_mod.MIN_TWO_COL:
            body = monitor_mod.compose_pt(left, mon_pt, width)
        else:
            lines = [h for h, _ in left]
            if mon_pt and d.get("monitor_compact"):
                lines.append(self._pt_line((f" {d['monitor_compact']}", self._MUTED))[0])
            body = "\n".join(lines)
        return HTML(rule + "\n" + body)

    def _footer(self):
        """The same panel as a rich renderable — pinned live under the feed while
        a turn works, with the gerund + elapsed clock animating in place. Guarded
        like the toolbar: a bad frame must cost the panel, not the turn."""
        try:
            d = self._status_data()
            d["gerund"] = self.renderer.gerund
            d["elapsed"] = time.monotonic() - self._turn_t0
            return build_footer(d, self.renderer.console.width)
        except Exception as e:
            self._note_status_error(e)
            return Text(f" ✻ {self.renderer.gerund}…", style="dim")

    # --- interrupts ----------------------------------------------------------

    def _on_sigint(self, signum, frame) -> None:
        """Ctrl-C cancels the in-flight task and nothing else. The REPL task is
        never the target — Ctrl-D and /quit stay the only deliberate exits."""
        task = self._interrupt_target
        if task is None or task.done():
            return
        task.cancel()
        try:  # the loop may be parked in select() with a long timeout
            task.get_loop().call_soon_threadsafe(lambda: None)
        except RuntimeError:
            pass

    @contextlib.contextmanager
    def _sigint_cancels(self, task: asyncio.Task | None):
        """Own SIGINT, aimed at `task` — the innermost thing that can be
        abandoned (this turn, or the question holding stdin). None disarms it.

        asyncio.run installs a handler that cancels the MAIN task: the REPL
        itself. A Ctrl-C mid-turn therefore never reaches run()'s
        `except KeyboardInterrupt` — it arrives as CancelledError and unwinds
        through the `finally` that consolidates memory and unloads the GPUs, so
        interrupting a reply ends the whole session. Taking the signal ourselves
        is the only way to make Ctrl-C mean "this turn", not "this session".

        Nests: the inner block restores the outer block's handler and target.
        run() holds a disarmed outer one for the whole session, because arming
        only at turn start leaves a few milliseconds where asyncio.run's handler
        is still the one on duty."""
        prev_target, self._interrupt_target = self._interrupt_target, task
        try:
            prev_handler = signal.signal(signal.SIGINT, self._on_sigint)
            installed = True
        except (ValueError, OSError):  # not the main thread — leave SIGINT alone
            prev_handler, installed = None, False
        try:
            yield
        finally:
            if installed:
                with contextlib.suppress(ValueError, OSError, TypeError):
                    signal.signal(
                        signal.SIGINT, prev_handler or signal.default_int_handler)
            self._interrupt_target = prev_target

    async def _run_turn(self, coro):
        """Run a turn as its own task, so a Ctrl-C cancels IT and the REPL loop
        survives. Sets self.interrupted; returns the coroutine's result, or None
        when the turn was interrupted."""
        self.interrupted = False
        task = asyncio.ensure_future(coro)
        previous_turn = getattr(self, "_turn_interrupt_target", None)
        if previous_turn is None or previous_turn.done():
            self._turn_interrupt_target = task
        try:
            with self._sigint_cancels(task):
                return await task
        except asyncio.CancelledError:
            if not _own_cancel():
                raise  # the REPL itself is being torn down — let it
        except KeyboardInterrupt:
            # Only reachable where the handler above couldn't install (a REPL
            # driven off the main thread), so the turn must be stopped by hand.
            task.cancel()
        finally:
            self._turn_interrupt_target = previous_turn
        self.interrupted = True
        return None

    @staticmethod
    def _read_line(prompt: str, stop: threading.Event) -> str | None:
        """input(), but abandonable. A plain input() off-thread cannot be
        cancelled: the worker stays parked on stdin forever and steals keystrokes
        from the next prompt. Polling lets an interrupted question hand the
        terminal back. None = EOF (Ctrl-D) or abandoned."""
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while not stop.is_set():
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            except (OSError, ValueError):  # stdin isn't selectable — read it straight
                return sys.stdin.readline() or None
            if ready:
                return sys.stdin.readline() or None
        return None

    async def _read_answer(self, prompt: str) -> str | None:
        """Ask a question at the terminal. Ctrl-C answers it (None) rather than
        unwinding the session, and stdin is released on every exit path."""
        studio = getattr(self, "studio", None)
        if studio is not None and studio.client_count:
            return await studio.request_permission("Confirm action", {}, prompt.strip(), {"y": "Yes", "n": "No"})
        self.renderer.force_echo_on()  # the Live footer leaves echo suppressed
        stop = threading.Event()
        task = asyncio.ensure_future(asyncio.to_thread(self._read_line, prompt, stop))
        try:
            with self._sigint_cancels(task):
                return await task
        except asyncio.CancelledError:
            if not _own_cancel():
                raise
            return None
        finally:
            stop.set()

    # --- permissions ---------------------------------------------------------

    def _always_key(self, tool_name: str, tool_input: dict) -> str:
        """The key a session-wide 'always' approval is remembered under. Shell is
        scoped to the exact command — approving `git status` once must never
        greenlight a later `rm -rf …`. Everything else is keyed by tool name."""
        if policy.capability(tool_name) == policy.SHELL:
            return f"{tool_name}\x00{tool_input.get('command', '')}"
        return tool_name

    async def _permission(self, tool_name: str, tool_input: dict) -> bool:
        granted = await self._decide_permission(tool_name, tool_input)
        if granted:
            # The last moment guaranteed to run BEFORE the tool does — snapshot any
            # later and the "undo" records the file Dream has already changed.
            await self._snapshot(tool_name, tool_input)
        return granted

    async def _snapshot(self, tool_name: str, tool_input: dict) -> None:
        """Record the pre-write state of the files this tool is about to change.
        The read-and-hash runs off the event loop so a big file can't stall the
        stream that's still rendering."""
        # Deletes are snapshotted too, so a single-file delete_file is one
        # checkpoint_restore away. (A folder is recorded as skipped by the
        # checkpoint — not restorable — which the tool's description does not
        # promise.)
        if policy.capability(tool_name) not in (policy.WRITE, policy.DESTRUCTIVE):
            return
        # policy resolves a tool's path operands the same way the tools execute
        # them; a second implementation here could drift and snapshot the wrong file.
        paths = policy._paths(tool_input, self.workspace)
        if not paths:
            return
        try:
            self._turn_checkpoint = await asyncio.to_thread(
                self.checkpoints.take,
                self._turn_label or "turn",
                paths,
                self._turn_checkpoint,
            )
        except OSError as e:
            self._record_error("(checkpoint)", f"snapshot failed — {type(e).__name__}: {e}")

    async def _seal_checkpoint(self) -> None:
        """Record what Dream LEFT, at the end of the turn. Without it a restore
        can't tell Dream's write from an edit the user made afterwards, so it refuses
        to touch the file at all."""
        cid, self._turn_checkpoint = self._turn_checkpoint, None
        if cid is None:
            return
        try:
            await asyncio.to_thread(self.checkpoints.seal, cid)
        except (OSError, KeyError) as e:
            self._record_error("(checkpoint)", f"seal failed — {type(e).__name__}: {e}")

    @property
    def _loop_workspace(self):
        """The active autonomous loop's scratch directory, if one is running."""
        loop = getattr(self, "_active_loop", None)
        return getattr(loop, "workspace", None) if loop else None

    async def _decide_permission(self, tool_name: str, tool_input: dict) -> bool:
        engine = getattr(self, "engine", None)
        loop_dir = self._loop_workspace
        if loop_dir is not None and policy.capability(tool_name) in (policy.WRITE, policy.DESTRUCTIVE):
            targets = policy._paths(tool_input, self.workspace)
            if any(path.is_relative_to(loop_dir) and path != loop_dir / "progress.md" for path in targets):
                self.renderer.system("Run records and acceptance criteria are managed by Dream; write progress.md instead.")
                return False
        native_shell = tool_name in {"run_bash", f"mcp__{config.MCP_SERVER_NAME}__run_bash"}
        scope = getattr(engine, "execution_scope", None)
        capability = getattr(engine, "execution_capability", None)
        if (native_shell and self.mode != "plan" and scope is not None
                and scope.workspace == self.workspace
                and (capability is None or not capability.enforces(scope))):
            from ..core.execution import probe_sandbox
            mode, workspace = self.mode, self.workspace
            engine_workspace = getattr(engine, "workspace", None)
            refreshed = await probe_sandbox(scope)
            # A probe is evidence for the captured executor only. The user can
            # change sessions, modes or scope while that asynchronous check runs.
            if (getattr(self, "engine", None) is not engine or self.mode != mode
                    or self.workspace != workspace
                    or getattr(engine, "workspace", None) != engine_workspace
                    or getattr(engine, "execution_scope", None) is not scope):
                self.renderer.system("Shell authorization changed during the sandbox check; retry in the current session and scope.")
                return False
            engine.execution_capability = refreshed
        decision, reason = policy.decide(tool_name, tool_input, self.mode, self.workspace,
                                       execution_scope=getattr(engine, "execution_scope", None),
                                       execution_capability=getattr(engine, "execution_capability", None) if native_shell else None)
        capability = getattr(engine, "execution_capability", None)
        uncontained = native_shell and capability is not None and not capability.available
        if uncontained and decision != "deny":
            decision, reason = "ask", "outside workspace protection unavailable: run this exact command with host access"
        if decision == "allow":
            return True
        if decision == "deny":
            self.renderer.system(f"✗ {tool_name.split('__')[-1]} blocked — {reason}")
            return False
        # Boundary writes retain their own prompts. Explicit native Bash grants
        # below are exact-command/session scoped and distinguish host access.
        boundary = reason.startswith("outside workspace")
        host_option = native_shell and not getattr(getattr(engine, "execution_scope", None), "red_team", False)
        # ...except the loop's own scratch directory. `var/loops/<run>/` is created
        # BY Dream for this run and holds progress.md, which the loop instructs the
        # worker to keep current and which the independent grader reads. It sits
        # outside the session workspace, so every write to it hit this boundary and
        # asked — and in headless `--loop` there is nobody to ask, so it was denied.
        # The loop then ran with its own deliverable file unwritable. Dream's own
        # run directory is not user data; writing there is not a boundary crossing.
        if boundary and self._loop_workspace is not None and not native_shell:
            inner, _ = policy.decide(tool_name, tool_input, self.mode, self._loop_workspace)
            if inner == "allow":
                return True
        key = self._always_key(tool_name, tool_input)
        # Native shell grants are explicit, exact-command and executor-scoped.
        # Keep host grants separate: a sandbox approval can never fall back to host.
        captured = (engine, self.mode, self.workspace, scope,
                    getattr(engine, 'workspace', None), capability)
        def unchanged():
            return (getattr(self, 'engine', None) is captured[0]
                    and self.mode == captured[1] and self.workspace == captured[2]
                    and getattr(engine, 'execution_scope', None) is captured[3]
                    and getattr(engine, 'workspace', None) == captured[4]
                    and getattr(engine, 'execution_capability', None) is captured[5])
        effect = policy._shell_effect(str(tool_input.get('command') or ''), self.workspace)[0] if native_shell else None
        remember_native = bool(native_shell and scope is not None
                               and scope.workspace == self.workspace
                               and getattr(engine, 'workspace', None) == self.workspace
                               and not scope.red_team and not effect)
        remember_sandbox = bool(remember_native and capability is not None and capability.enforces(scope))
        remember_host = remember_native and host_option
        native_key = f'{id(engine)}\x00{self.workspace}\x00{scope!r}\x00{key}'
        sandbox_key, host_key = 'sandbox\x00' + native_key, 'host\x00' + native_key
        # Serialize prompts so concurrent sub-agent requests don't race on stdin.
        async with self._perm_lock:
            if native_shell and not unchanged():
                self.renderer.system('Shell authorization changed while waiting; retry in the current session and scope.')
                return False
            if remember_sandbox and sandbox_key in self._always_allow:
                return True
            if remember_host and host_key in self._always_allow:
                engine.approve_command(str(tool_input.get('command') or ''), uncontained=True)
                return True
            if not native_shell and not boundary and key in self._always_allow:
                return True
            desc = summarize_tool_input(tool_name, tool_input)
            if reason:
                desc += f"\n{'⚠ ' if boundary else 'Approval reason: '}{reason}"
            if native_shell:
                desc += ("\nSandbox execution keeps workspace protection. Outside-sandbox execution "
                         "uses your normal host access. A once choice does not remember approval. "
                         "Always choices remember only this exact command in this workspace for this session, "
                         "not other commands or future sessions.")
            # The live footer owns the bottom of the screen; hand it back to
            # stdin for the duration of the question.
            self.renderer.live_pause()
            try:
                self.renderer.permission_request(tool_name, desc)
                choices = (
                    "  run? [y]in sandbox once / [h]outside sandbox once / [n]o: " if host_option and not uncontained else
                    "  run? [y]outside sandbox once / [n]o: " if uncontained else
                    "  allow? [y]es / [n]o: " if boundary
                    else "  allow? [y]es / [n]o / [a]lways this session: "
                )
                if native_shell:
                    choices = ('  run? [y]outside sandbox once / [n]o' if uncontained else
                               '  run? [y]in sandbox once / [n]o')
                    if host_option and not uncontained:
                        choices += ' / [h]outside sandbox once'
                    extras = (' / [a]always in sandbox this session' if remember_sandbox else '')
                    extras += ' / [ha]always outside sandbox this session' if remember_host else ''
                    choices = choices.rstrip(': ') + extras + ': '
                studio = getattr(self, "studio", None)
                from contextlib import nullcontext
                timing = getattr(engine, "turn_timing", None)
                with timing.phase("approval_wait") if timing else nullcontext():
                    if studio is not None and studio.client_count:
                        yes_label = ("Run outside sandbox once" if uncontained else "Run in sandbox once") if native_shell else "Allow once"
                        buttons = {"y": yes_label, "n": "Deny"}
                        if host_option and not uncontained:
                            buttons["h"] = "Run outside sandbox once"
                        if remember_sandbox:
                            buttons['a'] = 'Always allow in sandbox (session)'
                        if remember_host:
                            buttons['ha'] = 'Always allow outside sandbox (session)'
                        if not native_shell and not boundary:
                            buttons["a"] = "Allow this session"
                        ans = await studio.request_permission(tool_name, tool_input, desc, buttons)
                    else:
                        ans = await self._read_answer(choices)
            finally:
                self.renderer.live_resume()
            if ans is None:  # Ctrl-C / Ctrl-D at the question means no
                return False
            if native_shell and not unchanged():
                self.renderer.system('Shell authorization changed during approval; retry in the current session and scope.')
                return False
            ans = ans.strip().lower()
            if ans in ('a', 'always') and remember_sandbox:
                self._always_allow.add(sandbox_key)
                return True
            if ans == 'ha' and remember_host:
                engine.approve_command(str(tool_input.get('command') or ''), uncontained=True)
                self._always_allow.add(host_key)
                return True
            if ans in ("h", "host") and host_option:
                engine.approve_command(str(tool_input.get("command") or ""), uncontained=True)
                return True
            if ans in ("a", "always") and not native_shell and not boundary:
                self._always_allow.add(key)
                return True
            approved = ans in ("y", "yes")
            if approved and uncontained:
                engine.approve_command(str(tool_input.get("command") or ""), uncontained=True)
            return approved

    # --- lifecycle -----------------------------------------------------------

    def _boot_engine(self) -> Engine:
        """Build an Engine bound to THIS app's provider + workspace. The one place
        engines are constructed, so a fresh session (via /new) can never drift back
        to the default provider (Claude) or Dream's own repo."""
        return Engine(
            provider=self.provider, model=self.model,
            can_use_tool=self._permission, workspace=self.workspace,
            mode_getter=lambda: self.mode,  # lets a CLI backend track the sandbox to the mode
            moe=self.moe,  # a council session runs on the orchestrator with consult/council
            emit=self._render_event,  # Studio tools open artifacts in the user's panel this way
        )

    def _reset_session_accounting(self) -> None:
        """A fresh session starts with an empty panel and no carried-over approvals."""
        self._project_restored_context = None
        self._active_project_id = None
        self._always_allow = set()
        self.activity_counts = {"created": 0, "edited": 0, "deleted": 0}
        self.activity = {"created": [], "edited": [], "deleted": []}
        self.tool_count = 0
        self._recent_files = []
        self._pending_activity = {}
        self.session_errors = []  # /errors must not carry a prior session's failures
        self._status_broken = False  # a fresh session may report a bad panel again
        self.mindmap = MindMap()
        self.plan = PlanTracker()
        self.friction = FrictionMeter()
        self.meter = InferenceMeter()

    async def start(self) -> None:
        self.engine = self._boot_engine()
        await self.engine.start()
        await self._associate_project_on_start()
        self.provider_label = self.engine.provider_label
        self.provider_kind = self.engine.provider.key
        await self._start_monitor()
        await self._start_studio()
        self._welcome()
        try:  # seed the mind map with what's top-of-mind at wake-up (dim, not lit)
            self.mindmap.seed(self.engine.store.top_memories(limit=8))
        except Exception:
            pass

    def _welcome(self) -> None:
        eng = self.engine
        self.renderer.show_logo()
        s = eng.stats()
        engine_label = getattr(self, "provider_label", "Claude · Anthropic")
        if getattr(self, "provider_kind", "anthropic") == "machx":
            engine_label += "  ⚡ local"
        if config.SEMANTIC_MEMORY:
            recall = "hybrid — keyword + semantic" + (" + rerank" if config.RERANK else "")
        else:
            recall = "keyword (FTS5)"
        rows = [
            ("engine", engine_label),
            (
                "memory",
                f"{s.get('memories', 0)} memories "
                f"({s.get('semantic', 0)}s · {s.get('procedural', 0)}p · "
                f"{s.get('episodic', 0)}e) · {s.get('sessions', 0)} sessions",
            ),
            ("recall", recall),
            ("session", eng.session_id),
        ]
        notes = []
        if eng.imported_memories:
            notes.append(f"imported {eng.imported_memories} memory file(s) from disk")
        prev = eng.store.previous_session(eng.session_id)
        if prev and prev.get("summary"):
            last = prev["summary"]
            # One line, no wrap — the panel stays tidy at typical widths.
            notes.append(f"last time · {last[:84] + '…' if len(last) > 84 else last}")
        if eng.tool_warnings:
            notes.append(f"⚠ {len(eng.tool_warnings)} custom tool(s) failed to load (see logs)")
        self.renderer.welcome(rows, notes)

    async def run_autonomous(self, goal: str, max_iterations: int | None = None, *,
                             resume_run_id: str | None = None, resume_note: str | None = None) -> None:
        """Headless: boot, drive the autonomous loop toward a goal, consolidate, exit."""
        await self.start()
        try:
            options = {"resume_run_id": resume_run_id, "resume_note": resume_note} if resume_run_id else {}
            await self._loop(goal, max_iterations, **options)
        finally:
            await self._shutdown()

    async def run(self) -> None:
        await self.start()
        try:
            # Hold SIGINT for the whole session, disarmed between turns. Each
            # turn aims it at its own task; a Ctrl-C with nothing in flight does
            # nothing (prompt_toolkit owns Ctrl-C at the prompt and raises
            # KeyboardInterrupt below, without the signal ever firing).
            with self._sigint_cancels(None):
                while True:
                    try:
                        self.renderer.sep()  # divider above the prompt
                        self._accepting_input = True
                        try:
                            incoming = await self._next_input()
                        finally:
                            self._accepting_input = False
                        if isinstance(incoming, CouncilRequest):
                            await self._execute_council_control(incoming)
                            continue
                        workflow = incoming if isinstance(incoming, QueuedPrompt) else None
                        line = (incoming.text if workflow else incoming).strip()
                        if not line:
                            continue
                        if workflow is None and line.startswith("/"):
                            self.bus.publish(Event("turn_start", {}))
                            try:
                                if await self._command(line):
                                    break
                            finally:
                                self.bus.publish(Event("hello", self._studio_session_info()))
                                self.bus.publish(Event("turn_end", {}))
                            continue
                        self.renderer.sep()  # divider below the prompt, above the response
                        if workflow is None:
                            await self._ask(line)
                        else:
                            await self._ask(line, workflow=workflow)
                        if end_requested():
                            # end_conversation, confirmed by its second call: the
                            # turn finished; the session ends the way /quit does.
                            self.renderer.system("end_conversation confirmed — the session ends here")
                            break
                    except KeyboardInterrupt:
                        try:
                            await self.engine.interrupt()  # stop the in-flight generation
                        except Exception:
                            pass
                        self.renderer.system("interrupted — Ctrl-D or /quit to exit")
                    except EOFError:  # Ctrl-D — the real exit
                        break
                    except Exception as e:
                        self.renderer.error(f"{type(e).__name__}: {e}")
                        self.bus.publish(Event("error", f"{type(e).__name__}: {e}"))
                        self.bus.publish(Event("turn_end", {}))
        finally:
            await self._shutdown()

    # --- the ask loop --------------------------------------------------------

    def _feed_cockpit(self, ev) -> None:
        """Route each event into the cockpit: friction (every event, tolerant of the
        payload shape), the mind map (memory tool calls light their slug), and the
        plan (TodoWrite calls)."""
        self.friction.record(ev.kind, ev.data)
        self.meter.feed(ev.kind, ev.data)
        if ev.kind == "tool_use":
            data = ev.data or {}
            short = str(data.get("name") or "").split("__")[-1]
            inp = data.get("input") or {}
            if short == "remember":
                slug = inp.get("slug") or slugify(inp.get("title", "") or "")
                if slug:
                    self.mindmap.touch(slug, inp.get("kind", "semantic"), "remember")
            elif short == "forget":
                if inp.get("slug"):
                    self.mindmap.touch(inp["slug"], "", "forget")
            elif short in ("TodoWrite", "update_todos"):
                self.plan.ingest(short, inp)
        elif ev.kind == "tool_result":
            data = ev.data or {}
            if str(data.get("name") or "").split("__")[-1] == "recall":
                for slug in _RECALL_SLUG_RE.findall(data.get("content", "") or ""):
                    self.mindmap.touch(slug, "", "recall")

    def _render_event(self, ev) -> None:
        # The GUI sees exactly what the terminal sees, from the one funnel every
        # event already passes through. publish() cannot block or raise.
        if self.studio is not None:
            self.studio.retain_show(ev)
        self.bus.publish(ev)
        self._feed_cockpit(ev)
        if ev.kind == "text_delta":
            self.renderer.assistant_delta(ev.data)
        elif ev.kind == "thinking_delta":
            self.renderer.thinking_delta(ev.data)
        elif ev.kind == "assistant_done":
            self.renderer.end_line()
        elif ev.kind == "tool_use":
            data = ev.data or {}
            self.tool_count += 1
            # Classify NOW (created-vs-edited depends on whether the file exists
            # yet — it doesn't, pre-write), but hold it PENDING keyed by the tool
            # id; only commit it to the footer when the write actually SUCCEEDS.
            act = policy.classify_file_activity(
                data.get("name"), data.get("input") or {}, self.workspace
            )
            if act:
                self._pending_activity[data.get("id")] = act
            self.renderer.tool_use(data.get("name"), data.get("input") or {})
        elif ev.kind == "tool_result":
            data = ev.data or {}
            is_error = bool(data.get("is_error"))
            self.renderer.tool_result(data.get("name"), data.get("content"), is_error)
            short = str(data.get("name") or "").split("__")[-1]
            if short in CARD_TOOL_NAMES and not is_error:
                # A card's text form is the terminal's rendering of it, whole.
                self.renderer.card(short, str(data.get("content") or ""))
            act = self._pending_activity.pop(data.get("id"), None)
            if act and not is_error:  # the write/edit/delete actually landed
                kind_, name_ = act
                self.activity_counts[kind_] += 1  # the tally the footer shows
                self.activity[kind_].append(name_)
                del self.activity[kind_][:-50]  # bound: the names are only a tail
                marker = {"created": "+", "edited": "~", "deleted": "−"}[kind_]
                self._recent_files.append((marker, name_))
                del self._recent_files[:-50]  # bound: only the last 4 are shown
            if is_error:
                self._record_error(
                    str(data.get("name") or "tool").split("__")[-1], data.get("content"))
        elif ev.kind == "result":
            self.renderer.result(ev.data)
            fallback = config.CONTEXT_WINDOW if self.provider_kind == "anthropic" else None
            self.renderer.turn_stats(ev.data, fallback_window=fallback)
            if isinstance(ev.data, dict) and ev.data.get("is_error"):
                self._record_error("(turn ended)", str(ev.data.get("subtype") or "error"))
        elif ev.kind == "error":
            self.renderer.error(ev.data)
            self._record_error("(turn error)", ev.data)
        elif ev.kind == "system":
            self.renderer.system(ev.data)

    def _record_error(self, source: str, content: Any) -> None:
        """Stash a full error so /errors can recall it. Bounded to the last 100 so a
        pathological session can't grow this without limit."""
        text = (content if isinstance(content, str) else str(content or "")).strip()
        if not text:
            return
        self.session_errors.append((source, text))
        del self.session_errors[:-100]

    def _tasks(self, arg: str) -> None:
        """`/tasks` — open work from the task store; `done <id>` closes one;
        `add <title>` opens one; `all` includes done. THREADS.md follows."""
        c = self.renderer.console
        tasks = getattr(self.engine, "tasks", None)
        if tasks is None:
            c.print("[dim]no task store in this session[/dim]")
            return
        verb, _, rest = arg.strip().partition(" ")
        rest = rest.strip()
        if verb == "done":
            try:
                t = tasks.update(int(rest), status="done")
            except (TypeError, ValueError):
                c.print("usage: /tasks done <id>")
                return
            if t is None:
                c.print(f"[red]no task #{rest}[/red]")
                return
            tasks.write_threads()
            c.print("[green]done:[/green] ", Text(f"#{t['id']} {t['title']}"), sep="")
            return
        if verb == "add":
            if not rest:
                c.print("usage: /tasks add <title>")
                return
            t = tasks.add(rest)
            tasks.write_threads()
            c.print("[green]added:[/green] ", Text(f"#{t['id']} {t['title']}"), sep="")
            return
        if verb and verb not in ("all", "list"):
            c.print(Text("usage: /tasks [list|all] · /tasks add <title> · /tasks done <id>"))
            return
        rows = tasks.list(include_done=(verb == "all"))
        if not rows:
            c.print("[dim]nothing open. /tasks add <title>[/dim]")
            return
        c.print(f"[cyan]{len(rows)} task(s):[/cyan]")
        for t in rows:
            mark = {"active": "▶", "blocked": "■", "open": "○", "done": "✓"}.get(t["status"], "·")
            # A title is model text: rendered, never parsed. A '[/x]' in one used
            # to raise MarkupError and kill the command until it was renamed.
            c.print(f"  {mark} [green]#{t['id']}[/green] ",
                    Text(t["title"], style="default"), f" [dim][{t['status']}][/dim]", sep="")
            if t.get("notes"):
                c.print("      ", Text(t["notes"].splitlines()[0][:100], style="dim"), sep="")

    async def _instructions(self, arg: str) -> None:
        """`/instructions` — the user's standing preferences, injected into the system
        prompt at boot. Bare = show; `edit` opens $EDITOR; `clear` removes; anything
        else replaces the text. Changes apply next session (or immediately on /new)."""
        c = self.renderer.console
        applies = "[dim](applies next session; /new to apply now)[/dim]"
        if not arg:
            cur = instructions.load()
            if cur:
                c.print("[cyan]Dream's standing instructions:[/cyan]")
                c.print(cur)
            else:
                c.print("[dim]no instructions set. /instructions <text> · /instructions edit[/dim]")
            return
        if arg.lower() == "clear":
            instructions.clear()
            c.print(f"[green]instructions cleared[/green]  {applies}")
            return
        if arg.lower() == "edit":
            import os
            import subprocess

            config.INSTRUCTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
            self.renderer.live_pause()
            try:
                await asyncio.to_thread(subprocess.call, [editor, str(config.INSTRUCTIONS_FILE)])
            except Exception as e:
                c.print(f"[red]couldn't open {editor}: {e}[/red]")
            finally:
                self.renderer.live_resume()
            c.print(f"[green]instructions saved[/green]  {applies}")
            return
        instructions.save(arg)
        c.print(f"[green]instructions saved[/green]  {applies}")

    async def _resume(self, arg: str) -> None:
        """Reload a past session's summary + recent turns as context, then continue
        where you left off. `/resume` = most recent prior session; `/resume <id>` for
        a specific one."""
        store = self.engine.store
        c = self.renderer.console
        if arg:
            sess = store.get_session(arg)
        else:
            sess = store.previous_session(self.engine.session_id)
        if not sess:
            c.print("[dim]no prior session to resume[/dim]")
            return
        turns = store.session_turns(sess["id"], limit=14)
        lines = [f"Resuming session {sess['id']} ({sess.get('turn_count', 0)} turns)."]
        if sess.get("summary"):
            lines.append(f"Summary: {sess['summary']}")
        if turns:
            lines.append("Recent exchange:")
            for t in turns[-10:]:
                who = t["role"]
                body = " ".join((t.get("content") or "").split())[:200]
                if body:
                    lines.append(f"- {who}: {body}")
        context = "\n".join(lines)
        c.print(f"[green]↺ resumed {sess['id']}[/green] [dim]({len(turns)} turns loaded)[/dim]")
        # Fed to the model as context on the next turn — one coherent priming message.
        await self._ask(
            "[context restored from a previous session — continue from here, "
            f"don't re-answer]\n{context}\n\nReady to continue."
        )

    async def _rewind(self, arg: str) -> None:
        """`/rewind` — the undo for Dream's own edits. Bare lists the checkpoints
        (one per turn that wrote something); `/rewind <id>` puts those files back.
        A restore overwrites what's on disk now, so it asks first."""
        c = self.renderer.console
        if not arg:
            rows = await asyncio.to_thread(self.checkpoints.list, 10)
            if not rows:
                c.print("[dim]no checkpoints yet — one is taken the first time a turn "
                        "writes a file[/dim]")
                return
            for r in rows:
                # Text, not markup: the label is the user's own prompt line and may
                # well contain brackets.
                line = Text("  ")
                line.append(r["id"], style=f"bold {_CYAN}")
                line.append(f"  {r['label'] or '(no label)'}")
                line.append(f"  · {_age(r['created_at'])} · {r['files']} file(s)", style="dim")
                if not r["sealed"]:
                    line.append("  unsealed — that turn didn't finish", style="yellow")
                c.print(line)
            c.print("[dim]/rewind <id> to roll those files back[/dim]")
            return

        cid = f"{int(arg):04d}" if arg.isdigit() else arg  # '7' and '0007' are the same one
        rows = await asyncio.to_thread(self.checkpoints.list)
        row = next((r for r in rows if r["id"] == cid), None)
        if row is None:
            c.print(f"[red]no checkpoint '{arg}'[/red] [dim](/rewind lists them)[/dim]")
            return
        names = [p.rsplit("/", 1)[-1] for p in row["paths"]]
        shown = ", ".join(names[:6]) + (f" +{len(names) - 6} more" if len(names) > 6 else "")
        c.print(Text(f"  {row['id']}  {row['label'] or '(no label)'}"))
        c.print(Text(f"  {row['files']} file(s): {shown}", style="dim"))
        # Restoring overwrites the user's current files — their call, not Dream's. Same
        # hand-stdin-back dance as the permission prompt.
        self.renderer.live_pause()
        try:
            ans = await self._read_answer("  roll these back to their pre-turn state? [y/N] · ")
        finally:
            self.renderer.live_resume()
        if (ans or "").strip().lower() not in ("y", "yes"):
            c.print("[dim]nothing restored[/dim]")
            return
        try:
            rep = await asyncio.to_thread(self.checkpoints.restore, cid)
        except KeyError as e:
            c.print(f"[red]{e}[/red]")
            return
        c.print(Text(rep.summary()))
        if rep.skipped:
            # The headline must not be "restored" when files were left alone.
            c.print(Text(f"  ⚠ {len(rep.skipped)} file(s) were NOT restored (SKIPPED above) — "
                         f"they changed after the snapshot", style="bold yellow"))
        else:
            c.print(Text(f"  ✓ {len(rep.restored)} restored · {len(rep.deleted)} deleted",
                         style="green"))

    async def _review(self, arg: str) -> None:
        """`/review` — hand my own diff to an independent reader. Bare reviews the
        working tree, `--staged` the index, anything else is a base ref."""
        from ..core import review as review_mod

        c = self.renderer.console
        arg = arg.strip()
        staged = arg in ("--staged", "--cached")
        if arg.startswith("-") and not staged:
            c.print(r"usage: /review \[--staged]   ·   /review <base ref>")
            return
        base = None if (staged or not arg) else arg

        async def ask(prompt: str) -> str:
            # The same "prompt in, text out" reduction the autonomous loop drives
            # its evaluator with — it's the one shape both backends share.
            parts: list[str] = []
            async for ev in self.engine.ask(prompt):
                if ev.kind == "assistant_done":
                    parts.append(ev.data)
            return "\n".join(parts)

        what = "the index" if staged else base or "the working tree"
        self.renderer.system(f"reviewing {what}…")
        self._turn_t0 = time.monotonic()
        self.meter.turn_start()
        self.renderer.live_begin(self._footer)
        self.renderer.working()
        try:
            result = await self._run_turn(
                review_mod.run_review(self.workspace, ask, staged=staged, base=base)
            )
        finally:
            self.renderer.live_end()
            self.meter.turn_end()
        if result is None:  # interrupted — nothing was reviewed
            self.renderer.system("review interrupted.")
            return
        self._render_review(result)

    def _render_review(self, res) -> None:
        """Four outcomes, four looks. `unavailable` and `unparseable` must never be
        mistakable for a clean review — in both, nothing was actually checked."""
        c = self.renderer.console
        if res.status == "no-changes":
            c.print(Text("  ∅ nothing to review — no changes against that base", style="dim"))
        elif res.status == "unavailable":
            c.print(Text("  ⚠ THE REVIEW DID NOT RUN — nothing was checked", style="bold red"))
        elif res.status == "unparseable":
            c.print(Text("  ⚠ the reviewer answered, but not in findings form — this is NOT "
                         "a clean review", style="bold yellow"))
        elif res.findings:
            c.print(Text(f"  ⚑ {len(res.findings)} finding(s) across {len(res.files)} file(s)",
                         style=f"bold {_VIOLET}"))
        else:
            c.print(Text(f"  ✓ reviewed {len(res.files)} file(s) — the reviewer found nothing",
                         style="bold green"))
        for f in res.findings:
            loc = f"{f.file}:{f.line}" if f.line else (f.file or "(file not named)")
            line = Text("    ")
            line.append(f"{f.severity:<8}", style=_SEVERITY_COLOR.get(f.severity, _CYAN))
            line.append(f" {loc}", style="bold")
            line.append(f" — {f.summary}")
            c.print(line)
            if f.fix:
                c.print(Text(f"        fix: {f.fix}", style="dim"))
        if res.truncated:
            c.print(Text("  ⚠ the diff was TRUNCATED — the rest of the change was not reviewed",
                         style="yellow"))
        if res.detail:
            style = {"unavailable": "red", "unparseable": "yellow"}.get(res.status, "dim")
            c.print(Text(f"  {res.detail}", style=style))

    def _queue_gui_prompt(self, prompt: str | QueuedPrompt) -> None:
        if getattr(self, '_council_pending', None) is not None:
            raise ValueError('Wait for the Council action to finish before sending a message.')
        if self._gui_prompts.qsize() + (self._deferred_gui_prompt is not None) + self._pending_steering_count() >= 32:
            raise ValueError("Studio input queue is full. Wait for queued work to finish.")
        self._gui_prompts.put_nowait(prompt)

    def _pending_steering_count(self) -> int:
        return sum(1 for identifier, (_, source) in getattr(self, '_steer_receipts', {}).items()
                   if hasattr(source, 'receipts') and source.open
                   and (identifier not in source.receipts or source.receipts[identifier]['status'] == 'pending'))

    async def _steer_gui_prompt(self, prompt: str, identifier: str, target: dict | None = None) -> dict:
        """Explicit correction; unsupported workers keep the existing queue."""
        from ..core.backends.openai_compat import OpenAICompatBackend
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ValueError('Steering receipt ID is invalid.')
        if target is not None and (not isinstance(target, dict) or type(target.get('turn')) is not int
                                   or not isinstance(target.get('session_id'), str)):
            raise ValueError('Steering target is invalid.')
        if prompt.lstrip().startswith('/'):
            raise ValueError('Commands use Queue/Send, not Steer.')
        if getattr(self, '_council_pending', None) is not None:
            raise ValueError('Wait for the Council action to finish.')
        receipts = getattr(self, '_steer_receipts', None)
        if receipts is None:
            receipts = self._steer_receipts = {}
        previous = receipts.get(identifier)
        if previous:
            old_text, source = previous
            if old_text != prompt:
                raise ValueError('Steering receipt ID already belongs to different text.')
            if hasattr(source, 'receipts') and target != {'session_id': source.session, 'turn': source.turn}:
                raise ValueError('Steering target changed. The original receipt remains saved.')
            receipt = await source.submit(prompt, identifier) if hasattr(source, 'receipts') else source
            return {**receipt, 'duplicate': True}
        if len(receipts) >= 256:
            raise ValueError('This session has 256 steering receipts. Use Queue/Send or start a new session.')
        engine = self.engine
        if engine is None:
            raise ValueError('No agent is connected.')
        if getattr(self, '_ordinary_chat_active', False) and isinstance(engine.backend, OpenAICompatBackend):
            inbox = getattr(engine, '_steering_inbox', None)
            if inbox is None:
                raise ValueError('The chat turn is preparing. Keep your draft and try Steer again shortly.')
            if target != {'session_id': inbox.session, 'turn': inbox.turn}:
                raise ValueError('The active chat turn changed. Review the current conversation before sending your correction.')
            queue = getattr(self, '_gui_prompts', None)
            waiting = self._pending_steering_count()
            if waiting + (queue.qsize() if queue is not None else 0) + bool(getattr(self, '_deferred_gui_prompt', None)) >= 32:
                raise ValueError('Studio input queue is full. Wait for pending input to be used.')
            # Register before awaiting: retries share the same inbox and ID.
            receipts[identifier] = (prompt, inbox)
            try:
                return await inbox.submit(prompt, identifier)
            except BaseException:
                if identifier not in inbox.receipts:
                    receipts.pop(identifier, None)
                raise
        if target is not None:
            raise ValueError('The steering target is no longer the active ordinary chat turn. Review the conversation before using Queue/Send.')
        if isinstance(engine.backend, OpenAICompatBackend) and not getattr(self, '_interrupt_target', None):
            raise ValueError('No supported chat turn is active. Use Send.')
        self._queue_gui_prompt(prompt)
        receipt = {'id': identifier, 'status': 'queued_after_turn',
                   'reason': 'Live steering supports ordinary HTTP chat only. This message uses the existing after-turn queue.'}
        receipts[identifier] = (prompt, receipt)
        return receipt

    def _queue_workflow(self, prompt: str, metadata: dict) -> None:
        if self.engine is None:
            raise ValueError("No agent is connected. Choose an agent before starting this task.")
        if not isinstance(metadata, dict) or metadata.get('workspace') != str(self.workspace.resolve()):
            raise ValueError("Task workspace does not match this agent session")
        task_id, attempt, version = (metadata.get('workflow_task_id'), metadata.get('workflow_attempt'), metadata.get('workflow_version'))
        if not isinstance(task_id, str) or not re.fullmatch(r'[0-9a-f]{32}', task_id) or type(attempt) is not int or type(version) is not int:
            raise ValueError("Task dispatch metadata is invalid")
        queued = QueuedPrompt(prompt, task_id, attempt, version, str(self.workspace.resolve()))
        self._queue_gui_prompt(queued)
        self.bus.publish(Event('user', prompt))

    async def _workflow_event(self, workflow: QueuedPrompt, kind: str, detail: str = '') -> None:
        from ..workflows import WorkflowService
        def update():
            return WorkflowService(workflow.workspace).event(workflow.workflow_task_id, kind, detail,
                                                              attempt=workflow.workflow_attempt)
        meter = getattr(self.engine, 'runtime_meter', None)
        turn = getattr(self.engine, '_turn_index', None)
        task = await asyncio.to_thread(update)
        if (kind in {'final', 'result'} and task['attempt'] == workflow.workflow_attempt
                and task['status'] in {'ready_for_review', 'needs_attention'}
                and meter is not None):
            # The store transaction has completed. Keep format checks separate
            # from protocol completion and factual/task acceptance.
            meter.record('task_assessment', turn=turn, assessment_kind='artifact_format',
                         reference=task['id'], attempt=task['attempt'], status=task['status'],
                         artifact_sha256=[a['sha256'] for a in task['artifacts']
                                          if a['attempt'] == task['attempt']], task_success=None)

    async def _stream(self, prompt: str, *, workflow: QueuedPrompt | None = None) -> bool:
        saw_result = False
        failed = False
        ask = (self.engine.ask_chat if workflow is None and hasattr(self.engine, 'ask_chat')
               else self.engine.ask)
        async with contextlib.aclosing(ask(prompt)) as events:
            async for ev in events:
                self._render_event(ev)
                if ev.kind == 'error':
                    failed = True
                elif ev.kind == 'result':
                    saw_result = True
                    if not isinstance(ev.data, dict) or ev.data.get('is_error') or ev.data.get('subtype') not in (None, 'success'):
                        failed = True
                if workflow is None:
                    continue
                if ev.kind in {'tool_use', 'tool_result'}:
                    data = ev.data if isinstance(ev.data, dict) else {}
                    detail = str(data.get('name') or 'tool')[:150]
                    if data.get('is_error'):
                        detail += ': tool reported an error; follow the conversation for recovery.'
                    await self._workflow_event(workflow, ev.kind, detail)
                elif ev.kind == 'error':
                    failed = True
                    await self._workflow_event(workflow, 'error', str(ev.data)[:1000])
                elif ev.kind == 'result':
                    saw_result = True
                    if isinstance(ev.data, dict) and ev.data.get('is_error'):
                        failed = True
                        await self._workflow_event(workflow, 'error', str(ev.data.get('subtype') or 'Agent turn failed'))
                    elif isinstance(ev.data, dict) and ev.data.get('subtype') not in (None, 'success'):
                        failed = True
                        subtype = json.dumps(str(ev.data.get('subtype'))[:120])
                        await self._workflow_event(workflow, 'error',
                                                   f'Agent turn incomplete. Reported subtype: {subtype}. Review partial results before recovery.')
        if workflow is not None and not failed:
            await self._workflow_event(workflow, 'final' if saw_result else 'unknown',
                                 '' if saw_result else 'The agent stream ended without a final result. Inspect the conversation and files before recovery.')
        return saw_result and not failed

    async def _ask(self, prompt: str, *, workflow: QueuedPrompt | None = None) -> bool:
        self._project_turn_generation = getattr(self, '_project_turn_generation', 0) + 1
        if workflow is not None:
            from ..workflows import WorkflowService
            if workflow.workspace != str(self.workspace.resolve()):
                await self._workflow_event(workflow, 'interrupted', 'The active workspace changed before this task ran. Review it in its original workspace.')
                return
            def claim():
                return WorkflowService(workflow.workspace).claim(workflow.workflow_task_id,
                        attempt=workflow.workflow_attempt, expected_version=workflow.workflow_version)
            task = await asyncio.to_thread(claim)
            if task is None:
                self.renderer.system('Skipped a guided task that was recovered, revised or already started.')
                return
        self.bus.publish(Event("turn_start", {}))
        self._turn_t0 = time.monotonic()
        self.meter.turn_start()
        reset_end()  # an end_conversation armed in an earlier turn does not carry
        self._turn_label = _first_line(prompt)  # what /rewind will list this turn as
        # Pin the status footer for the whole turn (it survives while we work);
        # working() rotates the gerund, which animates in the footer — or prints
        # the classic ✻ line when there's no terminal to pin a footer to.
        self.renderer.live_begin(self._footer)
        self.renderer.working()
        self._ordinary_chat_active = workflow is None
        try:
            prepared_prompt = await self._project_prompt(prompt)
            stream = self._stream(prepared_prompt) if workflow is None else self._stream(prepared_prompt, workflow=workflow)
            completed = await self._run_turn(stream)
            if self.interrupted:
                if workflow is not None:
                    await self._workflow_event(workflow, "interrupted", "The agent turn was interrupted. Check existing effects before a new attempt.")
                await self.engine.interrupt()  # stop the in-flight generation
                self.renderer.system("interrupted — Ctrl-D or /quit to exit")
        except asyncio.CancelledError:
            if workflow is not None:
                await self._workflow_event(workflow, 'interrupted', 'The session stopped during this task. Check existing effects before a new attempt.')
            raise
        except Exception as exc:
            if workflow is not None:
                await self._workflow_event(workflow, 'error', f'{type(exc).__name__}: {exc}')
            raise
        finally:
            self._ordinary_chat_active = False
            self.bus.publish(Event("turn_end", {"interrupted": self.interrupted}))
            self.renderer.live_end()
            self.meter.turn_end()  # the pane must stop claiming "decoding"
            self._pending_activity.clear()  # tool_uses with no result never landed
            self.friction.turn_boundary()  # archive this turn's peak, decay the score
            self.mindmap.age_turn()  # lit memories fade one step
            await self._seal_checkpoint()
        self.renderer.end_line()
        if completed is True and not self.interrupted:
            self._project_restored_context = None
        return completed is True and not self.interrupted

    async def _loop(self, goal: str, max_iterations: int | None = None, *, resume_run_id: str | None = None,
                    resume_note: str | None = None) -> None:
        from ..core.loop import AutonomousLoop

        goal = await self._project_prompt(goal)
        self.renderer.rule("autonomous loop")
        self.renderer.system(f"goal: {goal}")
        loop = AutonomousLoop(self.engine, on_event=self._loop_sink, max_iterations=max_iterations)
        self._active_loop = loop  # so permission checks can see its scratch directory
        self._turn_t0 = time.monotonic()
        self.meter.turn_start()
        self._turn_label = f"loop: {_first_line(goal)}"
        self.renderer.live_begin(self._footer)  # footer stays pinned across iterations
        self.renderer.working()
        try:
            operation = loop.run(goal, resume_run_id=resume_run_id, resume_note=resume_note) if resume_run_id else loop.run(goal)
            result = await self._run_turn(operation)
        finally:
            self.renderer.live_end()
            self.meter.turn_end()
            self._pending_activity.clear()
            await self._seal_checkpoint()
        if self.interrupted:
            self.renderer.system("loop interrupted.")
            return
        self.renderer.end_line()
        icon = {"done": "✓", "need_input": "?", "budget": "⏳", "stopped": "◼", "error": "⚠"}
        self.renderer.rule(f"loop {result.status} {icon.get(result.status,'')} "
                           f"({result.iterations} iterations)")
        if result.message:
            self.renderer.info(result.message)
        if result.workspace:
            self.renderer.system(f"workspace: {result.workspace}")
        if getattr(result, "run_id", None):
            self.renderer.system(f"run: {result.run_id} · /loop-resume {result.run_id}")
        self._emit_loop_summary(result)

    def _emit_loop_summary(self, result) -> None:
        """One machine-readable line closing an autonomous run.

        `--loop` is Dream's headless mode, so something outside Dream is reading
        this output — a benchmark, a script, CI. The TUI shows tokens in the
        footer, but a footer is not parseable, and a harness whose usage cannot
        be read cannot be compared on cost at all. Printed to stdout, one line,
        after everything else, so it is trivial to grep and impossible to
        confuse with prose.
        """
        session = (self.meter.snapshot().get("session") or {}) if self.meter else {}
        payload = {
            "status": result.status,
            "iterations": result.iterations,
            "tokens_in": session.get("in_tokens", 0),
            "tokens_out": session.get("out_tokens", 0),
            "turns": session.get("turns", 0),
            "workspace": result.workspace,
            "run_id": getattr(result, "run_id", None),
        }
        print("DREAM_RESULT_JSON: " + json.dumps(payload, sort_keys=True), flush=True)

    def _loop_sink(self, ev) -> None:
        # Live-render each loop event exactly like a normal turn.
        self._render_event(ev)
        if ev.kind == "assistant_done":
            self.renderer.end_line()

    async def _start_monitor(self) -> None:
        """Bring up the GPU sampler off-thread (discovery shells out to xpu-smi
        once, ~0.4 s — boot must not feel it). Any failure just means the pane
        shows inference metrics without GPU rows."""
        if not self.show_monitor or self.gpu is not None:
            return
        try:
            self.gpu = await asyncio.to_thread(GpuSampler)
            self.gpu.start()
        except Exception:
            self.gpu = None

    def _studio_session_info(self) -> dict[str, Any]:
        inbox = getattr(self.engine, '_steering_inbox', None)
        return {
            "session_id": self.engine.session_id,
            "provider": self.provider_label,
            "model": self.engine.model,
            "reasoning_effort": self.engine.effort,
            "workspace": str(self.workspace),
            "project_id": getattr(self, '_active_project_id', None),
            'steering_target': ({'session_id': inbox.session, 'turn': inbox.turn}
                                if inbox is not None and inbox.open else None),
            'steering_receipts': ([{**{k: v for k, v in r.items() if k != 'text'},
                                   'recovery_path': str(inbox.path)} for r in inbox.receipts.values()]
                                  if inbox is not None else []),
        }

    async def _start_studio(self) -> None:
        """Bring up Dream Studio if it was asked for. Best-effort by design: a
        GUI that will not start is a missing pane, never a lost session."""
        if not self.gui_enabled:
            return
        from ..tools.context import set_studio

        set_studio(None)
        try:
            from ..gui.server import StudioServer

            self.studio = StudioServer(
                self.bus,
                port=config.GUI_PORT,
                on_prompt=self._queue_gui_prompt,
                on_steer=self._steer_gui_prompt,
                on_optimize_prompt=self._queue_prompt_optimizer,
                on_workflow=self._queue_workflow,
                telemetry=self._telemetry_snapshot,
                checkpoints=lambda: self.checkpoints,
                session=self._studio_session_info,
                runtime=lambda: self.engine.runtime_status(),
                learning=self._learning_status,
                on_control=self._runtime_control,
            )
            url = await self.studio.start()
            set_studio(self.studio)
            self.renderer.system(f"Dream Studio → {url}")
            if config.GUI_OPEN:
                import webbrowser

                try:
                    if not webbrowser.open(url):
                        self.renderer.system("Studio is ready; the browser did not open. Open the URL above.")
                except Exception as e:
                    self.renderer.system(f"Studio is ready; browser launch failed ({type(e).__name__}). "
                                         "Open the URL above.")
        except Exception as e:
            set_studio(None)
            studio, self.studio = self.studio, None
            if studio is not None:
                try:
                    await studio.stop()
                except Exception:
                    pass
            self.renderer.error(f"Dream Studio failed to start — {type(e).__name__}: {e}")

    def _learning_status(self) -> dict:
        from .. import demonstrations
        recorder = getattr(self, "recorder", None)
        return {"active": dict(recorder.info) if recorder and recorder.process is not None else None,
                "demonstrations": demonstrations.inventory()}

    async def _runtime_control(self, payload: dict):
        """Explicit Studio user actions, separate from model tool arguments."""
        from .. import demonstrations, extensions
        action = payload.get("action")
        if action in {"permission_mode", "permission_mode_status"}:
            if action == "permission_mode":
                self.mode = policy.next_mode(self.mode)
            return {"mode": self.mode, "modes": list(policy.MODES),
                    "labels": policy.MODE_LABEL}
        if action == 'council_status':
            return self._council_status()
        if action == 'project_open':
            from ..projects.library import ProjectLibrary
            if self._council_busy() or self._project_active_work():
                raise ValueError('Wait for the current turn, queued input or project action to finish.')
            project = ProjectLibrary().get(payload.get('project_id'))
            queued = {**payload, '_project_revision': project['revision'],
                      '_source_session': self.engine.session_id,
                      '_source_generation': getattr(self, '_project_turn_generation', 0)}
            return await self._queue_council_control(queued)
        if action in {'council_configure', 'council_ask', 'council_work'}:
            return await self._queue_council_control(payload)
        if action == "interrupt":
            target = getattr(self, "_turn_interrupt_target", None)
            if target is None or target.done():
                target = getattr(self, "_interrupt_target", None)
            active = target is not None and not target.done()
            if active:
                target.cancel()
            return {"interrupted": active}
        if action == 'reconcile_local_request':
            if payload.get('confirmed_idle') is not True:
                raise ValueError('Confirm that the local server request is no longer active before reconciliation.')
            request_id = payload.get('request_id')
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
                raise ValueError('Choose the observed request to reconcile.')
            reconcile = getattr(getattr(self.engine, 'backend', None), 'reconcile_local_request', None)
            if not callable(reconcile):
                raise ValueError('Local request reconciliation is unavailable for this adapter.')
            return reconcile(request_id, confirmed_idle=True)
        if action == "profile":
            from ..core.profiles import save_settings
            await asyncio.to_thread(save_settings, payload["profile"], payload.get("overrides"))
            return {"applies": "new sessions", "profile": payload["profile"]}
        if action == "performance":
            setter = getattr(getattr(getattr(self, "engine", None), "backend", None), "set_performance_mode", None)
            if not callable(setter):
                raise ValueError("Performance modes are unavailable for this adapter")
            mode = payload.get("mode")
            if not isinstance(mode, str) or mode not in {"custom", "quick", "balanced", "thorough"}:
                raise ValueError("Choose a performance mode: custom, quick, balanced or thorough")
            return setter(mode)
        if action == "extension":
            if type(payload.get("enabled")) is not bool:
                raise ValueError("enabled must be true or false")
            trusted = payload.get("trusted", False)
            if type(trusted) is not bool or (trusted and not str(payload["id"]).lower().startswith("hook:")):
                raise ValueError("Explicit trust applies only to a reviewed hook")
            return await asyncio.to_thread(extensions.set_enabled, payload["id"], payload["enabled"], trusted=trusted)
        if action == "module_trust":
            return await asyncio.to_thread(extensions.trust_module, payload["id"], payload["sha256"])
        if action == "learn_start":
            if getattr(self, "recorder", None) is None:
                self.recorder = demonstrations.Recorder()
            result = await self.recorder.start(payload["name"], region=tuple(payload["region"]),
                                                seconds=payload.get("seconds", 300))
            self.renderer.system(f"● RECORDING {result['name']} — /learn stop ends capture")
            return result
        if action == "learn_stop":
            recorder = getattr(self, "recorder", None)
            return await recorder.stop() if recorder else {"status": "idle"}
        if action == "learn_import":
            return await asyncio.to_thread(demonstrations.import_video, Path(payload["path"]), payload.get("name", ""))
        if action == "learn_extract":
            return await demonstrations.extract(payload["id"])
        if action == "learn_analyze":
            info = demonstrations.read(payload["id"])
            if info["status"] not in {"ready", "draft"}:
                raise ValueError("Extract frames before analyzing")
            self._queue_gui_prompt("/learn analyze " + info["id"])
            return {"queued": True, "provider": self.provider}
        if action == "learn_install":
            return {"path": str(await asyncio.to_thread(demonstrations.install, payload["id"])), "enabled": False}
        if action == "red_team":
            from ..core.execution import ExecutionScope, probe_sandbox
            if type(payload.get("enabled")) is not bool:
                raise ValueError("enabled must be true or false")
            if payload["enabled"]:
                minutes = payload.get("minutes", 15)
                if type(minutes) is not int or not 1 <= minutes <= 120:
                    raise ValueError("Choose a red-team duration between 1 and 120 minutes")
                target = (self.workspace / payload["target"]).resolve()
                scope = ExecutionScope(self.workspace, red_team=True, target_roots=(target,),
                                       expires_at=time.monotonic() + minutes * 60)
                scope.validate()
                capability = await probe_sandbox(scope)
                if not capability.available:
                    raise ValueError("Cannot enable red-team scope: " + capability.reason)
            else:
                scope = ExecutionScope(self.workspace)
                capability = await probe_sandbox(scope)
            self.engine.execution_scope = scope
            self.engine.execution_capability = capability
            self.engine._command_approvals.clear()
            return self.engine.runtime_status()["execution"]
        raise ValueError("Unknown runtime action")

    async def _project_prompt(self, prompt: str) -> str:
        """Associate this session and restore saved text only on explicit input.

        Engine project preparation supplies saved instructions and documents to
        every turn, including autonomous turns, without duplicating them here.
        """
        failure = getattr(self, '_project_open_error', None)
        if failure:
            raise ValueError(failure)
        project = await self._associate_project_session()
        if project is None:
            return prompt
        restored = getattr(self, '_project_restored_context', None)
        if not restored:
            return prompt
        return restored + '\n\nCurrent user request:\n' + prompt

    async def _associate_project_session(self):
        from ..projects.library import ProjectLibrary
        library = ProjectLibrary()
        project = await asyncio.to_thread(library.find_workspace, self.workspace)
        if project is not None:
            await asyncio.to_thread(library.associate, project['id'], self.engine.session_id)
        self._active_project_id = project['id'] if project else None
        return project

    async def _associate_project_on_start(self):
        """A broken catalog is visible without preventing the agent from starting."""
        try:
            await self._associate_project_session()
        except (ValueError, OSError) as exc:
            warning = f'Project association unavailable: {exc}'
            self.renderer.system(warning)
            self.bus.publish(Event('error', warning))

    def _project_active_work(self):
        target = getattr(self, '_interrupt_target', None)
        recorder = getattr(self, 'recorder', None)
        queue = getattr(getattr(self.engine, 'backend', None), '_idle_work', None)
        pending = queue.snapshot() if queue is not None else {}
        return ((target is not None and not target.done())
                or getattr(recorder, 'process', None) is not None
                or bool(pending.get('queued') or pending.get('running')))

    async def _queue_prompt_optimizer(self, body, files):
        """Serialize isolated drafting with ordinary input and provider changes."""
        if self._council_busy() or self._project_active_work():
            raise ValueError('Wait for the current work to finish, or use Quick structure without a model.')
        if not getattr(self.engine, '_backend_available', True):
            raise ValueError('The selected model is unavailable. Use Quick structure or reconnect Dream.')
        if body.get('session_id') != self.engine.session_id or body.get('workspace') != str(self.workspace):
            raise ValueError('The active session changed. Review the draft before optimizing.')
        return await self._queue_council_control({
            'action': 'prompt_optimize', 'draft': dict(body), 'files': [dict(f) for f in files],
            '_source_session': self.engine.session_id,
            '_source_generation': getattr(self, '_project_turn_generation', 0),
            '_source_provider': self.engine.provider.key, '_source_model': self.engine.model,
            '_source_profile': copy.deepcopy(self.engine.profile),
        })

    async def _prepare_optimized_prompt(self, payload):
        from ..core.evaluator import ReviewSettings
        from ..prompt_optimizer import optimize
        if (payload['_source_session'] != self.engine.session_id
                or payload['_source_generation'] != getattr(self, '_project_turn_generation', 0)
                or payload['_source_provider'] != self.engine.provider.key
                or payload['_source_model'] != self.engine.model
                or payload['_source_profile'] != self.engine.profile
                or payload['draft']['workspace'] != str(self.workspace)):
            raise ValueError('Session, model or workspace changed before optimization. Review your draft and retry.')
        # Explicit current-provider settings avoid evaluator environment overrides.
        # No Engine is created, no main history is reused and no weights are loaded.
        settings = ReviewSettings(provider=self.engine.provider, model=self.engine.model,
                                  profile=self.engine.profile, timeout=120.0)
        fields = {k: v for k, v in payload['draft'].items() if k not in ('session_id', 'workspace')}
        return await optimize(fields, payload['files'], self.workspace, settings)

    async def _execute_council_control(self, request):
        if request.payload.get('action') == 'prompt_optimize':
            self.bus.publish(Event('turn_start', {}))
            result = failure = None
            try:
                result = await self._run_turn(self._prepare_optimized_prompt(request.payload))
                if self.interrupted or result is None:
                    failure = ValueError('Prompt optimization stopped. Your task was not sent; keep or edit the draft.')
            except asyncio.CancelledError:
                failure = ValueError('Dream closed before prompt optimization completed. Your task was not sent.')
                raise
            except Exception as exc:
                failure = ValueError(str(exc) or 'Prompt optimization failed. Your task was not sent.')
            finally:
                self._council_pending = None
                self.bus.publish(Event('hello', self._studio_session_info()))
                self.bus.publish(Event('turn_end', {}))
                if not request.future.done():
                    if failure:
                        request.future.set_exception(failure)
                    else:
                        request.future.set_result(result)
            return
        if request.payload.get('action') != 'project_open':
            return await super()._execute_council_control(request)
        self.bus.publish(Event('turn_start', {}))
        result = None
        failure = None
        try:
            result = await self._open_project(request.payload)
        except asyncio.CancelledError:
            failure = ValueError('Project opening interrupted. Check the active session before retrying.')
            raise
        except Exception as exc:
            failure = ValueError(str(exc))
            self.bus.publish(Event('error', str(exc)))
        finally:
            self._council_pending = None
            self.bus.publish(Event('hello', self._studio_session_info()))
            self.bus.publish(Event('turn_end', {}))
            if not request.future.done():
                if failure:
                    request.future.set_exception(failure)
                else:
                    request.future.set_result(result)

    async def _open_project(self, payload):
        """Only the App input consumer may cross provider lifetime boundaries."""
        from ..projects.library import ProjectLibrary
        if (not self._gui_prompts.empty() or self._deferred_gui_prompt is not None
                or getattr(self, '_active_loop', None) is not None
                or self._project_active_work()
                or payload['_source_session'] != self.engine.session_id
                or payload['_source_generation'] != getattr(self, '_project_turn_generation', 0)):
            raise ValueError('Input or session changed while opening the project; queued work was not redirected.')
        library = ProjectLibrary()
        project = await asyncio.to_thread(library.get, payload['project_id'])
        if project['revision'] != payload['_project_revision']:
            raise ValueError('Project changed while opening. Review its settings and try again.')
        workspace = Path(project['workspace'])
        if not workspace.is_dir() or workspace.resolve() != workspace:
            raise ValueError('The saved workspace is missing or its path changed. Restore it or add a new project.')
        source_session = payload.get('session_id')
        restored = (await asyncio.to_thread(library.restored_context, project['id'], source_session)
                    if source_session is not None else None)
        old = self.engine
        old_workspace = self.workspace
        previous = await asyncio.to_thread(library.find_workspace, old_workspace)
        if previous:
            await asyncio.to_thread(library.associate, previous['id'], old.session_id)
        self.provider, self.model, self.moe = old.provider.key, old.model, old._moe
        candidate = None
        try:
            # Consolidation is a model request. Project opening only saves/closes
            # the existing session; explicit later consolidation remains separate.
            await old.stop(consolidate=False)
            self._reset_session_accounting()
            self.workspace = workspace
            candidate = self._boot_engine()
            candidate.effort = old.effort
            await asyncio.to_thread(library.associate, project['id'], candidate.session_id)
            await candidate.start()
            self.engine = candidate
        except BaseException as exc:
            if candidate is not None and isinstance(exc, asyncio.CancelledError):
                # Engine.start handles ordinary exceptions itself. Cancellation
                # is a BaseException and still needs cleanup on this owner task.
                try:
                    await candidate._cleanup()
                except BaseException as cleanup_error:
                    exc.add_note(f'Project connection cleanup failed: {cleanup_error}')
            self.workspace = old_workspace
            self._project_open_error = ('Project opening did not complete. The prior connection may be closed. '
                                        'Open a project again before sending work; no prompt was replayed.')
            raise
        self._project_open_error = None
        self._project_restored_context = restored
        self._active_project_id = project['id']
        self.provider_label = self.engine.provider_label
        self.provider_kind = self.engine.provider.key
        studio = getattr(self, 'studio', None)
        if studio is not None:
            studio.reset_project_view()
        self.bus.conversation.clear()
        self.bus.publish(Event('history', {'events': [], 'trimmed': False}))
        message = ('Saved conversation context is ready for your next message. This is a new agent session; '
                   'no tools were replayed.' if restored else 'Project opened in a new chat. Send a message to begin.')
        self.renderer.system(message)
        self.bus.publish(Event('system', message))
        return {'project_id': project['id'], 'session': self._studio_session_info(),
                'restoration': 'saved_context' if restored else 'new_chat', 'source_session_id': source_session}

    def _telemetry_snapshot(self) -> dict[str, Any]:
        """GPU + inference numbers for the Studio pane — the same sources the
        terminal footer reads, so the two panes can never disagree."""
        from dataclasses import asdict

        out: dict[str, Any] = {"available": True}
        try:
            snap = self.gpu.snapshot() if self.gpu is not None else None
            out["gpu"] = asdict(snap) if snap is not None else None
        except Exception:
            out["gpu"] = None
        try:
            out["inference"] = self.meter.snapshot() if self.meter is not None else None
        except Exception:
            out["inference"] = None
        return out

    async def _next_input(self) -> str | QueuedPrompt | CouncilRequest:
        """The next prompt, from whichever pane produces one first.

        With no GUI this is exactly the old call, so the terminal path is
        unchanged. With one, a prompt typed in the browser wakes the REPL —
        which does mean an unsent line being typed in the terminal is discarded,
        the one visible cost of driving one session from two places.
        """
        if self._deferred_gui_prompt is not None:
            line, self._deferred_gui_prompt = self._deferred_gui_prompt, None
            return line
        with patch_stdout():
            if self.studio is None:
                line = await self.session.prompt_async(PROMPT)
                self.bus.publish(Event("user", line))
                return line
            typed = asyncio.ensure_future(self.session.prompt_async(PROMPT))
            from_gui = asyncio.ensure_future(self._gui_prompts.get())
            done, pending = await asyncio.wait(
                {typed, from_gui}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            # Prefer terminal input when both arrive together; keep the GUI
            # message in the queue instead of consuming and silently losing it.
            winner = typed if typed in done else from_gui
            if typed in done and from_gui in done:
                self._deferred_gui_prompt = from_gui.result()
            if winner is from_gui:
                line = winner.result()
                if isinstance(line, CouncilRequest):
                    return line
                self.renderer.system(f"⟵ studio: {(line.text if isinstance(line, QueuedPrompt) else line)[:80]}")
                return line
            line = winner.result()
            self.bus.publish(Event("user", line))
            return line

    async def _shutdown(self) -> None:
        self._accepting_input = False
        pending = getattr(self, '_council_pending', None)
        if pending is not None and not pending.future.done():
            pending.future.set_exception(ValueError('Dream closed before the queued Council action ran.'))
            self._council_pending = None
        recorder = getattr(self, "recorder", None)
        if recorder is not None:
            await recorder.stop()
        # The hidden frame (headless Chromium) goes with the session, not with
        # the idle reaper.
        try:
            from ..gui import preview as _preview

            if _preview._PREVIEW is not None:
                await asyncio.wait_for(_preview._PREVIEW.aclose(), 5)
        except Exception:
            pass
        # Consolidation is opt-in: ask before committing this session to long-term
        # memory, so Dream doesn't silently remember every session. Default No.
        # DREAM_CONSOLIDATE=1 forces it on without asking; a closed stdin (Ctrl-D)
        # can't be prompted, so it defaults No.
        # Read the config directly (NOT self.consolidate_on_exit): the model-picker
        # path threads that flag as True by default, which would silently
        # auto-consolidate. Only DREAM_CONSOLIDATE=1 forces it on; otherwise ask.
        commit = config.CONSOLIDATE_ON_EXIT
        if self.engine and not commit:
            ans = await self._read_answer(
                "  Conversation is saved. Generate a summary and commit this session to long-term memory? "
                "This optional model pass may take several minutes. [y/N] · ")
            commit = (ans or "").strip().lower() in ("y", "yes")
        if self.studio is not None:
            from ..tools.context import set_studio

            set_studio(None)
            studio, self.studio = self.studio, None
            try:
                await studio.stop()
            except Exception:
                pass
        if self.gpu is not None:
            gpu, self.gpu = self.gpu, None
            await asyncio.to_thread(gpu.stop)  # joins a thread — not on the loop
        if not self.engine:
            return
        self.renderer.system("generating optional summary and memories…" if commit
                             else "closing with saved conversation; skipping memory consolidation…")
        summary = await self.engine.stop(consolidate=commit)
        if summary:
            self.renderer.system(f"session summary: {summary}")
        self.renderer.info("Goodnight. 🌙")

    # --- slash commands ------------------------------------------------------

    async def _command(self, line: str) -> bool:
        """Return True to quit."""
        parts = line[1:].split(maxsplit=1)
        if not parts:  # a bare "/" (or "/   ") must not crash the REPL
            return False
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        store = self.engine.store
        c = self.renderer.console

        if cmd in ("quit", "exit", "q"):
            return True
        if cmd == "help":
            c.print(HELP)
        elif cmd == "clear":
            c.clear()
        elif cmd == "status":
            from rich.json import JSON
            c.print(JSON.from_data(self.engine.runtime_status()))
        elif cmd == "learn":
            from .learn_cmd import command
            await command(self, arg)
        elif cmd == "red-team":
            import shlex
            from rich.json import JSON
            try:
                words = shlex.split(arg)
                if not words or len(words) > 2:
                    raise ValueError('/red-team "workspace subdirectory" [minutes] or /red-team off')
                payload = {"action": "red_team", "enabled": words[0].lower() != "off"}
                if payload["enabled"]:
                    payload.update(target=words[0], minutes=int(words[1]) if len(words) > 1 else 15)
                c.print(JSON.from_data(await self._runtime_control(payload)))
            except (ValueError, OSError) as exc:
                self.renderer.error(str(exc))
        elif cmd in {"profile", "extensions"}:
            import shlex
            from ..management import main as manage
            try:
                await asyncio.to_thread(manage, [cmd, *shlex.split(arg)])
            except (ValueError, SystemExit) as exc:
                c.print(f"Invalid {cmd} command: {exc}")
            if cmd == "profile" and arg:
                self.renderer.system("Profile saved. /new applies it to a fresh session.")
        elif cmd == "resume":
            await self._resume(arg)
        elif cmd == "runs":
            from ..management import list_runs
            for row in await asyncio.to_thread(list_runs):
                c.print(Text(f"{row['id']} · {row.get('status', 'unknown')} · {row.get('goal', '')}"))
        elif cmd == "loop-resume":
            run_id, _, note = arg.strip().partition(" ")
            if not run_id:
                self.renderer.info("/loop-resume <run-id> [what you verified about interrupted actions]")
            else:
                from ..core.run_state import RunState
                try:
                    journal = RunState(config.LOOP_DIR, run_id)
                    try:
                        goal = journal.state["goal"]
                    finally:
                        journal.close()
                    await self._loop(goal, resume_run_id=run_id, resume_note=note or None)
                except (OSError, ValueError, KeyError) as exc:
                    self.renderer.error(str(exc))
        elif cmd == "loop":
            if not arg:
                c.print("usage: /loop <goal>   (Dream works on it autonomously)")
            else:
                await self._loop(arg)
        elif cmd == "recall":
            if not arg:
                c.print("usage: /recall <query>")
            else:
                hits = store.search_memories(arg, limit=8)
                if not hits:
                    c.print(f"[dim]no memories for '{arg}'[/dim]")
                for h in hits:
                    c.print(f"[green]• [{h['kind']}] {h['title']}[/green] [dim]({h['slug']})[/dim]")
                    c.print(f"  {h['body']}")
        elif cmd == "remember":
            if "::" in arg:
                title, body = (p.strip() for p in arg.split("::", 1))
            else:
                title, body = arg[:60], arg
            if not body:
                c.print("usage: /remember <title> :: <body>")
            else:
                mem = store.upsert_memory("semantic", title, longterm.tag_body(body, "stated"),
                                          slug=slugify(title), source_session=self.engine.session_id)
                longterm.write_markdown(mem)
                c.print(f"[green]remembered '{mem['title']}' ({mem['slug']})[/green]")
        elif cmd == "memory":
            kind = arg if arg in ("semantic", "procedural") else None
            mems = store.all_memories(kind=kind)
            c.print(f"[cyan]{len(mems)} memory(ies){' ['+kind+']' if kind else ''}:[/cyan]")
            for m in mems:
                c.print(f"[dim]{m['kind'][:4]}[/dim] [green]{m['title']}[/green] "
                        f"[dim]({m['slug']}, sal {m['salience']})[/dim]")
        elif cmd == "tasks":
            self._tasks(arg)
        elif cmd == "plugins":
            from .. import plugins as _plugins

            # A plugin's name and description are third-party text: rendered,
            # never parsed, so a '[/bold]' in one cannot break the listing.
            for line in _plugins.status_lines():
                c.print(Text(line, style="red" if line.startswith("! ") else ""))
        elif cmd == "sessions":
            for s in store.recent_sessions(12):
                title = s.get("title") or "(untitled)"
                c.print(f"[green]{s['started_at']}[/green] {title} "
                        f"[dim]({s.get('turn_count',0)} turns)[/dim]")
                if s.get("summary"):
                    c.print(f"  [dim]{s['summary']}[/dim]")
        elif cmd == "agents":
            from ..core.subagents import subagents
            for name, a in subagents().items():
                c.print(f"[green]{name}[/green]: {a.description}")
        elif cmd == "model":
            if arg:
                try:
                    await self.engine.set_model(arg)
                    self.model = self.engine.model  # persist canonical selection across /new
                    c.print(f"[green]model → {self.model}[/green]")
                except Exception as e:
                    c.print(f"[red]could not switch model: {e}[/red]")
            else:
                c.print(f"model: {self.engine.model or '(CLI default)'}")
        elif cmd == "effort":
            if not arg:
                cur = _effort_label(self.engine.effort) if self.engine.effort else "default"
                c.print(f"effort: [cyan]{cur}[/cyan]   options: {', '.join(effort_mod.LEVELS)}")
            else:
                norm = effort_mod.normalize(arg)
                if norm is None:
                    c.print(f"[red]unknown effort '{arg}'[/red]  options: {', '.join(effort_mod.LEVELS)}")
                else:
                    try:
                        if self.provider_kind == 'anthropic':
                            from dataclasses import replace
                            from ..core.moe import MoeConfig
                            cfg = self.engine._moe or MoeConfig(self.provider, [])
                            native = {'med': 'medium', 'ultra': 'max'}.get(norm, norm)
                            await self.engine.configure_council(replace(cfg, orchestrator_effort=native))
                            self.moe = self.engine._moe
                            if norm == 'ultra':
                                self.engine.effort = norm  # retain its existing fan-out guidance
                        else:
                            self.engine.set_effort(norm)
                    except Exception as exc:
                        c.print(Text(f"could not change effort: {exc}", style='red'))
                    else:
                        c.print(f"[green]effort → {effort_mod.describe(norm)}[/green]")
        elif cmd == "export":
            # Selecting a long session out of the terminal does not work: the
            # moment it scrolls, prompt_toolkit repaints and the selection is
            # gone. Every turn is already in data/sessions/<id>.jsonl, so this
            # is a format change, not a capture.
            from .export import export_session

            parts_e = arg.split()
            if parts_e and parts_e[0].lower() == "library":
                # The session is the one deliverable every run produces; a loose
                # file in $HOME is found by accident. Filed, it can be searched.
                from ..tools.library_tools import library
                from . import library_cmd

                await library_cmd.file_session(
                    config.SESSIONS_DIR / f"{self.engine.session_id}.jsonl",
                    self.engine.session_id, library(), c.print,
                    tools="--no-tools" not in parts_e)
                return False
            fmt = "json" if "--json" in parts_e else "md"
            full = "--full" in parts_e
            tools = "--no-tools" not in parts_e
            where = next((p for p in parts_e if not p.startswith("--")), None)
            src = config.SESSIONS_DIR / f"{self.engine.session_id}.jsonl"
            if not src.exists():
                c.print(f"[red]no session log at {src}[/red]")
                return False
            dest = Path(where).expanduser() if where else (
                Path.home() / f"dream-{self.engine.session_id}.{'json' if fmt == 'json' else 'md'}")
            try:
                out, n = await asyncio.to_thread(
                    export_session, src, dest, fmt=fmt, full=full, tools=tools)
                c.print(f"[green]exported {n} events → {out}[/green]")
                c.print("[dim]/export [path] [--json] [--full] [--no-tools][/dim]")
            except OSError as e:
                c.print(f"[red]export failed — {type(e).__name__}: {e}[/red]")

        elif cmd == "mcp":
            mcp = getattr(self.engine, "_mcp", None)
            servers = getattr(mcp, "servers", None) or {}
            if not servers:
                c.print(f"[dim]no external MCP servers connected — add them to "
                        f"{config.MCP_CONFIG_PATH}[/dim]")
            for name, tools in servers.items():
                c.print(f"  [cyan]{name}[/cyan]  [dim]{len(tools)} tool(s):[/dim] "
                        + ", ".join(f"{name}__{t}" for t in tools))
            for w in getattr(mcp, "warnings", None) or []:
                c.print(f"  [yellow]{w}[/yellow]")

        elif cmd == "library":
            from ..tools.library_tools import library
            from . import library_cmd

            await library_cmd.run(arg, c.print, library())

        elif cmd == "maxtokens":
            # config.MAX_OUTPUT_TOKENS is read on EVERY request (see
            # openai_compat._max_tokens), so setting it here takes effect on the
            # next turn — no restart, no lost session. That is the whole point:
            # hitting the ceiling mid-build should not cost you the context you
            # hit it with.
            if not arg:
                c.print(f"max output tokens: [cyan]{config.MAX_OUTPUT_TOKENS:,}[/cyan] "
                        "per generation   usage: /maxtokens <n>")
                if getattr(self.engine.backend, "n_ctx", None):
                    c.print(f"[dim]clamped per-request to what the "
                            f"{self.engine.backend.n_ctx:,}-token window can still "
                            f"hold[/dim]")
            elif arg.replace("_", "").replace(",", "").isdigit():
                n = int(arg.replace("_", "").replace(",", ""))
                if n < 256:
                    c.print("[red]too small — a generation needs at least 256[/red]")
                else:
                    config.MAX_OUTPUT_TOKENS = n
                    c.print(f"[green]max output tokens: {n:,} per generation "
                            "(this session)[/green]")
                    c.print("[dim]persist it with DREAM_MAX_TOKENS in the "
                            "environment[/dim]")
            else:
                c.print("usage: /maxtokens <n>")

        elif cmd == "toolcalls":
            if not arg:
                c.print(f"tool-call budget: [cyan]{self.engine.tool_budget.describe()}[/cyan] per prompt"
                        "   usage: /toolcalls <n|off>")
            elif arg.lower() in ("off", "none", "0", "unlimited"):
                self.engine.set_tool_budget(None)
                c.print("[green]tool-call budget: off (unlimited)[/green]")
            elif arg.isdigit() and int(arg) > 0:
                self.engine.set_tool_budget(int(arg))
                c.print(f"[green]tool-call budget: {int(arg)} per prompt[/green]")
            else:
                c.print("usage: /toolcalls <n|off>")
        elif cmd == "instructions":
            await self._instructions(arg)
        elif cmd == "thoughts":
            a = arg.lower().strip()
            if a in ("on", "persist", "persistent"):
                self.renderer.set_thoughts_persistent(True)
                c.print("[green]thoughts: persistent — reasoning streams to the transcript[/green]")
            elif a in ("off", "ephemeral", ""):
                self.renderer.set_thoughts_persistent(False)
                c.print("[green]thoughts: ephemeral — shown live, then gone[/green]")
            else:
                c.print("usage: /thoughts on|off")
        elif cmd == "council":
            moe_cfg = getattr(self.engine, "_moe", None)
            if moe_cfg is None:
                c.print("[dim]Choose advisors using Council in Studio, or start a Dream MoE session in the terminal picker.[/dim]")
            elif not arg:
                c.print("usage: /council <question>")
            else:
                await self._run_turn(self._consult_council(arg))
        elif cmd == "rewind":
            await self._rewind(arg)
        elif cmd == "review":
            await self._review(arg)
        elif cmd == "mind":
            c.print(self.mindmap.tree(store))
        elif cmd == "monitor":
            a = arg.lower()
            if a not in ("", "on", "off"):
                c.print(r"usage: /monitor \[on|off]")  # escaped — rich eats bare [on|off]
                return False
            self.show_monitor = (a == "on") if a else not self.show_monitor
            if self.show_monitor:
                await self._start_monitor()
            elif self.gpu is not None:
                gpu, self.gpu = self.gpu, None
                await asyncio.to_thread(gpu.stop)  # joins a thread — not on the loop
            state = "on" if self.show_monitor else "off"
            c.print(f"[cyan]monitor {state}[/cyan] "
                    f"[dim]— GPU + inference speed pane on the right[/dim]")
        elif cmd == "plan":
            if self.plan.has_tasks():
                c.print(self.plan.tree())
            else:
                c.print("[dim]no active plan — tasks appear here when Dream uses TodoWrite[/dim]")
        elif cmd == "verbose":
            if arg.lower() == "on":
                self._verbose = True
            elif arg.lower() == "off":
                self._verbose = False
            elif arg == "":
                self._verbose = not self._verbose  # bare = toggle
            else:
                c.print("usage: /verbose [on|off]")
                return False
            self.renderer.set_verbose(self._verbose)
            c.print(f"[cyan]verbose {'on' if self._verbose else 'off'}[/cyan] "
                    f"[dim]— tool output shown {'in full' if self._verbose else 'collapsed'}[/dim]")
        elif cmd == "errors":
            if not self.session_errors:
                c.print("[green]no errors this session ✓[/green]")
            else:
                total = len(self.session_errors)
                shown = self.session_errors[-20:]
                head = f"[bold red]{total} error(s) this session[/bold red]"
                if total > len(shown):
                    head += f" [dim](showing last {len(shown)})[/dim]"
                c.print(head)
                for i, (source, text) in enumerate(shown, 1):
                    c.print(f"[red]{i}. {source}[/red]")
                    c.print(f"[dim]{text}[/dim]")
        elif cmd == "new":
            self.renderer.system("consolidating and starting a fresh session…")
            await self.engine.stop(consolidate=True)
            self._reset_session_accounting()
            self.engine = self._boot_engine()
            await self.engine.start()
            self._project_open_error = None
            self.bus.conversation.clear()
            self.bus.publish(Event("history", {"events": [], "trimmed": False}))
            await self._associate_project_on_start()
            self.provider_label = self.engine.provider_label
            self.provider_kind = self.engine.provider.key
            self._welcome()
        else:
            c.print(f"[red]unknown command: /{cmd}[/red]  (try /help)")
        return False
