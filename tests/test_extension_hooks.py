"""Trusted hook middleware: bounded execution and explicit failure semantics."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from dream import extensions, hooks


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAM_EXTENSION_SETTINGS", str(tmp_path / "data" / "extensions.json"))
    monkeypatch.setattr(extensions, "_USAGE_WARNINGS", [])


def register(tmp_path, code, *, name="check", mode="observe", event="before_tool", timeout=.5):
    script = tmp_path / f"{name}.py"
    script.write_text(code)
    spec = {"name": name, "command": [sys.executable, str(script)], "cwd": str(tmp_path),
            "events": [event], "mode": mode, "timeout_s": timeout}
    hooks.register_hook(spec)
    return spec, script


async def test_hooks_are_disabled_until_explicitly_trusted_and_enabled(tmp_path):
    spec, script = register(tmp_path, "from pathlib import Path\nPath('marker').write_text('ran')")
    assert (await hooks.run_hooks("before_tool")).outcomes == []
    assert not (tmp_path / "marker").exists()
    with pytest.raises(ValueError, match="explicitly trusted"):
        extensions.set_enabled("hook:check", True)
    extensions.set_enabled("hook:check", True, trusted=True)
    report = await hooks.run_hooks("before_tool", {"tool_name": "read_file"})
    assert report.allowed and report.outcomes[0].status == "completed"
    assert (tmp_path / "marker").exists()
    assert extensions.usage("hook:check")["counts"] == {"attempted": 1, "completed": 1}
    extensions.clear_override("hook:check")
    assert not extensions.is_enabled("hook:check"), "hook defaults remain off after clearing an override"
    assert (await hooks.run_hooks("before_tool")).outcomes == []


async def test_changing_trusted_script_revokes_trust_and_closed_gate_cannot_be_bypassed(tmp_path):
    _, script = register(tmp_path, 'print(\'{"decision":"continue"}\')', mode="gate")
    extensions.set_enabled("hook:check", True, trusted=True)
    assert (await hooks.run_hooks("before_tool")).allowed
    script.write_text("raise RuntimeError('replaced script')")
    assert not extensions.is_enabled("hook:check")
    report = await hooks.run_hooks("before_tool")
    assert not report.allowed and report.outcomes[0].status == "untrusted"
    assert report.outcomes[0].stdout == ""


@pytest.mark.parametrize("mode,allowed", [("observe", True), ("gate", False)])
async def test_timeout_is_bounded_and_failure_mode_is_explicit(tmp_path, mode, allowed):
    register(tmp_path, "import time\ntime.sleep(30)", mode=mode, timeout=.1)
    extensions.set_enabled("hook:check", True, trusted=True)
    started = time.monotonic()
    report = await hooks.run_hooks("before_tool")
    assert time.monotonic() - started < 2
    assert report.allowed is allowed
    assert report.outcomes[0].status == "timeout"
    assert extensions.usage("hook:check")["counts"]["failed"] == 1


@pytest.mark.parametrize("code,status", [
    ('print(\'{"decision":"deny","reason":"Missing evidence"}\')', "denied"),
    ('print(\'{"decision":"allow","permissions":["all"]}\')', "error"),
    ('print("not json")', "error"),
    ('raise SystemExit(4)', "error"),
    ('print("X" * 100000)', "output_limit"),
])
async def test_gate_denial_errors_and_permission_forgery_are_closed(tmp_path, code, status):
    register(tmp_path, code, mode="gate")
    extensions.set_enabled("hook:check", True, trusted=True)
    report = await hooks.run_hooks("before_tool")
    assert not report.allowed and report.outcomes[0].status == status
    assert len(report.outcomes[0].stdout.encode()) <= hooks.MAX_OUTPUT_BYTES


async def test_observation_cannot_grant_permissions_or_change_arguments_and_env_is_minimal(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TEST_TOKEN", "never forward this")
    register(tmp_path, "import sys,json,os\nevent=json.load(sys.stdin)\nprint(json.dumps({'event':event,'secret':os.getenv('SECRET_TEST_TOKEN'),'decision':'allow','permissions':['all']}))", event="after_tool")
    extensions.set_enabled("hook:check", True, trusted=True)
    payload = {"tool_name": "write_file", "tool_input": {"path": "safe.txt"}}
    report = await hooks.run_hooks("after_tool", payload)
    assert report.allowed and report.outcomes[0].status == "completed"
    output = json.loads(report.outcomes[0].stdout)
    assert output["secret"] is None and output["event"]["payload"] == payload
    assert report.to_dict().keys() == {"allowed", "outcomes"}
    assert payload == {"tool_name": "write_file", "tool_input": {"path": "safe.txt"}}


async def test_process_group_children_are_killed_on_timeout_and_cancellation(tmp_path):
    register(tmp_path, "import subprocess,time\nfrom pathlib import Path\np=subprocess.Popen(['/bin/sleep','30'])\nPath('child.pid').write_text(str(p.pid))\ntime.sleep(30)", timeout=.15)
    extensions.set_enabled("hook:check", True, trusted=True)
    await hooks.run_hooks("before_tool")

    def running(pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
        except FileNotFoundError:
            return False

    child = int((tmp_path / "child.pid").read_text())
    for _ in range(20):
        if not running(child):
            break
        await asyncio.sleep(.02)
    assert not running(child)
    (tmp_path / "child.pid").unlink()
    task = asyncio.create_task(hooks.run_hooks("before_tool"))
    for _ in range(50):
        if (tmp_path / "child.pid").exists():
            break
        await asyncio.sleep(.002)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    child = int((tmp_path / "child.pid").read_text())
    for _ in range(20):
        if not running(child):
            break
        await asyncio.sleep(.02)
    assert not running(child)
    assert extensions.usage("hook:check")["counts"]["cancelled"] == 1


@pytest.mark.parametrize("finish", ["timeout", "cancel", "success"])
async def test_supervisor_reaps_double_fork_setsid_children(tmp_path, finish):
    register(tmp_path, """import os,signal,time,sys
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.fork() == 0:
    os.setsid()
    if os.fork() != 0:
        os._exit(0)
    Path('detached.pid').write_text(str(os.getpid()))
    while True: time.sleep(.02)
while not Path('detached.pid').exists(): time.sleep(.005)
Path('leader.pid').write_text(str(os.getpid()))
print('stdout preserved', flush=True)
print('stderr preserved', file=sys.stderr, flush=True)
""" + ("while True: time.sleep(.02)\n" if finish != "success" else ""),
             timeout=.6 if finish == "timeout" else 3)
    extensions.set_enabled("hook:check", True, trusted=True)
    task = asyncio.create_task(hooks.run_hooks("before_tool"))
    try:
        if finish == "cancel":
            async with asyncio.timeout(2):
                while not (tmp_path / "leader.pid").exists():
                    await asyncio.sleep(.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            report = await asyncio.wait_for(task, 4)
            assert report.outcomes[0].status == ("timeout" if finish == "timeout" else "completed")
            assert report.outcomes[0].stdout == 'stdout preserved\n'
            assert report.outcomes[0].stderr == 'stderr preserved\n'
        assert not Path('/proc/' + (tmp_path / 'leader.pid').read_text()).exists()
        assert not Path('/proc/' + (tmp_path / 'detached.pid').read_text()).exists()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # If the assertion fails, leave no long-running test fixture behind.
        for name in ('leader.pid', 'detached.pid'):
            if (tmp_path / name).exists():
                try:
                    os.kill(int((tmp_path / name).read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass


@pytest.mark.parametrize("mode,allowed", [("observe", True), ("gate", False)])
async def test_supervision_unavailable_keeps_hook_failure_mode(tmp_path, monkeypatch, mode, allowed):
    register(tmp_path, 'pass', mode=mode)
    extensions.set_enabled("hook:check", True, trusted=True)

    def unavailable(command):
        raise hooks.ExecutionRefused('owned cleanup unavailable')

    monkeypatch.setattr(hooks, 'supervised_command', unavailable)
    report = await hooks.run_hooks('before_tool')
    assert report.allowed is allowed and report.outcomes[0].status == 'error'
    assert 'owned cleanup unavailable' in report.outcomes[0].reason


async def test_event_budget_and_input_bounds(tmp_path):
    register(tmp_path, "import time\ntime.sleep(.2)", name="a", timeout=.5)
    register(tmp_path, 'print(\'{"decision":"continue"}\')', name="b", mode="gate")
    for name in ("a", "b"):
        extensions.set_enabled("hook:" + name, True, trusted=True)
    report = await hooks.run_hooks("before_tool", budget_s=.05)
    assert not report.allowed
    assert all(r.status == "timeout" for r in report.outcomes)
    report = await hooks.run_hooks("before_tool", {"text": "x" * hooks.MAX_INPUT_BYTES})
    assert not report.allowed and report.outcomes[0].status == "input_error"


async def test_large_tool_inputs_do_not_fail_when_no_matching_enabled_hook(tmp_path):
    large = {"tool_input": {"content": "x" * (hooks.MAX_INPUT_BYTES + 1)}}
    assert (await hooks.run_hooks("before_tool", large)).allowed
    register(tmp_path, "pass", event="session_end")
    extensions.set_enabled("hook:check", True, trusted=True)
    assert (await hooks.run_hooks("before_tool", large)).allowed
    report = await hooks.run_hooks("session_end", large)
    assert report.allowed and report.outcomes[0].status == "input_error"
    register(tmp_path, "pass", event="before_tool", name="observe")
    extensions.set_enabled("hook:observe", True, trusted=True)
    report = await hooks.run_hooks("before_tool", large)
    assert report.allowed and report.outcomes[0].status == "input_error"


async def test_enabled_malformed_registration_still_closes_before_tool(tmp_path):
    register(tmp_path, "pass")
    extensions.set_enabled("hook:check", True, trusted=True)
    with extensions._transaction() as data:
        data["hooks"]["check"]["events"] = "invalid"
    report = await hooks.run_hooks("before_tool", {"large": "x" * hooks.MAX_INPUT_BYTES})
    assert not report.allowed and report.outcomes[0].status == "invalid"


async def test_malformed_settings_are_not_silently_accepted_at_before_tool(tmp_path):
    path = extensions.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{")
    assert not (await hooks.run_hooks("before_tool")).allowed
    after = await hooks.run_hooks("session_end")
    assert after.allowed and after.outcomes[0].status == "settings_error"
    assert path.read_text() == "{"


@pytest.mark.parametrize("patch", [{"command": "echo unsafe"}, {"command": ["python", "x.py"]},
                                  {"timeout_s": float("nan")}, {"timeout_s": 100},
                                  {"mode": "gate", "events": ["session_start"]}, {"permissions": ["all"]}])
def test_invalid_and_unbounded_registrations_are_refused(tmp_path, patch):
    spec = {"name": "check", "command": [sys.executable, "-c", "pass"], "cwd": str(tmp_path),
            "events": ["before_tool"], **patch}
    with pytest.raises(ValueError):
        hooks.register_hook(spec)
    assert not extensions.settings_path().exists()
