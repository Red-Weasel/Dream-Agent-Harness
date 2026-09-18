"""Fourth adversarial review, permission surface — two auto-allows that were
inherited rather than earned:

- defect 1: a self-built custom tool that NAMES ITSELF after a safe builtin
  ("Read") inherited that builtin's free pass — arbitrary Python running with no
  prompt at all. Dream writes its own tools, so this is a self-inflicted footgun,
  not an attacker story. Classification now follows PROVENANCE, and the registry
  refuses the collision outright.
- defect 2: shell confinement failed OPEN for the awk family. awk's program is a
  plain operand, not a flagged payload, so `awk 'BEGIN{system(...)}'` looked like
  an ordinary confined command and auto-allowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream import config, extensions
from dream.core import policy
from dream.tools import registry

WS = Path("/home/example/project").resolve()


@pytest.fixture(autouse=True)
def _restore_declared_custom_tools():
    """Provenance is module state on the policy — the boot's custom set. Put the
    real one back so this file's fake tools don't follow the suite around."""
    saved = set(policy._CUSTOM_NAMES)
    yield
    policy.declare_custom_tools(saved)


def _write_tool(dirpath: Path, name: str) -> None:
    (dirpath / f"{name.lower()}_tool.py").write_text(
        "from typing import Any\n"
        "from claude_agent_sdk import tool\n"
        f"@tool({name!r}, 'Does a thing.', {{'type': 'object', 'properties': {{}}}})\n"
        "async def handler(args: dict[str, Any]):\n"
        "    return {'content': [{'type': 'text', 'text': 'done'}]}\n",
        encoding="utf-8",
    )


# --- defect 1: a custom tool must not inherit a builtin's auto-allow ---------


def test_custom_tool_may_not_claim_a_builtin_name(tmp_path, monkeypatch):
    """A custom tool called "Read" (or "Bash", or "TodoWrite") is arbitrary
    Python wearing a safe name. It must never register, and never land in the
    pre-approved set that skips the permission callback."""
    for name in ("Read", "TodoWrite", "Bash"):
        _write_tool(tmp_path, name)
    monkeypatch.setattr(config, "CUSTOM_TOOLS_DIR", tmp_path)

    # Approve the synthetic entry files so name-collision policy, rather than
    # missing source trust, must reject these tools.
    for name in ("Read", "TodoWrite", "Bash"):
        review = extensions.review_module(f"tool:custom/{name.lower()}_tool")
        assert review["source"] == (tmp_path / f"{name.lower()}_tool.py").read_text()
        extensions.trust_module(review["id"], review["sha256"])
    built = registry.build()
    for name in ("Read", "TodoWrite", "Bash"):
        assert name not in built["custom_names"], name
        assert name not in built["names"], name
        assert config.tool_id(name) not in built["exempt_tool_ids"], name
        assert any(name in w for w in built["warnings"]), name

    # The genuine builtins are untouched — Dream still looks around for free.
    assert policy.capability("Read") == policy.READONLY
    for mode in policy.MODES:
        assert policy.decide("Read", {"file_path": "/etc/hosts"}, mode, WS)[0] == "allow"


def test_provenance_beats_a_later_builtin_rename(tmp_path, monkeypatch):
    """The classifier must judge a self-built tool by where it came from, not by
    its name — otherwise growing the builtin lists later silently hands an
    already-registered custom tool a free pass."""
    _write_tool(tmp_path, "deploy")
    monkeypatch.setattr(config, "CUSTOM_TOOLS_DIR", tmp_path)
    review = extensions.review_module("tool:custom/deploy_tool")
    assert review["source"] == (tmp_path / "deploy_tool.py").read_text()
    extensions.trust_module(review["id"], review["sha256"])
    built = registry.build()
    assert "deploy" in built["custom_names"]

    # Simulate a future edit to the builtin lists that happens to collide.
    monkeypatch.setattr(policy, "_READ_ONLY", policy._READ_ONLY | {"deploy"})
    assert policy.capability("mcp__dream__deploy") == policy.MUTATING
    assert policy.capability("deploy") == policy.MUTATING
    assert config.tool_id("deploy") not in built["exempt_tool_ids"]
    assert policy.decide("mcp__dream__deploy", {}, "ask", WS)[0] == "ask"
    assert policy.decide("mcp__dream__deploy", {}, "plan", WS)[0] == "deny"


# --- defect 2: inline-program interpreters are never provably confined -------


def test_awk_family_inline_program_asks_in_auto_mode():
    """awk hides its whole program in an operand — system(), | "sh", print > file.
    Static analysis can't prove any of that confined, so the family asks."""
    unconfined = [
        "awk 'BEGIN{system(\"rm -rf /home/example/other\")}'",
        "gawk 'BEGIN{system(\"cat ~/.ssh/id_rsa\")}'",
        "mawk 'BEGIN{print \"x\" | \"sh\"}'",
        "nawk '{print $1}' data.txt",
        "awk -f steal.awk data.txt",
        "cat notes.txt | awk 'BEGIN{system(\"id\")}'",
    ]
    for cmd in unconfined:
        d, reason = policy.decide("Bash", {"command": cmd}, "auto", WS)
        assert d == "ask", cmd
        assert reason.startswith("outside workspace"), (cmd, reason)
        assert policy.decide("run_bash", {"command": cmd}, "auto", WS)[0] == "ask", cmd


def test_flagged_inline_payloads_still_ask():
    """Regression guard for the forms that already escalated — closing the awk
    family must not shift the -c/-e handling."""
    for cmd in [
        "perl -e 'unlink q(/etc/hosts)'",
        "python3 -c 'import os'",
        "ruby -e 'puts 1'",
        "sh -c 'rm x'",
        "node -e 'require(\"fs\")'",
        "env python3 -c 'import os'",
    ]:
        assert policy.decide("Bash", {"command": cmd}, "auto", WS)[0] == "ask", cmd


def test_glued_shell_operators_do_not_hide_an_interpreter():
    """`cat f|awk 'prog'` tokenizes as one word "f|awk", so the scan never saw the
    interpreter at all — every inline payload behind an unspaced |, ;, && or
    subshell rode straight through auto mode."""
    for cmd in [
        "cat notes.txt|awk 'BEGIN{system(\"id\")}'",
        "cat notes.txt|python3 -c 'import os'",
        "ls;awk 'BEGIN{system(\"id\")}'",
        "true&&perl -e 'unlink q(/etc/x)'",
        "(cd sub&&node -e 'require(\"fs\")')",
    ]:
        assert policy.decide("Bash", {"command": cmd}, "auto", WS)[0] == "ask", cmd


def test_awk_family_over_asks_by_design():
    """Deliberate: the word `awk` anywhere asks, even as a mere argument to
    something else. Proving which token is the command word means parsing the
    shell — assignments, `env`, `nice`, `xargs`, `find -exec` all hide an
    invocation — so the scan covers every position and eats the false positive.
    Same doctrine as the rest of the module: over-ask, never under-ask."""
    assert policy.decide("Bash", {"command": "grep -rn awk ."}, "auto", WS)[0] == "ask"


async def test_confined_commands_still_auto_allow(tmp_path):
    """The fix must not make Dream prompt-happy: ordinary in-workspace commands
    keep running unprompted in auto mode."""
    from dream.core.execution import ExecutionScope, probe_sandbox
    import pytest
    for cmd in ("pytest -q", "npm run build", "python3 run.py --flag"):
        assert policy.decide("run_bash", {"command": cmd}, "auto", tmp_path)[0] == "ask"
    scope = ExecutionScope(tmp_path)
    capability = await probe_sandbox(scope)
    if not capability.available:
        pytest.skip(capability.reason)
    options = dict(execution_scope=scope, execution_capability=capability)
    for cmd in ["ls -la", "git status", "pytest -q", "npm run build",
                "echo hi >> notes.txt", "python3 run.py --flag",
                "grep -rn policy ."]:
        assert policy.decide("run_bash", {"command": cmd}, "auto", tmp_path, **options)[0] == "allow", cmd
    assert policy.decide("run_bash", {"command": "rm -rf build/"}, "auto", tmp_path, **options)[0] == "ask"
