"""Where a session's time went: prefill, re-reads, decode, vision, tools, compactions (DREAM-127, fix list #102).

Read-only. Joins the session's runtime log (var/logs/runtime/<session>.jsonl) with the engine log
(var/logs/machx.log), the method of the 2026-09-25 gap analysis: sum the engine's [gen] lines (prefill ms and
decode ms), call a request with more than 10k uncached prompt tokens a re-read, and take tool time and
compactions from the runtime events. The engine lines of the session are the stamped lines between its first and
last runtime event (both logs carry wall-clock time since DREAM-127); a log from before that has no clock, and
then --machx-lines FIRST:LAST (1-based, inclusive) names them by hand, as the gap analysis did.

    python scripts/session_report.py <session-id> [--root DIR] [--machx-lines 16563:17000]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REREAD_TOKENS = 10_000        # uncached prompt tokens above which a request is a re-read
DROP_TOKENS = 3_000           # a prompt this much smaller than the one before it in a turn: a reset/compaction
JOIN_WINDOW_S = 30.0          # a usage event and its [gen] line are written within this of each other
MARGIN_S = 5.0

STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d{3})\] ")
GEN = re.compile(r"\[gen\] prefill (\d+) tok \((\d+) cached\) / (\d+) ms.*decode (\d+) tok / (\d+) ms")
VISION = re.compile(r"vision: image at \d+, .* encoded in (\d+) ms")


def _stamp_time(line: str) -> float | None:
    m = STAMP.match(line)
    if not m:
        return None
    return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) + int(m.group(2)) / 1000


def _engine_lines(log: list[str], events: list[dict], span: str | None) -> tuple[list[tuple[float | None, str]], str]:
    if span:
        first, last = (int(x) for x in span.split(":"))
        return [(_stamp_time(line), line) for line in log[first - 1:last]], f"machx.log lines {first}-{last}"
    stamps = [e["ts"] for e in events if isinstance(e.get("ts"), (int, float))]
    if not stamps:
        return [], "the runtime events carry no wall clock (logged before DREAM-127): pass --machx-lines"
    stamped = [(t, line) for t, line in ((_stamp_time(line), line) for line in log) if t is not None]
    if not stamped:
        return [], "no timestamped engine lines in machx.log (logged before DREAM-127): pass --machx-lines"
    lo, hi = min(stamps) - MARGIN_S, max(stamps) + MARGIN_S
    return [(t, line) for t, line in stamped if lo <= t <= hi], "machx.log lines stamped inside the session"


def _fmt(x: float | int | None, digits: int = 1) -> str:
    if x is None:
        return ""
    return f"{x:,.{digits}f}" if isinstance(x, float) else f"{x:,}"


def report(session: str, root: Path, span: str | None = None) -> str:
    runtime = root / "var" / "logs" / "runtime" / f"{session}.jsonl"
    events = [json.loads(line) for line in runtime.read_text(encoding="utf-8").splitlines() if line.strip()]
    machx = root / "var" / "logs" / "machx.log"
    # Bytes, split on "\n" only, as the stamper and grep -n count lines (read_text would turn "\r" into lines).
    log = machx.read_bytes().decode("utf-8", "replace").split("\n") if machx.exists() else []
    lines, source = _engine_lines(log, events, span)

    gens, vision_ms, images = [], 0, 0
    for t, line in lines:
        m = GEN.search(line)
        if m:
            p, c, pms, d, dms = map(int, m.groups())
            gens.append({"t": t, "prompt": p, "new": p - c, "prefill_ms": pms, "decode": d, "decode_ms": dms})
        m = VISION.search(line)
        if m:
            vision_ms += int(m.group(1))
            images += 1
    rereads = [g for g in gens if g["new"] > REREAD_TOKENS]

    usage = [e for e in events if e.get("event") == "usage"]
    tools = [e for e in events if e.get("event") == "tool_started"]
    tool_s = sum(((e.get("phases") or {}).get("tool_execution") or {}).get("seconds") or 0
                 for e in events if e.get("event") == "turn_timing")
    compactions = sum(1 for e in events if e.get("event") in ("context_compacted", "compaction"))
    drops, previous = 0, {}
    for e in usage:
        turn, n = e.get("turn"), int(e.get("input_tokens") or 0)
        if turn in previous and n < previous[turn] - DROP_TOKENS:
            drops += 1
        previous[turn] = n

    # Join: a usage event and the [gen] line with its prompt size, closest in time; with --machx-lines (lines picked
    # by hand, perhaps with no clock) by prompt size alone, the next such line in order.
    by_time = span is None
    free, joined = list(gens), 0
    for e in usage:
        match = [g for g in free if g["prompt"] == int(e.get("input_tokens") or 0)]
        if by_time and isinstance(e.get("ts"), (int, float)):
            match = sorted((g for g in match if abs(g["t"] - e["ts"]) <= JOIN_WINDOW_S), key=lambda g: abs(g["t"] - e["ts"]))
        if match:
            free.remove(match[0])
            joined += 1

    turns = sorted({e.get("turn") for e in events if e.get("turn") is not None})
    rows = [
        ("Prefill", len(gens), sum(g["prefill_ms"] for g in gens) / 1000, sum(g["new"] for g in gens)),
        (f"Re-reads (>{REREAD_TOKENS // 1000}k uncached)", len(rereads), sum(g["prefill_ms"] for g in rereads) / 1000,
         sum(g["new"] for g in rereads)),
        ("Decode", len(gens), sum(g["decode_ms"] for g in gens) / 1000, sum(g["decode"] for g in gens)),
        ("Vision encode", images, vision_ms / 1000, None),
        ("Tool execution", len(tools), float(tool_s), None),
        ("Compactions (logged)", compactions, None, None),
        ("Prompt drops (resets)", drops, None, None),
    ]
    out = [f"Session {session}: {len(turns)} turn(s), {len(usage)} requests in the runtime log; engine: {source}.",
           "", "| Item | Count | Seconds | Tokens |", "|---|---|---|---|"]
    out += [f"| {name} | {_fmt(n)} | {_fmt(s)} | {_fmt(tok)} |" for name, n, s, tok in rows]
    how = f"same prompt size, within {JOIN_WINDOW_S:g} s" if by_time else "same prompt size, in order"
    out += ["", f"{joined} of {len(usage)} usage events joined to an engine [gen] line ({how}).",
            f"{len(free)} [gen] line(s) in range not joined (still counted in the totals): requests this session's "
            "runtime log does not name, such as another client's."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("session")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--machx-lines", metavar="FIRST:LAST")
    args = parser.parse_args(argv)
    print(report(args.session, args.root, args.machx_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
