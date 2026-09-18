"""Every backend receives the explicit workspace path, including projects outside
Dream's source tree. Fixtures use disposable directories and no provider network.
"""

from __future__ import annotations

import pytest

import dream.config as config
import dream.core.engine as engine_mod
from dream.core.backends import cli_agent
from dream.core.engine import Engine


class _FakeBackend:
    """Stands in for every real backend: captures the system prompt the engine
    hands over, connects/disconnects without touching the network."""

    def __init__(self, *args, **kwargs):
        from dream.core.providers import get_provider
        self.provider = kwargs.get("provider") or get_provider("anthropic")
        self.system_prompt = kwargs.get("system_prompt")
        self.adapter = None  # _register_persistent_mcp early-returns
        self.mcp_config = None

    async def connect(self):
        pass

    async def disconnect(self):
        pass


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Hermetic engine start: every path Dream writes goes to tmp, the heavy
    edges (embedder, browser, real backends) are stubbed. Returns a workspace
    directory that is NOT Dream's root — a synthetic external-project scenario."""
    data = tmp_path / "data"
    mem = tmp_path / "memory"
    var = tmp_path / "var"
    monkeypatch.setattr(config, "SEMANTIC_MEMORY", False)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "SESSIONS_DIR", data / "sessions")
    monkeypatch.setattr(config, "DB_PATH", data / "dream.db")
    monkeypatch.setattr(config, "MEMORY_DIR", mem)
    monkeypatch.setattr(config, "SEMANTIC_DIR", mem / "semantic")
    monkeypatch.setattr(config, "PROCEDURAL_DIR", mem / "procedural")
    monkeypatch.setattr(config, "EPISODIC_DIR", mem / "episodic")
    monkeypatch.setattr(config, "IDENTITY_FILE", mem / "IDENTITY.md")
    monkeypatch.setattr(config, "THREADS_FILE", mem / "THREADS.md")
    monkeypatch.setattr(config, "INSTRUCTIONS_FILE", mem / "INSTRUCTIONS.md")
    monkeypatch.setattr(config, "VAR_DIR", var)
    monkeypatch.setattr(config, "LOG_DIR", var / "logs")
    monkeypatch.setattr(config, "LOOP_DIR", var / "loops")
    monkeypatch.setattr(config, "SCREENSHOT_DIR", var / "screenshots")
    monkeypatch.setattr(engine_mod, "get_browser", lambda: None)
    monkeypatch.setattr(engine_mod, "AnthropicBackend", _FakeBackend)
    monkeypatch.setattr(engine_mod, "OpenAICompatBackend", _FakeBackend)
    monkeypatch.setattr(cli_agent, "CliAgentBackend", _FakeBackend)

    ws = tmp_path / "example-project"
    ws.mkdir()
    return ws


@pytest.mark.parametrize("provider", ["anthropic", "machx", "codex"])
async def test_workspace_path_reaches_every_backend(workspace, provider):
    # anthropic = SDK path, machx = openai-compat local path, codex = CLI path.
    eng = Engine(provider=provider, workspace=workspace)
    await eng._start()
    try:
        prompt = eng.backend.system_prompt
        assert "## Your workspace" in prompt
        # The ABSOLUTE path — "here" / "the repo" must resolve to the session
        # workspace, not wherever the model guesses.
        assert str(workspace.resolve()) in prompt
    finally:
        await eng._cleanup()


async def test_workspace_section_defaults_to_root_when_unset(workspace):
    # No workspace given → the engine falls back to config.ROOT; the model must
    # still be told that absolute path rather than left to guess.
    eng = Engine(provider="machx")
    await eng._start()
    try:
        prompt = eng.backend.system_prompt
        assert "## Your workspace" in prompt
        assert str(config.ROOT) in prompt
    finally:
        await eng._cleanup()
