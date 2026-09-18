"""Process guidance reaches the first provider request without model discovery."""
from pathlib import Path

import pytest

from dream import config, extensions
from dream.core.backends.base import Event
from dream.core.engine import Engine
from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory
from dream.tools.context import ToolContext


class RecordingBackend:
    def __init__(self):
        self.prompts = []
        self.prepared = []

    def prepare_turn(self, tools):
        self.prepared.append(tuple(tools))

    async def ask(self, prompt):
        self.prompts.append(prompt)
        yield Event('assistant_done', 'Observed fixture result.')
        yield Event('result', {'is_error': False})


@pytest.fixture
def engine(tmp_path, monkeypatch):
    from dream.tools import installed_skill_tools
    monkeypatch.setattr(config, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setenv('DREAM_SKILL_DIRS', str(Path(__file__).resolve().parents[1] / 'skills'))
    monkeypatch.setattr(installed_skill_tools, '_CACHE', None)
    instance = Engine(provider='openai', workspace=tmp_path, profile='lean')
    instance.store = MemoryStore(tmp_path / 'memory.db')
    instance.store.start_session(instance.session_id)
    instance.working = WorkingMemory(instance.store, instance.session_id)
    instance._tool_context = ToolContext(instance.store, instance.working, None, instance.session_id, workspace=tmp_path)
    instance.backend = RecordingBackend()
    instance._started = True
    return instance


async def test_explicit_workflow_is_supplied_before_first_request_without_polluting_user_log(engine):
    request = 'Use the coding skill to fix the sorting bug in the project.'
    events = [e async for e in engine.ask(request)]
    sent = engine.backend.prompts[0]
    assert sent.startswith(request)
    assert len(sent) > len(request) + 100
    assert len(sent) - len(request) < 4600
    assert any(e.kind == 'system' and 'coding' in str(e.data) for e in events)
    assert 'read_file' in engine.backend.prepared[0]
    users = [t for t in engine.store.session_turns(engine.session_id) if t['role'] == 'user']
    assert users[0]['content'] == request


async def test_simple_chat_has_no_workflow_or_extra_tools(engine):
    events = [e async for e in engine.ask('Hello there')]
    assert engine.backend.prompts == ['Hello there']
    assert not any(e.kind == 'system' and 'workflow' in str(e.data).lower() for e in events)
    assert engine.backend.prepared == [()]


async def test_disabled_workflow_is_not_injected(engine):
    extensions.set_enabled('skill:coding', False)
    request = 'Use the coding skill to fix a bug.'
    events = [e async for e in engine.ask(request)]
    assert engine.backend.prompts == [request]
    assert not any(e.kind == 'system' and 'Using' in str(e.data) and 'coding' in str(e.data) for e in events)
