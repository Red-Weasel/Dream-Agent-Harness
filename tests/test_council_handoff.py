"""CPU lifecycle fixtures, including AnyIO's actual task/scope ordering."""
import asyncio
from types import SimpleNamespace
import anyio
import pytest
from dream.core import engine as mod
from dream.core.engine import Engine, Event
from dream.core.moe import MoeConfig
from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory
from dream.tools.context import ToolContext


class Backend:
    def __init__(self, name, events, fail=False):
        self.provider_label = name
        self.events = events
        self.fail = fail
        self.scope = None
        self.prompts = []

    async def connect(self):
        self.events.append(('connect', self.provider_label, asyncio.current_task()))
        if self.fail:
            raise RuntimeError('connection refused')
        self.scope = anyio.CancelScope()
        self.scope.__enter__()

    async def disconnect(self):
        self.events.append(('disconnect', self.provider_label, asyncio.current_task()))
        if self.scope:
            self.scope.__exit__(None, None, None)
            self.scope = None

    def set_effort(self, value):
        self.effort = value

    async def ask(self, prompt):
        self.prompts.append(prompt)
        yield Event('assistant_done', 'answer')
        yield Event('result', {'subtype': 'success', 'is_error': False})


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.config, 'SESSIONS_DIR', tmp_path / 'sessions')
    from dream.skills.selection import TaskGuidance
    monkeypatch.setattr('dream.skills.selection.select_for_task', lambda prompt: TaskGuidance())
    monkeypatch.setattr('dream.projects.build_context', lambda *args: {'text': '', 'warnings': [], 'names': [], 'revision': 0})
    e = Engine(provider='machx', model='original-model', workspace=tmp_path)
    e.store = MemoryStore(tmp_path / 'test.db')
    e.store.start_session(e.session_id)
    e.working = WorkingMemory(e.store, e.session_id)
    e.browser = object()
    e._tool_context = ToolContext(e.store, e.working, e.browser, e.session_id, workspace=tmp_path)
    e._started = True
    e._assembled_system_prompt = 'base'
    e._session_tools = {}
    e._built_tools = {'tools': [], 'server': {}, 'exempt_tool_ids': []}
    e._mcp = SimpleNamespace(servers=[])
    yield e
    e.store.close()


async def test_roster_update_preserves_backend_and_delivers_current_roster(engine):
    e = engine
    old = Backend('old', [])
    e.backend = old
    cfg = MoeConfig('machx', ['codex'], 2, 17, True, {'codex': 'advisor-model'})
    await e.configure_council(cfg)
    assert e.backend is old and e.model == 'original-model'
    assert e._tool_context.moe_config == cfg
    await e.configure_council(MoeConfig('machx', ['gemini']))
    async with asyncio.timeout(5):
        async for _ in e._ask('hello'):
            pass
    assert 'gemini' in old.prompts[-1] and 'codex' not in old.prompts[-1]


async def test_handoff_preserves_dream_state_and_scope_order(engine, monkeypatch):
    e = engine
    events = []
    old = Backend('old', events)
    new = Backend('new', events)
    e.backend = old
    await old.connect()
    e.working.log_turn('user', 'keep this goal')
    e.working.log_turn('assistant', 'prior response')
    e.working.log_turn('tool_result', 'PRIVATE TOOL DETAIL')
    state = (e.session_id, e.store, e.working, e.workspace, e.browser, e._tool_context)
    async def create():
        assert e.provider.key == 'openai' and e.model == 'new-model'
        return new
    monkeypatch.setattr(e, '_create_backend', create, raising=False)
    try:
        await e.configure_council(MoeConfig('openai', ['codex']), model='new-model')
        assert (e.session_id, e.store, e.working, e.workspace, e.browser, e._tool_context) == state
        assert [(a, n) for a, n, _ in events] == [('connect', 'old'), ('disconnect', 'old'), ('connect', 'new')]
        assert all(task is asyncio.current_task() for _, _, task in events)
        async with asyncio.timeout(5):
            async for _ in e._ask('continue'):
                pass
        assert 'keep this goal' in new.prompts[-1]
        assert 'provider-private' in new.prompts[-1]
        assert 'PRIVATE TOOL DETAIL' not in new.prompts[-1]
    finally:
        await e.backend.disconnect()


async def test_failed_handoff_reconnects_old_and_keeps_selection(engine, monkeypatch):
    e = engine
    events = []
    old = Backend('old', events)
    failed = Backend('failed', events, fail=True)
    e.backend = old
    await old.connect()
    e.working.log_turn('user', 'still here')
    cfg = MoeConfig('machx', ['codex'])
    await e.configure_council(cfg)
    async def create():
        return failed
    monkeypatch.setattr(e, '_create_backend', create, raising=False)
    try:
        with pytest.raises(RuntimeError, match='connection refused'):
            await e.configure_council(MoeConfig('openai', []), model='next')
        assert e.backend is old and e.provider.key == 'machx' and e.model == 'original-model'
        assert e._moe == cfg and e._tool_context.moe_config == cfg
        assert [(a, n) for a, n, _ in events][-4:] == [('disconnect', 'old'), ('connect', 'failed'), ('disconnect', 'failed'), ('connect', 'old')]
        assert 'still here' in e.council_context()
    finally:
        await e.backend.disconnect()


async def test_invalid_config_never_disconnects(engine):
    e = engine
    e.backend = Backend('old', [])
    with pytest.raises(ValueError):
        await e.configure_council(MoeConfig('machx', ['codex', 'codex']))
    assert e.backend.events == []


async def test_explicit_advice_reaches_next_main_turn(engine):
    e = engine
    e.backend = Backend('old', [])
    await e.record_council_results('which route?', [{'advisor': 'codex', 'answer': 'keep the dissent'}])
    async for _ in e.ask('continue'):
        pass
    assert 'keep the dissent' in e.backend.prompts[-1]
    assert e.store.session_turns(e.session_id)[0]['role'] == 'council'
    assert e._pending_council == ()


def test_recent_history_is_bounded_and_preserves_latest_turn(engine):
    for i in range(130):
        engine.working.log_turn('user', f'turn {i}: ' + 'x' * 3000)
    context = engine.council_context()
    assert 'turn 129:' in context and 'turn 0:' not in context
    assert len(context) < 12500


async def test_main_effort_applies_without_reconnect_when_supported(engine):
    e = engine
    e.backend = Backend('old', [])
    e.provider = mod.get_provider('openai')
    await e.configure_council(MoeConfig('openai', [], orchestrator_effort='high'))
    assert e.effort == e.backend.effort == 'high'
    assert e.backend.events == []


async def test_claude_effort_change_reconnects_same_main_with_continuity(engine, monkeypatch):
    e = engine
    events = []
    e.provider = mod.get_provider('anthropic')
    old, new = Backend('old', events), Backend('new', events)
    e.backend = old
    await old.connect()
    e.working.log_turn('user', 'preserve this task')
    async def create():
        new.set_effort(e.effort)
        return new
    monkeypatch.setattr(e, '_create_backend', create)
    try:
        await e.configure_council(MoeConfig('anthropic', [], orchestrator_effort='max'))
        assert e.backend is new and e.effort == new.effort == 'max'
        assert e._pending_handoff.required[0].content == 'preserve this task'
    finally:
        await e.backend.disconnect()


async def test_rollback_failure_blocks_asks_with_actionable_error(engine, monkeypatch):
    e = engine
    old = Backend('old', [])
    e.backend = old
    await old.connect()
    old.fail = True
    async def create():
        return Backend('new', [], fail=True)
    monkeypatch.setattr(e, '_create_backend', create)
    with pytest.raises(RuntimeError, match='rollback reconnect failed'):
        await e.configure_council(MoeConfig('openai', []))
    with pytest.raises(RuntimeError, match='Restart Dream'):
        async for _ in e.ask('continue'):
            pass


async def test_multiple_explicit_consultations_survive_until_next_turn(engine):
    await engine.record_council_results('first question', [{'advisor': 'codex', 'answer': 'first advice'}])
    await engine.record_council_results('second question', [{'advisor': 'gemini', 'answer': 'second advice'}])
    assert 'first advice' in engine._pending_council[0].content
    assert 'second advice' in engine._pending_council[1].content


async def test_disconnect_failure_is_reported_as_unavailable(engine):
    class Broken(Backend):
        async def disconnect(self):
            raise RuntimeError('disconnect failed')
    engine.backend = Broken('old', [])
    with pytest.raises(RuntimeError, match='disconnect'):
        await engine.configure_council(MoeConfig('openai', []))
    with pytest.raises(RuntimeError, match='Restart Dream'):
        async for _ in engine.ask('continue'):
            pass


async def test_cleanup_closes_backend_before_outer_mcp_scope(engine, monkeypatch):
    events = []
    engine.backend = Backend('backend', events)
    class MCP:
        async def stop(self):
            events.append(('mcp-stop', 'mcp', asyncio.current_task()))
    engine._mcp = MCP()
    async def hooks(*args, **kwargs):
        return SimpleNamespace(allowed=True, outcomes=[])
    monkeypatch.setattr('dream.hooks.run_hooks', hooks)
    await engine._cleanup()
    assert [kind for kind, *_ in events] == ['disconnect', 'mcp-stop']


async def test_handoff_updates_idle_bridge_bounds_and_preserves_ledger(engine, monkeypatch):
    from dream.mcp.bridge import SessionToolBridge, read_discovery
    from dataclasses import replace
    e = engine
    bridge = SessionToolBridge(e.session_id, lambda: [], lambda *args: None, timeout=1200, max_concurrency=4)
    bridge.publish('http://127.0.0.1:39999')
    bridge._requests['fixture'] = 'done'
    e._tool_bridge = bridge
    e.backend = Backend('old', [])
    await e.backend.connect()
    async def create():
        return Backend('new', [])
    monkeypatch.setattr(e, '_create_backend', create)
    monkeypatch.setattr(mod, 'resolve_profile', lambda *args, **kwargs: replace(e.profile, max_parallel=1, subagent_timeout_s=100))
    try:
        await e.configure_council(MoeConfig('openai', []))
        assert bridge.timeout == 100
        assert bridge._slots._value == 1 and bridge._max_pending == 4
        assert bridge._requests == {'fixture': 'done'}
        assert read_discovery(bridge.discovery_path)['call_timeout_s'] == 100
    finally:
        await e.backend.disconnect()
        await bridge.close()


async def test_active_member_round_trip_with_real_engine_and_cancel_scopes(engine, monkeypatch):
    from dream.tui.council import CouncilControls
    e = engine
    events = []
    e.backend = Backend('original', events)
    await e.backend.connect()
    cfg = MoeConfig('machx', ['codex'], advisor_models={'codex': 'gpt-6-astra'},
                    advisor_efforts={'codex': 'high'})
    await e.configure_council(cfg)
    created = []
    async def create():
        backend = Backend(e.provider.key, events)
        created.append(backend)
        return backend
    monkeypatch.setattr(e, '_create_backend', create)
    obj = CouncilControls()
    obj.engine = e
    obj.workspace = e.workspace
    obj.interrupted = False
    obj.renderer = SimpleNamespace(system=lambda text: None)
    obj.bus = SimpleNamespace(publish=lambda event: None)
    async def ask(prompt):
        async def stream():
            result = False
            async for event in e.ask(prompt):
                if event.kind == 'result':
                    result = not event.data.get('is_error')
            return result
        return await asyncio.create_task(stream())
    obj._ask = ask
    try:
        result = await obj._work_council('Inspect and polish existing files', 'codex')
        assert result == {'completed': ['codex'], 'main_restored': True}
        assert e._moe == cfg and e.model == 'original-model'
        assert e._tool_context.workspace == obj.workspace
        assert [backend.provider_label for backend in created] == ['codex', 'machx']
        assert all(task is asyncio.current_task() for _, _, task in events)
        assert 'Inspect and polish existing files' in created[0].prompts[0]
        assert 'Inspect and polish existing files' in e.council_context()
        assert e._pending_handoff is not None
    finally:
        await e.backend.disconnect()
