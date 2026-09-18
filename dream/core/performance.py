"""Explicit session generation choices, independent of transport and saved settings."""
from __future__ import annotations


class PerformanceModes:
    """Preserve the session baseline and derive bounded, reversible selections.

    ``effort_levels`` must come from the active model's reported capabilities,
    ordered from least to most effort. An absent ladder changes output only.
    The backend snapshots selections at the start of the next user turn.
    """

    def __init__(self, output_tokens: int, reasoning_effort: str | None, *,
                 effort_levels: tuple[str, ...] = (), supported: bool = True,
                 unavailable_reason: str = ""):
        if type(output_tokens) is not int or output_tokens <= 0:
            raise ValueError("output_tokens must be a positive integer")
        self._original = {"output_tokens": output_tokens, "reasoning_effort": reasoning_effort}
        self._levels = tuple(effort_levels)
        if any(not isinstance(level, str) or not level for level in self._levels):
            raise ValueError("effort_levels must contain reported effort names")
        self._supported = supported
        self._reason = unavailable_reason
        self._current = "custom"

    def _modes(self) -> list[dict]:
        original = self._original
        levels = self._levels
        efforts = (levels[0], levels[(len(levels) - 1) // 2], levels[-1]) if levels else (original["reasoning_effort"],) * 3
        descriptions = (
            "Shorter output allowance. Lower reported reasoning effort." if levels else "Shorter output allowance. Reasoning unchanged.",
            "Moderate output allowance. Middle reported reasoning effort." if levels else "Moderate output allowance. Reasoning unchanged.",
            "Full original output allowance. Highest reported reasoning effort." if levels else "Full original output allowance. Reasoning unchanged.",
        )
        rows = [{"name": "custom", "label": "Custom", "description": "Restore original session settings.", **original}]
        for name, cap, effort, description in zip(
            ("quick", "balanced", "thorough"), (2048, 8192, original["output_tokens"]), efforts, descriptions
        ):
            rows.append({"name": name, "label": name.title(), "description": description,
                         "output_tokens": min(original["output_tokens"], cap), "reasoning_effort": effort})
        return rows

    def select(self, name: str) -> dict:
        if not isinstance(name, str) or name not in ("custom", "quick", "balanced", "thorough"):
            raise ValueError("Choose a performance mode: custom, quick, balanced or thorough")
        if not self._supported:
            raise ValueError("Performance modes are unavailable: " + (self._reason or "adapter does not expose generation controls"))
        self._current = name
        return self.status()["effective"]

    def status(self) -> dict:
        rows = self._modes()
        selected = next(row for row in rows if row["name"] == self._current)
        return {"supported": self._supported, "current": self._current, "reason": self._reason,
                "applies": "next_user_turn", "reasoning_supported": bool(self._levels),
                "effective": {key: selected[key] for key in ("output_tokens", "reasoning_effort")},
                "modes": rows}
