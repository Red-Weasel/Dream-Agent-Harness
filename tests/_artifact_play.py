"""The scripted turn the artifact tests share (A0, SPEC 5): a real StudioServer with the REAL tool handlers,
driven as Dream drives them -- `user`, `turn_start`, `tool_use`, the handler, `tool_result`, `result`,
`turn_end` -- through the session's event funnel (retain_show, then bus.publish: what tui/app.py's
_render_event does), so a pane test sees exactly the events and the tool results a session makes.
No model, no engine, no GPU: the hidden frame is headless Chromium on the CPU."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from dream.core.backends.base import Event
from dream.gui import preview as preview_mod
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tools import context as tool_context, studio
from dream.tools.context import ToolContext, set_context, set_studio


class Play:
    """One session's Studio server and tool context. `async with Play(ws) as play:` starts the server
    (`play.url`, `play.companion_url`), binds the tools to the workspace and the funnel, and tears it all down."""

    def __init__(self, workspace: Path, *, session_id: str = "play", **server_kwargs: Any) -> None:
        self.workspace = Path(workspace)
        self.session_id = session_id
        self.prompts: list[str] = []
        self.events: list[Event] = []       # everything the funnel carried, in order
        self.calls = 0
        kwargs = {"on_prompt": self.prompts.append,
                  "session": {"workspace": str(self.workspace), "model": "fixture", "session_id": session_id}}
        kwargs.update(server_kwargs)
        self.server = StudioServer(EventBus(), **kwargs)
        self.url = ""

    @property
    def companion_url(self) -> str:
        return self.url.replace("/#", "/?companion=1#")

    def funnel(self, ev: Event) -> None:
        self.events.append(ev)
        self.server.retain_show(ev)
        self.server.bus.publish(ev)

    async def __aenter__(self) -> "Play":
        set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                                session_id=self.session_id, workspace=self.workspace, emit=self.funnel))
        preview_mod._PREVIEW = None
        studio._forget_reports()
        self.url = await self.server.start()
        set_studio(self.server)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        set_studio(None)
        tool_context._CTX = None
        studio._forget_reports()
        await self.server.stop()
        pv = preview_mod._PREVIEW
        if pv is not None:
            await pv.aclose()
        preview_mod._PREVIEW = None

    async def connected(self, timeout: float = 5.0) -> None:
        """Wait for a Studio page to be on the socket."""
        for _ in range(int(timeout / 0.05)):
            if self.server.client_count:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("no Studio page connected")

    async def turn(self, prompt: str, steps: list[tuple[Any, dict[str, Any]]], *,
                   reply: str = "Done.") -> list[dict[str, Any]]:
        """A scripted turn: the owner's prompt, then each (tool, args) run through the real handler between its
        tool_use and tool_result events, then the reply and the turn's end. Returns the tool results."""
        self.funnel(Event("user", prompt))
        self.funnel(Event("turn_start", {}))
        results = []
        for tool, args in steps:
            self.calls += 1
            call_id = f"call-{self.calls}"
            self.funnel(Event("tool_use", {"id": call_id, "name": tool.name, "input": dict(args)}))
            result = await tool.handler(dict(args))
            results.append(result)
            self.funnel(Event("tool_result", {"id": call_id, "content": result["content"][0]["text"],
                                              "is_error": bool(result.get("is_error"))}))
        self.funnel(Event("text_delta", reply))
        self.funnel(Event("result", {}))
        self.funnel(Event("turn_end", {}))
        return results


def text(result: dict[str, Any]) -> str:
    return result["content"][0]["text"]
