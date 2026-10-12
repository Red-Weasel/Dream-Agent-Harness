"""DREAM-246: Gemini bridge registration, without any actual CLI or settings IO.

CLI contract: https://geminicli.com/docs/tools/mcp-server/
The command/name precede --; every server argument follows it unchanged.
"""

import asyncio
from types import SimpleNamespace

import pytest

from dream.core.backends.gemini_adapter import GeminiAdapter
from dream.core.engine import Engine


NAME = "dream-session-fixture-246"
COMMAND = ["/fixture path/python", "-B", "-m", "dream.mcp", "--server-option", "a b"]
ENV = {"DREAM_SESSION_ID": "fixture-session", "DREAM_PARENT_BRIDGE_FILE": "/fixture path/bridge.json"}


def test_registration_preserves_server_and_environment_tokens():
    argv = GeminiAdapter().mcp_register_argv(NAME, ENV, COMMAND)
    assert argv is not None, "Gemini must supply its bridge registration command"
    assert argv[:3] == ["gemini", "mcp", "add"]
    boundary = argv.index("--")
    options = argv[3:boundary - 2]
    assert argv[boundary - 2:boundary] == [NAME, COMMAND[0]]
    assert argv[boundary + 1:] == COMMAND[1:]
    assert options[options.index("--scope") + 1] == "user"
    assert options[options.index("--transport") + 1] == "stdio"
    assert options.count("--env") == len(ENV)
    for key, value in ENV.items():
        token = f"{key}={value}"
        assert options[options.index(token) - 1] == "--env"
    assert "--trust" not in argv


def test_registration_allows_no_environment_or_server_arguments():
    argv = GeminiAdapter().mcp_register_argv(NAME, {}, ["python3"])
    assert argv is not None
    assert argv[-3:] == [NAME, "python3", "--"]
    assert "--env" not in argv


def test_cleanup_targets_only_same_name_and_user_scope():
    argv = GeminiAdapter().mcp_deregister_argv(NAME)
    assert argv is not None, "Gemini must supply its bridge cleanup command"
    assert argv[:3] == ["gemini", "mcp", "remove"]
    assert argv[3:] in (["--scope", "user", NAME], [NAME, "--scope", "user"])


@pytest.mark.parametrize("name,env,command", [
    ("", {}, ["python3"]),
    ("--other-name", {}, ["python3"]),
    ("bad\x00name", {}, ["python3"]),
    (NAME, {}, []),
    (NAME, {}, [""]),
    (NAME, {}, ["python3", "bad\x00arg"]),
    (NAME, {"BAD=KEY": "value"}, ["python3"]),
    (NAME, {"GOOD_KEY": "bad\x00value"}, ["python3"]),
    (NAME, {"GOOD_KEY": "value=would-be-truncated"}, ["python3"]),
    (NAME, {"GOOD_KEY": ""}, ["python3"]),
    (NAME, {}, ["--would-override-gemini-options"]),
])
def test_invalid_registration_refuses(name, env, command):
    with pytest.raises(ValueError):
        GeminiAdapter().mcp_register_argv(name, env, command)


@pytest.mark.parametrize("name", ["", "--other-name", "bad\x00name"])
def test_invalid_cleanup_name_refuses(name):
    with pytest.raises(ValueError):
        GeminiAdapter().mcp_deregister_argv(name)


@pytest.mark.parametrize("outcome", ["success", "exit_error", "timeout", "cancel"])
async def test_engine_registration_and_cleanup_preserve_unrelated_names(monkeypatch, tmp_path, outcome):
    # Avoid Engine initialization, provider startup and global tool context. These
    # are the real existing registration/cleanup methods, with only IO mocked.
    engine = Engine.__new__(Engine)
    engine.workspace = str(tmp_path)
    engine.provider = SimpleNamespace(label="Gemini fixture")
    engine.backend = SimpleNamespace(adapter=GeminiAdapter(), mcp_config={
        "name": NAME, "command": COMMAND[0], "args": COMMAND[1:], "env": ENV,
    })
    engine._cli_mcp_name = None
    monkeypatch.setattr(engine, "_cli_setup_env", lambda: {"PATH": "/fixture/bin"})
    registrations = {"unrelated-server": "keep", "dream-other-session": "keep"}
    calls = []

    async def fake_run_owned(argv, **kwargs):
        calls.append(list(argv))
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["env"] == {"PATH": "/fixture/bin"}
        assert "--trust" not in argv
        assert NAME in argv
        if argv[2] == "add":
            # Registration might finish before cancellation is observed.
            registrations[NAME] = "fixture"
            if outcome == "cancel":
                raise asyncio.CancelledError
            return SimpleNamespace(returncode=int(outcome == "exit_error"),
                                   timed_out=outcome == "timeout", output=b"fixture failure")
        assert argv[2] == "remove"
        registrations.pop(NAME)
        return SimpleNamespace(returncode=0, timed_out=False, output=b"")

    monkeypatch.setattr("dream.core.execution.run_owned", fake_run_owned)
    if outcome == "success":
        await engine._register_persistent_mcp()
    else:
        error = asyncio.CancelledError if outcome == "cancel" else RuntimeError
        with pytest.raises(error):
            await engine._register_persistent_mcp()
    assert engine._cli_mcp_name == NAME
    await engine._deregister_persistent_mcp()
    await engine._deregister_persistent_mcp()  # repeated cleanup is harmless
    assert engine._cli_mcp_name is None
    assert [call[2] for call in calls] == ["add", "remove"]
    assert registrations == {"unrelated-server": "keep", "dream-other-session": "keep"}
