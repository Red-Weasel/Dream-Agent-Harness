"""The TUI garbled the terminal when a key was pressed mid-turn: during a turn a
rich Live footer repaints while stdin echo is still on, so echoed keystrokes land
in the region rich is redrawing. The renderer now suppresses stdin echo while the
Live footer is up and restores it (flushing buffered input) when the turn ends.

These drive the echo helpers against a REAL pty so we're testing actual termios
behaviour, not a mock — and confirm they're a safe no-op off a TTY (the CI case).
"""

from __future__ import annotations

import os
import pty
import sys
import termios
from types import SimpleNamespace

from dream.tui.render import Renderer


def _use_stdin_fd(monkeypatch, fd: int) -> None:
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(fileno=lambda: fd))


def test_echo_off_then_restore_round_trips_the_tty_echo_bit(monkeypatch):
    master, slave = pty.openpty()
    try:
        _use_stdin_fd(monkeypatch, slave)
        r = Renderer()

        before = termios.tcgetattr(slave)
        assert before[3] & termios.ECHO, "pty should start with echo on"

        r._echo_off()
        during = termios.tcgetattr(slave)
        assert not (during[3] & termios.ECHO), "echo must be suppressed mid-turn"
        assert r._saved_termios is not None

        r._echo_restore(flush=True)
        after = termios.tcgetattr(slave)
        assert after[3] & termios.ECHO, "echo must be restored after the turn"
        assert after == before  # settings fully round-tripped
        assert r._saved_termios is None
    finally:
        os.close(master)
        os.close(slave)


def test_echo_off_is_idempotent_across_pause_resume(monkeypatch):
    # begin → pause (restore, for a permission prompt) → resume (suppress again) → end
    master, slave = pty.openpty()
    try:
        _use_stdin_fd(monkeypatch, slave)
        r = Renderer()
        original = termios.tcgetattr(slave)

        r._echo_off()                      # live_begin
        r._echo_restore()                  # live_pause → permission input() can echo
        assert termios.tcgetattr(slave)[3] & termios.ECHO
        r._echo_off()                      # live_resume
        assert not (termios.tcgetattr(slave)[3] & termios.ECHO)
        r._echo_restore(flush=True)        # live_end
        assert termios.tcgetattr(slave) == original
    finally:
        os.close(master)
        os.close(slave)


def test_echo_helpers_are_noop_without_a_tty(monkeypatch):
    rd, wr = os.pipe()  # a pipe is not a terminal
    try:
        _use_stdin_fd(monkeypatch, rd)
        r = Renderer()
        r._echo_off()
        assert r._saved_termios is None      # nothing saved, no exception
        r._echo_restore(flush=True)          # no-op, no exception
    finally:
        os.close(rd)
        os.close(wr)


def test_force_echo_on_recovers_a_suppressed_terminal(monkeypatch):
    # A question's answer field must be visible even if the Live footer left echo
    # off (or the restore was skipped). force_echo_on guarantees echo + canonical.
    master, slave = pty.openpty()
    try:
        _use_stdin_fd(monkeypatch, slave)
        r = Renderer()
        r._echo_off()                                   # simulate mid-turn suppression
        assert not (termios.tcgetattr(slave)[3] & termios.ECHO)
        r._echo_off()  # ensure the "already suppressed" guard doesn't strand it
        r.force_echo_on()
        lflags = termios.tcgetattr(slave)[3]
        assert lflags & termios.ECHO      # visible
        assert lflags & termios.ICANON    # line-mode (typeable)
        assert r._saved_termios is None   # bookkeeping cleared so a turn re-arms
    finally:
        os.close(master); os.close(slave)


def test_force_echo_on_is_noop_off_a_tty(monkeypatch):
    rd, wr = os.pipe()
    try:
        _use_stdin_fd(monkeypatch, rd)
        Renderer().force_echo_on()  # must not raise on a non-tty
    finally:
        os.close(rd); os.close(wr)
