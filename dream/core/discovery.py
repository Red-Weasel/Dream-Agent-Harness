"""Experimental description-first discovery (DREAM-258, phase 2): one explicit, opt-in mode.

Default behaviour is untouched. With ``DREAM_DISCOVERY_MODE=descriptions`` set when the process starts, the
model's INITIAL view of tools and skills is a bounded list of one-or-two-sentence descriptions; a tool's full
definition and a skill's workflow enter the context only when the model asks for them. Tools stay registered and
every call still goes through the backend's own execution path (scope, enablement, full-schema validation, the
run meter, permission, the handler): this module decides what is SHOWN, never what may run.

Pure policy: no I/O beyond reading the environment variable, no store, no backend, no module-level mutable state.

ONE STARTUP DECISION. The process's opt-in is read once, when this module is first imported (``_STARTUP``), and
is never rebound by Dream afterwards. Every caller that is not handed a mode -- the system prompt's wake-up, the
MCP section, task skill selection, a backend built without one -- gets that same frozen value from
``resolve(None)``, so they cannot disagree with each other when the variable is changed while Dream runs. A
changed variable is REPORTED (``drift``; the HTTP backend says it once at a turn's start) and never applied:
restart Dream to change the mode. The variable must therefore be in the environment before Dream is imported.

What this is NOT: per-session isolation. The mode is the process's. A builder may hand one backend, prompt or
selection call an explicit mode (tests, an embedding caller), but only the callers it hands it to follow it: the
Engine's task skill selection is not handed one today, so it follows the process's mode whatever a single backend
was built with. A backend built in a mode other than the process's says so at its first turn.

Where it applies. The tools array (lookup, wrapper, catalog) is shaped only by the OpenAI-compatible HTTP
backend's own loop. The native Claude SDK / CLI backends keep their own tool discovery, which this module does
not see or shape and the HTTP context inspector cannot read; a session on one of them still gets the wake-up's
described skill indexes, the described MCP section and no eager task workflow when the process opted in, because
those are built outside the backend. That combination has not been exercised.

Sizes here are STRUCTURAL bounds in characters. They are not token counts and are never presented as one:
``report`` labels every token figure UNAVAILABLE unless the caller injects a counter, and never says a target was
met on its own authority (``qualified`` is always False here; qualification is the evaluator's).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .tool_budget_schemas import LOOKUP_TOOL_NAME

MODE_ENV = "DREAM_DISCOVERY_MODE"
DEFAULT = "default"
DESCRIPTIONS = "descriptions"
# Spellings that mean "unchanged". Anything else that is not DESCRIPTIONS is a problem, said, and never an enable.
_DEFAULT_SPELLINGS = frozenset({"", DEFAULT, "off"})

# The invocation wrapper. A grammar-constrained server lets the model emit only the names in the tools array, and
# that array must stay fixed for a local engine's prompt cache: so one stable entry carries the real tool's name.
INVOKE_TOOL_NAME = "tool_call"

# Structural bounds, in CHARACTERS, fixed whatever the window or the size of the toolset/preset.
DESCRIPTION_CHARS = 140       # one catalog or index description (one or two sentences)
CATALOG_CHARS = 600          # all tool catalog lines together
SKILL_INDEX_CHARS = 300      # all installed-skill index lines together
LEARNED_INDEX_CHARS = 200     # all learned-skill index lines together
LEARNED_LINE_CHARS = 200      # one learned-skill line (its slug leads, so a cut keeps the slug)
MCP_SERVERS_SHOWN = 4        # external servers named in the system prompt
MCP_INDEX_CHARS = 160         # aggregate server lines; exact names are never truncated
SEARCH_LIMIT = 8              # lines one search returns

# The owner's target for the initial discovery surface, in the MODEL's tokens. Nothing in Dream can count those
# today, so this is a figure to compare an injected count against, not a claim.
TARGET_TOKENS = 2222
UNAVAILABLE = "UNAVAILABLE"
# What a report must be given before it may compare a total against the target: leaving one out would make the
# total smaller than the surface really is. A section that is absent is passed as "", by a caller who knows it is
# absent; a part nobody supplied is MISSING and stops the comparison.
#   base_prompt      the base tier of the system prompt as sent (BASE or COMPACT_BASE with its paths filled in).
#                    It is tool, memory-tool and skill guidance almost throughout, so it is counted whole rather
#                    than split by judgement. Correction 1: leaving it out let a report call ~17,000 uncounted
#                    characters "within target".
#   stable_sections  the Engine's per-session sections of the system prompt (workspace, delegation, ...): the
#                    delegation section is tool guidance. Counted whole.
#   mcp_section      mcp_client.prompt_section's text; "" when no server is connected OR when it is already
#                    inside stable_sections (this module cannot detect a part counted twice or left out of both).
REQUIRED_COMPONENTS = ("lookup_tool", "invocation_tool", "control_tools", "base_prompt", "stable_sections",
                       "learned_skill_index", "installed_skill_index", "mcp_section", "task_guidance")
# What `total_tokens` is, said in every report that has one.
TOTAL_KIND = ("sum of the injected counter's count of each part's own text, taken part by part. Not additive "
              "across part boundaries (a tokenizer merges across them), not a count of the serialized request, "
              "and without the engine's chat template or its rendering of the tools array")
# Parts of the initial prompt this report does not hold: none of them is tool or skill guidance Dream writes.
NOT_COUNTED = ("response style", "the user's global and project instructions", "project notebook",
               "wake-up sections other than the two skill indexes", "the user's message")


@dataclass(frozen=True)
class Mode:
    """A resolved discovery mode. `problem` is set when the request could not be honoured (an unknown value, a
    name conflict): the mode is then DEFAULT and the caller is expected to say so."""

    name: str = DEFAULT
    raw: str | None = None
    problem: str | None = None

    @property
    def descriptions(self) -> bool:
        return self.name == DESCRIPTIONS


def shown(raw: Any) -> str:
    """A configured value as a notice may quote it: short, one line, printable."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(raw)[:40])
    return " ".join(text.split())


def _parse(value: Any) -> Mode:
    raw = str(value)
    text = raw.strip().lower()
    if text in _DEFAULT_SPELLINGS:
        return Mode(DEFAULT, raw)
    if text == DESCRIPTIONS:
        return Mode(DESCRIPTIONS, raw)
    return Mode(DEFAULT, raw, problem=(
        f"{MODE_ENV}='{shown(raw)}' is not a discovery mode (the only one is '{DESCRIPTIONS}'; unset it for the "
        "default). Tool and skill discovery is unchanged."))


def read_environment(environ: Mapping[str, str] | None = None) -> Mode:
    """What DREAM_DISCOVERY_MODE asks for in `environ` (else the process's environment) AS IT IS NOW. This is a
    reading, not the mode in force: `startup` is. Used to take the startup decision and to notice a change."""
    value = (os.environ if environ is None else environ).get(MODE_ENV)
    return Mode() if value is None else _parse(value)


# The process's one decision, taken when this module is first imported. Dream never rebinds it; a test that
# stands for "the process started with ..." replaces it for that test.
_STARTUP: Mode = read_environment()


def startup() -> Mode:
    """The mode this process started in: what every caller without a mode of its own follows."""
    return _STARTUP


def resolve(value: Any = None, *, environ: Mapping[str, str] | None = None) -> Mode:
    """The mode `value` asks for. None is the process's startup decision (`startup`), NOT the environment as it
    is now: a variable changed while Dream runs is not applied. With `environ` given, None reads that mapping
    instead (an explicit reading for a caller that owns one, and for tests).

    An unknown value never enables anything: the result is DEFAULT with `problem` naming it."""
    if isinstance(value, Mode):
        return value
    if value is None:
        return startup() if environ is None else read_environment(environ)
    return _parse(value)


def drift(environ: Mapping[str, str] | None = None) -> str | None:
    """None while the environment still asks for what the process started with; otherwise one line saying the
    variable changed and that the change is not applied. A different mode, or a new value that is not a mode,
    is a change; an unknown value going away is not (the mode was the default with it and is without it)."""
    was, now = startup(), read_environment(environ)
    if now.name == was.name and (now.problem is None or now.problem == was.problem):
        return None
    said = "unset" if now.raw is None else f"'{shown(now.raw)}'"
    return (f"{MODE_ENV} changed while Dream runs (now {said}). Dream keeps the discovery mode it started with "
            f"('{was.name}') for the system prompt, the tool list and task skill selection. Restart Dream to "
            "change it.")


# --- descriptions ------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def concise(text: Any, limit: int = DESCRIPTION_CHARS, sentences: int = 2) -> str:
    """At most `sentences` leading sentences of `text` on one line, at most `limit` characters."""
    flat = " ".join(str(text or "").split())
    if not flat:
        return ""
    out = " ".join(_SENTENCE_END.split(flat)[:max(1, sentences)])
    if len(out) > limit:
        out = out[:max(1, limit) - 1].rstrip() + "…"
    return out


def entry_line(name: str, description: Any, limit: int = DESCRIPTION_CHARS) -> str:
    """`name — what it is for`, on one line; the name alone when there is nothing to say."""
    desc = concise(description, limit)
    return f"{name} — {desc}" if desc else str(name)


def catalog_line(schema: dict[str, Any]) -> str:
    fn = schema["function"]
    return entry_line(fn["name"], fn.get("description"))


def bounded_lines(lines: Iterable[str], max_chars: int) -> tuple[list[str], int]:
    """The leading lines that fit `max_chars` (each counted with its line break), and how many were left out."""
    kept: list[str] = []
    used = omitted = 0
    for line in lines:
        cost = len(line) + 1
        if omitted or used + cost > max_chars:
            omitted += 1
            continue
        kept.append(line)
        used += cost
    return kept, omitted


# --- the tools array ---------------------------------------------------------

_LOOKUP_GUIDANCE = (
    "Find tools: search matches names/descriptions, including unlisted tools. "
    "Read name's full definition before first use; invoke via tool_call(name, arguments). "
    "Tools with their own signatures are called directly."
)
_INVOKE_DESCRIPTION = (
    "Run one tool by its exact name, with the arguments its definition describes "
    f"({LOOKUP_TOOL_NAME} returns the definition). The same checks and approvals apply as for a direct call."
)


def omission_notice(omitted: int) -> str:
    """The catalog's last line when tools were left out. Bracketed, no ` — `: never the shape of an entry."""
    return f"[{omitted} more not listed: find them with `search`.]"


def is_entry(line: str) -> bool:
    """Whether a line under `Available:` (or of a search result) is a tool's entry rather than a bracketed
    notice. A tool name never starts with `[`."""
    return bool(line.strip()) and not line.lstrip().startswith("[")


def lookup_schema(lines: Sequence[str], omitted: int) -> dict[str, Any]:
    """The lookup tool, carrying the discovery guidance and the bounded catalog in its description, so both
    travel with the tool that acts on them and are part of the tools array any count is taken of. No `enum` of
    names: a name list grows with the toolset, and an unknown name is answered, not executed.

    Every line after `Available:` is one tool, `name — description` (or the name alone). The count of tools left
    out is NOT such a line: it is bracketed and has no dash, so neither a model nor a parser can take it for a
    tool called "12 more not listed"."""
    text = _LOOKUP_GUIDANCE
    if lines:
        text += "\nAvailable:\n" + "\n".join(lines)
    if omitted:
        text += "\n" + omission_notice(omitted)
    return {
        "type": "function",
        "function": {
            "name": LOOKUP_TOOL_NAME,
            "description": text,
            "parameters": {"type": "object",
                           "properties": {"name": {"type": "string"}, "search": {"type": "string"}}},
        },
    }


def invoke_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": INVOKE_TOOL_NAME,
            "description": _INVOKE_DESCRIPTION,
            "parameters": {"type": "object",
                           "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}},
                           "required": ["name"]},
        },
    }


@dataclass(frozen=True)
class Surface:
    """What description mode offers at the start: the tools array, which tools have a catalog line, how many do
    not, and the array's parts as text (non-overlapping; together they are the whole array) for `report`."""

    tools: tuple[dict[str, Any], ...]
    listed: tuple[str, ...]
    omitted: int
    components: Mapping[str, str] = field(default_factory=dict)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def surface(schemas: Sequence[dict[str, Any]], *, controls: Sequence[dict[str, Any]] = (),
            priority: Iterable[str] = (), max_chars: int = CATALOG_CHARS) -> Surface:
    """The description-mode tools array for `schemas` (the enabled, in-scope tools; each gets a catalog line
    while the bound allows) plus `controls` (schemas the loop cannot work without, sent whole and never dropped).

    `priority` names are given a line first; the listing itself follows the input order, so the same toolset
    always renders the same text. Every line comes from a schema in `schemas`: nothing is listed that is not
    there. The result does not depend on a window."""
    first = set(priority)
    order = ([s for s in schemas if s["function"]["name"] in first]
             + [s for s in schemas if s["function"]["name"] not in first])
    chosen: set[str] = set()
    used = 0
    for schema in order:
        cost = len(catalog_line(schema)) + 1
        if used + cost > max_chars:
            continue
        chosen.add(schema["function"]["name"])
        used += cost
    listed = [s for s in schemas if s["function"]["name"] in chosen]
    lookup = lookup_schema([catalog_line(s) for s in listed], len(schemas) - len(listed))
    invoke = invoke_schema()
    kept_controls = [json.loads(_dumps(c)) for c in controls]
    return Surface(
        tools=(lookup, invoke, *kept_controls),
        listed=tuple(s["function"]["name"] for s in listed),
        omitted=len(schemas) - len(listed),
        components={"lookup_tool": _dumps(lookup), "invocation_tool": _dumps(invoke),
                    "control_tools": _dumps(kept_controls) if kept_controls else ""},
    )


def search(schemas: Sequence[dict[str, Any]], query: str, limit: int = SEARCH_LIMIT) -> tuple[list[str], int]:
    """(catalog lines, total matches) of the tools whose name or WHOLE description holds every word of `query`:
    the words past a line's short preview are searched too, and a tool without a catalog line is found."""
    words = [w for w in re.split(r"\W+", str(query).lower()) if w]
    if not words:
        return [], 0
    hits = []
    for schema in schemas:
        fn = schema["function"]
        hay = f"{fn['name']} {fn.get('description') or ''}".lower()
        if all(w in hay for w in words):
            hits.append(catalog_line(schema))
    return hits[:max(0, limit)], len(hits)


def definition_text(schema: dict[str, Any]) -> str:
    """A tool's full definition as the lookup returns it: how to run it, then the definition itself as JSON."""
    fn = schema["function"]
    return (f"Definition of {fn['name']} (run it with {INVOKE_TOOL_NAME}(name=\"{fn['name']}\", "
            f"arguments={{...}})):\n" + _dumps(fn))


def unwrap_invocation(args: Any, *, controls: Iterable[str]) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """A `tool_call`'s (target name, target arguments, refusal). Exactly one of the pair and the refusal is set.

    Refused, with nothing run: no usable name; a control of the loop or the wrapper itself as the target (so no
    call can route through the wrapper twice, and delegation keeps its own path); arguments that are not one JSON
    object; arguments written beside `arguments` instead of inside it. Unwrapping grants nothing: the caller
    still runs the target through its whole execution path."""
    me = INVOKE_TOOL_NAME
    if not isinstance(args, dict):
        return None, None, f"Error: {me} takes an object with `name` and `arguments`. Nothing ran."
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        return None, None, (f"Error: {me} needs `name`, the exact name of the tool to run. Nothing ran. "
                            f"{LOOKUP_TOOL_NAME}(search=...) finds a tool.")
    name = name.strip()
    if name.lower() in {str(c).lower() for c in controls} | {me}:
        return None, None, (f"Error: {me} cannot run '{shown(name)}': it is a control of this conversation, "
                            "not a catalog tool. Nothing ran. If it is in your tool list, call it directly.")
    extra = sorted(k for k in args if k not in ("name", "arguments"))
    if extra:
        return None, None, (f"Error: {me} takes only `name` and `arguments`; put the tool's own arguments "
                            f"({', '.join(shown(k) for k in extra[:6])}) inside `arguments`. Nothing ran.")
    inner = args.get("arguments")
    if inner is None:
        inner = {}
    if isinstance(inner, str):
        try:
            inner = json.loads(inner) if inner.strip() else {}
        except ValueError:
            return None, None, (f"Error: {me} `arguments` must be a JSON object, and yours was text that is not "
                                "valid JSON. Nothing ran.")
    if not isinstance(inner, dict):
        return None, None, (f"Error: {me} `arguments` must be a JSON object, but yours is "
                            f"{type(inner).__name__}. Nothing ran.")
    return name, inner, None


# --- honest accounting -------------------------------------------------------


def report(components: Mapping[str, str], *, counter: Callable[[str], int] | None = None,
           counter_label: str | None = None, target: int = TARGET_TOKENS,
           required: Sequence[str] = REQUIRED_COMPONENTS) -> dict[str, Any]:
    """The initial discovery surface, part by part, for a later evaluator.

    Characters are measured. Tokens are whatever `counter` says of each part's text, and UNAVAILABLE without
    one, or when it fails or answers with anything but a non-negative integer: nothing is estimated in its
    place. `within_target` is a comparison only when every count is in hand AND every required part was
    supplied; otherwise None. `qualified` is always False: a counter injected here is the caller's, a part-wise
    count is not the engine's count of the rendered prompt, and neither is a qualification on a model.

    The target covers every initial tool and skill part of the prompt, the compulsory ones included: the base
    prompt's guidance, the Engine's stable sections and the control schemas are REQUIRED parts (see
    REQUIRED_COMPONENTS), never optional and never dropped to fit. A part that was not supplied and could not
    be reconstructed is listed in `missing_components`, the report is `complete: False` and no comparison is
    made. A surface whose compulsory parts alone exceed the target is reported over it (`within_target: False`,
    `over_target_by`): that is the honest answer, not a reason to leave a part out.

    `total_tokens` is a sum of separate counts of each part's text (`total_kind`): tokens are not additive
    across part boundaries and the engine's template and tools rendering are not in it."""
    parts = {str(key): str(text or "") for key, text in components.items()}
    missing = [key for key in required if key not in parts]
    out: dict[str, Any] = {
        "mode": DESCRIPTIONS,
        "target_tokens": target,
        "components": {key: {"chars": len(text), "tokens": UNAVAILABLE} for key, text in parts.items()},
        "total_chars": sum(len(text) for text in parts.values()),
        "total_tokens": UNAVAILABLE,
        "total_kind": TOTAL_KIND,
        "count_status": UNAVAILABLE,
        "counter": None,
        "required_components": list(required),
        "missing_components": missing,
        "complete": not missing,
        "not_counted": list(NOT_COUNTED),
        "within_target": None,
        "over_target_by": None,
        "qualified": False,
        "note": ("Characters are measured; tokens are not counted here. No claim about the target is made."
                 + ("" if not missing else " Required parts were not supplied (" + ", ".join(missing)
                    + "), so the totals are of an incomplete surface.")),
    }
    if counter is None:
        return out
    counts: dict[str, int] = {}
    try:
        for key, text in parts.items():
            value = counter(text) if text else 0
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("counter did not return a non-negative integer")
            counts[key] = value
    except Exception as exc:
        out["note"] = (f"The injected counter failed ({type(exc).__name__}); tokens stay UNAVAILABLE and no claim "
                       "about the target is made.")
        return out
    for key, value in counts.items():
        out["components"][key]["tokens"] = value
    out["total_tokens"] = sum(counts.values())
    out["count_status"] = "COUNTED"
    out["counter"] = shown(counter_label) if counter_label else "injected (unnamed)"
    if not missing:
        out["within_target"] = out["total_tokens"] <= target
        out["over_target_by"] = max(0, out["total_tokens"] - target)
    out["note"] = ("Token figures are the injected counter's, taken part by part and summed: not additive across "
                   "part boundaries, not the engine's count of the rendered prompt, and not a qualification on "
                   "any model."
                   + ("" if not missing else " Required parts are missing (" + ", ".join(missing)
                      + "), so the total is of an incomplete surface and no comparison with the target is made."))
    return out
