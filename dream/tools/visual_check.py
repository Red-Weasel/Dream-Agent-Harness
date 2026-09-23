"""visual_check: a structured "please look" request for a model that cannot see (DREAM-097).

`measure_image` gives a model without image input exact pixel facts about its render; when those
numbers cannot settle a question ("is the header text legible?", "which of these two looks right?")
the model hands the user the picture and one precise question instead of prose. This is
questions_v2's flow, not a second mechanism: the tool emits the Studio "ask" event with a form whose
`images` the panel renders inline -- served by the token-checked download route, which serves only
files inside the workspace -- the user picks a radio option or types an answer, and the answer
arrives as the NEXT PROMPT keyed by id, so after calling it the model ends its turn. Without Studio
the paths and the question come back as text for the reply, as questions_v2 does.

An image that cannot be shown -- missing, not a raster image a browser renders, or outside the
workspace -- is named in the result and the rest of the form still shows; with nothing to show the
call fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from ..core.backends.base import Event
from .context import ctx, err, in_thread, ok, studio

_MAX_IMAGES = 6
_ANSWER_ID = "answer"
# What a browser renders in an <img>, by Pillow's format names (MPO: a camera JPEG carrying extra pictures).
_BROWSER_FORMATS = {"PNG", "JPEG", "MPO", "GIF", "WEBP", "BMP"}


def _resolve(raw: str) -> Path | None:
    """Relative paths live in the session workspace; absolute paths pass through; None for a path no
    filesystem can hold (a NUL byte)."""
    try:
        return (ctx().workspace / Path(raw).expanduser()).resolve()
    except ValueError:
        return None


def _size(p: Path) -> str:
    """'WxH' of a raster image a browser can show; ValueError (naming the file) for anything else."""
    from PIL import Image

    try:
        with Image.open(p) as im:
            fmt, (w, h) = im.format, im.size
    except Exception as e:
        raise ValueError(f"Could not read {p.name} as an image: {type(e).__name__}: {e}") from e
    if fmt not in _BROWSER_FORMATS:
        raise ValueError(f"Could not read {p.name} as an image a browser shows ({fmt}; use PNG, JPEG, GIF, WEBP or BMP)")
    return f"{w}x{h}"


def _title(shown: list[Path]) -> str:
    if len(shown) <= 2:
        return "Please look at " + " and ".join(p.name for p in shown)
    return f"Please look at {len(shown)} images"


@tool(
    "visual_check",
    "Show the user 1-6 images with one precise question when you cannot see them yourself. "
    "For a render or screenshot whose `measure_image` numbers cannot settle the question: the images, "
    "your question and the optional `options` (radio buttons; the user can always type instead) appear "
    "as a form in Studio. It does NOT return an answer: after calling it, END YOUR TURN -- the answer "
    "arrives as the next prompt, keyed by id `answer`. Studio shows only images inside the workspace "
    "(save_screenshot save_path, or copy_files). No approval needed.",
    {
        "type": "object",
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": _MAX_IMAGES,
                      "description": "1-6 images (png/jpg/gif/webp/bmp), relative to the workspace or absolute."},
            "question": {"type": "string", "description": "One precise question about what is shown."},
            "options": {"type": "array", "items": {"type": "string"},
                        "description": "Answers to pick from (radio buttons); omit for a free-text answer."},
        },
        "required": ["paths", "question"],
    },
)
async def visual_check(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("paths")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or not all(isinstance(r, str) and r.strip() for r in raw):
        return err(f"visual_check needs 'paths': a list of 1-{_MAX_IMAGES} image paths.")
    if len(raw) > _MAX_IMAGES:
        return err(f"visual_check shows at most {_MAX_IMAGES} images ({len(raw)} given).")
    question = args.get("question")
    if question is not None and not isinstance(question, str):
        return err(f"visual_check: 'question' must be a string (one precise question about what is shown), "
                   f"not {type(question).__name__} ({question!r}).")
    question = (question or "").strip()
    if not question:
        return err("visual_check needs a 'question': one precise question about what is shown.")
    options = args.get("options")
    if options is not None and (not isinstance(options, list)
                                or not all(isinstance(o, str) and o.strip() for o in options)):
        return err("visual_check: 'options' must be a list of non-empty strings (omit it for a free answer).")
    options = [o.strip() for o in options] if options else []

    # The emit hook is always wired (the TUI funnel); Studio itself is what may be missing (questions_v2).
    emit = ctx().emit
    showing = emit is not None and studio() is not None
    ws = ctx().workspace.resolve()
    shown: list[Path] = []
    images: list[dict[str, str]] = []
    problems: list[str] = []
    for r in raw:
        p = _resolve(r)
        if p is None:
            problems.append(f"Cannot show {r!r}: no file can have that path (it holds a NUL byte)")
            continue
        if not p.is_file():
            problems.append(f"Cannot show {p.name}: No file at {p}")
            continue
        try:
            size = await in_thread(_size, p)
        except ValueError as e:
            problems.append(f"Cannot show {p.name}: {e}")
            continue
        if showing and not p.is_relative_to(ws):
            problems.append(f"Cannot show {p.name}: {p} is outside the workspace, which is all Studio serves; "
                            "capture it with save_screenshot save_path=<a path inside the workspace> or copy_files it in.")
            continue
        shown.append(p)
        images.append({"path": p.relative_to(ws).as_posix() if showing else str(p), "name": p.name, "size": size})
    if not shown:
        return err("visual_check: no image to show.\n" + "\n".join(problems))
    tail = ("\n" + "\n".join(problems)) if problems else ""

    if not showing:
        return ok("Studio is not open, so ask this in your reply and end the turn:\n\n"
                  "Please look at " + ", ".join(f"{im['path']} ({im['size']})" for im in images) + "\n"
                  + question + "\n"
                  + ("pick one: " + " / ".join(options) if options else "free answer") + tail)

    q: dict[str, Any] = ({"id": _ANSWER_ID, "kind": "text-options", "title": question, "options": options, "multi": False}
                         if options else {"id": _ANSWER_ID, "kind": "freeform", "title": question})
    emit(Event("studio", {"op": "ask", "form": {"title": _title(shown), "images": images, "questions": [q]}}))
    names = " and ".join(p.name for p in shown) if len(shown) <= 2 else f"{len(shown)} images"
    return ok(f"Showing {names} with your question in Studio. END YOUR TURN now; the answer comes back as the "
              f"next prompt, keyed by id ({_ANSWER_ID})." + tail)
