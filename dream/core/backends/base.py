"""Backend interface + the shared Event type the whole harness renders and logs against."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

PermissionCallback = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class Event:
    """A rendered event from the agent's turn, consumed by the TUI and memory logging."""

    kind: str  # text_delta | thinking_delta | tool_use | tool_result | assistant_done | result | error | system
    data: Any = None


def content_to_text(content: Any) -> str:
    """Flatten a tool-result content payload (str | list of blocks | None) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    out.append(item.get("text", ""))
                elif item.get("type") == "image":
                    out.append("[image]")
                else:
                    out.append(json.dumps(item)[:800])
            else:
                out.append(str(item))
        return "\n".join(out)
    return str(content)


class Backend:
    """A model conversation. Implementations own the agent loop and tool execution and
    emit ``Event``s. The Engine supplies the system prompt, tools, and a permission
    callback, then logs whatever the backend yields."""

    provider_label: str = "backend"

    async def connect(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def ask(self, prompt: str) -> AsyncIterator[Event]:  # pragma: no cover - interface
        raise NotImplementedError

    async def set_model(self, model: str | None) -> None:  # pragma: no cover
        raise NotImplementedError

    def set_effort(self, level: str | None) -> None:
        """Apply a reasoning-effort level. Backend-specific; default no-op (a
        backend that has no such knob simply ignores it)."""
        return None

    async def context_usage(self) -> dict[str, Any] | None:
        return None

    async def disconnect(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError
