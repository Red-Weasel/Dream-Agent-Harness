"""A shell command Dream runs must not inherit the user's secrets.

Measured before this guard existed: a subprocess saw 130+ host variables, 38
with names like key/token/secret/auth. The harness-lab benchmark surfaced it.
"""

import os

from dream.tools.native import _ENV_KEEP, _SECRET_HINTS, _scrubbed_env


def test_secret_shaped_variables_are_dropped(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "sk-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("DB_PASSWORD", "hunter2")
    monkeypatch.setenv("SOME_AUTH_SOCK", "/run/agent")

    env = _scrubbed_env()

    assert "MY_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "DB_PASSWORD" not in env
    assert "SOME_AUTH_SOCK" not in env


def test_the_shell_still_works(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/home/someone")

    env = _scrubbed_env()

    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/home/someone"
    for name in _ENV_KEEP:
        if name in os.environ:
            assert name in env, f"{name} is needed by a shell and was dropped"


def test_ordinary_variables_pass_through(monkeypatch):
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.setenv("DREAM_MONITOR", "0")

    env = _scrubbed_env()

    assert env["EDITOR"] == "vim"
    assert env["DREAM_MONITOR"] == "0"


def test_no_secret_shaped_name_survives():
    leaked = [
        k for k in _scrubbed_env()
        if k not in _ENV_KEEP and any(h in k.upper() for h in _SECRET_HINTS)
    ]
    assert not leaked, f"secret-shaped variables reached the shell: {leaked}"
