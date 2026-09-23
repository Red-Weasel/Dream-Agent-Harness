"""Vision: let Dream actually *see* an image — a screenshot it took, or any local
image file — instead of only reading extracted text."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .. import config
from ..core import vision_helper
from .context import ctx, err, in_thread, ok

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_MAX_BYTES = 8_000_000  # keep the base64 payload sane
_MAX_IMAGES = 6         # per call; one model round trip for a whole review set


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
    "Look at one or several images and actually see them — a screenshot from `browse` (pass "
    "the path it returned) or any local image files. Use this when the visual matters: page "
    "layout, a chart, a design, a screenshot of an error. `paths` shows up to 6 images in ONE "
    "call (one model round trip instead of one per image). Supports png/jpg/webp/gif. When image input "
    "is off but the owner configured a vision helper, the images go to that provider and its description "
    "comes back as text.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to one image file."},
            "paths": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                      "description": "Several image files to view together (max 6)."},
            "question": {"type": "string",
                         "description": "Optional: what to look for or answer about the image(s); read by a vision "
                                        "helper describing them for you."},
        },
    },
)
async def see(args: dict[str, Any]) -> dict[str, Any]:
    try:
        context = ctx()
        multimodal = getattr(context, "multimodal", True)
        # Borrowed eyes (DREAM-098): with image input off, the owner may have named a provider that describes instead.
        helper = getattr(context, "vision_helper", None) if multimodal is False else None
    except RuntimeError:
        multimodal, helper = True, None  # Standalone callers retain image inspection support.
    if multimodal is False and not helper:
        return err("Image input is disabled for this session. No image was read. "
                   "A saved path is not visual evidence; report visual checks as unverified.")
    wanted = [str(x) for x in (args.get("paths") or [])] or ([str(args["path"])] if args.get("path") else [])
    if not wanted:
        return err("see needs 'path' or 'paths'.")
    if len(wanted) > _MAX_IMAGES:
        return err(f"see shows at most {_MAX_IMAGES} images per call; you passed {len(wanted)}.")
    blocks: list[dict[str, Any]] = []
    names: list[str] = []
    for raw in wanted:
        try:
            p = _resolve(raw)
        except (OSError, ValueError) as exc:
            return err(f"Cannot open image: {exc}")
        if p is None:
            return err(f"No image file found at '{raw}'.")
        mime = _MIME.get(p.suffix.lower())
        if mime is None:
            return err(f"Unsupported image type '{p.suffix}' ({p.name}). Use png/jpg/webp/gif.")

        def read_bounded(path=p):
            with path.open("rb") as source:
                return source.read(_MAX_BYTES + 1)
        try:
            data = await in_thread(read_bounded)
        except OSError as exc:
            return err(f"Cannot read image '{p.name}': {exc}")
        if len(data) > _MAX_BYTES:
            return err(
                f"Image '{p.name}' exceeds the {_MAX_BYTES//1_000_000}MB limit. "
                "Use a smaller image or a viewport (not full-page) screenshot."
            )
        blocks.append({"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime})
        names.append(p.name)
    if helper:
        # The pixels go to the helper in one single-turn request; only its words reach the session's model.
        provider = vision_helper.helper_provider(helper)
        try:
            text = await vision_helper.describe(blocks, args.get("question"), provider)
        except Exception as exc:   # a HelperError, or whatever else the transport raised: either way nobody saw the image
            why = str(exc) if isinstance(exc, vision_helper.HelperError) else f"{type(exc).__name__}: {exc}"
            return err(f"Vision helper {provider.label} could not describe {', '.join(names)}: {why}. No description "
                       "was produced and nobody saw the image; report the visual check as unverified or ask the user "
                       "to look with `visual_check`.")
        return ok(f"Described by {provider.label}: {text}")
    blocks.append({"type": "text", "text": f"(viewing {', '.join(names)})"})
    return {"content": blocks}
