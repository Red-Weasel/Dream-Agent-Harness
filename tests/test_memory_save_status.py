"""Observed consolidation status using isolated storage and fake providers."""
import asyncio
from datetime import datetime

import pytest

from dream import config
from dream.core.backends.base import Event
from dream.core.engine import Engine
from dream.memory import longterm
from dream.memory.store import MemoryStore


class ScriptedBackend:
    def __init__(self, events, waiting=None):
        self.events = events
        self.waiting = waiting
        self.calls = 0

    async def ask(self, prompt):
        self.calls += 1
        if self.waiting:
            self.waiting.set()
            await asyncio.Event().wait()
        for event in self.events:
            yield event


@pytest.fixture
def engine(tmp_path):
    engine = Engine(provider='machx', model='fixture', workspace=tmp_path)
    engine.store = MemoryStore(tmp_path / 'fixture.db')
    engine.store.start_session(engine.session_id)
    engine.store.add_note(engine.session_id, 'Retain the deployment instructions.')
    yield engine
    engine.store.close()


def save(engine):
    return engine.runtime_status()['memory']['save']


def test_idle_status_is_read_only_and_reports_scope(engine):
    engine.backend = ScriptedBackend([])
    status = save(engine)
    assert status['state'] == 'idle'
    assert status['session_id'] == engine.session_id
    assert status['workspace'] == str(engine.workspace)
    assert status['source'] == 'engine.consolidate'
    status['state'] = 'forged'
    assert save(engine)['state'] == 'idle'
    assert engine.backend.calls == 0


@pytest.mark.asyncio
async def test_saved_is_reported_after_markdown_and_session_commit(engine, monkeypatch):
    engine.backend = ScriptedBackend([Event('assistant_done', 'SUMMARY: Kept deployment instructions.')])
    original = longterm.write_markdown
    observed = []

    def write(memory):
        observed.append(save(engine)['state'])
        return original(memory)

    monkeypatch.setattr(longterm, 'write_markdown', write)
    summary = await engine.consolidate()
    status = save(engine)
    assert summary == 'Kept deployment instructions.'
    assert observed == ['saving']
    assert status['state'] == 'saved' and status['error'] is None
    assert status['started_at'] <= status['updated_at'] == status['finished_at']
    assert datetime.fromisoformat(status['finished_at']).tzinfo is not None
    assert engine.store.get_session(engine.session_id)['summary'] == summary
    assert (config.MEMORY_DIR / f'session-{engine.session_id.lower()}.md').exists()


@pytest.mark.asyncio
async def test_provider_failure_is_visible_and_retains_notes(engine):
    engine.backend = ScriptedBackend([Event('error', 'fixture provider failure')])
    assert await engine.consolidate() is None
    status = save(engine)
    assert status['state'] == 'failed' and 'fixture provider failure' in status['error']
    assert engine.store.session_notes(engine.session_id, only_unconsolidated=True)


@pytest.mark.asyncio
async def test_markdown_failure_cannot_report_saved(engine, monkeypatch):
    engine.backend = ScriptedBackend([Event('assistant_done', 'SUMMARY: Kept deployment instructions.')])

    def fail(memory):
        raise OSError('fixture disk unavailable')

    monkeypatch.setattr(longterm, 'write_markdown', fail)
    await engine.consolidate()
    status = save(engine)
    assert status['state'] == 'failed'
    assert 'episodic save' in status['error'].lower()
    assert not list(config.MEMORY_DIR.glob('session-*.md'))


@pytest.mark.asyncio
async def test_cancelled_provider_does_not_leave_status_running(engine):
    waiting = asyncio.Event()
    engine.backend = ScriptedBackend([], waiting)
    job = asyncio.create_task(engine.consolidate())
    await asyncio.wait_for(waiting.wait(), 2)
    assert save(engine)['state'] == 'consolidating'
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert save(engine)['state'] == 'failed'
    assert 'interrupted' in save(engine)['error'].lower()
    assert engine.store.session_notes(engine.session_id, only_unconsolidated=True)


@pytest.mark.asyncio
async def test_no_backend_reports_unavailable(engine):
    assert await engine.consolidate() is None
    assert save(engine)['state'] == 'unavailable'


@pytest.mark.asyncio
async def test_final_commit_error_remains_an_error_and_retry_clears_it(engine, monkeypatch):
    engine.backend = ScriptedBackend([Event('assistant_done', 'SUMMARY: Retained instructions.')])
    original = engine.store.end_session

    def fail(*args):
        raise OSError('fixture session commit failed')

    monkeypatch.setattr(engine.store, 'end_session', fail)
    with pytest.raises(OSError, match='fixture session commit failed'):
        await engine.consolidate()
    assert save(engine)['state'] == 'failed'
    assert 'fixture session commit failed' in save(engine)['error']
    monkeypatch.setattr(engine.store, 'end_session', original)
    await engine.consolidate()
    assert save(engine)['state'] == 'saved'
    assert save(engine)['error'] is None
