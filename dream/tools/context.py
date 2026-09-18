"""Runtime context shared with tool handlers.

Tools are defined at import time but need live state (the memory store, this session's
working memory, the browser) when they run. The engine builds a ``ToolContext`` at boot
and installs it here; handlers fetch it lazily with ``ctx()``.
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from anyio import to_thread

from .. import config
from ..memory.store import MemoryStore
from ..memory.working import WorkingMemory
from ..web.browser import Browser


@dataclass
class ToolContext:
    store: MemoryStore
    working: WorkingMemory
    browser: Browser
    session_id: str
    # The session's working directory: relative tool paths resolve here, and the
    # permission policy treats writes beyond it as always-ask.
    workspace: Path = field(default_factory=lambda: config.ROOT)
    # Dream MoE: the advisor line-up, set only in a council session (None otherwise).
    # The consult/council tools read it to know who to ask. Type: core.moe.MoeConfig.
    moe_config: Any = None
    # Whether the driving model can look at images. `see` is stripped from the
    # toolset when it can't, so a result that points at `see` sends the model
    # after a tool it doesn't have.
    multimodal: bool = True
    # Push an Event to the session's viewers (the TUI funnel, and Studio when it
    # is open). Set by the app; None when nothing is listening. The Studio tools
    # use it to open an artifact in the user's panel.
    emit: Callable[[Any], None] | None = None
    # Phase 11: work that outlives the turn (dream.memory.tasks.TaskStore), or None.
    tasks: Any = None
    # Observed run accounting for delegated Council/review calls. This is a
    # session object, never inferred from another Engine's global fallback.
    runtime_meter: Any = None
    # Per-Engine owned browser/windows and observation tokens, closed on cleanup.
    computer: Any = None


_CTX: ToolContext | None = None
_BOUND: ContextVar[ToolContext | None] = ContextVar("dream_tool_context", default=None)
# The Studio server, when it is running. Kept beside the context rather than in it:
# Studio starts after the engine builds the ToolContext, and stops before it.
_STUDIO: Any = None


def set_studio(server: Any) -> None:
    global _STUDIO
    _STUDIO = server


def studio() -> Any:
    return _STUDIO


def set_context(context: ToolContext) -> None:
    global _CTX
    _CTX = context


def ctx() -> ToolContext:
    bound = _BOUND.get()
    if bound is not None:
        return bound
    if _CTX is None:
        raise RuntimeError("Tool context not initialized — engine did not boot tools.")
    return _CTX


def bound_runtime_meter():
    """Accounting must never attach to a different session's legacy fallback."""
    context = _BOUND.get()
    return context.runtime_meter if context is not None else None


@contextmanager
def bind_context(context: ToolContext):
    """Bind a session for async tools and inherited child tasks, then restore it."""
    token = _BOUND.set(context)
    try:
        yield context
    finally:
        _BOUND.reset(token)


async def in_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a blocking (sqlite) call off the event loop."""
    return await to_thread.run_sync(functools.partial(fn, *args, **kwargs))


def ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}
