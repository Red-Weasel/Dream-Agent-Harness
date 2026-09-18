"""The Claude-Code-style tasks panel: ingest TodoWrite todos, render the checklist
(done struck, in-progress bold, pending muted), the collapsed strip, and /plan tree."""

from rich.console import Console, Group
from rich.text import Text

from dream.tui.plan import PlanTracker


def _todos(*triples):
    """(content, status, activeForm?) → the TodoWrite todos list shape."""
    out = []
    for t in triples:
        content, status = t[0], t[1]
        item = {"content": content, "status": status}
        if len(t) > 2:
            item["activeForm"] = t[2]
        out.append(item)
    return {"todos": out}


def _plain(renderable) -> str:
    c = Console(record=True, force_terminal=True, width=100)
    c.print(renderable)
    return c.export_text()


def _sample() -> PlanTracker:
    p = PlanTracker()
    p.ingest(
        "TodoWrite",
        _todos(
            ("Read the spec", "completed", "Reading the spec"),
            ("Wire the backend", "completed", "Wiring the backend"),
            ("Render the panel", "in_progress", "Rendering the panel"),
            ("Add the strip", "pending", "Adding the strip"),
            ("Write the tests", "pending", "Writing the tests"),
        ),
    )
    return p


def test_ingest_todowrite_populates_plan():
    p = _sample()
    assert p.has_tasks() is True
    assert p.active() == "Render the panel"


def test_empty_tracker_has_no_tasks():
    p = PlanTracker()
    assert p.has_tasks() is False
    assert p.active() == ""
    # Empty renderables stay quiet, never crash.
    assert _plain(p.strip()).strip() == ""
    assert isinstance(p.panel(80), Group)
    assert p.panel(80).renderables == []


def test_panel_shows_all_glyphs():
    out = _plain(_sample().panel(80))
    assert "✔" in out  # done
    assert "■" in out  # in-progress
    assert "□" in out  # pending
    # Every task's content is on the checklist.
    for content in ("Read the spec", "Render the panel", "Write the tests"):
        assert content in out


def test_panel_done_items_are_struck_through():
    grp = _sample().panel(80)
    lines = [r for r in grp.renderables if isinstance(r, Text)]
    done = next(t for t in lines if "Read the spec" in t.plain)
    assert any("strike" in str(s.style) for s in done.spans)
    # The live task is bold violet, not struck.
    doing = next(t for t in lines if "Render the panel" in t.plain)
    assert any("#a78bfa" in str(s.style) for s in doing.spans)
    assert not any("strike" in str(s.style) for s in doing.spans)


def test_panel_activity_line_prefers_active_form():
    out = _plain(_sample().panel(80))
    # The activity line uses the present-continuous activeForm of the live task.
    assert "Rendering the panel" in out


def test_strip_shows_count_and_active():
    out = _plain(_sample().strip())
    assert "plan" in out
    assert "2/5" in out  # 2 of 5 completed
    assert "Render the panel" in out


def test_strip_active_survives_long_content():
    p = PlanTracker()
    long = "Do a really quite absurdly long amount of careful work here now please"
    p.ingest("TodoWrite", _todos((long, "in_progress"), ("next", "pending")))
    out = _plain(p.strip())
    assert "0/2" in out
    assert "…" in out  # long active content is ellipsized


def test_tree_lists_every_task():
    out = _plain(_sample().tree())
    assert "plan" in out and "2/5" in out
    for content in ("Read the spec", "Render the panel", "Add the strip"):
        assert content in out


def test_clear_drops_the_plan():
    p = _sample()
    assert p.has_tasks()
    p.clear()
    assert p.has_tasks() is False
    assert p.active() == ""


def test_reingest_replaces_plan():
    p = _sample()
    p.ingest("TodoWrite", _todos(("Only task", "in_progress")))
    assert p.active() == "Only task"
    assert _plain(p.strip()).count("/") == 1
    assert "0/1" in _plain(p.strip())


def test_tolerates_plan_tool_shape():
    """The future local backend surfaces the same fields under a plan tool."""
    p = PlanTracker()
    p.ingest("plan", {"plan": [{"content": "local task", "status": "in_progress"}]})
    assert p.has_tasks() is True
    assert p.active() == "local task"


def test_unknown_status_falls_back_to_pending():
    p = PlanTracker()
    p.ingest("TodoWrite", _todos(("mystery", "banana")))
    out = _plain(p.panel(80))
    assert "□" in out  # rendered as pending, not crashed


def test_stray_tool_call_does_not_wipe_plan():
    p = _sample()
    p.ingest("read_file", {"path": "somewhere.py"})
    assert p.has_tasks() is True
    assert p.active() == "Render the panel"
