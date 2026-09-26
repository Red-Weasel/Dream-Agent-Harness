"""`/critique [cli] [note]` -- a render critic the owner asks for (fix list #100, DREAM-138).

Nothing runs it but the command: no turn, hook, loop or timer does (each consult costs the owner a CLI run).
It takes the latest render-vs-reference comparison the blender-animation skill wrote
(renders/compare_<name>.png: photo | model [| overlay]), or else the newest image in renders/ with the
reference photos in refs/, and asks one CLI advisor through the Council consult path (moe.consult_advisor,
the DREAM-136 parity run: the advisor opens the image files in the workspace itself), read-only (plan) whatever
Dream's mode, for a numbered defect list; a critique whose first line does not say the images were opened is not
forwarded. The session's previous critique is re-checked item by item. The answer is shown in the chat pane and
sent to the model as the owner's request, marked dream:critique (core/turn_origin.py). A failed consult is
shown plainly; nothing is sent and the session goes on.
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from pathlib import Path

from rich.text import Text

from .. import config
from ..core import council_config, moe, turn_origin
from ..core.backends.base import Event
from ..core.run_state import atomic_write

# The owner's name for each critic -> its Council provider key. The default is the first available, in this order.
PROVIDERS = {"claude": "anthropic", "codex": "codex", "gemini": "gemini", "grok": "grok"}
TIMEOUT = 180.0
IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_REFERENCES = 6
USAGE = "/critique [claude|codex|gemini|grok] [note]"
# consult_advisor never raises: a failure comes back as "[<label>: unavailable — …]", and a backend error
# after partial text ends in "[failed — …]" (core/moe.py).
_FAILED = re.compile(r"^\[[^\]\n]*unavailable — |\[failed — ")
# The critic's first line says whether it opened the images; anything but "opened" is not forwarded (a critique of
# images it never saw would be a guess).
OPENED, NOT_OPENED = "IMAGES: opened", "IMAGES: not opened"
MAX_KEPT = 8000   # characters of a critique stored and sent on


def parse(argument: str) -> tuple[str | None, str]:
    """(cli or None, the owner's note): the first word names a critic only when it is one."""
    words = argument.strip().split(maxsplit=1)
    if words and words[0].lower() in PROVIDERS:
        return words[0].lower(), words[1].strip() if len(words) > 1 else ""
    return None, argument.strip()


def default_cli() -> str | None:
    """The first of claude, codex, gemini, grok the Council lists as available (local prerequisites only)."""
    available = {row["key"] for row in council_config.provider_choices() if row.get("available")}
    return next((cli for cli, key in PROVIDERS.items() if key in available), None)


def _images(folder: Path, workspace: Path) -> list[Path]:
    """Image files in `folder`, inside the workspace once links are resolved."""
    root = workspace.resolve()
    if not folder.is_dir():
        return []
    return [p for p in folder.iterdir()
            if p.suffix.lower() in IMAGES and p.is_file() and p.resolve().is_relative_to(root)]


def _clean(text: str) -> str:
    """A file name as prompt text: control characters (line breaks included) removed, so a name cannot add lines."""
    return "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))


def _cap(text: str) -> str:
    return text if len(text) <= MAX_KEPT else text[:MAX_KEPT] + f"\n[… critique truncated at {MAX_KEPT} characters]"


def _opened(answer: str) -> str | None:
    """The critique after its "IMAGES: opened" first line, or None when the critic did not say it opened them."""
    first, _, rest = answer.partition("\n")
    return rest.strip() if first.strip().strip("*`_ ").lower() == OPENED.lower() else None


def select_images(workspace: Path) -> dict | None:
    """The latest comparison image; without one, the newest render and the reference photos; None if no render."""
    renders = _images(workspace / "renders", workspace)
    newest = lambda paths: max(paths, key=lambda p: p.stat().st_mtime, default=None)  # noqa: E731
    renders = [p for p in renders if not p.stem.endswith("_model")]   # compare_to_reference's render half
    compare = newest([p for p in renders if p.name.startswith("compare_") and p.suffix.lower() == ".png"])
    if compare is not None:
        return {"compare": compare, "render": None, "references": []}
    render = newest(renders)
    if render is None:
        return None
    references = sorted(_images(workspace / "refs", workspace), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"compare": None, "render": render, "references": references[:MAX_REFERENCES]}


def build_prompt(workspace: Path, images: dict, note: str, previous: dict | None) -> str:
    rel = lambda p: _clean(p.relative_to(workspace).as_posix())  # noqa: E731
    if images["compare"] is not None:
        look = (f"- {rel(images['compare'])}: the comparison, the reference photo on the left, the render next to it "
                "(a third panel, if there is one, overlays the two)")
    else:
        look = f"- {rel(images['render'])}: the render"
        look += "".join(f"\n- {rel(p)}: a reference photo" for p in images["references"])
        if not images["references"]:
            look += "\nNo reference photo was found in refs/; say what you can judge from the render alone."
    parts = [
        "The owner asked for a critique of a 3D render against its reference. Open and look at these image files "
        f"in the workspace (paths relative to it):\n{look}",
        "Compare the render to the reference. Answer with a numbered defect list covering shape, proportions, "
        "details, materials and lighting. Make each item concrete and checkable: name the part, say what is wrong "
        "and what the reference shows instead, so someone can look again and tell whether it was fixed. List only "
        "what you can see. Do not edit any files.",
        f'Your first line must be exactly "{OPENED}" if you opened and looked at every image above, or '
        f'"{NOT_OPENED}" (then say why) if you could not; never guess at an image you did not see.',
    ]
    if note:
        parts.append(f"The owner's note: {note}")
    if previous:
        parts.append("Before the new list, re-check every item of the previous critique below against these images "
                     "and mark each FIXED, NOT FIXED or CAN'T TELL with a short reason.\n"
                     f"Previous critique:\n{_cap(previous['text'])}")
    return "\n\n".join(parts)


def _store(session_id: str) -> Path:
    return config.VAR_DIR / "critiques" / f"{session_id}.json"


def _previous(session_id: str) -> dict | None:
    try:
        kept = json.loads(_store(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return kept if isinstance(kept, dict) and isinstance(kept.get("text"), str) else None


def _say(app, kind: str, text: str) -> None:
    (app.renderer.error if kind == "error" else app.renderer.system)(text)
    app.bus.publish(Event(kind, text))


async def command(app, argument: str):
    cli, note = parse(argument)
    cli = cli or default_cli()
    if cli is None:
        return _say(app, "error", "No critic CLI is available (claude, codex, gemini or grok). Nothing was sent.")
    images = select_images(app.workspace)
    if images is None:
        return _say(app, "error", "No render to critique: no renders/compare_*.png and no image in renders/. "
                                  "Nothing was sent.")
    session = app.engine.session_id
    previous = _previous(session)
    key = PROVIDERS[cli]
    cfg = getattr(app.engine, "_moe", None)
    seconds = max(TIMEOUT, cfg.timeout_seconds if cfg else 0.0)
    # Read-only whatever the session's mode (as the prompt optimizer): a critic looks, it never edits.
    options = {"cwd": str(app.workspace), "mode": "plan", "timeout": seconds}
    if cfg and key in cfg.advisor_models:
        options["model"] = cfg.advisor_models[key]
    if cfg and key in cfg.advisor_efforts:
        options["effort"] = cfg.advisor_efforts[key]
    shown = [_clean(p.relative_to(app.workspace).as_posix())
             for p in (images["compare"] or images["render"], *images["references"])]
    _say(app, "system", f"Critique: asking {cli} about {', '.join(shown)}…")
    answer = await app._run_turn(moe.consult_advisor(key, build_prompt(app.workspace, images, note, previous),
                                                     **options))
    if app.interrupted:
        return _say(app, "system", "Critique interrupted. Nothing was sent to the model.")
    answer = (answer or "").strip()
    if not answer or _FAILED.search(answer):
        return _say(app, "error", f"Critique failed ({cli}, {seconds:.0f} s limit): {answer or 'no answer'}. "
                                  "Nothing was sent to the model; the session goes on.")
    critique = _opened(answer)
    if not critique:
        return _say(app, "error", f"Critique failed ({cli}): the critic could not open the images or did not say it "
                                  f"had; nothing was sent to the model. Its answer: {answer[:2000]}")
    kept = _cap(critique)
    atomic_write(_store(session), json.dumps({"cli": cli, "text": kept}) + "\n")
    text = f"Critique · {cli}\n{critique}"
    app.renderer.console.print(Text(text))
    identifier = "critique-" + uuid.uuid4().hex
    app.bus.publish(Event("tool_use", {"id": identifier, "name": "critique", "input": {"cli": cli, "note": note}}))
    app.bus.publish(Event("tool_result", {"id": identifier, "name": "critique", "content": text}))
    _say(app, "system", "Dream's own note, not from you: the critique goes to the model as your request (/critique).")
    with turn_origin.generated(turn_origin.CRITIQUE):   # Dream's wrapper around the critic's answer, marked
        await app._ask(
            f"[Render critique the owner asked for with /critique: {cli} compared {', '.join(shown)}]\n"
            "The owner asks you to address each item below: fix it, or dispute it with evidence (a fresh render, "
            "a measurement, the reference photo). Report per item number what you did.\n\n" + kept)
