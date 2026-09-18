"""Workspace boundary + mode cycle: writes inside obey the mode, writes outside
always ask, plan mode blocks changes, reads are free everywhere."""

from pathlib import Path

import pytest

from dream.core import policy


WS = Path("/home/example/project").resolve()


def test_mode_cycle():
    assert policy.next_mode("ask") == "accept-edits"
    assert policy.next_mode("accept-edits") == "auto"
    assert policy.next_mode("auto") == "plan"
    assert policy.next_mode("plan") == "ask"
    assert policy.next_mode("bogus") == "ask"


def test_reads_always_allowed():
    for mode in policy.MODES:
        d, _ = policy.decide("Read", {"file_path": "/etc/hosts"}, mode, WS)
        assert d == "allow"
    d, _ = policy.decide("mcp__dream__recall", {"query": "x"}, "ask", WS)
    assert d == "allow"


def test_write_inside_obeys_mode():
    inp = {"file_path": str(WS / "src/main.py")}
    assert policy.decide("Write", inp, "ask", WS)[0] == "ask"
    assert policy.decide("Write", inp, "accept-edits", WS)[0] == "allow"
    assert policy.decide("Write", inp, "auto", WS)[0] == "allow"
    assert policy.decide("Write", inp, "plan", WS)[0] == "deny"


def test_write_outside_always_asks_even_in_auto():
    inp = {"file_path": "/home/example/other/secret.txt"}
    for mode in ("accept-edits", "auto"):
        d, reason = policy.decide("Edit", inp, mode, WS)
        assert d == "ask" and reason.startswith("outside workspace")
    # Plan mode still wins (no changes at all).
    assert policy.decide("Edit", inp, "plan", WS)[0] == "deny"


def test_relative_write_resolves_into_workspace():
    d, _ = policy.decide("Write", {"file_path": "notes/todo.md"}, "accept-edits", WS)
    assert d == "allow"  # relative → inside the workspace


def test_shell_gated_by_mode():
    inp = {"command": "ls -la"}
    assert policy.decide("Bash", inp, "ask", WS)[0] == "ask"
    assert policy.decide("Bash", inp, "accept-edits", WS)[0] == "ask"  # edits≠shell
    assert policy.decide("Bash", inp, "auto", WS)[0] == "allow"
    assert policy.decide("Bash", inp, "plan", WS)[0] == "deny"


def test_activity_classification():
    created = policy.classify_file_activity(
        "write_file", {"path": str(WS / "new-file.py")}, WS
    )
    assert created == ("created", "new-file.py")
    edited = policy.classify_file_activity(
        "Edit", {"file_path": str(WS / "existing.py")}, WS
    )
    assert edited == ("edited", "existing.py")
    deleted = policy.classify_file_activity("Bash", {"command": "rm -rf build/"}, WS)
    assert deleted == ("deleted", "(via shell)")
    assert policy.classify_file_activity("Read", {"file_path": "x"}, WS) is None


def test_interpreter_inline_code_escalates_in_auto_mode():
    # Auto mode auto-allows plain shell, but an interpreter running INLINE CODE is
    # unconfined (it can write anywhere) → must still ask. These forms previously
    # slipped through: glued -c, version-suffixed binary, and stdin-piped code.
    escalate = [
        "python3 -c 'import os'",                 # canonical
        "python3 -c'import os; open(\"/x\",\"w\")'",  # GLUED flag (shlex → -cimport…)
        "python3.12 -c 'import os'",              # version-suffixed name
        "pypy3 -c 'x'",                           # version-suffixed alt interpreter
        "perl -e'unlink q(/etc/hosts)'",          # glued -e
        "bash -c'cp a /home/example/evil'",         # glued bash -c
        "echo 'import os' | python3",             # code via stdin, no flag
    ]
    for cmd in escalate:
        assert policy.decide("Bash", {"command": cmd}, "auto", WS)[0] == "ask", cmd


async def test_running_a_script_file_in_workspace_is_not_escalated(tmp_path):
    from dream.core.execution import ExecutionScope, probe_sandbox
    args = {"command": "python3 run.py --flag"}
    assert policy.decide("run_bash", args, "auto", tmp_path)[0] == "ask"
    scope = ExecutionScope(tmp_path)
    capability = await probe_sandbox(scope)
    if not capability.available:
        pytest.skip(capability.reason)
    assert policy.decide("run_bash", args, "auto", tmp_path,
                         execution_scope=scope, execution_capability=capability)[0] == "allow"
