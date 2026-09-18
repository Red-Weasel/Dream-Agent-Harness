"""Exiting Dream must free the GPUs: machx.stop() takes down the server process
group, escalates if needed, and is safe to call when nothing is running."""

import subprocess
import time

import dream.config as config
from dream.local import machx


def test_stop_kills_the_process_group(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    proc = subprocess.Popen(["sleep", "300"], start_new_session=True)
    (tmp_path / "machx.pid").write_text(str(proc.pid), encoding="utf-8")

    assert machx.stop(timeout=3) is True
    # The process must actually be gone, not just signalled.
    assert proc.wait(timeout=5) != 0
    assert not (tmp_path / "machx.pid").exists()


def test_stop_is_idempotent_when_nothing_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    assert machx.stop() is False  # no pid file at all

    # A stale pid file (dead process) is cleaned up, not trusted.
    proc = subprocess.Popen(["true"])
    proc.wait()
    time.sleep(0.1)
    (tmp_path / "machx.pid").write_text(str(proc.pid), encoding="utf-8")
    assert machx.stop() is False
    assert not (tmp_path / "machx.pid").exists()
