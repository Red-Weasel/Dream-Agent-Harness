"""questions_v2: a structured question form, rendered in Studio.

Asking good questions at the start of design work is the difference between a
board of guesses and a board of options. The form is native UI in the user's panel:
radios or checkboxes for text options, SVG swatches for visual ones, sliders,
a file picker, free text. It does NOT return an answer: the user answers in Studio,
and the answers arrive as the next prompt. So after calling it, end the turn.

With no Studio open, the tool returns the questions as text for the reply, so
the model asks in words and the user answers in words.
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from ..core.backends.base import Event
from .context import ctx, err, ok, studio

KINDS = ("text-options", "svg-options", "slider", "file", "freeform")
_REQUIRED_TEXT_OPTIONS = ("Explore a few options", "Decide for me")


def validate(form: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """A clean copy of the form, or what is wrong with it."""
    title = str(form.get("title") or "").strip()
    if not title:
        return None, "the form needs a title"
    qs = form.get("questions")
    if not isinstance(qs, list) or not qs:
        return None, "the form needs a non-empty 'questions' list"
    clean: list[dict[str, Any]] = []
    ids: set[str] = set()
    for i, q in enumerate(qs):
        if not isinstance(q, dict):
            return None, f"questions[{i}] must be an object"
        qid, kind, qt = str(q.get("id") or "").strip(), str(q.get("kind") or ""), str(q.get("title") or "").strip()
        if not qid or not qid.replace("_", "").isalnum():
            return None, f"questions[{i}] needs a snake_case 'id'"
        if qid in ids:
            return None, f"questions[{i}]: duplicate id '{qid}'"
        ids.add(qid)
        if kind not in KINDS:
            return None, f"questions[{i}] ('{qid}'): kind must be one of {', '.join(KINDS)}"
        if not qt:
            return None, f"questions[{i}] ('{qid}') needs a title"
        c: dict[str, Any] = {"id": qid, "kind": kind, "title": qt}
        if q.get("subtitle"):
            c["subtitle"] = str(q["subtitle"])
        if kind in ("text-options", "svg-options"):
            opts = q.get("options")
            if not isinstance(opts, list) or not opts or not all(isinstance(o, str) and o.strip() for o in opts):
                return None, f"questions[{i}] ('{qid}'): {kind} needs a non-empty list of string options"
            opts = [o.strip() for o in opts]
            if kind == "text-options":
                for must in _REQUIRED_TEXT_OPTIONS:
                    if must not in opts:
                        opts.append(must)
                if "Other" not in opts:
                    opts.append("Other")
            c["options"] = opts
            c["multi"] = bool(q.get("multi"))
        elif kind == "slider":
            try:
                lo, hi = float(q.get("min", 0)), float(q.get("max", 100))
                step = float(q.get("step", 1))
                default = float(q.get("default", lo))
            except (TypeError, ValueError):
                return None, f"questions[{i}] ('{qid}'): slider min/max/step/default must be numbers"
            import math
            if not all(math.isfinite(v) for v in (lo, hi, step, default)):
                return None, f"questions[{i}] ('{qid}'): slider values must be finite numbers"
            if not (lo < hi) or step <= 0:
                return None, f"questions[{i}] ('{qid}'): slider needs min < max and step > 0"
            c.update({"min": lo, "max": hi, "step": step, "default": min(max(default, lo), hi)})
        elif kind == "file":
            if q.get("accept"):
                if not isinstance(q["accept"], str):
                    return None, f"questions[{i}] ('{qid}'): accept must be a string like \"image/*\""
                c["accept"] = q["accept"]
        clean.append(c)
    return {"title": title, "questions": clean}, None


def as_text(form: dict[str, Any]) -> str:
    lines = [f"## {form['title']}"]
    for n, q in enumerate(form["questions"], 1):
        lines.append(f"{n}. {q['title']}" + (f" — {q['subtitle']}" if q.get("subtitle") else ""))
        if q["kind"] in ("text-options", "svg-options"):
            lines.append("   " + ("pick any: " if q.get("multi") else "pick one: ") + " / ".join(q["options"]))
        elif q["kind"] == "slider":
            lines.append(f"   number from {q['min']:g} to {q['max']:g} (default {q['default']:g})")
        elif q["kind"] == "file":
            lines.append("   attach a file" + (f" ({q['accept']})" if q.get("accept") else ""))
        else:
            lines.append("   free text")
    return "\n".join(lines)


@tool(
    "questions_v2",
    "Present a structured question form to the user for gathering design preferences. "
    "Use it when starting something new or the ask is ambiguous — one round of focused "
    "questions, the most important first, and it is better to ask too many than too "
    "few. Kinds: text-options (radio, or checkbox with multi; \"Explore a few options\", "
    "\"Decide for me\" and \"Other\" are added for you), svg-options (each option an "
    "inline SVG string, ~80×56 viewBox, for visual choices), slider (min/max/step/default; "
    "be generous with ranges), file (the upload lands in uploads/ and its path is the "
    "answer), freeform. The form does NOT return an answer: after calling it, END YOUR "
    "TURN — the answers arrive as the next prompt, keyed by question id.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Overall form title."},
            "questions": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string", "description": "snake_case answer key"},
                "kind": {"type": "string", "enum": list(KINDS)},
                "title": {"type": "string"}, "subtitle": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "multi": {"type": "boolean"},
                "min": {"type": "number"}, "max": {"type": "number"},
                "step": {"type": "number"}, "default": {"type": "number"},
                "accept": {"type": "string"},
            }, "required": ["id", "kind", "title"]}},
        },
        "required": ["title", "questions"],
    },
)
async def questions_v2(args: dict[str, Any]) -> dict[str, Any]:
    form, why = validate(args)
    if why:
        return err(f"questions_v2: {why}.")
    emit = ctx().emit
    # The emit hook is always wired (the TUI funnel); Studio itself is what may be
    # missing. A form nobody can see must come back as text (Gate 6 finding 1).
    if emit is None or studio() is None:
        return ok("Studio is not open, so ask these in your reply and end the turn:\n\n"
                  + as_text(form))
    emit(Event("studio", {"op": "ask", "form": form}))
    n = len(form["questions"])
    return ok(f"Form '{form['title']}' with {n} question(s) is showing in Studio. END YOUR "
              f"TURN now; the answers come back as the next prompt, keyed by id "
              f"({', '.join(q['id'] for q in form['questions'])}).")


def answers_as_prompt(form_title: str, answers: dict[str, Any]) -> str:
    """How Studio turns a submitted form into the next prompt."""
    lines = [f"Answers to '{form_title}':"]
    for k, v in answers.items():
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


QUESTION_TOOLS = [questions_v2]
