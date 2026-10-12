"""A question that ends without Enter still leaves its row, so the status footer cannot land beside it.

Seen 2026-10-09 in a DeepSleep pane: "allow? [y]es / [n]o / [a]lways this session: >> asking each change | file".
An interrupted or abandoned question echoed no newline; the footer was then redrawn from the cursor, on the question's row.
"""
import contextlib
import types

from dream.tui.app import App


def asker(answer):
    stub = types.SimpleNamespace(studio=None, renderer=types.SimpleNamespace(force_echo_on=lambda: None))
    stub._read_line = lambda prompt, stop: answer
    stub._sigint_cancels = lambda task: contextlib.nullcontext()
    return stub


async def test_an_unanswered_question_ends_its_row(capsys):
    assert await App._read_answer(asker(None), "  allow? ") is None
    assert capsys.readouterr().out == "\n"


async def test_an_answered_question_adds_nothing(capsys):
    assert await App._read_answer(asker("y\n"), "  allow? ") == "y\n"   # the terminal echoed that Enter itself
    assert capsys.readouterr().out == ""
