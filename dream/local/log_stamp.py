"""Stamp each line the engine writes with local wall-clock time (DREAM-127, fix list #102).

`machx.serve` runs the engine with its stdout and stderr on a pipe to this process, whose own stdout is
machx.log (opened for append by Dream). Every line, and every "\\r"-ended progress segment, gets a
"[YYYY-MM-DD HH:MM:SS.mmm] " prefix as it arrives; the engine's text is not changed. Before this the log had
no clock at all, so a request in the runtime log and its [gen] line here could be matched only by token counts.

The stamper is in the engine's process group, so `machx.stop`'s SIGTERM to the group reaches it too: it ignores
SIGTERM, SIGINT and SIGHUP and leaves when the engine's output ends (EOF), so the engine's shutdown lines are
kept and the engine never writes to a closed pipe. Standard library only: it runs as `python -I <this file>`.
"""
from __future__ import annotations

import os
import re
import signal
import sys
import time

_MAX_LINE = 1 << 16      # a longer run with no newline is written out rather than held


def stamp(now: float | None = None) -> bytes:
    t = time.time() if now is None else now
    return (time.strftime("[%Y-%m-%d %H:%M:%S", time.localtime(t)) + f".{int(t % 1 * 1000):03d}] ").encode()


def _write(fd: int, data: bytes) -> None:
    try:
        while data:
            data = data[os.write(fd, data):]
    except OSError:
        # The log cannot be written (a full disk, a removed file): keep draining the pipe regardless, or the
        # engine would block on a full pipe or die writing to a closed one. The log was lost either way.
        pass


# A segment ends at "\n", "\r\n" or "\r", and is written as soon as it is read: the loaders' progress
# ("layer 12/43\r") ends no line for minutes, and machx.wait_ready takes the log's growth for a live load (gate round 1:
# held back as a partial line, a DeepSeek-V4 pool load read as stalled). A "\r\n" split across two reads gets a stamp
# before its "\n"; the engine's bytes are unchanged either way.
_SEGMENT = re.compile(rb"[^\r\n]*(?:\r\n|\n|\r)")


def _utf8_tail(data: bytes) -> int:
    """How many bytes at the end of `data` are an unfinished UTF-8 character (0 when it ends on a boundary)."""
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte < 0x80:
            return 0
        if byte >= 0xC0:                                  # the character's lead byte
            need = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
            return back if need > back else 0
    return 0


def main(src: int = 0, dst: int = 1) -> int:
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    pending = b""
    while True:
        chunk = os.read(src, 1 << 16)
        if not chunk:
            break
        # Split once, at the last boundary in the NEW bytes: scanning the whole unfinished line on every read was
        # quadratic (gate: a 270 KB line with no newline took over 60 s, and the engine's writes blocked meanwhile).
        cut = max(chunk.rfind(b"\n"), chunk.rfind(b"\r"))
        if cut >= 0:
            segments = _SEGMENT.findall(pending + chunk[:cut + 1])     # ends on a boundary: one linear pass
            pending = chunk[cut + 1:]
        else:
            pending += chunk
            if len(pending) <= _MAX_LINE:
                continue
            keep = _utf8_tail(pending)                     # never cut a character: the next stamp would split it
            segments, pending = [pending[:len(pending) - keep]], pending[len(pending) - keep:]
        prefix = stamp()
        _write(dst, b"".join(prefix + segment for segment in segments))
    if pending:
        _write(dst, stamp() + pending + b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
