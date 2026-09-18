"""Per-prompt tool-call budget.

A bound on how many tools the model may call while answering a single prompt.
Local/CLI backends can loop, so they get a default cap (``DEFAULT_TOOL_BUDGET_LOCAL``);
hosted Claude is trusted to stop itself and runs unlimited. Pure bookkeeping — the
engine resets it per turn, records each ``tool_use``, and wraps up when it's exhausted.
"""

from __future__ import annotations

import dream.config as config


class ToolBudget:
    """A resettable counter capped at ``limit`` tool calls (``None`` = unlimited)."""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.count = 0

    def reset(self) -> None:
        """Zero the counter for a new prompt."""
        self.count = 0

    def record(self) -> None:
        """Tally one tool call."""
        self.count += 1

    def exhausted(self) -> bool:
        """True once the cap is reached (never for an unlimited budget)."""
        return self.limit is not None and self.count >= self.limit

    def remaining(self) -> int | None:
        """Calls left before the cap, or ``None`` if unlimited."""
        if self.limit is None:
            return None
        return max(0, self.limit - self.count)

    def describe(self) -> str:
        """Short status for the panel: ``"off"`` or ``"12/25"``."""
        if self.limit is None:
            return "off"
        return f"{self.count}/{self.limit}"


def default_tool_budget(provider_kind: str) -> int | None:
    """Default budget for a provider kind: unlimited for ``"anthropic"``, else the local cap."""
    if provider_kind == "anthropic":
        return None
    return config.DEFAULT_TOOL_BUDGET_LOCAL
