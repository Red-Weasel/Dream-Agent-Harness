"""The visible memory map: touch lights, age fades, the strip carries the right
markers, and the /mind tree groups by kind and marks lit nodes."""

import tempfile
from pathlib import Path

from rich.console import Console

from dream.memory.store import MemoryStore
from dream.tui.mindmap import MindMap


def _text(renderable) -> str:
    console = Console(record=True, force_terminal=True, width=100)
    console.print(renderable)
    return console.export_text()


class _FakeStore:
    """The minimal surface MindMap.tree() leans on."""

    def __init__(self, mems, links=None):
        self._mems = mems
        self._links = links or {}

    def all_memories(self):
        return list(self._mems)

    def linked_slugs(self, slug):
        return list(self._links.get(slug, []))


# --- strip: empty / touch lights / markers ----------------------------------


def test_empty_strip_when_nothing_touched():
    assert MindMap().strip().plain == ""


def test_touch_lights_with_filled_marker():
    mm = MindMap()
    mm.touch("user-prefs", "semantic", "recall")
    s = mm.strip().plain
    assert s.startswith("mind")
    assert "user-prefs" in s
    assert "◆" in s  # lit = filled diamond
    assert "◇" not in s


def test_seed_is_dim_not_lit_then_touch_lights():
    mm = MindMap()
    mm.seed([{"slug": "search-well", "kind": "procedural"}])
    s = mm.strip().plain
    assert "search-well" in s
    assert "◇" in s and "◆" not in s  # seeded = faded, not lit
    mm.touch("search-well", "procedural", "recall")
    assert "◆" in mm.strip().plain


# --- age: fades, then drops --------------------------------------------------


def test_age_fades_lit_to_faded():
    mm = MindMap(keep=2)
    mm.touch("a", "semantic", "recall")
    assert "◆" in mm.strip().plain
    mm.age_turn()
    s = mm.strip().plain
    assert "◇" in s and "◆" not in s  # still on the map, now faded
    assert "a" in s


def test_age_drops_node_past_window():
    mm = MindMap(keep=2)
    mm.touch("a", "semantic", "recall")
    for _ in range(3):  # keep=2 -> age 3 is past the window
        mm.age_turn()
    assert mm.strip().plain == ""


def test_lit_leads_faded_in_strip():
    mm = MindMap()
    mm.seed([{"slug": "old-note", "kind": "semantic"}])
    mm.touch("fresh-note", "episodic", "remember")
    s = mm.strip().plain
    assert s.index("fresh-note") < s.index("old-note")


def test_forget_strikes_but_still_shows():
    mm = MindMap()
    mm.touch("gone", "semantic", "forget")
    s = mm.strip().plain
    assert "gone" in s and "◆" in s


# --- tree: groups by kind, marks lit, shows links ---------------------------


def test_tree_groups_by_kind_and_marks_lit_fake_store():
    store = _FakeStore(
        [
            {"slug": "user-prefs", "kind": "semantic", "title": "The user likes concision"},
            {"slug": "search-well", "kind": "procedural", "title": "Search well"},
        ],
        links={"user-prefs": ["dream-harness"]},
    )
    mm = MindMap()
    mm.touch("user-prefs", "semantic", "recall")
    out = _text(mm.tree(store))
    assert "mind" in out
    assert "semantic (1)" in out and "procedural (1)" in out
    assert "The user likes concision" in out and "Search well" in out
    assert "◆" in out  # the lit leaf is marked
    assert "dream-harness" in out  # [[link]] shown as a cross-reference


def test_tree_over_real_store():
    store = MemoryStore(Path(tempfile.mkdtemp()) / "t.db")
    store.upsert_memory(
        "semantic",
        "The user is rebuilding Dream",
        "They are rebuilding this harness. See [[dream harness]].",
    )
    store.upsert_memory("procedural", "Search well", "web_search then browse.")
    mm = MindMap()
    mm.touch("the-user-is-rebuilding-dream", "semantic", "recall")
    out = _text(mm.tree(store))
    assert "semantic (1)" in out and "procedural (1)" in out
    assert "The user is rebuilding Dream" in out
    assert "dream-harness" in out  # link target from [[dream harness]]
    assert "◆" in out
