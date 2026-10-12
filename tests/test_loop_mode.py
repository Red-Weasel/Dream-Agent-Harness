"""Headless --loop must be able to execute, while the workspace boundary holds for file tools. (Since 2026-10-11, the
owner's decision, auto runs every shell command on the host without asking: the shell is no longer held to it.)

Measured in the harness-lab benchmark: with accept-edits (the interactive
default) Dream was blocked in 11 of 11 unattended runs — every shell command asks,
and a headless run has nobody to answer, so every one was refused. It still
scored 90.9%, by reasoning to answers it was forbidden to execute.
"""

from pathlib import Path

import pytest

from dream.core import policy


@pytest.fixture
def ws(tmp_path):
    w = (tmp_path / "ws").resolve()
    w.mkdir()
    return w


def test_auto_runs_shell_without_asking(ws):
    # No shell sandbox (the owner, 2026-10-11): auto runs every shell command without asking, no sandbox needed.
    for tool in ("Bash", "run_bash"):
        for command in ("python3 solve.py", "cat data.csv"):
            assert policy.decide(tool, {"command": command}, "auto", ws) == ("allow", "auto mode")


def test_accept_edits_asks_for_every_shell_command(ws):
    """Characterising the interactive default: correct with a human present,
    fatal to autonomy without one."""
    assert policy.decide("Bash", {"command": "cat data.csv"}, "accept-edits", ws)[0] == "ask"


def test_auto_runs_outside_the_workspace_too_and_the_asking_modes_name_it(ws):
    """No shell sandbox (the owner, 2026-10-11): auto runs outside-workspace shell commands too, headless --loop
    included. Ask and Accept edits ask, with the boundary as the reason; plan denies."""
    for command in ("echo x > ~/.bashrc", "cat /etc/passwd", "rm -rf /tmp/other"):
        assert policy.decide("Bash", {"command": command}, "auto", ws) == ("allow", "auto mode")
        for mode in ("ask", "accept-edits"):
            decision, reason = policy.decide("Bash", {"command": command}, mode, ws)
            assert decision == "ask" and reason.startswith("outside workspace"), (command, mode, reason)
        assert policy.decide("Bash", {"command": command}, "plan", ws)[0] == "deny"


def test_loop_defaults_to_auto_and_the_flag_overrides(monkeypatch, tmp_path):
    import dream.__main__ as entry

    seen = {}

    class FakeApp:
        def __init__(self, **kw):
            self.mode = "accept-edits"

        def run_autonomous(self, goal, iterations):
            seen["mode"] = self.mode
            async def noop():
                return None
            return noop()

    monkeypatch.setattr("dream.tui.app.App", FakeApp)

    monkeypatch.setattr("sys.argv", ["dream", "--loop", "goal", "--workspace", str(tmp_path)])
    entry.main()
    assert seen["mode"] == "auto", "headless loop did not default to auto"

    monkeypatch.setattr(
        "sys.argv",
        ["dream", "--loop", "goal", "--workspace", str(tmp_path), "--mode", "accept-edits"],
    )
    entry.main()
    assert seen["mode"] == "accept-edits", "--mode did not override the loop default"
