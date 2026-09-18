"""Which tool schemas are worth their context — the local backend's schema tax.

The Claude backend gets this for free (``ENABLE_TOOL_SEARCH``), but the
OpenAI-compatible backend hands the server EVERY tool schema on EVERY round,
self-built ``custom/`` tools included — a set that only grows. On a 16k local
window that is a large slice of the context spent before the first token, and
spent again each tool round.

So: send the schemas that earn their tokens and replace the rest with a one-line
catalog plus a lookup tool that fetches a schema on demand. The same trade
ToolSearch makes — names always visible, because a tool the model doesn't know
about may as well not exist; only the parameters wait to be asked for.

Pure policy: no I/O, no store, no backend. Usage counts arrive through an
injected callable, so ranking sharpens when a memory store is behind it and
still works when nothing is.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Sequence

from .context_budget import estimate

# Share of the window the tools array may claim, leaving the rest for the system
# prompt, the history, and the answer.
DEFAULT_BUDGET_FRAC = 0.10

# The escape hatch's name. Deliberately plain: a small local model gets one guess.
LOOKUP_TOOL_NAME = "tool_schema"

# Ceiling on a catalog line's description. The catalog says a tool is THERE; a
# paragraph of it would spend the tokens deferring was meant to save.
_CATALOG_DESC_CAP = 100


def measure(schemas: Sequence[dict[str, Any]]) -> int:
    """Admission's estimate of the complete serialized tools array."""
    return estimate(list(schemas))


def catalog_line(schema: dict[str, Any]) -> str:
    """A deferred tool's whole presence in the context: ``name — first sentence``.

    One line, always: the catalog is read as a list, and a tool that wraps onto
    three lines reads as three tools."""
    fn = schema["function"]
    return f"{fn['name']} — {_first_sentence(fn.get('description') or '')}"


def _first_sentence(desc: str) -> str:
    head = desc.strip().split("\n", 1)[0]
    cut = head.find(". ")
    if cut != -1:
        head = head[: cut + 1]
    if len(head) > _CATALOG_DESC_CAP:
        head = head[: _CATALOG_DESC_CAP - 1].rstrip() + "…"
    return head


def lookup_schema(
    deferred: Sequence[dict[str, Any]],
    allowance: int | None = None,
    usage: Callable[[str], int] | None = None,
) -> dict[str, Any]:
    """The lookup tool's own schema, carrying the catalog of what it can fetch.

    The catalog rides in the description rather than the system prompt so the
    names travel with the tool that acts on them — and so ``measure`` counts
    them, which is the only way the budget can be honest about what the hatch
    costs.

    That honesty is also why the catalog is BOUNDED (Phase 14). One line per
    deferred tool means the hatch grows with every tool Dream is given: at 94
    tools it cost 2,375 tokens, half the floor at a 16K window. With an
    ``allowance`` the catalog lists only what fits, best-ranked first, and says
    how many it left out. Nothing becomes unreachable: the ``name`` enum still
    carries every deferred tool (a name is cheap; a description line is not),
    and ``search`` finds the unlisted ones by word.
    """
    names = [s["function"]["name"] for s in deferred]
    ranked = _rank(list(deferred), usage)
    lines, shown, spent = [], 0, 0
    for s in ranked:
        line = catalog_line(s)
        # These bytes live inside a JSON description string. Count escaped
        # quotes/control characters and a separator, using admission's ruler.
        cost = estimate(json.dumps(line + "\n", ensure_ascii=False)[1:-1])
        if allowance is not None and spent + cost > allowance:
            continue
        lines.append(line)
        spent += cost
        shown += 1
    # The listing follows the input order, so the same toolset always renders
    # the same catalog whatever the ranking decided to include.
    listed = {ln.split(" — ")[0] for ln in lines}
    lines = [catalog_line(s) for s in deferred if s["function"]["name"] in listed]
    hidden = len(names) - shown
    tail = "" if not hidden else f"\n{hidden} more — use `search`."
    return {
        "type": "function",
        "function": {
            "name": LOOKUP_TOOL_NAME,
            "description": (
                "Search tools; read schemas by name before calling.\n"
                "Deferred:\n"
                + "\n".join(lines) + tail
            ),
            "parameters": {
                "type": "object",
                # The shared description explains both arguments. Repeating it
                # per property (and emitting an empty required list) consumes
                # scarce 8K context without changing either parameter's meaning.
                "properties": {
                    # enum so a grammar-constrained server can only emit a real
                    # name; the catalog above says what each one is FOR. EVERY
                    # deferred name is here, listed or not.
                    "name": {
                        "type": "string",
                        "enum": names,
                    },
                    "search": {
                        "type": "string",
                    },
                },
            },
        },
    }


def search_catalog(deferred: Sequence[dict[str, Any]], query: str, limit: int = 8) -> list[str]:
    """Deferred tools whose name or description matches every word of ``query``.
    The way to a tool the bounded catalog above did not have room to name."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    if not words:
        return []
    out = []
    for s in deferred:
        fn = s["function"]
        hay = f"{fn['name']} {fn.get('description') or ''}".lower()
        if all(w in hay for w in words):
            out.append(catalog_line(s))
    return out[:limit]


def _rank(
    schemas: Sequence[dict[str, Any]], usage: Callable[[str], int] | None
) -> list[dict[str, Any]]:
    """Best-kept first: value per token, so a tool earns its width.

    ``(1 + uses) / cost`` — the +1 keeps a never-used tool ranked by cheapness
    instead of collapsing every unused tool into one indistinguishable tie, and
    with no usage callable at all the whole ranking degrades to cheapest-first.
    Cost then name break ties, so the same inputs always split the same way.
    """
    def key(s: dict[str, Any]) -> tuple[float, int, str]:
        name = s["function"]["name"]
        cost = max(measure([s]), 1)
        uses = usage(name) if usage is not None else 0
        return (-(1 + uses) / cost, cost, name)

    return sorted(schemas, key=key)


# The catalog's ceiling. At a 128K window the whole catalog fits anyway, so
# there is nothing to buy past this. There is deliberately NO floor: when the
# budget cannot afford description lines the catalog empties, and the names
# still travel in the lookup tool's `name` enum, which is where a
# grammar-constrained server reads them from. Losing a tool's one-line
# description costs discovery, not reach — and `search` buys the discovery
# back on demand.
_CATALOG_MAX_TOKENS = 2500


def _allowance(budget: int, sent: list[dict[str, Any]],
               deferred: list[dict[str, Any]]) -> int:
    """How many tokens the catalog's LINES may spend.

    What is left of the budget after the sent schemas AND the hatch's own fixed
    cost — its base description, its `name` enum, its `search` argument. Bound
    the lines alone and the fixed part pushes the total over by exactly the
    amount nobody counted."""
    fixed = measure(sent + [lookup_schema(deferred, 0)])
    return max(0, min(budget - fixed, _CATALOG_MAX_TOKENS))


def _cost(sent: list[dict[str, Any]], deferred: list[dict[str, Any]],
          allowance: int = _CATALOG_MAX_TOKENS,
          usage: Callable[[str], int] | None = None) -> int:
    """What the tools array really costs: the sent schemas plus the hatch, which
    only ships when there is something to look up. The hatch is priced at the
    allowance AND the ranking it will actually get — price it with a different
    ranking and the catalog that ships is a different size than the one the
    budget was checked against."""
    if not deferred:
        return measure(sent)
    return measure(sent + [lookup_schema(deferred, allowance, usage)])


def select(
    schemas: Sequence[dict[str, Any]],
    window: int,
    *,
    always: frozenset[str] = frozenset(),
    budget_frac: float = DEFAULT_BUDGET_FRAC,
    usage: Callable[[str], int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split ``schemas`` into what to send and what to catalog.

    Returns ``(sent, deferred_names)``. ``sent`` carries the lookup tool's schema
    whenever anything was deferred, and that schema's cost counts against the
    budget — an escape hatch that blows the budget is not an escape hatch. Both
    lists follow the input order; ranking decides membership only, so the tools
    array stays stable across rounds.

    The budget is a target, not a guarantee. Tools named in ``always`` are never
    deferred (the loop needs read/write/shell/memory callable without a round
    trip) and the hatch always ships. Those two can push the total over; nothing
    else can.
    """
    budget = int(window * budget_frac)
    if measure(schemas) <= budget:
        return list(schemas), []

    kept = {s["function"]["name"] for s in schemas if s["function"]["name"] in always}

    def split(names: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return ([s for s in schemas if s["function"]["name"] in names],
                [s for s in schemas if s["function"]["name"] not in names])

    # Greedy over the ranking, and it keeps going past the first tool that
    # doesn't fit: a lone expensive-but-well-used tool must not shut the door on
    # the cheap ones ranked behind it.
    for cand in _rank([s for s in schemas if s["function"]["name"] not in kept], usage):
        trial = kept | {cand["function"]["name"]}
        trial_sent, trial_deferred = split(trial)
        allowance = _allowance(budget, trial_sent, trial_deferred)
        if _cost(trial_sent, trial_deferred, allowance, usage) <= budget:
            kept = trial

    sent, deferred = split(kept)
    if deferred:
        # Spend only what remains after kept schemas and the fixed hatch cost.
        # At small windows the catalog lines can be empty; names remain in enum.
        sent.append(lookup_schema(deferred, _allowance(budget, sent, deferred), usage))
    return sent, [s["function"]["name"] for s in deferred]
