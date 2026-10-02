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


def test_serve_leaves_the_default_profile_path_out_of_a_launch_with_lanes(popen, monkeypatch):
    """DREAM-154: the V4.1 engine refuses IE_DS41_PROFILE_OUT at --parallel > 1 (engine P4 B6b: one request's reset
    of the routing counts would wipe the other lanes'), so Dream's default goes on a one-lane launch only. An
    explicit value is still passed on (local/settings.engine_lanes refuses V4.1 lanes with it before this)."""
    monkeypatch.delenv("IE_DS41_PROFILE_OUT", raising=False)
    machx.serve(Path("/models/DeepSeek-V4.1-Flash"), gpus=2, ctx=75000, options={"parallel": 2})
    assert "IE_DS41_PROFILE_OUT" not in popen[-1]["env"]
    machx.serve(Path("/models/DeepSeek-V4.1-Flash"), gpus=2, ctx=75000, options={"parallel": 1})
    assert popen[-1]["env"]["IE_DS41_PROFILE_OUT"].endswith("ds41-dream-profile.txt")
    monkeypatch.setenv("IE_DS41_PROFILE_OUT", "/tmp/mine.txt")
    machx.serve(Path("/models/MiMo-V2.6-Flash-RL"), gpus=2, ctx=75000, options={"parallel": 2})
    assert popen[-1]["env"]["IE_DS41_PROFILE_OUT"] == "/tmp/mine.txt"


@pytest.mark.parametrize("servers, profile", [
    ([{"name": "v41", "model": "~/models/DeepSeek-V4.1-Flash", "parallel": 2}], False),
    ([{"name": "mimo", "model": "~/models/MiMo-V2.6-Flash-RL", "parallel": 1},
      {"name": "v41", "model": "~/models/DeepSeek-V4.1-Flash", "parallel": 3}], False),
    ([{"name": "v41", "model": "~/models/DeepSeek-V4.1-Flash", "parallel": 1}], True),
    ([{"name": "v41", "model": "~/models/DeepSeek-V4.1-Flash"}], True)])
def test_supervise_leaves_the_default_profile_path_out_of_a_layout_with_lanes(popen, monkeypatch, tmp_path, servers,
                                                                              profile):
    """DREAM-155 (DREAM-154 gate finding 2): the supervisor passes its environment to every child, and a V4.1 child
    with lanes refuses IE_DS41_PROFILE_OUT. A layout with a server at parallel > 1 gets no default; one lane keeps it."""
    import json
    monkeypatch.delenv("IE_DS41_PROFILE_OUT", raising=False)
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({"version": 1, "servers": servers}))
    machx.supervise(layout)
    assert ("IE_DS41_PROFILE_OUT" in popen[-1]["env"]) is profile
