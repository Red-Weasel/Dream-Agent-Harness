"""Ephemeral thoughts: reasoning shows only in the live region and vanishes when the
answer lands — it must never pile into the transcript (unless /thoughts on)."""

from __future__ import annotations

import io

from rich.console import Console

from dream.tui.render import Renderer


def _renderer() -> Renderer:
    # A non-terminal console captures anything printed to the transcript.
    return Renderer(Console(file=io.StringIO(), force_terminal=False))


def _transcript(r: Renderer) -> str:
    return r.console.file.getvalue()


def test_ephemeral_thoughts_never_reach_the_transcript():
    r = _renderer()
    r._live = object()  # pretend a live region is pinned
    r._status_cb = lambda: None

    r.thinking_delta("weighing the ")
    r.thinking_delta("options carefully")
    assert "weighing the options carefully" in r._thought_buf
    assert _transcript(r) == ""  # nothing streamed to the transcript


def test_answer_landing_clears_thoughts():
    r = _renderer()
    r._live = object()
    r._status_cb = lambda: None
    r.thinking_delta("thinking hard")
    r.assistant_delta("Here is the answer")
    assert r._thought_buf == ""  # thoughts vanish the moment the answer starts


def test_tool_use_clears_thoughts():
    r = _renderer()
    r._live = object()
    r._status_cb = lambda: None
    r.thinking_delta("I should look this up")
    r.tool_use("mcp__dream__recall", {"query": "x"})
    assert r._thought_buf == ""


def test_thought_buffer_stays_bounded():
    r = _renderer()
    r._live = object()
    r._status_cb = lambda: None
    for i in range(40):
        r.thinking_delta(f"line {i}\n")
    assert r._thought_buf.count("\n") <= 8  # only recent reasoning is kept


def test_persistent_mode_streams_reasoning_to_transcript():
    r = _renderer()
    r.set_thoughts_persistent(True)  # /thoughts on
    r._live = None  # off-terminal → prints straight through
    r.thinking_delta("thinking out loud")
    assert "thinking out loud" in _transcript(r)
