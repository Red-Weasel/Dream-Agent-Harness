"""The prompt box while a turn works (the owner, 2026-10-11): type during a turn and Enter sends it, as in Claude Code.

Rich's live footer owns the screen while a turn works, so prompt_toolkit cannot take keys then. This reads them itself
(no echo, no line mode; Ctrl-C stays a signal, so it still stops the turn) and keeps the text for the footer to draw."""
from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import re
import sys

_PASTE_START, _PASTE_END = "\x1b[200~", "\x1b[201~"
_NEWLINE_KEYS = ("\x1b[13;2u", "\x1b\r")  # Shift+Enter, Alt+Enter: a new line, as at the prompt between turns
_SEQUENCE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|O.|[^\[O])", re.S)  # any other key's escape sequence: ignored
LIMIT = 64000  # what one steering message may hold


def _terminal_write(code: str) -> None:
    # Straight to the terminal: while the footer is up, Rich holds sys.stdout and would print the code as text.
    with contextlib.suppress(Exception):
        sys.__stdout__.write(code)
        sys.__stdout__.flush()


class TurnInput:
    def __init__(self, on_submit):
        self.text = ""
        self.on_change = None  # the footer redraws at once instead of at its next tick
        self.keys = {}  # a control key -> its action (the optimizer's buttons: Ctrl+O, Ctrl+])
        self._on_submit = on_submit
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._rest = ""  # an escape sequence cut between two reads
        self._paste: str | None = None  # a bracketed paste in progress
        self._fd: int | None = None
        self._saved = None
        self._loop = None

    def feed(self, data: bytes) -> None:
        s = self._rest + self._decoder.decode(data)
        self._rest = ""
        i = 0
        while i < len(s):
            if self._paste is not None:
                end = s.find(_PASTE_END, i)
                if end < 0:
                    keep = next((k for k in range(min(len(_PASTE_END) - 1, len(s) - i), 0, -1)
                                 if _PASTE_END.startswith(s[-k:])), 0)
                    self._paste += s[i:len(s) - keep]
                    self._rest = s[len(s) - keep:]
                    break
                self._insert((self._paste + s[i:end]).replace("\r\n", "\n").replace("\r", "\n"))
                self._paste = None
                i = end + len(_PASTE_END)
                continue
            ch = s[i]
            if ch == "\x1b":
                if s.startswith(_PASTE_START, i):
                    self._paste = ""
                    i += len(_PASTE_START)
                    continue
                key = next((k for k in _NEWLINE_KEYS if s.startswith(k, i)), None)
                if key:
                    self._insert("\n")
                    i += len(key)
                    continue
                found = _SEQUENCE.match(s, i)
                if found is None:  # cut short: the rest comes with the next read
                    self._rest = s[i:]
                    break
                i = found.end()
                continue
            if ch in "\r\n":
                text, self.text = self.text, ""
                if text.strip():
                    self._on_submit(text)
            elif ch in "\x7f\x08":
                self.text = self.text[:-1]
            elif ch == "\x15":  # Ctrl+U empties the box
                self.text = ""
            elif ch in self.keys:
                self.keys[ch]()
            elif ch >= " ":
                self._insert(ch)
            i += 1
        if self.on_change is not None:
            self.on_change()

    def _insert(self, text: str) -> None:
        self.text = (self.text + text)[:LIMIT]

    def attach(self) -> None:
        """Take the keys while the footer is up. A no-op off a terminal or with no running event loop."""
        if self._fd is not None:
            return
        try:
            import termios

            fd = sys.stdin.fileno()
            if not os.isatty(fd):
                return
            loop = asyncio.get_running_loop()
            saved = termios.tcgetattr(fd)
            mode = termios.tcgetattr(fd)
            mode[3] &= ~(termios.ICANON | termios.ECHO | termios.ECHONL)
            mode[6][termios.VMIN], mode[6][termios.VTIME] = 1, 0
            termios.tcsetattr(fd, termios.TCSANOW, mode)
            loop.add_reader(fd, self._readable)
        except Exception:
            return
        self._fd, self._saved, self._loop = fd, saved, loop
        _terminal_write("\x1b[?2004h")  # a paste arrives whole, not as one Enter per line

    def detach(self) -> None:
        """Give the keys back (a question, or the turn ended). What was typed and not sent stays in self.text."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        self._rest, self._paste = "", None
        with contextlib.suppress(Exception):
            self._loop.remove_reader(fd)
        _terminal_write("\x1b[?2004l")
        with contextlib.suppress(Exception):
            import termios

            termios.tcsetattr(fd, termios.TCSANOW, self._saved)

    def _readable(self) -> None:
        try:
            data = os.read(self._fd, 4096)
        except OSError:
            data = b""
        if not data:  # stdin closed
            self.detach()
            return
        self.feed(data)
