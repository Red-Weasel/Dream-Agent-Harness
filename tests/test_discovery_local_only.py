"""Description discovery by default is for a local model only (owner, 2026-10-13: [feedback]): a Claude, CLI or
remote session keeps the full tool list, system prompt and task skill selection. A DREAM_DISCOVERY_MODE set
explicitly applies to every provider."""
from types import SimpleNamespace

import pytest

from dream.core import discovery
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.providers import Provider, get_provider
from dream.core.system_prompt import DESCRIPTION_BASE
from test_description_discovery import _started_with

LOCAL_HTTP = Provider(key="openai", label="LM Studio", kind="openai", base_url="http://127.0.0.1:1234/v1")
REMOTE_HTTP = Provider(key="openai", label="OpenAI", kind="openai", base_url="https://api.openai.com/v1")
DESCRIPTION_HEAD = DESCRIPTION_BASE[:60]


@pytest.mark.parametrize("provider,descriptions", [
    (get_provider("machx"), True), (LOCAL_HTTP, True),
    (get_provider("anthropic"), False), (get_provider("codex"), False), (REMOTE_HTTP, False),
])
def test_unset_is_descriptions_for_a_local_model_only(monkeypatch, provider, descriptions):
    _started_with(monkeypatch, None)
    assert discovery.for_provider(provider).descriptions is descriptions


@pytest.mark.parametrize("value,descriptions", [("descriptions", True), ("default", False), ("off", False)])
def test_an_explicit_mode_applies_to_every_provider(monkeypatch, value, descriptions):
    _started_with(monkeypatch, value)
    for provider in (get_provider("machx"), get_provider("anthropic"), get_provider("codex"), REMOTE_HTTP):
        assert discovery.for_provider(provider).descriptions is descriptions


def _engine(tmp_path, monkeypatch, provider):
    from dream.core import engine as engine_module
    from dream.memory.store import MemoryStore
    monkeypatch.setattr(engine_module.config, "SESSIONS_DIR", tmp_path / "sessions")
    e = engine_module.Engine(provider=provider, model="fixture", workspace=tmp_path)
    e._mcp = SimpleNamespace(servers=[])
    e._session_tools = {}
    e._built_tools = {"tools": [], "server": {}, "exempt_tool_ids": []}
    e._local_subs = {}
    e.store = MemoryStore(tmp_path / "t.db")
    e.store.start_session(e.session_id)
    return e


@pytest.mark.parametrize("provider,descriptions", [("machx", True), ("anthropic", False), ("codex", False)])
def test_the_engine_system_prompt_follows_the_provider(tmp_path, monkeypatch, provider, descriptions):
    _started_with(monkeypatch, None)
    e = _engine(tmp_path, monkeypatch, provider)
    try:
        assert (DESCRIPTION_HEAD in str(e._system_prompt())) is descriptions
    finally:
        e.store.close()


def test_a_remote_http_backend_keeps_the_full_tool_list(monkeypatch):
    _started_with(monkeypatch, None)
    remote = OpenAICompatBackend(provider=REMOTE_HTTP, model="m", system_prompt="S", tools=[], permission_cb=None)
    local = OpenAICompatBackend(provider=LOCAL_HTTP, model="m", system_prompt="S", tools=[], permission_cb=None)
    assert not remote._discovery.descriptions and local._discovery.descriptions
    assert not any("was built in discovery mode" in note for note in remote._discovery_notes)

