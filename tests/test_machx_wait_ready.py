"""Waiting for a local engine to finish loading.

The old rule was a flat 600-second deadline, which encodes an assumption that
stopped being true the moment a model lived on an external drive: DeepSeek-V4
uploads a ~147 GB expert pool, which takes ~4 minutes read from NVMe and 20-25
minutes read from a USB HDD at ~120 MB/s. The deadline fired mid-load and the
launcher then STOPPED the half-loaded server, so twenty minutes of loading were
thrown away and the drive was blamed for being slow.

A wall-clock deadline cannot tell "slow" from "stuck". Engine progress can:
the loader writes a line per layer as the pool goes up. So the timeout measures
SILENCE, not elapsed time — a load that is still talking is still alive, however
long it takes, and one that has said nothing for minutes is the one to give up
on.
"""

from __future__ import annotations

import time
from pathlib import Path

import dream.local.machx as machx


class _Proc:
    """Stands in for the engine subprocess."""
    def __init__(self, alive: bool = True):
        self._alive = alive
        self.returncode = None

    def poll(self):
        return None if self._alive else 1

    def die(self):
        self._alive = False
        self.returncode = 1


def _log(tmp_path: Path, text: str = "") -> Path:
    p = tmp_path / "machx.log"
    p.write_text(text, encoding="utf-8")
    return p


def test_ready_as_soon_as_the_server_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(machx, "is_serving", lambda timeout=2.0: True)
    assert machx.wait_ready(_Proc(), log_path=_log(tmp_path)) is True


def test_a_process_that_dies_during_load_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(machx, "is_serving", lambda timeout=2.0: False)
    p = _Proc()
    p.die()
    assert machx.wait_ready(p, log_path=_log(tmp_path)) is False


def test_a_slow_but_ADVANCING_load_is_not_killed(monkeypatch, tmp_path):
    """The whole point. The log keeps growing, far past any stall window; the
    load must be allowed to finish."""
    monkeypatch.setattr(machx, "is_serving", lambda timeout=2.0: False)
    log = _log(tmp_path, "start\n")
    proc, layer = _Proc(), {"n": 0}

    def serving(timeout=2.0):
        layer["n"] += 1
        # a line per layer, arriving steadily, for far more polls than the
        # stall window would tolerate if it were a wall-clock deadline
        log.write_text("start\n" + "".join(
            f"[ds4-tp] expert pool: layer {i}/43\n" for i in range(layer["n"])
        ), encoding="utf-8")
        return layer["n"] >= 40      # ready only after 40 layers

    monkeypatch.setattr(machx, "is_serving", serving)
    assert machx.wait_ready(proc, stall_s=0.5, poll_s=0.001, log_path=log) is True
    assert layer["n"] >= 40


def test_a_SILENT_load_gives_up_after_the_stall_window(monkeypatch, tmp_path):
    monkeypatch.setattr(machx, "is_serving", lambda timeout=2.0: False)
    log = _log(tmp_path, "nothing more will be written\n")
    t0 = time.monotonic()
    assert machx.wait_ready(_Proc(), stall_s=0.4, poll_s=0.01, log_path=log) is False
    assert time.monotonic() - t0 < 5   # gave up promptly, not after 10 minutes


def test_the_absolute_ceiling_still_bounds_a_log_that_never_stops(monkeypatch, tmp_path):
    """A pathological engine that chatters forever without ever serving must
    still end. Progress-awareness must not mean 'wait indefinitely'."""
    log = _log(tmp_path)
    n = {"i": 0}

    def serving(timeout=2.0):
        n["i"] += 1
        log.write_text("x" * n["i"], encoding="utf-8")   # always advancing
        return False

    monkeypatch.setattr(machx, "is_serving", serving)
    assert machx.wait_ready(_Proc(), stall_s=10, max_s=0.5, poll_s=0.01,
                            log_path=log) is False


def test_a_missing_log_falls_back_to_the_stall_window(monkeypatch, tmp_path):
    """No log to read (a relocated DREAM_ROOT, a permissions problem) must not
    mean 'wait forever' or 'crash' — just no progress signal."""
    monkeypatch.setattr(machx, "is_serving", lambda timeout=2.0: False)
    assert machx.wait_ready(_Proc(), stall_s=0.3, poll_s=0.01,
                            log_path=tmp_path / "does-not-exist.log") is False


def test_the_default_stall_window_is_generous_enough_for_a_usb_load():
    """A USB HDD emits a layer line every few seconds; the window must clear
    that comfortably, and the ceiling must clear a 25-minute load."""
    import inspect
    sig = inspect.signature(machx.wait_ready)
    assert sig.parameters["stall_s"].default >= 180
    assert sig.parameters["max_s"].default >= 5400
