"""Render Dream TUI components to SVG and optional PNG for visual inspection."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from claude_agent_sdk import tool

from ... import config
from ..context import err, in_thread, ok

_COMPONENTS = ("landing", "welcome", "stats")


def _render(component: str, width: int) -> tuple[str, str | None]:
    """Sync worker: render the component to SVG, and to PNG when a chromium exists."""
    os.environ.setdefault("DREAM_FORCE_LOGO", "1")
    from rich.console import Console
    from rich.text import Text

    from ...tui.render import Renderer, format_turn_stats

    console = Console(record=True, force_terminal=True, width=width)
    r = Renderer(console)
    if component == "landing":
        r.show_logo()
    elif component == "welcome":
        r.show_logo()
        r.welcome(
            rows=[
                ("engine", "MachX · sample-model  ⚡ local"),
                ("memory", "41 memories (22s · 9p · 10e) · 14 sessions"),
                ("recall", "hybrid — keyword + semantic + rerank"),
                ("session", "20260704-000000-samp"),
            ],
            notes=["last time · (sample data for preview)"],
        )
    else:  # stats
        line = format_turn_stats(
            {"stats": {"pp_tps": 812.4, "gen_tps": 42.3, "ctx_used": 34840,
                       "n_ctx": 120000, "duration_s": 134.2}}
        )
        console.print(Text(f"  {line}", style="dim"))

    config.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    svg = config.SCREENSHOT_DIR / f"preview-{component}.svg"
    console.save_svg(str(svg), title="Dream")

    chrome = (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    if not chrome:
        return str(svg), None
    png = svg.with_suffix(".png")
    subprocess.run(
        [chrome, "--headless", "--disable-gpu", f"--screenshot={png}",
         "--window-size=1250,950", "--force-device-scale-factor=1.4",
         "--hide-scrollbars", f"file://{svg}"],
        capture_output=True, timeout=60,
    )
    return str(svg), (str(png) if png.exists() else None)


@tool(
    "preview_tui",
    "Render one of Dream's own TUI components — 'landing' (wordmark + lockup), "
    "'welcome' (the boot panel), or 'stats' (the per-turn stats line) — to an image, "
    "so you can look at your own interface with see() while restyling it. Returns "
    "the image path.",
    {
        "type": "object",
        "properties": {
            "component": {
                "type": "string",
                "description": "landing | welcome | stats (default landing)",
            },
            "width": {"type": "integer", "description": "Terminal width in columns (default 96)."},
        },
        "required": [],
    },
)
async def preview_tui(args: dict[str, Any]) -> dict[str, Any]:
    component = args.get("component", "landing")
    if component not in _COMPONENTS:
        return err(f"component must be one of {', '.join(_COMPONENTS)}.")
    width = max(60, min(int(args.get("width", 96)), 200))
    try:
        svg, png = await in_thread(_render, component, width)
    except Exception as e:
        return err(f"render failed: {type(e).__name__}: {e}")
    if png:
        return ok(f"Rendered '{component}' → {png}\nLook at it with see(path=\"{png}\").")
    return ok(
        f"Rendered '{component}' → {svg}. No chromium found to rasterize it; "
        "see() needs a raster image, so either install chrome or open the SVG directly."
    )
