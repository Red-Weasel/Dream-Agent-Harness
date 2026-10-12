"""Startup ownership cleanup, with no Engine initialization or process execution."""
import asyncio
from types import SimpleNamespace

import pytest

from dream.core.backends.gemini_adapter import GeminiAdapter
from dream.core.engine import Engine


NAME = 'dream-start-cleanup-fixture'


def lifecycle(monkeypatch, tmp_path, *, cause, hold_cleanup=False):
    engine = Engine.__new__(Engine)
    events = []
    registrations = {'unrelated-server': 'keep', 'dream-other-session': 'keep'}
    committed = asyncio.Event()
    removing = asyncio.Event()
    release = asyncio.Event()
    startup_error = RuntimeError('original synthetic registration failure')

    async def disconnect(): events.append('backend_closed')
    async def bridge_close(): events.append('bridge_closed')
    async def mcp_stop(): events.append('mcp_closed')
    async def hooks(name, data):
        assert name == 'session_end' and data == {'session': 'startup-fixture'}
        events.append('session_end')
        return []

    engine.workspace = tmp_path
    engine.session_id = 'startup-fixture'
    engine.provider = SimpleNamespace(label='Synthetic CLI')
    engine.backend = SimpleNamespace(adapter=GeminiAdapter(), disconnect=disconnect,
        mcp_config={'name': NAME, 'command': 'fixture-python', 'args': ['-m', 'dream.mcp'], 'env': {}})
    engine.store = SimpleNamespace(close=lambda: events.append('store_closed'))
    engine._mcp = SimpleNamespace(stop=mcp_stop)
    engine._tool_bridge = SimpleNamespace(close=bridge_close)
    engine._bridge_server = engine._bridge_task = engine._bridge_socket = None
    engine._cli_mcp_name = None
    engine._started = False
    engine._backend_available = True
    engine._record_hooks = lambda result: None
    engine._log_stderr = lambda text: events.append('cleanup_warning')
    monkeypatch.setattr(engine, '_cli_setup_env', lambda: {'PATH': '/fixture-only'})
    monkeypatch.setattr('dream.hooks.run_hooks', hooks)

    async def owned(argv, **kwargs):
        assert kwargs['cwd'] == tmp_path and kwargs['env'] == {'PATH': '/fixture-only'}
        assert argv[:2] == ['gemini', 'mcp'] and NAME in argv
        if argv[2] == 'add':
            registrations[NAME] = 'committed'
            events.append('registration_committed')
            committed.set()
            if cause == 'error': raise startup_error
            await asyncio.Event().wait()
            raise AssertionError('Cancelled startup must not continue.')
        assert argv[2] == 'remove'
        events.append('removal_entered')
        removing.set()
        if hold_cleanup: await release.wait()
        assert registrations.pop(NAME) == 'committed'
        events.append('registration_removed')
        return SimpleNamespace(returncode=0, timed_out=False, output=b'')

    monkeypatch.setattr('dream.core.execution.run_owned', owned)
    # Narrow startup to the production registration stage; start and all cleanup
    # methods themselves remain real, including bridge teardown and ownership.
    engine._start = engine._register_persistent_mcp
    return SimpleNamespace(engine=engine, events=events, registrations=registrations,
        committed=committed, removing=removing, release=release, startup_error=startup_error)


def assert_cleaned(fixture):
    assert fixture.registrations == {'unrelated-server': 'keep', 'dream-other-session': 'keep'}
    assert fixture.engine._cli_mcp_name is None
    assert fixture.engine._tool_bridge is None
    assert fixture.engine._started is False and fixture.engine._backend_available is False
    for event in ('session_end', 'registration_removed', 'backend_closed', 'mcp_closed', 'bridge_closed', 'store_closed'):
        assert fixture.events.count(event) == 1
    assert fixture.events.index('registration_removed') < fixture.events.index('backend_closed')


@pytest.mark.parametrize('cause', ['cancel', 'error'])
async def test_actual_start_cleans_committed_registration_and_preserves_startup_failure(monkeypatch, tmp_path, cause):
    fixture = lifecycle(monkeypatch, tmp_path, cause=cause)
    task = asyncio.create_task(fixture.engine.start())
    await asyncio.wait_for(fixture.committed.wait(), 1)
    if cause == 'cancel': task.cancel('original startup cancellation')
    error = asyncio.CancelledError if cause == 'cancel' else RuntimeError
    with pytest.raises(error) as observed:
        await task
    if cause == 'error': assert observed.value is fixture.startup_error
    else: assert observed.value.args == ('original startup cancellation',)
    assert_cleaned(fixture)


@pytest.mark.parametrize('cause', ['cancel', 'error'])
async def test_repeated_cancellation_waits_for_registration_removal_and_resource_cleanup(monkeypatch, tmp_path, cause):
    fixture = lifecycle(monkeypatch, tmp_path, cause=cause, hold_cleanup=True)
    task = asyncio.create_task(fixture.engine.start())
    entered = asyncio.create_task(fixture.removing.wait())
    try:
        await asyncio.wait_for(fixture.committed.wait(), 1)
        if cause == 'cancel': task.cancel('original startup cancellation')
        done, _ = await asyncio.wait((task, entered), timeout=1, return_when=asyncio.FIRST_COMPLETED)
        assert entered in done, 'Startup exited without attempting ownership cleanup.'
        assert NAME in fixture.registrations
        task.cancel('second cancellation during cleanup')
        await asyncio.sleep(0)
        task.cancel('third cancellation during cleanup')
        await asyncio.sleep(0)
        assert not task.done(), 'Repeated cancellation escaped before committed registration removal.'
        fixture.release.set()
        error = asyncio.CancelledError if cause == 'cancel' else RuntimeError
        with pytest.raises(error) as observed:
            await asyncio.wait_for(task, 1)
        if cause == 'error': assert observed.value is fixture.startup_error
        else: assert observed.value.args == ('original startup cancellation',)
        assert_cleaned(fixture)
    finally:
        fixture.release.set()
        entered.cancel()
        if not task.done(): task.cancel()
        await asyncio.gather(task, entered, return_exceptions=True)


async def test_nonpersistent_backend_cleanup_stays_in_sdk_scope_owner_task():
    engine = Engine.__new__(Engine)
    engine._cli_mcp_name = None
    owner = asyncio.current_task()
    error = RuntimeError('synthetic SDK startup failure')
    cleaned = []

    async def start():
        raise error

    async def cleanup():
        assert asyncio.current_task() is owner
        cleaned.append(True)

    engine._start, engine._cleanup = start, cleanup
    with pytest.raises(RuntimeError) as observed:
        await engine.start()
    assert observed.value is error
    assert cleaned == [True]


async def test_cli_cleanup_failure_is_visible_without_replacing_startup_error():
    engine = Engine.__new__(Engine)
    engine._cli_mcp_name = NAME
    error = RuntimeError('synthetic startup failure')
    warnings = []

    async def start():
        raise error

    async def cleanup():
        raise ValueError('synthetic cleanup failure')

    engine._start, engine._cleanup = start, cleanup
    engine._log_stderr = warnings.append
    with pytest.raises(RuntimeError) as observed:
        await engine.start()
    assert observed.value is error
    assert warnings == ['CLI startup cleanup failed: ValueError']
    assert error.__notes__ == warnings
