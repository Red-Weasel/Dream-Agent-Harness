"""Tool traces stay one tidy line each, at the width of a DeepSleep pane.

The owner, 2026-10-09, about a 76-column pane: [feedback]. The trace printed the
absolute path, wrapped it back to column 0, cut the file's name off, dumped the first 100 characters of a file as a
read's "result", hid the reason of a failed call behind "1 ok, 1 failed", and boxed each permission question.
"""
from rich.console import Console

from dream.tui.render import Renderer

WIDTH = 76
WS = "/home/user/projects/demo"
REPORT = WS + "/reports/local_ai_frontier_feedback_loop_report.md"


def renderer(width=WIDTH):
    console = Console(record=True, force_terminal=True, width=width)
    r = Renderer(console)
    r.workspace_getter = lambda: WS
    return r, console


def lines(console):
    return console.export_text().split("\n")


def test_tool_call_is_one_line_with_the_path_inside_the_workspace():
    r, console = renderer()
    r.tool_use("write_file", {"path": REPORT, "content": "x" * 30000})
    r.tool_result("write_file", "wrote 24871 chars", False)
    r._flush_tool_run()
    out = [ln.rstrip() for ln in lines(console) if ln.strip()]
    assert out == ["● Write(reports/local_ai_frontier_feedback_loop_report.md)", "  ⎿  wrote 24871 chars"]


def test_a_path_too_long_for_the_pane_keeps_the_file_name():
    r, console = renderer(48)
    r.tool_use("read_file", {"path": REPORT})
    r.tool_result("read_file", "text\n", False)
    r._flush_tool_run()
    head = next(ln for ln in lines(console) if ln.startswith("●"))
    assert len(head.rstrip()) <= 48 and "…" in head
    assert head.rstrip().endswith("loop_report.md)")


def test_nothing_wraps_at_the_pane_width():
    r, console = renderer()
    r.tool_use("run_bash", {"command": "cd reports && wc -l local_ai_frontier_feedback_loop_report.md && grep -c '^## ' x.md"})
    r.tool_result("run_bash", "483 local_ai_frontier_feedback_loop_report.md\n9\n", False)
    r.tool_use("grep", {"pattern": "frontier " * 30})
    r.tool_result("grep", "one very long matching line " * 12, False)
    r._flush_tool_run()
    shown = [ln.rstrip() for ln in lines(console) if ln.strip()]
    assert len(shown) == 4, shown                      # a call line and a result line, twice
    assert all(len(ln) <= WIDTH for ln in shown)
    assert shown[0].startswith("● Bash(cd reports && wc -l") and shown[0].endswith("…)")
    assert shown[1] == "  ⎿  483 local_ai_frontier_feedback_loop_report.md (+1 line)"


def test_a_read_reports_its_size_not_the_file():
    r, console = renderer()
    r.tool_use("read_file", {"path": REPORT})
    r.tool_result("read_file", "--- *Report generated as part of the council*\n" + "line\n" * 482, False)
    r._flush_tool_run()
    out = console.export_text()
    assert "  ⎿  483 lines" in out and "Report generated" not in out


def test_a_blank_line_opens_every_tool_block():
    r, console = renderer()
    r.tool_use("list_dir", {"path": WS})
    r.tool_result("list_dir", "reports/", False)
    r.tool_use("read_file", {"path": REPORT})
    r.tool_result("read_file", "a\nb\n", False)
    r._flush_tool_run()
    out = lines(console)
    heads = [i for i, ln in enumerate(out) if ln.startswith("●")]
    assert len(heads) == 2 and out[heads[0]].rstrip() == "● List(.)"
    assert all(not out[i - 1].strip() for i in heads)       # the line above each call is empty
    assert all(out[i + 1].startswith("  ⎿") for i in heads)  # and its result sits right under it


def test_a_failed_call_in_a_run_shows_why():
    r, console = renderer()
    for content, failed in (("Error: content is 30112 characters; the limit is 25000", True), ("wrote 24871 chars", False)):
        r.tool_use("write_file", {"path": REPORT})
        r.tool_result("write_file", content, failed)
    r._flush_tool_run()
    out = console.export_text()
    assert "×2" in out and "1 ok · 1 failed" in out
    assert "the limit is 25000" in out


def test_a_long_error_wraps_under_itself_not_to_the_margin():
    r, console = renderer()
    r.tool_use("write_file", {"path": REPORT})
    r.tool_result("write_file", "Error: " + "the write was refused because the file is too large " * 4, True)
    r._flush_tool_run()
    body = [ln for ln in lines(console) if "refused" in ln]
    assert len(body) > 1
    assert body[0].startswith("  ⎿  ✗ ") and all(ln.startswith("     ") for ln in body[1:])
    assert all(len(ln.rstrip()) <= WIDTH for ln in body)


def test_permission_question_has_no_box():
    r, console = renderer()
    r.permission_request("delete_file", "delete_file(paths=['reports/old_draft.md'])\nApproval reason: deletes: reports/old_draft.md")
    out = console.export_text()
    assert not set("╭╮╰╯│─") & set(out)
    assert "delete_file(paths=['reports/old_draft.md'])" in out and "deletes: reports/old_draft.md" in out
    assert all(len(ln.rstrip()) <= WIDTH for ln in out.split("\n"))


def test_turn_statistics_fit_one_line():
    r, console = renderer(66)        # the whole line is 79 cells; the reading speed is what gives way
    r.turn_stats({"stats": {"duration_s": 117.0, "ctx_used": 39084, "n_ctx": 262144, "pp_tps": 811, "gen_tps": 171}})
    shown = [ln.rstrip() for ln in lines(console) if ln.strip()]
    assert len(shown) == 1 and len(shown[0]) <= 66
    assert "1m57s" in shown[0] and "ctx 39.1k/262.1k (15%)" in shown[0] and "gen 171 t/s" in shown[0]
    assert "pp 811" not in shown[0]


def test_notes_wrap_under_their_own_text():
    r, console = renderer(40)
    r.system("Shell authorization changed while waiting; retry in the current session and scope.")
    shown = [ln.rstrip() for ln in lines(console) if ln.strip()]
    assert len(shown) > 1 and shown[0].startswith("  · ")
    assert all(ln.startswith("    ") and len(ln) <= 40 for ln in shown[1:])
