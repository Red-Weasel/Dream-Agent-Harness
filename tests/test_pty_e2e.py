"""End-to-end: the real TUI in a real pseudo-terminal, a scripted model
streaming at a known rate, and the monitor pane visibly LIVE — the strongest
verification of the whole telemetry stack short of loading a model."""

from __future__ import annotations

import fcntl
import json
import os
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

import pty

DRIVER = Path(__file__).with_name("pty_driver.py")


def _drain(master: int, buf: bytearray, timeout: float, state: dict) -> None:
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([master], [], [], 0.25)
        if not r:
            continue
        try:
            chunk = os.read(master, 65536)
        except OSError:
            return  # child side closed
        if not chunk:
            return
        buf.extend(chunk)
        # Behave like a real terminal: answer every cursor-position request —
        # prompt_toolkit won't render its bottom toolbar without a CPR reply.
        n = buf.count(b"\x1b[6n", state["cpr_seen_at"])
        state["cpr_seen_at"] = len(buf)
        for _ in range(n):
            try:
                os.write(master, b"\x1b[25;1R")
            except OSError:
                return


def _wait_for(master: int, buf: bytearray, needle: bytes, timeout: float,
              state: dict) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if needle in buf:
            return True
        _drain(master, buf, 0.3, state)
    return needle in buf


def test_monitor_pane_live_in_real_terminal(tmp_path):
    master, slave = pty.openpty()
    # A wide terminal, so the two-column layout engages (>= 100 cols).
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 130, 0, 0))
    env = {**os.environ, "DREAM_ROOT": str(tmp_path), "DREAM_MONITOR": "1",
           "TERM": "xterm-256color", "DREAM_GUI": "0", "DREAM_GUI_OPEN": "0",
           "DREAM_SEMANTIC_MEMORY": "0", "DREAM_RERANK": "0",
           "DREAM_DB": str(tmp_path / "data" / "dream.db"),
           "DREAM_LIBRARY_DB": str(tmp_path / "data" / "library" / "library.db"),
           "DREAM_EXTENSION_SETTINGS": str(tmp_path / "extensions.json"),
           "DREAM_MCP_CONFIG": str(tmp_path / "mcp.json"),
           "DREAM_PLUGINS_DIR": str(tmp_path / "plugins")}
    env.pop("DREAM_CONSOLIDATE", None)  # exit must not try to dream
    proc = subprocess.Popen(
        [sys.executable, str(DRIVER)],
        stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True,
    )
    os.close(slave)
    buf = bytearray()
    state = {"cpr_seen_at": 0}
    try:
        # Boot: logo, welcome panel, then the prompt.
        assert _wait_for(master, buf, b"Dream", 45, state), "TUI never booted"
        assert _wait_for(master, buf, b"you", 15, state), "prompt never appeared"
        os.write(master, b"hello\n")
        # While the scripted model streams at ~30 tok/s the pane must go live:
        # the sliding-window rate appears mid-turn, exact pp stats at round end.
        assert _wait_for(master, buf, b"t/s live", 20, state), "live tokens/s never shown"
        assert _wait_for(master, buf, b"gen 30.0 t/s", 15, state), "exact decode rate missing"
        assert b"\xe2\x9a\xa1 monitor" in buf  # "⚡ monitor" pane header
        assert b"ttft 0.8" in buf  # the scripted 0.8s first token, measured live
        # Prompt-processing time + rate land with the round's stats event and
        # paint in the idle toolbar (which needs the CPR replies above).
        assert _wait_for(master, buf, b"pp 2.00s @2000 t/s", 12, state), \
            "prompt processing time missing"
        assert _wait_for(master, buf, b"\xce\xa3 1t", 8, state), \
            "session rollup missing"  # "Σ 1t"
        # Exit: Ctrl-D, then decline the memory-commit question if it appears.
        os.write(master, b"\x04")
        _drain(master, buf, 2.0, state)
        if b"commit this session" in buf:
            os.write(master, b"n\n")
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        os.close(master)

    text = buf.decode("utf-8", "replace")
    # The turn actually streamed and finished (transcript reached the answer).
    assert "word word" in text
    # Fixture GPU rows must render on every host alongside real inference metrics.
    assert "Fixture GPU" in text
    assert "42%" in text
    assert "fan 777 rpm" in text
    report = json.loads((tmp_path / "telemetry-fixture.json").read_text())
    assert report["hardware_attempts"] == 0
    assert report["starts"] == report["stops"] == 1
    assert report["snapshots"] > 0
    assert report["running"] is False and report["app_released"] is True
