"""Repeated identical tool calls collapse into one line with ×N — the fix for the
looping-model wall of duplicate tool lines."""

from rich.console import Console

from dream.tui.render import Renderer


def _render(events) -> str:
    console = Console(record=True, force_terminal=True, width=100)
    r = Renderer(console)
    for kind, *rest in events:
        if kind == "use":
            r.tool_use(rest[0], rest[1])
        elif kind == "result":
            r.tool_result(rest[0], rest[1], rest[2])
        elif kind == "text":
            r.assistant_delta(rest[0])
        elif kind == "flush":
            r._flush_tool_run()
    r._flush_tool_run()
    return console.export_text()


def test_repeated_calls_collapse_to_one_line():
    events = []
    for _ in range(23):
        events.append(("use", "write_file", {"path": "/x/__init__.py"}))
        events.append(("result", "write_file", "Wrote 221 chars", False))
    out = _render(events)
    # One folded line, not 23.
    assert out.count("write_file(path=/x/__init__.py)") == 1
    assert "×23" in out
    assert "23×" in out  # the success tally line
    assert out.count("Wrote 221 chars") <= 1


def test_distinct_calls_each_shown():
    out = _render([
        ("use", "read_file", {"path": "a.py"}),
        ("result", "read_file", "contents A", False),
        ("use", "read_file", {"path": "b.py"}),
        ("result", "read_file", "contents B", False),
    ])
    assert "read_file(path=a.py)" in out and "read_file(path=b.py)" in out
    assert "×" not in out  # no counter when nothing repeats


def test_errors_surface_in_collapsed_run():
    events = [("use", "write_file", {"path": "x"}), ("result", "write_file", "boom", True)]
    out = _render(events)
    assert "boom" in out and "✗" in out


def test_text_flushes_the_run():
    out = _render([
        ("use", "search", {"query": "q"}),
        ("result", "search", "hits", False),
        ("text", "Here is what I found."),
    ])
    assert "search(query=q)" in out and "Here is what I found." in out
