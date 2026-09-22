"""Fix #41: a contained shell command that only reads runs without asking, in every
mode but plan. 2026-09-21: 145 s of the first 264 s waited on `ls`/`grep`/`cat`."""
from __future__ import annotations
import pytest
from dream.core import policy
from dream.core.execution import ExecutionScope, SandboxCapability


def decision(workspace, command, *, mode="ask", contained=True):
    scope = ExecutionScope(workspace)
    capability = SandboxCapability(True, "fixture", scope, "/fixture/bwrap") if contained else None
    return policy.decide("run_bash", {"command": command}, mode, workspace,
                         execution_scope=scope, execution_capability=capability)


@pytest.mark.parametrize("command", ['cd "/p" && ls -la src | head -40', "grep -n foo src/a.js | head", "sed -n '1,40p' a.js",
                                     "git status && git diff --stat", "wc -l falcon9/*.js && ls -la diag/ | head -30"])
@pytest.mark.parametrize("mode", ["ask", "accept-edits", "auto"])
def test_contained_read_only_shell_is_allowed(tmp_path, command, mode):
    assert decision(tmp_path, command, mode=mode)[0] == "allow"


def test_plan_mode_still_denies(tmp_path):
    assert decision(tmp_path, "ls -la", mode="plan")[0] == "deny"


def test_without_containment_it_still_asks(tmp_path):
    assert decision(tmp_path, "ls -la", contained=False)[0] == "ask"


@pytest.mark.parametrize("command", ["sed -i 's/a/b/' a.js", "echo x > out.txt", "python3 scripts/render.py", "rm -rf build"])
def test_writes_and_programs_still_ask(tmp_path, command):
    assert decision(tmp_path, command)[0] in {"ask", "deny"}
