"""DREAM-246: a Gemini invocation may discover only its own Dream bridge.

Pure argv checks: no installed CLI, account configuration or provider calls.
"""

import pytest

from dream.core.backends.gemini_adapter import GeminiAdapter


def invocation(config):
    return GeminiAdapter().argv(
        "fixture prompt", cwd="/fixture workspace", resume_id=None,
        sandbox="read-only", mcp_config=config,
    )


def allowed_names(argv):
    option = "--allowed-mcp-server-names"
    assert argv.count(option) == 1, "A bridged invocation needs one explicit MCP allowlist"
    index = argv.index(option)
    return argv[index + 1]


def test_concurrent_sessions_discover_only_their_own_bridge():
    first = invocation({"name": "dream-session-first"})
    second = invocation({"name": "dream-session-second"})
    assert allowed_names(first) == "dream-session-first"
    assert allowed_names(second) == "dream-session-second"
    assert "dream-session-second" not in first
    assert "dream-session-first" not in second
    for argv in (first, second):
        assert "--trust" not in argv
        assert argv[-2:] == ["-p", "fixture prompt"]
        assert argv[argv.index("--approval-mode") + 1] == "plan"


def test_no_bridge_config_keeps_ordinary_invocation():
    assert invocation(None) == [
        "gemini", "--output-format", "stream-json", "--approval-mode", "plan",
        "--include-directories", "/fixture workspace", "-p", "fixture prompt",
    ]


@pytest.mark.parametrize("name", [
    "", "--other-server", "first,second", "name with space", "bad\x00name", None, 17,
])
def test_malformed_scope_name_cannot_expand_discovery(name):
    with pytest.raises(ValueError):
        invocation({"name": name})
