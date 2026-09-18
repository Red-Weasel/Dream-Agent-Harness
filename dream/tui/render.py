"""Rendering for the TUI: streaming assistant text, compact tool traces, banners.

The agent sees full tool results; the human sees tidy summaries. Assistant text is
streamed raw (markup disabled) so the model's brackets and code never get mangled.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from rich import box as rbox
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# (config is intentionally not imported here — the renderer is presentation only.)

# Brand palette — the atom logo's violet→cyan orbit, as terminal colors.
_BRAND_STOPS = ((0x7C, 0x3A, 0xED), (0x8B, 0x5C, 0xF6), (0x60, 0xA5, 0xFA), (0x22, 0xD3, 0xEE))
_VIOLET = "#a78bfa"
_PURPLE = "#8b5cf6"
_BLUE = "#60a5fa"
_CYAN = "#22d3ee"

_WORDMARK = (
    "██████╗ ██████╗ ███████╗ █████╗ ███╗   ███╗",
    "██╔══██╗██╔══██╗██╔════╝██╔══██╗████╗ ████║",
    "██║  ██║██████╔╝█████╗  ███████║██╔████╔██║",
    "██║  ██║██╔══██╗██╔══╝  ██╔══██║██║╚██╔╝██║",
    "██████╔╝██║  ██║███████╗██║  ██║██║ ╚═╝ ██║",
    "╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝",
)


def _brand_color(t: float) -> str:
    """Hex color at position t∈[0,1] along the brand gradient."""
    t = min(max(t, 0.0), 1.0) * (len(_BRAND_STOPS) - 1)
    i = min(int(t), len(_BRAND_STOPS) - 2)
    f = t - i
    a, b = _BRAND_STOPS[i], _BRAND_STOPS[i + 1]
    return "#{:02x}{:02x}{:02x}".format(
        round(a[0] + (b[0] - a[0]) * f),
        round(a[1] + (b[1] - a[1]) * f),
        round(a[2] + (b[2] - a[2]) * f),
    )


def _dim_hex(hex_color: str, f: float = 0.55) -> str:
    """Scale a hex color toward black — an explicit 'dim' that renders the same
    everywhere (terminals disagree about the dim attribute)."""
    r = round(int(hex_color[1:3], 16) * f)
    g = round(int(hex_color[3:5], 16) * f)
    b = round(int(hex_color[5:7], 16) * f)
    return f"#{r:02x}{g:02x}{b:02x}"


def brand_rule(width: int, f: float = 0.55) -> Text:
    """A full-width ─ rule wearing a muted version of the logo's violet→cyan
    gradient — the frame line above/below the prompt and atop the status panel."""
    t = Text()
    pos = 0
    chunk = max(width // 24, 1)
    while pos < width:
        w = min(chunk, width - pos)
        t.append("─" * w, style=_dim_hex(_brand_color(pos / max(width - 1, 1)), f))
        pos += w
    return t


def pt_gradient_rule(width: int, f: float = 0.55) -> str:
    """The same gradient rule as prompt_toolkit HTML, for the bottom toolbar."""
    parts = []
    pos = 0
    chunk = max(width // 24, 1)
    while pos < width:
        w = min(chunk, width - pos)
        color = _dim_hex(_brand_color(pos / max(width - 1, 1)), f)
        parts.append(f'<style fg="{color}">{"─" * w}</style>')
        pos += w
    return "".join(parts)


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("dream")
    except Exception:
        return "0.1"


def _fmt_k(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(int(n))


def _fmt_dur(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _fmt_tps(tps: float) -> str:
    return f"{tps:.0f}" if tps >= 100 else f"{tps:.1f}"


def format_turn_stats(
    data: dict[str, Any], fallback_window: int | None = None, now: Any = None
) -> str | None:
    """One dim line per completed pass: when it finished (wall clock — so you know
    how long it's been sitting done), how long it ran, context fill, and the
    prefill/decode split. Works from backend-measured stats when present and
    degrades to whatever the result event carries."""
    from datetime import datetime

    stats = data.get("stats") or {}
    usage = data.get("usage") or {}
    now = now or datetime.now()

    dur = stats.get("duration_s")
    if dur is None and data.get("duration_ms"):
        dur = data["duration_ms"] / 1000.0

    used = stats.get("ctx_used")
    if used is None and usage:
        used = (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + (usage.get("output_tokens") or 0)
        ) or None
    window = stats.get("n_ctx") or fallback_window

    parts = [f"done {now.strftime('%H:%M:%S')}"]
    if dur:
        parts.append(_fmt_dur(dur))
    if used and window:
        parts.append(f"ctx {_fmt_k(used)}/{_fmt_k(window)} ({used / window:.0%})")
    elif used:
        parts.append(f"ctx {_fmt_k(used)}")
    if stats.get("pp_tps"):
        parts.append(f"pp {_fmt_tps(stats['pp_tps'])} t/s")
    if stats.get("gen_tps"):
        parts.append(f"gen {_fmt_tps(stats['gen_tps'])} t/s")
    elif usage.get("output_tokens") and dur:
        # Hosted APIs don't expose the prefill/decode split; show effective rate.
        parts.append(f"gen ~{_fmt_tps(usage['output_tokens'] / dur)} t/s")
    if data.get("total_cost_usd"):
        parts.append(f"${data['total_cost_usd']:.2f}")
    if len(parts) == 1 and not dur:
        return None  # nothing worth a line
    return "⏱ " + " · ".join(parts)


def _preview(value: Any, limit: int = 80) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def summarize_tool_input(name: str, tool_input: dict[str, Any]) -> str:
    """A one-line human summary of a tool call."""
    short = name.replace("mcp__dream__", "")
    if not tool_input:
        return short
    # Show the most meaningful field first for common tools.
    for key in ("query", "url", "title", "command", "file_path", "path", "text", "slug"):
        if key in tool_input:
            return f"{short}({key}={_preview(tool_input[key])})"
    first = next(iter(tool_input.items()))
    return f"{short}({first[0]}={_preview(first[1])})"


# Whimsical present-participles, Claude-Code style — shown while a tool runs so a
# repeated call reads as motion, not a broken stutter.
_GERUNDS = (
    "Flambéing", "Osmosing", "Whirring", "Percolating", "Conjuring", "Marinating",
    "Untangling", "Noodling", "Kneading", "Simmering", "Effervescing", "Tessellating",
    "Ruminating", "Galvanizing", "Burnishing", "Cavorting", "Frobnicating", "Wrangling",
)


def _gerund(seed: int) -> str:
    return _GERUNDS[seed % len(_GERUNDS)]


_SEP_STYLE = _dim_hex(_PURPLE, 0.75)

# The monitor pane (tui.monitor) renders into the right column of the footer /
# toolbar. Constants live HERE so monitor.py can import them without a cycle.
MONITOR_PANE_WIDTH = 42
MONITOR_MIN_TWO_COL = 100  # narrower terminals get the one-line compact strip


def build_footer(d: dict[str, Any], width: int) -> Group:
    """The status panel pinned under the feed while a turn works — the rich twin
    of the prompt_toolkit bottom toolbar, topped with the brand gradient rule so
    the frame under the prompt never disappears."""
    rows: list[Any] = [brand_rule(width)]
    top = Text(" ")
    if d.get("gerund"):
        top.append(f"✻ {d['gerund']}… ", style=f"italic {_VIOLET}")
        if d.get("elapsed") is not None:
            e = int(d["elapsed"])  # whole seconds — a calm clock, not a jittery one
            top.append(f"({e}s)" if e < 60 else f"({_fmt_dur(e)})", style="dim")
        top.append("   ")
    top.append(f"⏵⏵ {d['mode_label']}", style=f"bold {d['mode_color']}")
    top.append(f" · ws {d['ws']}", style="dim")
    if d.get("effort"):
        top.append(f" · effort {d['effort']}", style="dim")
    rows.append(top)
    mid = Text(" ")
    sep = Text(" │ ", style=_SEP_STYLE)
    # Zero tallies stay quiet; a count only takes its color once it's nonzero.
    for label, count, color in (
        ("created", d["created"], "green"),
        ("edited", d["edited"], "yellow"),
        ("deleted", d["deleted"], "red"),
        ("tools", d["tools"], _CYAN),
    ):
        mid.append(f"{label} {count}", style=color if count else "dim")
        mid.append_text(sep.copy())
    mid.append(d["tokens"], style=_VIOLET)
    rows.append(mid)
    rows.append(Text(f" {d['recent']}", style="dim"))

    # --- cockpit: plan · mind · friction ---
    panel = d.get("plan_panel")
    plan_strip = d.get("plan_strip")
    if panel is not None:
        rows.append(panel)
    elif plan_strip is not None and str(plan_strip):
        row = Text(" ")
        row.append_text(plan_strip)
        rows.append(row)
    mind = d.get("mind")
    if mind is not None and str(mind):
        row = Text(" ")
        row.append_text(mind)
        rows.append(row)
    spark = d.get("friction_spark")
    if spark or d.get("friction_label"):
        fr = Text(" friction ", style="dim")
        fr.append(spark or "", style=_VIOLET)
        fr.append(f" {d.get('friction_label', '')}", style="dim")
        if d.get("friction_reason"):
            fr.append(f"  ⚠ {d['friction_reason']}", style="#fbbf24")
        rows.append(fr)

    # --- the monitor pane: right column when there's room, one strip when not ---
    mon = d.get("monitor")  # list[Text] from tui.monitor.rich_lines, or None
    if mon and width >= MONITOR_MIN_TWO_COL:
        rule, body = rows[0], rows[1:]
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(width=MONITOR_PANE_WIDTH + 2)
        grid.add_row(Group(*body), Padding(Group(*mon), (0, 0, 0, 2)))
        return Group(rule, grid)
    if mon and d.get("monitor_compact"):
        rows.append(Text(f" {d['monitor_compact']}", style="dim"))
    return Group(*rows)


class Renderer:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._open: str | None = None  # None | "assistant" | "thinking"
        # Collapsed tool run: consecutive identical calls fold into one line + ×N.
        self._run_sig: str | None = None
        self._run_short: str = ""
        self._run_count = 0
        self._run_ok = 0
        self._run_err = 0
        self._run_note = ""
        self._gerund_seed = 0
        # Live mode: while a turn works, a rich Live pins the status footer to the
        # bottom and the in-progress line renders inside it; completed lines are
        # printed above. (Partial prints above a Live region would garble it.)
        self._live = None
        self._live_paused = False
        self._status_cb = None
        self._saved_termios = None  # stdin echo settings saved while the footer is up
        self._verbose = False  # /verbose: show tool output in full, not collapsed
        self._accum: list[tuple[str, str | None]] = []  # the unflushed partial line
        # Ephemeral thoughts: reasoning streams inside the live region only and
        # vanishes when the answer lands (ChatGPT/Grok-style), instead of piling into
        # the transcript. /thoughts on (or DREAM_THOUGHTS_PERSIST=1) restores the
        # classic persistent 💭 stream.
        self._thoughts_persistent = os.environ.get("DREAM_THOUGHTS_PERSIST") == "1"
        self._thought_buf = ""  # accumulated ephemeral reasoning (never flushed)

    def set_thoughts_persistent(self, on: bool) -> None:
        self._thoughts_persistent = on
        if not on:
            self._thought_buf = ""

    # --- live footer ---------------------------------------------------------

    def live_begin(self, status_cb) -> bool:
        """Pin a status footer (from status_cb() → renderable) below the feed for
        the duration of a turn. No-op off-terminal; returns whether live is on."""
        if not self.console.is_terminal or self._live is not None:
            return self._live is not None
        self._status_cb = status_cb
        self._start_live()
        return True

    def _start_live(self) -> None:
        from rich.live import Live

        self._live = Live(
            get_renderable=self._live_view,
            console=self.console,
            refresh_per_second=8,
            transient=True,
        )
        self._live.start(refresh=True)
        self._echo_off()

    def _echo_off(self) -> None:
        """Suppress stdin echo while the Live footer owns the screen. Without this,
        keys typed mid-turn are echoed by the terminal INTO the region rich is
        repainting — which garbles the display (hitting Enter during a tool run was
        the trigger). We don't read stdin during a turn, so nothing is lost;
        live_end() flushes whatever was typed. No-op unless stdin is a real TTY, and
        every failure is swallowed so terminal twiddling can never break a turn."""
        if self._saved_termios is not None:
            return  # already suppressed (begin → resume with no restore between)
        try:
            import termios

            fd = sys.stdin.fileno()
            if not os.isatty(fd):
                return
            saved = termios.tcgetattr(fd)
            new = termios.tcgetattr(fd)
            new[3] = new[3] & ~(termios.ECHO | termios.ECHONL)  # lflags: no echo
            termios.tcsetattr(fd, termios.TCSANOW, new)
            self._saved_termios = saved
        except Exception:
            self._saved_termios = None

    def _echo_restore(self, flush: bool = False) -> None:
        """Restore the stdin echo settings _echo_off saved. flush=True also drops
        anything typed during the turn so it can't leak into the next prompt."""
        saved, self._saved_termios = self._saved_termios, None
        if saved is None:
            return
        try:
            import termios

            fd = sys.stdin.fileno()
            termios.tcsetattr(fd, termios.TCSANOW, saved)
            if flush:
                termios.tcflush(fd, termios.TCIFLUSH)
        except Exception:
            pass

    def force_echo_on(self) -> None:
        """Guarantee a normal echoing, canonical terminal for a raw input() read —
        no matter what echo suppression the Live footer left behind. Without this a
        question's answer field could be invisible (echo off) or eat keystrokes.
        Clears the suppression bookkeeping so a later turn re-arms it cleanly."""
        self._saved_termios = None
        try:
            import termios

            fd = sys.stdin.fileno()
            if not os.isatty(fd):
                return
            m = termios.tcgetattr(fd)
            m[3] |= (termios.ECHO | termios.ECHONL | termios.ICANON)  # echo + line mode ON
            termios.tcsetattr(fd, termios.TCSANOW, m)
        except Exception:
            pass

    def _live_view(self):
        parts: list[Any] = []
        accum = self._accum[:]  # snapshot — the refresh thread renders concurrently
        if accum:
            t = Text()
            for s, style in accum:
                t.append(s, style=style)
            parts.append(t)
        # Ephemeral reasoning: the last ~2 lines' worth, dim, above the footer —
        # gone the instant answer text starts.
        thought = self._thought_buf
        if thought.strip():
            tail = " ".join(thought.split())[-200:]
            parts.append(Text(f"  💭 {tail}", style=f"italic {_dim_hex(_VIOLET, 0.7)}"))
        try:
            footer = self._status_cb() if self._status_cb else None
        except Exception:
            footer = None
        if footer is not None:
            parts.append(footer)
        return Group(*parts)

    def live_end(self) -> None:
        self._live_paused = False
        self._thought_buf = ""  # thoughts don't survive the turn
        self._echo_restore(flush=True)  # echo back on FIRST — even if _live is None,
        #                                 so echo can never be left suppressed
        if self._live is None:
            return
        if self._accum:
            self._flush_accum()  # partial line joins the transcript before teardown
        live, self._live = self._live, None
        self._status_cb = None
        live.stop()

    def live_pause(self) -> None:
        """Drop the footer while stdin is needed (permission prompts)."""
        if self._live is not None:
            live, self._live = self._live, None
            live.stop()
            self._echo_restore()  # permission input() must echo the typed y/n
            self._live_paused = True

    def live_resume(self) -> None:
        if self._live_paused:
            self._live_paused = False
            self._start_live()

    # --- streaming (assistant text + thinking) ------------------------------

    def _flush_accum(self) -> None:
        """Print the buffered line into the transcript (above the live region)."""
        t = Text()
        for s, style in self._accum:
            t.append(s, style=style)
        self._accum.clear()
        self.console.print(t)

    def _push(self, text: str, style: str | None) -> None:
        """Buffer streamed text; each completed line is printed above the footer,
        the trailing partial stays visible inside the live region."""
        while "\n" in text:
            head, text = text.split("\n", 1)
            if head:
                self._accum.append((head, style))
            self._flush_accum()
        if text:
            self._accum.append((text, style))

    def _open_stream(self, kind: str, parts: list[tuple[str, str | None]]) -> None:
        if self._open == kind:
            return
        self._flush_tool_run()  # close any collapsed tool run before text streams
        self.end_line()
        self._open = kind
        if self._live is not None:
            for s, style in parts:
                self._push(s, style)
        else:
            prefix = Text()
            for s, style in parts:
                prefix.append(s, style=style)
            self.console.print(prefix, end="")

    def assistant_delta(self, text: str) -> None:
        self._thought_buf = ""  # the answer is landing — ephemeral thoughts vanish
        self._open_stream(
            "assistant", [("\ndream ", f"bold {_CYAN}"), ("› ", _BLUE)]
        )
        if self._live is not None:
            self._push(text, None)
        else:
            self.console.print(text, end="", markup=False, highlight=False)

    def thinking_delta(self, text: str) -> None:
        if self._thoughts_persistent:
            # Classic behavior: the reasoning streams into the transcript.
            self._open_stream("thinking", [("\n  💭 ", f"italic {_VIOLET}")])
            if self._live is not None:
                self._push(text, "dim italic")
            else:
                self.console.print(
                    Text(text, style="dim italic"), end="", markup=False, highlight=False
                )
            return
        # Ephemeral: accumulate for the live region only; never touch the transcript.
        # Off-terminal (no live region) there's nowhere transient to show it, so drop it.
        if self._live is None:
            return
        self._thought_buf += text
        lines = self._thought_buf.splitlines()
        if len(lines) > 8:  # keep the buffer bounded to recent reasoning
            self._thought_buf = "\n".join(lines[-8:])

    def end_line(self) -> None:
        if self._accum:
            self._flush_accum()
            self._open = None
            return
        if self._open is not None and self._live is None:
            self.console.print()
        self._open = None

    # --- tools ---------------------------------------------------------------

    def _flush_tool_run(self) -> None:
        """Emit the collapsed line for the current run of identical tool calls.
        One call → a normal line; many → one line with ×N and a pass/fail tally."""
        if self._run_sig is None:
            return
        n = self._run_count
        head = Text("  ⚙ ", style=_BLUE) + Text(self._run_sig, style=_dim_hex(_BLUE, 0.72))
        if n > 1:
            head += Text(f"  ×{n}", style=f"bold {_CYAN}")
        self.console.print(head)
        if self._run_ok or self._run_err:
            if self._run_err and not self._run_ok:
                line = Text("    ✗ ", style="red") + Text(
                    f"{self._run_short}: {self._run_note}", style="red"
                )
            elif self._run_err:
                line = Text("    ✓ ", style="green") + Text(
                    f"{self._run_ok} ok, ", style="dim"
                ) + Text(f"{self._run_err} failed", style="red")
            elif n > 1:
                line = Text("    ✓ ", style="green") + Text(
                    f"{self._run_ok}× · last: {self._run_note}", style="dim"
                )
            else:
                line = Text("    ✓ ", style="green") + Text(self._run_note, style="dim")
            self.console.print(line)
        self._run_sig = None
        self._run_count = self._run_ok = self._run_err = 0
        self._run_note = ""

    def tool_use(self, name: str, tool_input: dict[str, Any]) -> None:
        self._thought_buf = ""  # reasoning resolved into an action
        sig = summarize_tool_input(name, tool_input)
        if sig == self._run_sig:
            self._run_count += 1  # same call again — fold it in, don't reprint
            return
        self._flush_tool_run()
        self.end_line()
        self._run_sig = sig
        self._run_short = name.replace("mcp__dream__", "")
        self._run_count = 1

    def set_verbose(self, on: bool) -> None:
        self._verbose = on

    def tool_result(self, name: str, content: str, is_error: bool) -> None:
        # Attributed to the open run; printed when the run flushes. Errors get a
        # much longer preview than successes (you need to read them), and /verbose
        # opens both up to near-full so nothing important is clipped on screen.
        if is_error:
            self._run_err += 1
            self._run_note = _preview(content, 4000 if self._verbose else 400)
        else:
            self._run_ok += 1
            self._run_note = _preview(content, 4000 if self._verbose else 100)

    def card(self, name: str, text: str) -> None:
        """A visual-answer card's text form, whole. The preview line above it is
        one collapsed line; the card is the answer, so it is not clipped."""
        self._flush_tool_run()
        self.end_line()
        body = re.sub(r"\n\n\((shown (as a card|inline) in Studio)\)\s*$", "", text)
        self.console.print(Panel(Text(body), title=f"[dim]{name}[/dim]", border_style=_dim_hex(_PURPLE, 0.8),
                                 expand=False, padding=(0, 1)))

    def working(self) -> None:
        """A one-line 'thinking' cue at the top of a turn — a rotating gerund so a
        working turn reads as motion even before the first token streams. Under a
        live footer the gerund animates there instead, so nothing is printed."""
        self._gerund_seed += 1
        if self._live is not None:
            return
        self.console.print(
            Text(f"\n  ✻ {_gerund(self._gerund_seed)}…", style=f"italic {_VIOLET}")
        )

    @property
    def gerund(self) -> str:
        """The current turn's gerund — the live footer displays it with a timer."""
        return _gerund(self._gerund_seed)

    # --- misc ----------------------------------------------------------------

    def result(self, data: dict[str, Any]) -> None:
        self._flush_tool_run()
        self.end_line()
        if data.get("is_error"):
            extra = ""
            if data.get("api_error_status"):
                extra = f" (HTTP {data['api_error_status']})"
            self.console.print(
                Text(f"  ⚠ turn ended with an error: {data.get('subtype')}{extra}", style="red")
            )

    def turn_stats(self, data: dict[str, Any], fallback_window: int | None = None) -> None:
        line = format_turn_stats(data, fallback_window=fallback_window)
        if line:
            self.end_line()
            self.console.print(Text(f"  {line}", style="dim"))

    def error(self, msg: str) -> None:
        self._flush_tool_run()
        self.end_line()
        self.console.print(Text(f"  ⚠ {msg}", style="bold red"))

    def system(self, msg: str) -> None:
        self._flush_tool_run()
        self.end_line()
        self.console.print(Text(f"  · {msg}", style="dim"))

    def info(self, msg: str) -> None:
        self.console.print(Text(msg, style=_CYAN))

    def rule(self, label: str = "") -> None:
        self.console.rule(label, style=_dim_hex(_PURPLE, 0.8))

    def sep(self) -> None:
        """A thin divider between turns — a full-width rule in the brand gradient,
        rendered locally (costs no model tokens)."""
        self.console.print(brand_rule(self.console.width))

    # --- permission prompt ---------------------------------------------------

    def permission_request(self, name: str, desc: str) -> None:
        self._flush_tool_run()
        self.end_line()
        self.console.print(
            Panel(
                Text(desc, style="bold"),
                title=f"[yellow]permission — {name}[/yellow]",
                border_style="yellow",
                expand=False,
            )
        )

    # --- banner --------------------------------------------------------------

    def show_logo(self) -> None:
        """The Dream wordmark: native ANSI with the brand's violet→cyan
        gradient. No raster art — crisp at any cell size, instant, and rich
        downsamples the colors gracefully on non-truecolor terminals."""
        if not self.console.is_terminal and os.environ.get("DREAM_FORCE_LOGO") != "1":
            return
        width = max(len(ln) for ln in _WORDMARK)
        out = Text("\n")
        out.append("  ❋ ", style=f"bold {_VIOLET}")
        out.append("D R E A M", style=f"bold {_VIOLET}")
        out.append(f"   v{_version()}", style="dim")
        out.append("\n\n")
        for line in _WORDMARK:
            out.append("  ")
            for x, ch in enumerate(line):
                color = _brand_color(x / max(width - 1, 1))
                # Solid blocks carry the gradient; the ╔═╝ shadow recedes.
                out.append(ch, style=f"bold {color}" if ch == "█" else f"dim {color}")
            out.append("\n")
        # The engine lockup: MACH╳ wears the same gradient, everything around it quiet.
        out.append("\n  ")
        out.append("⚡ ", style="bold #fbbf24")
        out.append("powered by  ", style="dim italic")
        mach = "MACH╳"
        for i, ch in enumerate(mach):
            out.append(ch, style=f"bold {_brand_color(i / (len(mach) - 1))}")
            out.append(" ")
        out.append(" I N F E R E N C E", style="dim")
        out.append("\n")
        out.append(
            "\n  Your models. Your workspace.\n",
            style="dim italic",
        )
        self.console.print(out)

    def welcome(self, rows: list[tuple[str, str]], notes: list[str] | None = None) -> None:
        """The landing panel: session facts in a rounded box, Claude-Code style."""
        body = Text()
        body.append("Home. Awake. This harness is yours.\n\n", style=f"italic {_VIOLET}")
        keyw = max((len(k) for k, _ in rows), default=0)
        for k, v in rows:
            body.append(f" {k.ljust(keyw)}  ", style=f"bold {_CYAN}")
            body.append(f"{v}\n")
        for note in notes or []:
            body.append(f"\n {note}", style="dim")
        body.append("\n\n /help for commands · Ctrl-D to leave", style="dim")
        self.console.print(
            Panel(
                body,
                box=rbox.ROUNDED,
                title=f"[bold {_VIOLET}]❋ Dream[/]",
                title_align="left",
                border_style="#8b5cf6",
                expand=False,
                padding=(0, 2),
            )
        )
