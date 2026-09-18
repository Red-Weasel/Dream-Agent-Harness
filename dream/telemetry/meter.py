"""Inference speed, measured live from the event stream.

The meter is fed every Event the TUI renders and answers the questions the
monitor pane displays in real time: how long until the first token (TTFT),
how fast tokens are streaming *right now* (sliding-window tokens/s), what the
exact server-measured prefill/decode split was (when a backend reports one),
and what this session has cost in tokens and time.

Two grades of truth, clearly separated:

- **estimates** while streaming — hosted APIs stream text chunks, not token
  counts, so the live rate estimates tokens as chars/4 and is labelled ``~``;
- **exact** numbers absorbed from ``stats`` events (per round, llama.cpp-style
  server timings) and ``result`` events (per turn) — these overwrite the
  estimates whenever they exist.

Pure bookkeeping: no I/O, no threads, injectable clock. Safe to feed from any
event loop.
"""

from __future__ import annotations

import time
import math
from collections import deque
from typing import Any, Callable

_WINDOW_S = 3.0  # sliding window for the live rate
_CHARS_PER_TOKEN = 4.0  # honest rough estimate; exact stats replace it


class InferenceMeter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.state = "idle"  # idle | waiting | thinking | decoding | tools
        # --- per-turn ---
        self._turn_t0: float | None = None
        self.ttft_s: float | None = None  # first output of the turn
        self._round_t0: float | None = None  # since turn start or last tool result
        self.round_ttft_s: float | None = None  # prefill proxy for the latest round
        self._out_chars = 0
        self._samples: deque[tuple[float, float]] = deque()  # (t, cumulative est tokens)
        # exact per-round numbers from the newest "stats" event
        self.pp_tps: float | None = None
        self.gen_tps: float | None = None
        self.pp_time_s: float | None = None
        self.prompt_tokens: int | None = None
        # --- rollups ---
        self.last_turn: dict[str, Any] | None = None
        self.session_turns = 0
        self.session_in_tokens = 0
        self.session_out_tokens = 0
        self.session_gen_s = 0.0

    # --- feeding -------------------------------------------------------------

    def turn_start(self) -> None:
        now = self._clock()
        self.state = "waiting"
        self._turn_t0 = now
        self._round_t0 = now
        self.ttft_s = None
        self.round_ttft_s = None
        self._out_chars = 0
        self._samples.clear()
        self.pp_tps = self.gen_tps = self.pp_time_s = None
        self.prompt_tokens = None

    def turn_end(self) -> None:
        """The turn is over without a ``result`` event — interrupted, or ended by
        an error. Only ``result`` used to return the meter to idle, so an
        interrupted turn left the pane claiming "decoding" forever."""
        self.state = "idle"
        self._samples.clear()

    def feed(self, kind: str, data: Any) -> None:
        if kind in ("text_delta", "thinking_delta"):
            self._on_delta(kind, data if isinstance(data, str) else "")
        elif kind == "tool_use":
            self.state = "tools"
        elif kind == "tool_result":
            # The next generation round starts prefilling as soon as the result
            # is in the context; its first delta closes round_ttft.
            self._round_t0 = self._clock()
            self.round_ttft_s = None
        elif kind == "stats" and isinstance(data, dict):
            self._on_round_stats(data)
        elif kind == "result" and isinstance(data, dict):
            self._on_result(data)
        elif kind == "background_usage" and isinstance(data, dict):
            # This event is live metadata, never a retained/replayed result. It
            # adds received usage without resetting the active foreground turn.
            usage = data.get("usage")
            if isinstance(usage, dict):
                incoming = usage.get("prompt_tokens")
                outgoing = usage.get("completion_tokens")
                if type(incoming) is int and incoming >= 0:
                    self.session_in_tokens += incoming
                if type(outgoing) is int and outgoing >= 0:
                    self.session_out_tokens += outgoing
            duration = data.get("duration_s")
            if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
                self.session_gen_s += duration

    def _on_delta(self, kind: str, text: str) -> None:
        if not text:
            return  # an empty/None delta is no evidence of anything
        now = self._clock()
        if self._turn_t0 is not None and self.ttft_s is None:
            self.ttft_s = now - self._turn_t0
        if self._round_t0 is not None and self.round_ttft_s is None:
            self.round_ttft_s = now - self._round_t0
        self.state = "thinking" if kind == "thinking_delta" else "decoding"
        self._out_chars += len(text)
        self._samples.append((now, self._out_chars / _CHARS_PER_TOKEN))
        cutoff = now - _WINDOW_S
        while len(self._samples) > 1 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _on_round_stats(self, s: dict[str, Any]) -> None:
        """Exact per-round numbers (openai_compat: llama.cpp server timings or
        the client-side TTFT split). Newest round wins the display."""
        try:
            pp_ms = float(s.get("pp_ms") or 0.0)
            gen_ms = float(s.get("gen_ms") or 0.0)
            pp_n = int(s.get("pp_n") or 0)
            gen_n = int(s.get("gen_n") or 0)
        except (TypeError, ValueError):
            return  # a malformed stats payload is ignored, never fatal
        if pp_ms > 0:
            self.pp_time_s = pp_ms / 1000.0
            if pp_n:
                self.pp_tps = pp_n / (pp_ms / 1000.0)
        if gen_ms > 0 and gen_n:
            self.gen_tps = gen_n / (gen_ms / 1000.0)
        if pp_n:
            self.prompt_tokens = pp_n

    def _on_result(self, data: dict[str, Any]) -> None:
        stats = data.get("stats") or {}
        usage = data.get("usage") or {}
        dur = stats.get("duration_s")
        if dur is None and data.get("duration_ms"):
            dur = data["duration_ms"] / 1000.0
        in_tok = int(stats.get("prompt_tokens") or usage.get("input_tokens")
                     or usage.get("prompt_tokens") or 0)
        out_tok = int(stats.get("completion_tokens") or usage.get("output_tokens")
                      or usage.get("completion_tokens") or 0)
        gen_tps = stats.get("gen_tps")
        if gen_tps is None and out_tok and dur:
            gen_tps = out_tok / dur  # effective rate — hosted APIs hide the split
        self.last_turn = {
            "duration_s": dur,
            "ttft_s": self.ttft_s,
            "pp_tps": stats.get("pp_tps") or self.pp_tps,
            "gen_tps": gen_tps,
            "in_tokens": in_tok or None,
            "out_tokens": out_tok or None,
            "ctx_used": stats.get("ctx_used"),
            "n_ctx": stats.get("n_ctx"),
        }
        self.session_turns += 1
        self.session_in_tokens += in_tok
        self.session_out_tokens += out_tok
        if dur:
            self.session_gen_s += float(dur)
        self.state = "idle"

    # --- reading -------------------------------------------------------------

    def live_tps(self) -> float | None:
        """Estimated decode rate over the sliding window — None when idle, when
        there aren't two samples to draw a line through, or when nothing has
        streamed for a whole window (a stalled or abandoned turn must not keep
        reporting the rate it had when it stopped)."""
        if self.state not in ("decoding", "thinking") or len(self._samples) < 2:
            return None
        (t0, n0), (t1, n1) = self._samples[0], self._samples[-1]
        if t1 <= t0 or self._clock() - t1 > _WINDOW_S:
            return None
        return (n1 - n0) / (t1 - t0)

    def out_tokens_est(self) -> int:
        return int(self._out_chars / _CHARS_PER_TOKEN)

    def elapsed_s(self) -> float | None:
        return self._clock() - self._turn_t0 if self._turn_t0 is not None else None

    def snapshot(self) -> dict[str, Any]:
        """Everything the monitor pane needs, in one dict."""
        sess_tps = (
            self.session_out_tokens / self.session_gen_s if self.session_gen_s > 0 else None
        )
        return {
            "state": self.state,
            "ttft_s": self.ttft_s,
            "round_ttft_s": self.round_ttft_s,
            "pp_time_s": self.pp_time_s,
            "pp_tps": self.pp_tps,
            "gen_tps": self.gen_tps,
            "live_tps": self.live_tps(),
            "out_tokens_est": self.out_tokens_est(),
            "prompt_tokens": self.prompt_tokens,
            "elapsed_s": self.elapsed_s(),
            "last_turn": self.last_turn,
            "session": {
                "turns": self.session_turns,
                "in_tokens": self.session_in_tokens,
                "out_tokens": self.session_out_tokens,
                "gen_s": self.session_gen_s,
                "avg_tps": sess_tps,
            },
        }
