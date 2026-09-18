"""Visual answers: typed cards, widgets, image search, and elicitation.

The chat prompt turns answers into interfaces with typed widgets. Here each card
tool validates its payload against the schema the chat prompt uses, hands the
card to Studio through the session's event funnel, and returns a plain-text
rendering of the same data as its result — which is what the terminal shows,
and what the model reads back. A card never fetches from the network: what it
shows is what the model passed. A malformed payload is refused, never rendered.

Names follow the chat prompt. The two `visualize:` tools drop the colon — it is
an MCP server prefix there, and a function name may not carry it here.
"""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

from claude_agent_sdk import tool

from ..core.backends.base import Event
from .context import ctx, err, ok, studio

# --- a JSON-schema subset validator ---------------------------------------------------------
# type, required, properties, items, enum, minItems/maxItems, minLength/maxLength,
# minimum/maximum, format "url". Enough for every card schema; nothing clever.


def _check(value: Any, schema: dict[str, Any], path: str = "") -> str | None:
    t = schema.get("type")
    where = path or "payload"
    if t == "object":
        if not isinstance(value, dict):
            return f"{where} must be an object"
        for req in schema.get("required", []):
            if req not in value:
                return f"{where}.{req} is required"
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                why = _check(value[k], sub, f"{where}.{k}")
                if why:
                    return why
        return None
    if t == "array":
        if not isinstance(value, list):
            return f"{where} must be a list"
        if "minItems" in schema and len(value) < schema["minItems"]:
            return f"{where} needs at least {schema['minItems']} item(s)"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"{where} allows at most {schema['maxItems']} item(s)"
        items = schema.get("items")
        if items:
            for i, v in enumerate(value):
                why = _check(v, items, f"{where}[{i}]")
                if why:
                    return why
        return None
    if t == "string":
        if not isinstance(value, str):
            return f"{where} must be a string"
        if "enum" in schema and value not in schema["enum"]:
            return f"{where} must be one of {', '.join(schema['enum'])}"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return f"{where} is longer than {schema['maxLength']} characters"
        if schema.get("minLength", 1 if schema.get("required_nonempty") else 0) and not value.strip():
            return f"{where} must not be empty"
        if schema.get("format") == "url" and not re.match(r"^https?://[^\s]+$", value):
            return f"{where} must be an absolute http(s) URL"
        return None
    if t == "number" or t == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{where} must be a number"
        if not math.isfinite(value):
            return f"{where} must be a finite number"
        if t == "integer" and int(value) != value:
            return f"{where} must be an integer"
        if "minimum" in schema and value < schema["minimum"]:
            return f"{where} must be at least {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{where} must be at most {schema['maximum']}"
        return None
    if t == "boolean":
        return None if isinstance(value, bool) else f"{where} must be true or false"
    return None


def _public(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema the model receives: `required_nonempty` is the validator's own
    keyword, not JSON Schema, so it is stripped from every level."""
    out = copy.deepcopy(schema)

    def strip(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("required_nonempty", None)
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)
    strip(out)
    return out


def _s(desc: str = "", **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "string"}
    if desc:
        d["description"] = desc
    d.update(kw)
    return d


_SUMMARY = _s("One short sentence (under 15 words) naming what this card shows, for surfaces "
              "that can't render it. Write this last.", required_nonempty=True)
_URL = _s("Absolute https URL. Omit if you don't have a real one — never fabricate a link.", format="url")


def _card(name: str, description: str, schema: dict[str, Any], render):
    """One typed card: validate, show in Studio, return the text form."""
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        why = _check(args, schema)
        if why:
            return err(f"{name}: {why}. Nothing was shown.")
        text = render(args)
        emit = ctx().emit
        if emit is not None and studio() is not None:
            emit(Event("studio", {"op": "widget", "widget": name, "data": args, "text": text}))
            return ok(text + "\n\n(shown as a card in Studio)")
        return ok(text)
    handler.__name__ = name
    return tool(name, description, _public(schema))(handler)


# --- the eleven typed cards ----------------------------------------------------------------


def _r_chart(a: dict[str, Any]) -> str:
    lines = [f"[{a['style']} chart] {a.get('title') or ''}".rstrip()]
    for s in a["series"]:
        nm = s.get("name") or "series"
        if s.get("points"):
            pts = ", ".join(f"({p['x']:g}, {p['y']:g})" for p in s["points"][:40])
            lines.append(f"  {nm}: {pts}")
        else:
            lines.append(f"  {nm}: " + ", ".join(f"{v:g}" for v in (s.get("values") or [])[:60]))
    xa, ya = a.get("x_axis") or {}, a.get("y_axis") or {}
    if xa.get("label") or ya.get("label"):
        lines.append(f"  x: {xa.get('label', '')}   y: {ya.get('label', '')}")
    if xa.get("categories"):
        lines.append("  categories: " + ", ".join(str(c) for c in xa["categories"][:60]))
    return "\n".join(lines)


chart_display_v0 = _card(
    "chart_display_v0",
    "Display a simple chart (line, bar, or scatter) inline. Use for quick, standard charts of "
    "a small dataset already in the conversation or just computed: a trend over time, a "
    "comparison across a handful of categories, the relationship between two numeric "
    "variables. Prefer this over visualize_show_widget for plain charts. Not for dashboards "
    "or anything needing custom styling.",
    {"type": "object", "required": ["series", "style"], "properties": {
        "title": _s("Optional title rendered above the chart."),
        "style": _s("The chart type.", enum=["line", "bar", "scatter"]),
        "series": {"type": "array", "minItems": 1, "maxItems": 12, "description": "One or more series.",
                   "items": {"type": "object", "properties": {
                       "name": _s("Optional; a name means a legend."),
                       "color": _s("Optional hex color; only when the data has a semantic color."),
                       "values": {"type": "array", "items": {"type": "number"}, "description": "1-d data for bar/line."},
                       "points": {"type": "array", "items": {"type": "object", "required": ["x", "y"],
                                  "properties": {"x": {"type": "number"}, "y": {"type": "number"}}},
                                  "description": "2-d points for scatter."}}}},
        "x_axis": {"type": "object", "properties": {"label": _s(), "categories": {"type": "array", "items": _s()}}},
        "y_axis": {"type": "object", "properties": {"label": _s(), "min": {"type": "number"}, "max": {"type": "number"}}},
    }},
    _r_chart,
)


def _r_products(a: dict[str, Any], head: str) -> str:
    lines = [head]
    for p in a["products"]:
        line = f"  • {p['name']}" + (f" — {p['price']}" if p.get("price") else "")
        if p.get("url"):
            line += f"  <{p['url']}>"
        lines.append(line)
        if p.get("blurb"):
            lines.append(f"      {p['blurb']}")
        for at in p.get("attributes") or []:
            lines.append(f"      {at['label']}: {at['value']}")
    lines.append(f"  ({a['summary']})")
    return "\n".join(lines)


_PRODUCT = {"type": "object", "required": ["name"], "properties": {
    "name": _s("Product name (a few words).", required_nonempty=True),
    "price": _s("Display price with currency, e.g. '$549'. Omit when unknown."),
    "url": _URL,
    "blurb": _s("Up to one paragraph on why, and the trade-offs. Don't restate the name or price."),
}}

comparison_card_display_v0 = _card(
    "comparison_card_display_v0",
    "Show 2–3 products side by side in a comparison table with aligned attribute rows. For "
    "shopping questions weighing a small set of named options against the same criteria. "
    "Use the SAME attribute labels, in the SAME order, for every product. One product → "
    "featured_card_display_v0; more than three → product_carousel_display_v0; options that "
    "don't share attributes, or a single recommendation with reasoning → prose.",
    {"type": "object", "required": ["products", "summary"], "properties": {
        "products": {"type": "array", "minItems": 2, "maxItems": 3, "items": {
            "type": "object", "required": ["name", "attributes"], "properties": {
                "name": _s(required_nonempty=True), "price": _s(), "url": _URL,
                "attributes": {"type": "array", "minItems": 2, "maxItems": 8, "items": {
                    "type": "object", "required": ["label", "value"],
                    "properties": {"label": _s(required_nonempty=True), "value": _s()}}}}}},
        "summary": _SUMMARY}},
    lambda a: _r_products(a, "[comparison]"),
)

featured_card_display_v0 = _card(
    "featured_card_display_v0",
    "Show your single best product pick as one rich card: name, optional price, and a blurb "
    "on why it's the pick and its trade-offs. For 'just tell me which one to get'. Several "
    "to browse → product_carousel_display_v0; weighing named options → "
    "comparison_card_display_v0; not a purchasable product → prose.",
    {"type": "object", "required": ["products", "summary"], "properties": {
        "products": {"type": "array", "minItems": 1, "maxItems": 1, "items": _PRODUCT},
        "summary": _SUMMARY}},
    lambda a: _r_products(a, "[featured pick]"),
)

product_carousel_display_v0 = _card(
    "product_carousel_display_v0",
    "Show a paged product carousel — one product per page with name, price, and a short "
    "blurb — for looking closely at a handful of recommended products one at a time. Single "
    "best pick → featured_card_display_v0; named options on shared criteria → "
    "comparison_card_display_v0.",
    {"type": "object", "required": ["products", "summary"], "properties": {
        "products": {"type": "array", "minItems": 1, "maxItems": 6, "items": _PRODUCT},
        "summary": _SUMMARY}},
    lambda a: _r_products(a, "[products]"),
)


def _r_itinerary(a: dict[str, Any]) -> str:
    lines = [f"[itinerary] {a.get('title') or ''}".rstrip()]
    for d in a["days"]:
        lines.append(f"  {d['day_label']}")
        for s in d["stops"]:
            t = f"{s['time']} — " if s.get("time") else ""
            lines.append(f"    • {t}{s['name']}" + (f": {s['blurb']}" if s.get("blurb") else ""))
    lines.append(f"  ({a['summary']})")
    return "\n".join(lines)


itinerary_display_v0 = _card(
    "itinerary_display_v0",
    "Show a day-by-day travel timeline with tabbed days and a list of stops per day. For "
    "trip-planning answers that are an ordered itinerary across 1–7 days with at least one "
    "named stop each. More than 7 days or 12 stops in a day, or general travel advice → prose.",
    {"type": "object", "required": ["days", "summary"], "properties": {
        "title": _s("Short heading, e.g. '3 days in Tokyo'."),
        "days": {"type": "array", "minItems": 1, "maxItems": 7, "items": {
            "type": "object", "required": ["day_label", "stops"], "properties": {
                "day_label": _s("Tab label under 12 chars: 'Day 1', 'Sat 14 Jun'.", maxLength=12),
                "stops": {"type": "array", "minItems": 1, "maxItems": 12, "items": {
                    "type": "object", "required": ["name"], "properties": {
                        "name": _s(required_nonempty=True), "time": _s("Optional clock time or slot."),
                        "blurb": _s("Optional one line on what to do there.")}}}}}},
        "summary": _SUMMARY}},
    _r_itinerary,
)


def _r_links(a: dict[str, Any]) -> str:
    lines = ["[links]"]
    for l in a["links"]:
        dom = l.get("domain") or re.sub(r"^https?://([^/]+).*$", r"\1", l["url"])
        lines.append(f"  • {l['title']} — {dom}\n    {l['url']}")
        if l.get("snippet"):
            lines.append(f"    {l['snippet']}")
    lines.append(f"  ({a['summary']})")
    return "\n".join(lines)


link_preview_display_v0 = _card(
    "link_preview_display_v0",
    "Show 1–6 web links as preview cards with title, source, and an optional snippet — "
    "search results, citations, 'read more' references that back up your answer. Never "
    "fabricate a URL; drop an entry you don't have a real absolute http(s) link for. In-chat "
    "content or a single incidental link → prose.",
    {"type": "object", "required": ["links", "summary"], "properties": {
        "links": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
            "type": "object", "required": ["url", "title"], "properties": {
                "url": _s("Absolute http(s) URL.", format="url"), "title": _s("One line, under ~80 chars.", required_nonempty=True),
                "domain": _s("Optional display host; derived from the URL when omitted."),
                "snippet": _s("Optional one or two sentences on why this link is relevant.")}}},
        "summary": _SUMMARY}},
    _r_links,
)


def _r_options(a: dict[str, Any]) -> str:
    lines = [f"[options] {a.get('title') or ''}".rstrip()]
    for o in a["options"]:
        lines.append(f"  {o['title']} — {o['description']}")
        for b in o["bullets"]:
            lines.append(f"    - {b}")
    lines.append(f"  ({a['summary']})")
    return "\n".join(lines)


options_card_display_v0 = _card(
    "options_card_display_v0",
    "Show a structured set of 2–8 distinct approaches the user could take, each with a "
    "one- or two-sentence description and at least two concrete next steps. For personal "
    "questions where the answer is alternatives to choose between. One nuanced "
    "recommendation, an A-vs-B comparison, or options that need explanation more than "
    "action → prose.",
    {"type": "object", "required": ["options", "summary"], "properties": {
        "title": _s("Short heading for the set."),
        "options": {"type": "array", "minItems": 2, "maxItems": 8, "items": {
            "type": "object", "required": ["title", "description", "bullets"], "properties": {
                "title": _s(required_nonempty=True), "description": _s(required_nonempty=True),
                "bullets": {"type": "array", "minItems": 2, "maxItems": 8, "items": _s(required_nonempty=True)}}}},
        "summary": _SUMMARY}},
    _r_options,
)


def _r_places(a: dict[str, Any]) -> str:
    lines = ["[places]"]
    for p in a["places"]:
        lines.append(f"  • {p['name']}" + (f" — {p['description']}" if p.get("description") else ""))
        if p.get("tips"):
            lines.append("    " + " · ".join(p["tips"]))
    lines.append(f"  ({a['summary']})")
    return "\n".join(lines)


places_list_display_v0 = _card(
    "places_list_display_v0",
    "Show a stacked list of 1–8 specific places with a short description and up to three "
    "very short tips each — cafes, hikes, neighbourhoods, hotels the user might visit. "
    "Only for places you found via web search or already know. Photos are not fetched; "
    "the card shows what you pass.",
    {"type": "object", "required": ["places", "summary"], "properties": {
        "places": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
            "type": "object", "required": ["name"], "properties": {
                "name": _s(required_nonempty=True), "description": _s("Optional one or two sentences."),
                "tips": {"type": "array", "maxItems": 3, "items": _s("2–4 word label, e.g. 'Book ahead'.")}}}},
        "summary": _SUMMARY}},
    _r_places,
)


def _r_quiz(a: dict[str, Any]) -> str:
    lines = [f"[quiz · {a.get('initial_mode') or 'quiz'}] {a.get('title') or ''}".rstrip()]
    if a.get("description"):
        lines.append(f"  {a['description']}")
    for i, q in enumerate(a["questions"], 1):
        lines.append(f"  {i}. {q['question']}")
        for o in q["options"]:
            mark = "✓" if o["id"] == q["correct_option_id"] else " "
            lines.append(f"     {mark} [{o['id']}] {o['text']}")
        lines.append(f"     → {q['explanation']}")
    return "\n".join(lines)


def _quiz_ok(a: dict[str, Any]) -> str | None:
    for i, q in enumerate(a["questions"]):
        ids = [o["id"] for o in q["options"]]
        if len(set(ids)) != len(ids):
            return f"questions[{i}] has duplicate option ids"
        if q["correct_option_id"] not in ids:
            return f"questions[{i}].correct_option_id must match one of its options' ids"
    return None


_QUIZ_SCHEMA = {"type": "object", "required": ["questions"], "properties": {
    "title": _s(), "description": _s("Optional one-line summary of what the quiz covers."),
    "initial_mode": _s("'quiz' (graded, default) or 'flashcards' (flip cards for review).", enum=["quiz", "flashcards"]),
    "questions": {"type": "array", "minItems": 1, "maxItems": 30, "items": {
        "type": "object", "required": ["id", "question", "options", "correct_option_id", "explanation"],
        "properties": {
            "id": _s("Unique within the quiz, e.g. 'q1'.", required_nonempty=True),
            "question": _s(required_nonempty=True),
            "options": {"type": "array", "minItems": 2, "maxItems": 6, "items": {
                "type": "object", "required": ["id", "text"],
                "properties": {"id": _s(required_nonempty=True), "text": _s(required_nonempty=True)}}},
            "correct_option_id": _s(required_nonempty=True),
            "explanation": _s("Why the correct answer is correct; concise.", required_nonempty=True),
            "hint": _s("Optional nudge that does not give it away."),
            "correct_feedback": _s(), "incorrect_feedback": _s()}}}}}


async def _quiz_handler(args: dict[str, Any]) -> dict[str, Any]:
    why = _check(args, _QUIZ_SCHEMA) or _quiz_ok(args)
    if why:
        return err(f"quiz_display_v0: {why}. Nothing was shown.")
    text = _r_quiz(args)
    emit = ctx().emit
    if emit is not None and studio() is not None:
        emit(Event("studio", {"op": "widget", "widget": "quiz_display_v0", "data": args, "text": text}))
        return ok(text + "\n\n(shown as a card in Studio)")
    return ok(text)


quiz_display_v0 = tool(
    "quiz_display_v0",
    "Generate an interactive multiple-choice quiz as a card; the same questions can be "
    "flipped through as flashcards. For 'quiz me', practice questions, self-assessment, "
    "including from documents the user shared. Each question needs plausible distractors, "
    "a concise explanation, optionally a hint. Default to 5 questions.",
    _public(_QUIZ_SCHEMA),
)(_quiz_handler)


def _r_steps(a: dict[str, Any]) -> str:
    lines = [f"[steps · {a.get('view') or 'stepper'}]"]
    for i, s in enumerate(a["steps"], 1):
        lines.append(f"  {i}. {s['title']} — {s['description']}")
    lines.append(f"  ({a['summary']})")
    return "\n".join(lines)


step_card_display_v0 = _card(
    "step_card_display_v0",
    "Show a numbered, step-by-step walkthrough for fixing or setting something up: 2–8 "
    "ordered steps, each with a short imperative title and a one- or two-sentence "
    "description. 'stepper' reveals one at a time (steps that must be done in order); "
    "'list' shows all (a checklist to scan). A single step, non-procedural advice, or a "
    "coding task wanting code → prose.",
    {"type": "object", "required": ["steps", "summary"], "properties": {
        "view": _s("'stepper' (default) or 'list'.", enum=["stepper", "list"]),
        "steps": {"type": "array", "minItems": 2, "maxItems": 8, "items": {
            "type": "object", "required": ["title", "description"],
            "properties": {"title": _s(required_nonempty=True), "description": _s(required_nonempty=True)}}},
        "summary": _SUMMARY}},
    _r_steps,
)


def _r_translation(a: dict[str, Any]) -> str:
    out = [f"[translation] {a['source_language']} → {a['target_language']}",
           f"  {a['source_text']}", f"  → {a['translation']}"]
    if a.get("pronunciation"):
        out.append(f"    ({a['pronunciation']})")
    return "\n".join(out)


translation_display_v0 = _card(
    "translation_display_v0",
    "Show a translation card — original and translation side by side with copy affordances "
    "— when the user asks how to say or write a specific short passage in another language. "
    "Do NOT repeat the translation in your reply; add one or two sentences of nuance only. "
    "Not for single-word lookups, long documents, or grammar explanations.",
    {"type": "object",
     "required": ["source_language", "source_text", "summary", "target_lang", "target_language", "translation"],
     "properties": {
         "source_lang": _s("BCP-47 tag, e.g. 'en'."), "source_language": _s(required_nonempty=True),
         "source_text": _s(required_nonempty=True),
         "target_lang": _s("BCP-47 tag, e.g. 'ja', 'es-MX'.", required_nonempty=True),
         "target_language": _s(required_nonempty=True), "translation": _s(required_nonempty=True),
         "pronunciation": _s("Romanization when the target script is not Latin."),
         "summary": _SUMMARY}},
    _r_translation,
)

# --- the widget pair --------------------------------------------------------------------

_RULES = {
    "diagram": """# diagram — SVG flowcharts, architecture, sequences
Canvas: an <svg viewBox="0 0 960 540"> (desktop) or "0 0 380 640" (mobile); width 100%,
no fixed pixel size. Colors: use the CSS variables the frame provides —
--bg, --surface, --line, --text, --muted, --accent (violet), --accent-2 (cyan),
--ok, --warn, --danger — and nothing hard-coded. Text: font-family var(--font),
14px minimum, 12px only for labels on edges. Boxes: 8px radius, 1px var(--line)
stroke, var(--surface) fill; arrows with a marker-end. Layout: left→right or
top→bottom, 40px gutters, no overlaps; at most ~12 nodes per diagram — split
beyond that. Never load external fonts or images.
Example: <svg viewBox="0 0 960 540" width="100%"><defs><marker id="a" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8z" fill="var(--muted)"/></marker></defs><rect x="40" y="220" width="200" height="80" rx="8" fill="var(--surface)" stroke="var(--line)"/><text x="140" y="266" text-anchor="middle" font-family="var(--font)" font-size="16" fill="var(--text)">Client</text><line x1="240" y1="260" x2="360" y2="260" stroke="var(--accent)" stroke-width="2" marker-end="url(#a)"/></svg>""",
    "mockup": """# mockup — UI screens as HTML
One screen per widget, sized to its device (390×844 phone, 1280×800 desktop),
centered, background var(--bg). Real copy, not lorem. Hit targets ≥44px on
phone. Use the design tokens (--accent, --surface, --line, --text, --muted,
--radius: 10px, --font). No external CSS or fonts; no network. State changes
in plain JS are fine; keep it under ~200 lines.
Example: <div style="width:390px;margin:0 auto;background:var(--bg);font-family:var(--font)"><header style="padding:16px;border-bottom:1px solid var(--line);color:var(--text)">Orders</header><button style="margin:16px;height:44px;padding:0 20px;border:0;border-radius:var(--radius);background:var(--accent);color:#fff">New order</button></div>""",
    "interactive": """# interactive — calculators, forms, small tools
Plain HTML + JS, self-contained; no libraries. Inputs labeled; results update
live; keyboard works. Persist nothing. sendPrompt(text) is available: it puts
text into the user's composer for them to send — never call it on load or on a
timer, only from an explicit user action. Same tokens as mockups.
Example: <label>Amount <input id="a" type="number" value="100"></label><p>Tip: <b id="t">15.00</b></p><button id="b">Use this</button><script>const a=document.getElementById("a"),t=document.getElementById("t");a.oninput=()=>{t.textContent=(a.value*0.15).toFixed(2)};document.getElementById("b").onclick=()=>sendPrompt("Tip is "+t.textContent)</script>""",
    "chart": """# chart — hand-drawn SVG charts when chart_display_v0 is too plain
viewBox "0 0 960 540". Axes in var(--line), labels in var(--muted) 12–14px,
series in --accent, --accent-2, --ok, --warn, --danger in that order; a legend
when there is more than one series. Gridlines light. Numbers formatted with
thousands separators. Title 18px var(--text) top-left.
Example: <svg viewBox="0 0 960 540" width="100%"><text x="24" y="40" font-size="18" fill="var(--text)" font-family="var(--font)">Signups</text><line x1="80" y1="480" x2="920" y2="480" stroke="var(--line)"/><rect x="120" y="280" width="80" height="200" fill="var(--accent)"/><rect x="280" y="180" width="80" height="300" fill="var(--accent)"/><text x="160" y="505" text-anchor="middle" font-size="12" fill="var(--muted)">Jan</text></svg>""",
    "data_viz": """# data_viz — tables and small multiples
A <table> with sticky header, zebra rows via var(--surface), numeric columns
right-aligned, units in the header not the cells. Sort by clicking a header is
fine in plain JS. Max ~40 rows; summarise beyond that.
Example: <table style="border-collapse:collapse;font-family:var(--font);color:var(--text)"><thead><tr><th style="position:sticky;top:0;background:var(--surface);text-align:left;padding:6px 10px">Region</th><th style="text-align:right;padding:6px 10px">Revenue (USD)</th></tr></thead><tbody><tr><td style="padding:6px 10px">North</td><td style="text-align:right;padding:6px 10px">1,240,000</td></tr></tbody></table>""",
    "art": """# art — illustration in SVG
Flat shapes, 2–4 colors from the tokens plus one accent, no gradients heavier
than two stops, no external images. viewBox "0 0 960 540". Keep paths simple;
prefer geometry over detail.
Example: <svg viewBox="0 0 960 540" width="100%"><rect width="960" height="540" fill="var(--bg)"/><circle cx="480" cy="270" r="140" fill="var(--accent-2)"/><path d="M200 420 Q480 300 760 420 L760 540 L200 540z" fill="var(--surface)"/></svg>""",
    "elicitation": """# elicitation — a widget that asks
Buttons for 2–6 choices, radios for one-of, checkboxes for many; a Send button
that calls sendPrompt with a one-line answer keyed by the question. Never
auto-send. Same tokens as mockups.
Example: <p style="font-family:var(--font);color:var(--text)">Which matters most?</p><button onclick="sendPrompt('Priority: speed')">Speed</button> <button onclick="sendPrompt('Priority: cost')">Cost</button>""",
}


@tool(
    "visualize_read_me",
    "Returns the design rules for visualize_show_widget (the CSS variables the frame "
    "provides, colors, typography, layout rules, sizing) for the modules you name: diagram, "
    "mockup, interactive, data_viz, art, chart, elicitation. Call before your first "
    "visualize_show_widget; call again for a different module. Do not narrate this call.",
    {"type": "object", "properties": {
        "modules": {"type": "array", "items": _s(enum=list(_RULES)), "minItems": 1, "maxItems": 7},
        "platform": _s("'mobile' for a ~380px viewport, else 'desktop'.", enum=["mobile", "desktop", "unknown"])},
     "required": ["modules"]},
)
async def visualize_read_me(args: dict[str, Any]) -> dict[str, Any]:
    why = _check(args, {"type": "object", "required": ["modules"], "properties": {
        "modules": {"type": "array", "minItems": 1, "maxItems": 7, "items": _s(enum=list(_RULES))}}})
    if why:
        return err(f"visualize_read_me: {why}.")
    platform = args.get("platform") or "desktop"
    head = (f"Studio widget frame ({platform}): sandboxed, no network, CSS variables set on :root: "
            "--bg --surface --line --text --muted --accent --accent-2 --ok --warn --danger --radius "
            "--font. Everything you render must live inside one self-contained SVG or HTML string.")
    return ok(head + "\n\n" + "\n\n".join(_RULES[m] for m in args["modules"]))


@tool(
    "visualize_show_widget",
    "Show visual content — SVG graphics, diagrams, charts, or interactive HTML widgets — "
    "inline in Studio alongside your reply: flowcharts, architecture diagrams, dashboards, "
    "forms, calculators, data tables, small games, illustrations. Auto-detected: code "
    "starting with <svg is SVG, otherwise HTML. It renders in the same sandboxed, "
    "network-less frame as an artifact, with the design tokens from visualize_read_me. A "
    "global sendPrompt(text) puts text in the user's composer for them to send. Call "
    "visualize_read_me first; do not narrate it.",
    _public({"type": "object", "required": ["title", "widget_code"], "properties": {
        "title": _s("Short snake_case identifier, specific enough to tell visuals apart; also the file name.",
                    required_nonempty=True, maxLength=80),
        "widget_code": _s("SVG (starts with <svg) or HTML.", required_nonempty=True),
        "loading_messages": {"type": "array", "minItems": 1, "maxItems": 4, "items": _s()}}}),
)
async def visualize_show_widget(args: dict[str, Any]) -> dict[str, Any]:
    why = _check(args, {"type": "object", "required": ["title", "widget_code"], "properties": {
        "title": _s(required_nonempty=True, maxLength=80), "widget_code": _s(required_nonempty=True)}})
    if why:
        return err(f"visualize_show_widget: {why}.")
    title = re.sub(r"[^A-Za-z0-9_-]+", "_", args["title"]).strip("_") or "widget"
    code = str(args["widget_code"])
    kind = "svg" if code.lstrip().lower().startswith("<svg") else "html"
    emit = ctx().emit
    if emit is None or studio() is None:
        # No panel: keep the file so `show_html` and screenshots can still see it.
        from pathlib import Path
        p = (ctx().workspace / "widgets" / f"{title}.{'svg' if kind == 'svg' else 'html'}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
        return ok(f"Studio is not open; the {kind} widget was written to {p} — show_html it, "
                  f"or hand it to the user as a file.")
    emit(Event("studio", {"op": "widget", "widget": "visualize_show_widget",
                          "data": {"title": title, "kind": kind, "code": code,
                                   "loading": args.get("loading_messages") or []}}))
    return ok(f"Widget '{title}' ({kind}, {len(code):,} chars) is showing in Studio.")


# --- image_search -----------------------------------------------------------------------


@tool(
    "image_search",
    "Search the web for images, for any query where seeing something helps — places, "
    "animals, food, products, style, diagrams, historical photos, exercises. Returns 3–5 "
    "images with their dimensions and shows them inline in Studio. Skip for text, code, "
    "and technical-support work. Queries 3–6 words with context: 'Paris France Eiffel "
    "Tower', not 'Paris'.",
    _public({"type": "object", "required": ["query"], "properties": {
        "query": _s(required_nonempty=True),
        "max_results": {"type": "integer", "minimum": 3, "maximum": 5, "description": "3–5 (default 3)."}}}),
)
async def image_search(args: dict[str, Any]) -> dict[str, Any]:
    why = _check(args, {"type": "object", "required": ["query"], "properties": {
        "query": _s(required_nonempty=True), "max_results": {"type": "integer", "minimum": 3, "maximum": 5}}})
    if why:
        return err(f"image_search: {why}.")
    n = int(args.get("max_results") or 3)
    from ..web import searxng

    try:
        res = await searxng.search(str(args["query"]), categories="images", limit=n * 3)
    except searxng.SearxngUnavailable as e:
        return err(str(e))
    except Exception as e:
        return err(f"image search failed: {type(e).__name__}: {e}")
    images = []
    for r in res.get("results") or []:
        src = r.get("img_src") or r.get("url")
        if not src or not str(src).startswith(("http://", "https://")):
            continue
        w, h = r.get("img_width") or r.get("width"), r.get("img_height") or r.get("height")
        if not (w and h) and r.get("resolution"):
            m = re.match(r"(\d+)\s*[x×]\s*(\d+)", str(r["resolution"]))
            if m:
                w, h = int(m.group(1)), int(m.group(2))
        images.append({"src": str(src), "thumb": str(r.get("thumbnail_src") or src),
                       "title": str(r.get("title") or "")[:120],
                       "width": int(w) if w else None, "height": int(h) if h else None,
                       "page": str(r.get("url") or "")})
        if len(images) >= n:
            break
    if not images:
        return ok(f"No images found for {args['query']!r}.")
    lines = [f"[images] {args['query']}"]
    for im in images:
        dims = f" {im['width']}×{im['height']}" if im["width"] and im["height"] else ""
        lines.append(f"  • {im['title'] or 'image'}{dims}\n    {im['src']}")
    text = "\n".join(lines)
    emit = ctx().emit
    if emit is not None and studio() is not None:
        emit(Event("studio", {"op": "widget", "widget": "image_search",
                              "data": {"query": args["query"], "images": images}, "text": text}))
        return ok(text + "\n\n(shown inline in Studio)")
    return ok(text)


# --- elicitation, research, ending ----------------------------------------------------------


@tool(
    "ask_user_input",
    "Present 1–3 questions with tappable options (2–4 short labels each) to gather the "
    "user's preferences, constraints, or goals before giving advice — much easier than "
    "typing. 'single_select' picks one, 'multi_select' one or more, 'rank_priorities' "
    "orders them. The answers arrive as the next prompt; END YOUR TURN after calling it. "
    "For a full design-brief form use questions_v2 instead.",
    _public({"type": "object", "required": ["questions"], "properties": {
        "questions": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
            "type": "object", "required": ["question", "options"], "properties": {
                "question": _s(required_nonempty=True),
                "options": {"type": "array", "minItems": 2, "maxItems": 4, "items": _s(required_nonempty=True)},
                "type": _s(enum=["single_select", "multi_select", "rank_priorities"])}}}}}),
)
async def ask_user_input(args: dict[str, Any]) -> dict[str, Any]:
    why = _check(args, {"type": "object", "required": ["questions"], "properties": {
        "questions": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
            "type": "object", "required": ["question", "options"], "properties": {
                "question": _s(required_nonempty=True),
                "options": {"type": "array", "minItems": 2, "maxItems": 4, "items": _s(required_nonempty=True)},
                "type": _s(enum=["single_select", "multi_select", "rank_priorities"])}}}}})
    if why:
        return err(f"ask_user_input: {why}.")
    qs = []
    for i, q in enumerate(args["questions"]):
        kind = q.get("type") or "single_select"
        if kind == "rank_priorities":
            # The Phase 6 form has no ranking control; a written order is one.
            qs.append({"id": f"q{i + 1}", "kind": "freeform", "title": q["question"],
                       "subtitle": "Rank in order, most important first: " + " · ".join(q["options"])})
            continue
        qs.append({"id": f"q{i + 1}", "kind": "text-options", "title": q["question"],
                   "options": list(q["options"]), "multi": kind == "multi_select", "subtitle": ""})
    lines = ["Pick by number:"]
    for i, q in enumerate(args["questions"], 1):
        lines.append(f"{i}. {q['question']}")
        for j, o in enumerate(q["options"], 1):
            lines.append(f"   {j}) {o}")
    emit = ctx().emit
    if emit is None or studio() is None:
        return ok("Studio is not open, so ask these in your reply and end the turn:\n" + "\n".join(lines))
    emit(Event("studio", {"op": "ask", "form": {"title": "Quick questions", "questions": qs}}))
    return ok("The options are showing in Studio. END YOUR TURN; the answers come back as the next prompt.")


@tool(
    "suggest_research",
    "Offer the user a deep research run — Dream's researcher subagent, many sources, a "
    "sourced report — as a 'Start research' card. Calling this does NOT start it; the user "
    "presses the button. Use when the request would benefit from a broad, many-source "
    "investigation; answer what you can directly in the same reply.",
    _public({"type": "object", "required": ["rationale"], "properties": {
        "rationale": _s("One short sentence on why research would help; describe the task shape, "
                        "do not quote the user.", required_nonempty=True, maxLength=200)}}),
)
async def suggest_research(args: dict[str, Any]) -> dict[str, Any]:
    why = _check(args, {"type": "object", "required": ["rationale"],
                        "properties": {"rationale": _s(required_nonempty=True, maxLength=200)}})
    if why:
        return err(f"suggest_research: {why}.")
    emit = ctx().emit
    if emit is None or studio() is None:
        return ok(f"Studio is not open; offer it in words: \"I can run a deep research pass "
                  f"({args['rationale']}) — say 'research it' and I will delegate to the researcher.\"")
    emit(Event("studio", {"op": "widget", "widget": "suggest_research", "data": {"rationale": args["rationale"]}}))
    return ok("A 'Start research' card is showing in Studio. Answer what you can now.")


_END_STATE: dict[str, Any] = {"armed": False, "requested": False}


def end_requested() -> bool:
    return bool(_END_STATE["requested"])


def reset_end() -> None:
    _END_STATE.update(armed=False, requested=False)


@tool(
    "end_conversation",
    "End the session. Only as a last resort after warnings, or when the user explicitly "
    "asks and has confirmed they understand it is permanent. The first call does not end "
    "anything: it returns a confirmation request; call it again to confirm.",
    {"type": "object", "properties": {}, "required": []},
)
async def end_conversation(args: dict[str, Any]) -> dict[str, Any]:
    if not _END_STATE["armed"]:
        _END_STATE["armed"] = True
        return ok("This will end the session and stop further messages. If you are certain, "
                  "call end_conversation again to confirm. This confirmation request is part of "
                  "the tool, not a message from the user.")
    _END_STATE["requested"] = True
    emit = ctx().emit
    if emit is not None:
        emit(Event("system", "end_conversation confirmed — the session ends after this turn"))
    return ok("Confirmed. Say goodbye; the session ends when this turn does.")


WIDGET_TOOLS = [
    chart_display_v0, comparison_card_display_v0, featured_card_display_v0, itinerary_display_v0,
    link_preview_display_v0, options_card_display_v0, places_list_display_v0,
    product_carousel_display_v0, quiz_display_v0, step_card_display_v0, translation_display_v0,
    visualize_read_me, visualize_show_widget, image_search, ask_user_input, suggest_research,
    end_conversation,
]

# The tools whose result text IS the answer in a terminal: the TUI prints it
# whole, where an ordinary tool result is a one-line preview.
CARD_TOOL_NAMES = frozenset(t.name for t in WIDGET_TOOLS[:11]) | {"image_search"}
