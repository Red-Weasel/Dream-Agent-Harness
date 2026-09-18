"""Getting a session out of Dream.

Scroll-selecting a long session in the terminal does not work — prompt_toolkit
repaints on scroll and the selection dies — and the harness already writes every
turn to data/sessions/<id>.jsonl as it happens. So an export is a format change
over a file that exists, which also means it works on a LIVE session without
touching it.
"""

from __future__ import annotations

import json

from dream.tui.export import export_session, read_log, to_markdown

EVENTS = [
    {"ts": "2026-08-15T10:00:00", "role": "user", "content": "build me a site"},
    {"ts": "2026-08-15T10:00:05", "role": "assistant", "content": "On it."},
    {"ts": "2026-08-15T10:00:06", "role": "tool_use", "tool": "write_file",
     "content": '{"path": "index.html"}'},
    {"ts": "2026-08-15T10:00:07", "role": "tool_result", "tool": "write_file",
     "content": "written"},
]


def _log(tmp_path, events=EVENTS, trailing=""):
    p = tmp_path / "20260815-100000-abcd.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n" + trailing,
                 encoding="utf-8")
    return p


def test_a_half_written_last_line_does_not_lose_the_session(tmp_path):
    """The session being exported is usually the one still running, so the file
    can end mid-line. Everything before it must survive."""
    p = _log(tmp_path, trailing='{"role": "assistant", "content": "cut off')
    assert len(list(read_log(p))) == len(EVENTS)


def test_markdown_carries_both_sides_of_the_conversation(tmp_path):
    md = to_markdown(list(read_log(_log(tmp_path))))
    assert "build me a site" in md and "On it." in md
    assert "## You" in md and "## Dream" in md


def test_tool_noise_is_collapsed_not_dropped(tmp_path):
    md = to_markdown(list(read_log(_log(tmp_path))))
    assert "<details>" in md and "write_file" in md and "written" in md


def test_no_tools_gives_a_clean_readable_transcript(tmp_path):
    md = to_markdown(list(read_log(_log(tmp_path))), tools=False)
    assert "build me a site" in md and "On it." in md
    assert "write_file" not in md and "<details>" not in md


def test_a_huge_tool_result_is_clipped_unless_full(tmp_path):
    big = [dict(EVENTS[3], content="x" * 50_000)]
    clipped = to_markdown(big)
    assert "more chars]" in clipped and len(clipped) < 10_000
    assert len(to_markdown(big, full=True)) > 49_000


def test_export_writes_markdown_and_reports_the_count(tmp_path):
    out, n = export_session(_log(tmp_path), tmp_path / "out.md")
    assert n == len(EVENTS) and out.read_text(encoding="utf-8").startswith("# Dream session")


def test_export_json_round_trips(tmp_path):
    out, n = export_session(_log(tmp_path), tmp_path / "out.json", fmt="json")
    assert json.loads(out.read_text(encoding="utf-8")) == EVENTS


def test_a_directory_destination_gets_a_sensible_filename(tmp_path):
    d = tmp_path / "somewhere"; d.mkdir()
    out, _ = export_session(_log(tmp_path), d)
    assert out.parent == d and out.name == "20260815-100000-abcd.md"


def test_the_command_is_wired_and_documented():
    from pathlib import Path
    app = (Path(__file__).parent.parent / "dream" / "tui" / "app.py").read_text()
    assert 'cmd == "export"' in app and "/export" in app
