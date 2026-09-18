"""The right-side monitor pane: GPU telemetry + inference speed.

Pure formatting over two inputs — a ``GpuSnapshot`` from the sampler thread and
an ``InferenceMeter.snapshot()`` dict — rendered twice from ONE row model:
as rich ``Text`` lines for the live footer's right column while a turn works,
and as prompt_toolkit HTML for the bottom toolbar while idle at the prompt.

A row is a list of ``(text, color)`` fragments; colors are plain hex strings so
the exact same palette drives both renderers. No I/O, no state — everything
here is trivially testable.
"""

from __future__ import annotations

import html as _html
from typing import Any

from rich.text import Text

from .render import (
    MONITOR_MIN_TWO_COL as MIN_TWO_COL,
    MONITOR_PANE_WIDTH as PANE_WIDTH,
    _CYAN,
    _VIOLET,
    _dim_hex,
    _fmt_k,
    _fmt_tps,
)

_DIM = _dim_hex(_VIOLET, 0.55)
_AMBER = "#fbbf24"
_RED = "#ef4444"
_GREEN = "#34d399"
_BARS = "▁▂▃▄▅▆▇█"

Rows = list[list[tuple[str, str | None]]]


def _util_color(pct: float) -> str:
    if pct >= 85:
        return _RED
    if pct >= 60:
        return _AMBER
    if pct >= 10:
        return _CYAN
    return _DIM


def _temp_color(c: float) -> str:
    if c >= 85:
        return _RED
    if c >= 70:
        return _AMBER
    return _DIM


def _bar(pct: float) -> str:
    return _BARS[min(int(pct / 100.0 * (len(_BARS) - 1) + 0.5), len(_BARS) - 1)]


def _gib(mib: float) -> str:
    return f"{mib / 1024:.1f}"


def _fmt_s(seconds: float) -> str:
    return f"{seconds:.2f}s" if seconds < 10 else f"{seconds:.0f}s"


# --- the row model -----------------------------------------------------------


def build_rows(gpu: Any, im: dict[str, Any]) -> Rows:
    """The pane as fragment rows: GPU block (discrete cards first), the busiest
    processes, then the inference block."""
    rows: Rows = [[("⚡ monitor", _VIOLET)]]
    rows += _gpu_rows(gpu)
    rows += _inference_rows(im)
    return rows


def _gpu_rows(gpu: Any) -> Rows:
    if gpu is None or not getattr(gpu, "ok", False):
        reason = getattr(gpu, "reason", None) or "sampler off"
        return [[("gpu — ", _DIM), (str(reason), _DIM)]]
    rows: Rows = []
    devices = sorted(gpu.devices, key=lambda d: d.integrated)  # discrete first
    procs: list[Any] = []
    for d in devices:
        if d.util_pct is not None:
            util = (f"{_bar(d.util_pct)}{d.util_pct:3.0f}% ", _util_color(d.util_pct))
        else:
            util = ("···  ", _DIM)
        vram = ""
        if d.vram_used_mib is not None and d.vram_total_mib:
            vram = f"{_gib(d.vram_used_mib)}/{_gib(d.vram_total_mib)}G"
        if d.integrated:
            # The iGPU line stays terse: shared system memory, no name.
            rows.append([("igpu ", _CYAN), util, (f"{vram} shared" if vram else "", _DIM)])
            continue
        frags: list[tuple[str, str | None]] = [
            (f"GPU{d.index} ", _CYAN),
            (f"{d.name[:11]:<11} ", None),  # 'Arc Pro B70' fits exactly
            util,
        ]
        if d.power_w is not None:
            frags.append((f"{d.power_w:3.0f}W ", _DIM))
        if d.temp_c is not None:
            frags.append((f"{d.temp_c:2.0f}° ", _temp_color(d.temp_c)))
        if vram:
            frags.append((vram, _VIOLET))
        rows.append(frags)
        procs.extend(d.procs)
    fans = [d.fan_rpm for d in devices if not d.integrated and d.fan_rpm is not None]
    if fans:
        rows.append([("fan " + " · ".join(str(r) for r in fans) + " rpm", _DIM)])
    procs = [p for p in procs if p.vram_mib >= 100]
    if procs:
        procs.sort(key=lambda p: p.vram_mib, reverse=True)
        joined = " · ".join(f"{p.name[:14]} {_gib(p.vram_mib)}G" for p in procs[:3])
        rows.append([(joined, _DIM)])
    return rows


def _inference_rows(im: dict[str, Any]) -> Rows:
    rows: Rows = [[("─ inference ─", _DIM)]]
    state = im.get("state", "idle")
    last = im.get("last_turn") or {}
    sess = im.get("session") or {}

    if state == "idle" and not last and not sess.get("turns"):
        rows.append([("idle — metrics arrive with the first turn", _DIM)])
        return rows

    # state · elapsed · the headline live rate
    frags: list[tuple[str, str | None]] = [("● ", _GREEN if state != "idle" else _DIM),
                                           (state, None)]
    if state != "idle" and im.get("elapsed_s") is not None:
        frags.append((f" {im['elapsed_s']:.0f}s", _DIM))
    live = im.get("live_tps")
    if live is not None:
        frags.append((f" · ~{_fmt_tps(live)} t/s live", _CYAN))
    elif state != "idle" and im.get("out_tokens_est"):
        frags.append((f" · out ~{im['out_tokens_est']}", _DIM))
    rows.append(frags)

    # ttft + prompt processing on one row, the decode rate on the next — the
    # three numbers the user asked for stay whole at pane width, never ellipsized.
    ttft = im.get("ttft_s") if im.get("ttft_s") is not None else last.get("ttft_s")
    pp_tps = im.get("pp_tps") or last.get("pp_tps")
    gen = im.get("gen_tps") or last.get("gen_tps")
    frags = []
    if ttft is not None:
        frags.append((f"ttft {_fmt_s(ttft)}", _CYAN))
    if im.get("pp_time_s") is not None:
        frags.append((f"pp {_fmt_s(im['pp_time_s'])}"
                      + (f" @{_fmt_tps(pp_tps)} t/s" if pp_tps else ""), _VIOLET))
    elif pp_tps:
        frags.append((f"pp {_fmt_tps(pp_tps)} t/s", _VIOLET))
    if frags:
        rows.append(_dotted(frags))
    frags = []
    if gen:
        frags.append((f"gen {_fmt_tps(gen)} t/s", _GREEN))
    if im.get("out_tokens_est"):
        frags.append((f"out ~{im['out_tokens_est']} tok", _DIM))
    if frags:
        rows.append(_dotted(frags))

    # context footprint of the last completed pass
    if last.get("ctx_used") and last.get("n_ctx"):
        pct = last["ctx_used"] / last["n_ctx"]
        rows.append([(f"ctx {_fmt_k(last['ctx_used'])}/{_fmt_k(last['n_ctx'])}"
                      f" ({pct:.0%})", _VIOLET)])
    elif last.get("ctx_used"):
        rows.append([(f"ctx {_fmt_k(last['ctx_used'])}", _VIOLET)])

    # session rollup
    if sess.get("turns"):
        frags = [(f"Σ {sess['turns']}t", None),
                 (f"in {_fmt_k(sess.get('in_tokens') or 0)}"
                  f" · out {_fmt_k(sess.get('out_tokens') or 0)}", _DIM)]
        if sess.get("avg_tps"):
            frags.append((f"avg {_fmt_tps(sess['avg_tps'])} t/s", _DIM))
        rows.append(_dotted(frags))
    return rows


def _dotted(frags: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """Join fragments with a dim ' · ' separator."""
    out: list[tuple[str, str | None]] = []
    for i, f in enumerate(frags):
        if i:
            out.append((" · ", _DIM))
        out.append(f)
    return out


# --- renderers ---------------------------------------------------------------


def rich_lines(rows: Rows) -> list[Text]:
    lines = []
    for row in rows:
        t = Text(no_wrap=True, overflow="ellipsis")
        for s, color in row:
            t.append(s, style=color)
        t.truncate(PANE_WIDTH)
        lines.append(t)
    return lines


def pt_lines(rows: Rows) -> list[tuple[str, int]]:
    """Each row as (prompt_toolkit HTML, plain width) — the width drives column
    padding in the toolbar composer."""
    out = []
    for row in rows:
        html_parts = []
        plain = 0
        for s, color in row:
            budget = PANE_WIDTH - plain
            if budget <= 0:
                break
            s = s[:budget]
            plain += len(s)
            esc = _html.escape(s)
            html_parts.append(f'<style fg="{color}">{esc}</style>' if color else esc)
        out.append(("".join(html_parts), plain))
    return out


def compose_pt(
    left: list[tuple[str, int]], right: list[tuple[str, int]], width: int
) -> str:
    """Two-column prompt_toolkit body: left lines padded out to the column
    boundary, monitor lines on the right. A too-long left line goes ragged for
    that row rather than truncating information."""
    left_w = width - PANE_WIDTH - 2
    out = []
    for i in range(max(len(left), len(right))):
        lh, lp = left[i] if i < len(left) else ("", 0)
        rh, _rp = right[i] if i < len(right) else ("", 0)
        pad = max(left_w - lp, 1) if rh else 0
        out.append(lh + " " * pad + rh)
    return "\n".join(out)


def compact_strip(gpu: Any, im: dict[str, Any]) -> str | None:
    """A one-line plain summary for terminals too narrow for the side pane."""
    bits: list[str] = []
    live = im.get("live_tps")
    gen = im.get("gen_tps") or (im.get("last_turn") or {}).get("gen_tps")
    ttft = im.get("ttft_s") if im.get("ttft_s") is not None else (
        (im.get("last_turn") or {}).get("ttft_s"))
    if live is not None:
        bits.append(f"~{_fmt_tps(live)} t/s")
    elif gen:
        bits.append(f"gen {_fmt_tps(gen)} t/s")
    if ttft is not None:
        bits.append(f"ttft {_fmt_s(ttft)}")
    if gpu is not None and getattr(gpu, "ok", False):
        fans = []
        for d in gpu.devices:
            if d.integrated:
                continue
            u = f"{d.util_pct:.0f}%" if d.util_pct is not None else "?"
            w = f" {d.power_w:.0f}W" if d.power_w is not None else ""
            bits.append(f"GPU{d.index} {u}{w}")
            if d.fan_rpm is not None:
                fans.append(str(d.fan_rpm))
        if fans:
            bits.append(f"fan {'/'.join(fans)}")
    return "⚡ " + " · ".join(bits) if bits else None
