"""The engine records a decode routing profile only when IE_DS41_PROFILE_OUT is set;
Dream never set it, so no Dream session has ever produced one (fix list #49)."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
import dream.config as config
from dream.local import machx


class _Proc:
    pid = 4242
    def poll(self):
        return None


@pytest.fixture
def popen(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(machx.subprocess, "Popen", lambda *a, **k: calls.append(k) or _Proc())
    monkeypatch.setattr(machx, "_pid_file", lambda: tmp_path / "machx.pid")
    return calls


def test_serve_sets_the_routing_profile_path_when_unset(popen, monkeypatch):
    monkeypatch.delenv("IE_DS41_PROFILE_OUT", raising=False)
    machx.serve(Path("/models/DeepSeek-V4.1-Flash"), gpus=2, ctx=75000)
    env = popen[-1]["env"]
    assert env["IE_DS41_PROFILE_OUT"].endswith("ds41-dream-profile.txt")


def test_serve_keeps_an_explicit_profile_path(popen, monkeypatch):
    monkeypatch.setenv("IE_DS41_PROFILE_OUT", "/tmp/mine.txt")
    machx.serve(Path("/models/DeepSeek-V4.1-Flash"), gpus=2, ctx=75000)
    assert popen[-1]["env"]["IE_DS41_PROFILE_OUT"] == "/tmp/mine.txt"
