"""Headless --loop must be able to execute, while the workspace boundary holds.

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


async def test_auto_runs_confined_shell_without_asking(ws):
    from dream.core.execution import ExecutionScope, probe_sandbox
    assert policy.decide("Bash", {"command": "python3 solve.py"}, "auto", ws)[0] == "ask"
    assert policy.decide("Bash", {"command": "cat data.csv"}, "auto", ws)[0] == "allow"
    scope = ExecutionScope(ws)
    capability = await probe_sandbox(scope)
    if not capability.available:
        pytest.skip(capability.reason)
    assert policy.decide("run_bash", {"command": "python3 solve.py"}, "auto", ws,
                         execution_scope=scope, execution_capability=capability)[0] == "allow"


def test_accept_edits_asks_for_every_shell_command(ws):
    """Characterising the interactive default: correct with a human present,
    fatal to autonomy without one."""
    assert policy.decide("Bash", {"command": "cat data.csv"}, "accept-edits", ws)[0] == "ask"


def test_auto_still_asks_outside_the_workspace(ws):
    """The whole reason auto is safe enough to default to headlessly."""
    for command in ("echo x > ~/.bashrc", "cat /etc/passwd", "rm -rf /tmp/other"):
        decision, reason = policy.decide("Bash", {"command": command}, "auto", ws)
        assert decision != "allow", f"auto allowed an unconfined command: {command}"


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
