"""Vision: let Dream actually *see* an image — a screenshot it took, or any local
image file — instead of only reading extracted text."""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from .. import config
from ..core import vision_helper
from . import mirror
from .context import ctx, err, in_thread, ok

# The only decoders Pillow may try on the bytes. Without the list it tries every plugin it
# has, and the EPS one runs ghostscript with no timeout: a `{ } loop` PostScript file named
# .png hung `see` for good (the DREAM-114 gate). MPO opens through the JPEG plugin.
_FORMATS = ["PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF", "ICO"]
# What leaves as it is on disk, keyed by the format Pillow sniffed (never by the file's
# extension): the engine's decoder (stb_image, built for PNG, JPEG and BMP only) reads PNG and
# JPEG; every other listed format is re-encoded as PNG first. MPO is a JPEG with extra frames
# appended (phone photos).
_PASS_THROUGH = {"PNG": "image/png", "JPEG": "image/jpeg", "MPO": "image/jpeg"}
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


def _normalise(data: bytes, name: str) -> tuple[bytes, str, str | None]:
    """Sniff and fully decode `data` with Pillow, whatever `name`'s extension says
    (DREAM-114: a WebP named .png went to the engine as image/png and the whole turn
    failed). PNG and JPEG pass through unchanged under their real MIME type; any other
    listed format becomes a PNG (first frame only, RGB or RGBA). Returns
    (bytes, mime, note); raises ValueError with Pillow's reason when it cannot decode."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data), formats=_FORMATS) as im:
            fmt = im.format
            if Image.MAX_IMAGE_PIXELS and im.width * im.height > Image.MAX_IMAGE_PIXELS:
                # Pillow's warn-only tier (about 89 million pixels): refused before it is decoded.
                raise ValueError(f"Image size ({im.width * im.height} pixels) exceeds limit of "
                                 f"{Image.MAX_IMAGE_PIXELS} pixels")
            im.load()  # most truncated or corrupt files fail here; the engine's own decoder is the last check
            if fmt in _PASS_THROUGH:
                return data, _PASS_THROUGH[fmt], None
            alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            out = io.BytesIO()
            im.convert("RGBA" if alpha else "RGB").save(out, format="PNG")
    except UnidentifiedImageError as exc:
        raise ValueError("cannot identify image file") from exc  # Pillow's words, minus the BytesIO repr
    except Exception as exc:  # OSError (truncated), SyntaxError, DecompressionBombError, ...
        raise ValueError(str(exc) or type(exc).__name__) from exc
    return out.getvalue(), "image/png", f"{name}: {fmt}, converted to PNG"


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
    found: list[Path] = []
    notes: list[str] = []  # conversions and files that could not be shown, one line each
    for raw in wanted:
        try:
            p = _resolve(raw)
        except (OSError, ValueError) as exc:
            return err(f"Cannot open image: {exc}")
        if p is None:
            return err(f"No image file found at '{raw}'.")

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
        # A bad file costs one line of this result, not the turn (DREAM-114).
        try:
            data, mime, note = await in_thread(_normalise, data, p.name)
        except ValueError as exc:
            notes.append(f"image not decodable: {p.name} ({exc})")
            continue
        if len(data) > _MAX_BYTES:  # only a re-encoded image can outgrow the size checked above
            notes.append(f"image not delivered: {p.name} (converted PNG is {len(data)/1_000_000:.1f}MB, "
                         f"over the {_MAX_BYTES//1_000_000}MB limit)")
            continue
        if note:
            notes.append(note)
        blocks.append({"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime})
        names.append(p.name)
        found.append(p)
    if not blocks:
        return err("\n".join(notes))
    trailer = "\n" + "\n".join(notes) if notes else ""
    # What the model looks at, the owner sees too (DREAM-111): the newest workspace image of
    # the call, in the Studio, while the owner follows the model's view.
    on_pane = mirror.show_newest(found)
    pane_note = f"; the user's Studio pane shows {on_pane.name} too" if on_pane is not None else ""
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
        return ok(f"Described by {provider.label}: {text}" + (f"\n({pane_note[2:]})" if pane_note else "") + trailer)
    blocks.append({"type": "text", "text": f"(viewing {', '.join(names)}{pane_note})" + trailer})
    return {"content": blocks}
