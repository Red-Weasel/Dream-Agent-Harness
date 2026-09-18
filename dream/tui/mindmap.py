"""The visible memory map — the ``mind`` strip that lights when memory is touched,
and the full ``/mind`` tree.

Self-contained ``rich`` renderables (import ``rich`` directly, not ``render.py``) so
the map is testable in isolation; the orchestrator wires ``strip()`` into the footer
and ``tree()`` into the ``/mind`` command. A node lights the turn it is recalled,
remembered, or forgotten, then fades over the next few turns until it drops off.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from rich.tree import Tree

# Brand hues, one per memory kind — the atom's violet→cyan orbit.
_VIOLET = "#a78bfa"  # semantic  (facts)
_BLUE = "#60a5fa"    # procedural (playbooks)
_CYAN = "#22d3ee"    # episodic   (events)
_KIND_HUE = {"semantic": _VIOLET, "procedural": _BLUE, "episodic": _CYAN}

# How many turns a touched node lingers (dimming each turn) before it drops.
_KEEP_TURNS = 6
# Cap on nodes shown in the one-line strip so it never wraps.
_STRIP_MAX = 12


def _dim_hex(hex_color: str, f: float = 0.55) -> str:
    """Scale a hex color toward black — an explicit 'dim' that renders the same
    everywhere (terminals disagree about the dim attribute)."""
    r = round(int(hex_color[1:3], 16) * f)
    g = round(int(hex_color[3:5], 16) * f)
    b = round(int(hex_color[5:7], 16) * f)
    return f"#{r:02x}{g:02x}{b:02x}"


def _shade(kind: str, age: int) -> str:
    """The kind's hue at brightness for ``age``: full at age 0 (lit this turn),
    fading toward a dim floor as it ages out of the window."""
    hue = _KIND_HUE.get(kind, _VIOLET)
    if age <= 0:
        return hue
    return _dim_hex(hue, max(0.6 - 0.08 * (age - 1), 0.3))


@dataclass
class _Node:
    slug: str
    kind: str
    age: int = 0     # 0 = lit this turn; +1 each age_turn; dropped past _KEEP_TURNS
    op: str = ""      # last op that touched it: recall | remember | forget
    seeded: bool = False


class MindMap:
    """Tracks which memories are lit (recalled/remembered/forgotten this turn) and
    fades them over subsequent turns."""

    def __init__(self, keep: int = _KEEP_TURNS) -> None:
        self._keep = keep
        # Insertion-ordered — dict preserves order, so ties in the strip sort keep
        # the order things were first seen.
        self._nodes: dict[str, _Node] = {}

    # --- mutation ------------------------------------------------------------

    def seed(self, memories: list[dict]) -> None:
        """Wake-up top-of-mind: seed the map dim (not lit). Existing (possibly lit)
        nodes are left untouched."""
        for m in memories:
            slug = m.get("slug")
            if not slug or slug in self._nodes:
                continue
            self._nodes[slug] = _Node(
                slug, m.get("kind") or "semantic", age=1, seeded=True
            )

    def touch(self, slug: str, kind: str, op: str) -> None:
        """Light a memory this turn. ``op`` in {"recall","remember","forget"}."""
        node = self._nodes.get(slug)
        if node is None:
            self._nodes[slug] = _Node(slug, kind or "semantic", age=0, op=op)
        else:
            node.age = 0
            node.op = op
            node.seeded = False
            if kind:
                node.kind = kind

    def age_turn(self) -> None:
        """Decay every node one turn; drop the ones that have faded past the window."""
        stale: list[str] = []
        for slug, node in self._nodes.items():
            node.age += 1
            if node.age > self._keep:
                stale.append(slug)
        for slug in stale:
            del self._nodes[slug]

    # --- render --------------------------------------------------------------

    def strip(self) -> Text:
        """One line: ``mind ◆ slug ◆ slug ◇ slug`` — lit nodes bright in their kind
        hue, faded ones dim. Empty when nothing is on the map."""
        if not self._nodes:
            return Text()
        # Freshest first (age ascending); sorted is stable so equal ages keep
        # insertion order.
        nodes = sorted(self._nodes.values(), key=lambda n: n.age)
        t = Text("mind", style=f"bold {_dim_hex(_VIOLET, 0.7)}")
        for n in nodes[:_STRIP_MAX]:
            lit = n.age == 0
            style = _shade(n.kind, n.age)
            if n.op == "forget":
                style = f"{style} strike"
            t.append(" ")
            t.append("◆" if lit else "◇", style=style)
            t.append(" ")
            t.append(n.slug, style=style)
        return t

    def tree(self, store) -> Tree:
        """The full ``/mind`` tree: kinds as branches, each memory a leaf, ``[[links]]``
        as cross-references, lit nodes marked. ``store`` need only expose
        ``all_memories()`` and ``linked_slugs(slug)``."""
        root = Tree(Text("mind", style=f"bold {_VIOLET}"))
        by_kind: dict[str, list[dict]] = {}
        for m in store.all_memories():
            by_kind.setdefault(m.get("kind") or "semantic", []).append(m)
        # Known kinds first, in orbit order; any stray kinds trail.
        order = ["semantic", "procedural", "episodic"]
        order += [k for k in by_kind if k not in order]
        for kind in order:
            group = by_kind.get(kind)
            if not group:
                continue
            hue = _KIND_HUE.get(kind, _VIOLET)
            branch = root.add(Text(f"{kind} ({len(group)})", style=f"bold {hue}"))
            for m in group:
                slug = m.get("slug", "")
                node = self._nodes.get(slug)
                lit = node is not None and node.age == 0
                if lit:
                    marker, mstyle, tstyle = "◆", hue, f"bold {hue}"
                elif node is not None:
                    faded = _shade(kind, node.age)
                    marker, mstyle, tstyle = "◇", faded, faded
                else:
                    marker, mstyle, tstyle = "·", "dim", "default"
                label = Text()
                label.append(f"{marker} ", style=mstyle)
                label.append(m.get("title") or slug, style=tstyle)
                leaf = branch.add(label)
                try:
                    links = store.linked_slugs(slug)
                except Exception:
                    links = []
                for lslug in links:
                    leaf.add(Text(f"↳ [[{lslug}]]", style="dim italic"))
        return root
