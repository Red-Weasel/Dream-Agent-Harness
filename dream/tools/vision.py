"""Vision: let Dream actually *see* an image — a screenshot it took, or any local
image file — instead of only reading extracted text."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .. import config
from .context import ctx, err, in_thread

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_MAX_BYTES = 8_000_000  # keep the base64 payload sane


def _resolve(path: str) -> Path | None:
    p = Path(path).expanduser()
    if p.is_absolute():
        candidates = [p]
    else:
        try:
            candidates = [ctx().workspace / p]
        except RuntimeError:
            # Standalone callers retain legacy lookup. A live session must not
            # substitute another workspace's same-named image for a missing file.
            candidates = [config.ROOT / p, config.SCREENSHOT_DIR / p, Path.cwd() / p]
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


@tool(
    "see",
    "Look at an image and actually see it — a screenshot from `browse` (pass the path it "
    "returned) or any local image file. Use this when the visual matters: page layout, a "
    "chart, a design, a screenshot of an error. Supports png/jpg/webp/gif.",
    {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the image file."}},
        "required": ["path"],
    },
)
async def see(args: dict[str, Any]) -> dict[str, Any]:
    try:
        multimodal = getattr(ctx(), "multimodal", True)
    except RuntimeError:
        multimodal = True  # Standalone callers retain image inspection support.
    if multimodal is False:
        return err("Image input is disabled for this session. No image was read. "
                   "A saved path is not visual evidence; report visual checks as unverified.")
    try:
        p = _resolve(args["path"])
    except (OSError, ValueError) as exc:
        return err(f"Cannot open image: {exc}")
    if p is None:
        return err(f"No image file found at '{args['path']}'.")
    mime = _MIME.get(p.suffix.lower())
    if mime is None:
        return err(f"Unsupported image type '{p.suffix}'. Use png/jpg/webp/gif.")
    def read_bounded():
        with p.open("rb") as source:
            return source.read(_MAX_BYTES + 1)
    try:
        data = await in_thread(read_bounded)
    except OSError as exc:
        return err(f"Cannot read image '{p.name}': {exc}")
    if len(data) > _MAX_BYTES:
        return err(
            f"Image exceeds the {_MAX_BYTES//1_000_000}MB limit. "
            "Use a smaller image or a viewport (not full-page) screenshot."
        )
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "content": [
            {"type": "image", "data": b64, "mimeType": mime},
            {"type": "text", "text": f"(viewing {p.name})"},
        ]
    }
