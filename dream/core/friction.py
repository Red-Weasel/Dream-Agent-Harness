"""The honest uncertainty meter — behavioral signals only, so it works on every
backend (no logprobs required). Watches the event stream for the things that
actually correlate with a model struggling: tool errors, back-to-back identical
tool calls, empty recall/search results, and self-correction phrases in the
answer text. Each signal bumps a rolling score that decays every turn; the last
few turns form a sparkline history. Pure logic — returns plain strings.
"""

from __future__ import annotations

import json
from typing import Any

# One glyph per intensity level, low→high. sparkline() maps window scores here.
_BARS = "▁▂▃▄▅▆▇"

# Assistant-text tells that the model is walking something back.
_SELF_CORRECTION = (
    "actually,",
    "wait,",
    "let me reconsider",
    "that's wrong",
    "correction:",
)

# Content markers for an empty recall / web_search result.
_EMPTY_MARKERS = (
    "no memories",
    "no memory",
    "no results",
    "0 results",
    "no matches",
    "nothing found",
)

# Tool-name hints for the searches we treat as "empty when they say so".
_SEARCHY = ("recall", "search", "web")


class FrictionMeter:
    """Accrues friction from behavioral signals, decays it each turn, and reports
    a 0..1 score, a coarse label, a sparkline of recent turns, and — when high —
    a short human reason."""

    # Per-signal weight added to the current score.
    W_ERROR = 0.34
    W_REPEAT = 0.28
    W_EMPTY = 0.22
    W_CORRECTION = 0.18

    # Fraction of the score that survives a turn boundary (persistent friction lingers).
    DECAY = 0.5
    # Label thresholds on score().
    LOW = 0.25
    HIGH = 0.6
    # How many past turns the sparkline remembers.
    WINDOW = 12

    def __init__(self) -> None:
        self._score: float = 0.0
        self._window: list[float] = []
        self._last_tool: tuple[str, str] | None = None
        # Signal tallies for the *current* turn, drives reason().
        self._counts: dict[str, int] = {
            "errors": 0,
            "repeats": 0,
            "empty": 0,
            "corrections": 0,
        }

    # -- ingest ------------------------------------------------------------

    def record(self, event_kind: str, payload: Any) -> None:
        """Fold one Event into the score. ``payload`` is the Event's ``data``:
        a dict for tool_use/tool_result, or a plain string for assistant text.
        Unknown kinds and malformed payloads are ignored."""
        if event_kind == "tool_use":
            self._on_tool_use(payload if isinstance(payload, dict) else {})
        elif event_kind == "tool_result":
            self._on_tool_result(payload if isinstance(payload, dict) else {})
        elif event_kind in ("assistant_done", "text_delta"):
            self._on_text(_as_text(payload))

    def _on_tool_use(self, payload: dict) -> None:
        name = str(payload.get("name", ""))
        key = (name, _canonical(payload.get("input")))
        if self._last_tool is not None and key == self._last_tool:
            self._bump("repeats", self.W_REPEAT)
        self._last_tool = key

    def _on_tool_result(self, payload: dict) -> None:
        if payload.get("is_error"):
            self._bump("errors", self.W_ERROR)
            return
        name = str(payload.get("name", "")).lower()
        content = str(payload.get("content", "")).lower()
        searchy = (not name) or any(k in name for k in _SEARCHY)
        if searchy and any(m in content for m in _EMPTY_MARKERS):
            self._bump("empty", self.W_EMPTY)

    def _on_text(self, text: str) -> None:
        low = text.lower()
        hits = sum(1 for phrase in _SELF_CORRECTION if phrase in low)
        for _ in range(hits):
            self._bump("corrections", self.W_CORRECTION)

    def _bump(self, signal: str, weight: float) -> None:
        self._counts[signal] += 1
        self._score = min(1.0, self._score + weight)

    # -- turn lifecycle ----------------------------------------------------

    def turn_boundary(self) -> None:
        """Close the current turn: archive its peak score into the sparkline
        window, decay the running score, and reset the per-turn tallies."""
        self._window.append(round(self._score, 4))
        if len(self._window) > self.WINDOW:
            self._window = self._window[-self.WINDOW :]
        self._score *= self.DECAY
        self._last_tool = None
        for k in self._counts:
            self._counts[k] = 0

    # -- readouts ----------------------------------------------------------

    def score(self) -> float:
        """Current friction, clamped to 0..1."""
        return max(0.0, min(1.0, self._score))

    def label(self) -> str:
        """Coarse band: ``low`` | ``warm`` | ``high``."""
        s = self.score()
        if s >= self.HIGH:
            return "high"
        if s >= self.LOW:
            return "warm"
        return "low"

    def sparkline(self) -> str:
        """The recent-turn window as block glyphs (empty until a turn closes)."""
        out = []
        for v in self._window:
            idx = min(len(_BARS) - 1, int(max(0.0, min(1.0, v)) * len(_BARS)))
            out.append(_BARS[idx])
        return "".join(out)

    def reason(self) -> str | None:
        """A short 'why' for the current turn — only when friction is high."""
        if self.label() != "high":
            return None
        c = self._counts
        parts: list[str] = []
        if c["errors"]:
            parts.append(f"{c['errors']} tool error{_plural(c['errors'])}")
        if c["empty"]:
            parts.append(f"{c['empty']} empty search{_plural(c['empty'], 'es')}")
        if c["repeats"]:
            parts.append(f"{c['repeats']} repeated call{_plural(c['repeats'])}")
        if c["corrections"]:
            parts.append(f"{c['corrections']} self-correction{_plural(c['corrections'])}")
        if not parts:
            return None
        return ", ".join(parts) + " this turn"


# -- helpers ---------------------------------------------------------------


def _as_text(payload: Any) -> str:
    """Coerce an assistant-text payload (str, or dict with a text field) to text."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("text", "content"):
            v = payload.get(key)
            if isinstance(v, str):
                return v
    return ""


def _canonical(value: Any) -> str:
    """A stable string key for a tool input, for back-to-back comparison."""
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _plural(n: int, suffix: str = "s") -> str:
    return "" if n == 1 else suffix
