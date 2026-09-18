"""Dream against the attack classes documented in 2025–26 agent incidents.

Sources for the classes (surveyed in harness-lab research/04-security-audit-recovery.md):
symlink fail-open (GhostApproval, Cursor DuneSlide CVE-2026-50548/50549), config
poisoning (AWS Kiro CVE-2026-10591), the "lethal trifecta" exfiltration shape
(EchoLeak CVE-2025-32711, ForcedLeak).

Dream's model is approval-based, not sandboxed: the correct outcome for a
boundary crossing is that it ASKS. In headless `--loop` there is nobody to ask,
so asking is refusing.
"""

from pathlib import Path

import pytest

from dream.core import policy


@pytest.fixture
def ws(tmp_path):
    w = (tmp_path / "ws").resolve()
    w.mkdir()
    return w


def _decision(tool, payload, ws, mode="accept-edits"):
    return policy.decide(tool, payload, mode, ws)[0]


def test_symlink_out_of_the_workspace_does_not_fail_open(ws, tmp_path):
    """The approval dialog must judge the RESOLVED target, not the path shown.

    A symlink whose name looks innocent but points outside is the shape that
    turned an approved write into an out-of-scope one in the 2026 reports.
    """
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (ws / "innocent").symlink_to(secret_dir)

    assert _decision("Write", {"file_path": str(ws / "innocent" / "key")}, ws) != "allow"
    assert _decision("Read", {"file_path": str(ws / "innocent" / "key")}, ws) is not None


def test_shell_path_through_a_symlink_is_also_resolved(ws, tmp_path):
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (ws / "innocent").symlink_to(secret_dir)

    assert _decision("Bash", {"command": "cat innocent/key"}, ws) != "allow"


def test_traversal_out_of_the_workspace_is_caught(ws):
    assert _decision("Write", {"file_path": str(ws / ".." / ".." / "escaped.txt")}, ws) != "allow"
    assert _decision("Bash", {"command": "echo x > ../../escaped.txt"}, ws) != "allow"


def test_writing_inside_the_workspace_still_works(ws):
    """The guard must not make ordinary work impossible."""
    assert _decision("Write", {"file_path": str(ws / "report.json")}, ws) == "allow"


def test_shell_always_asks_in_accept_edits_even_when_confined(ws):
    """Characterising the real policy, not asserting a preference.

    Shell is consequential, so accept-edits asks for every command — including a
    plainly confined `cat`. In headless `--loop` there is nobody to answer, which
    is why an autonomous Dream run cannot execute anything to check its own work.
    Recorded here so a future change to that trade-off is a deliberate one.
    """
    assert _decision("Bash", {"command": "cat data.csv"}, ws) == "ask"
    assert _decision("Bash", {"command": "python3 solve.py"}, ws) == "ask"


def test_config_poisoning_targets_are_outside_the_workspace(ws):
    """The 2026 pattern: untrusted content makes the agent rewrite the trust
    config that loads at next boot. Those paths are outside any session
    workspace, so the boundary rule is what stops it."""
    for target in ("~/.claude/settings.json", "~/.codex/config.toml", "~/.dream/config.py"):
        assert _decision("Write", {"file_path": target}, ws) != "allow", target


def test_home_directory_write_is_never_silent(ws):
    assert _decision("Write", {"file_path": "~/.bashrc"}, ws) != "allow"
    assert _decision("Bash", {"command": "echo evil >> ~/.bashrc"}, ws) != "allow"
