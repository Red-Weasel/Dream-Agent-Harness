"""Council workers use the real turn path on the provider lifecycle task."""
import asyncio
from types import SimpleNamespace

import pytest

from dream.core.moe import MoeConfig
from dream.tui.council import CouncilControls


@pytest.fixture
def worker(tmp_path):
    class Worker(CouncilControls):
        pass
    obj = Worker()
    obj.workspace = tmp_path
    obj.mode = 'auto'
    obj.events, obj.calls = [], []
    obj.bus = SimpleNamespace(publish=obj.events.append)
    obj.renderer = SimpleNamespace(system=lambda text: None)
    obj.engine = SimpleNamespace(_moe=MoeConfig('machx', ['codex', 'gemini'],
        advisor_models={'codex': 'gpt-6-astra', 'gemini': 'gemini-model'},
        advisor_efforts={'codex': 'high'}), model='local', effort=None,
        provider=SimpleNamespace(key='machx'), provider_label='Local', _backend_available=True)
    obj.interrupted = False
    obj.owner = None
    async def configure(cfg, model=None):
        if obj.owner is None:
            obj.owner = asyncio.current_task()
        assert asyncio.current_task() is obj.owner
        obj.calls.append(('configure', cfg.orchestrator, model, cfg.orchestrator_effort))
        obj.engine._moe = cfg
        obj.engine.provider = SimpleNamespace(key=cfg.orchestrator)
        obj.engine.provider_label = cfg.orchestrator
        obj.engine.model = model
        obj.engine.effort = cfg.orchestrator_effort
    async def ask(prompt):
        obj.calls.append(('work', obj.engine.provider.key, prompt, obj.mode, obj.workspace))
        return True
    obj.engine.configure_council = configure
    obj._ask = ask
    return obj


async def test_selected_member_edits_then_main_restored(worker):
    cfg = worker.engine._moe
    result = await worker._work_council('Polish the existing animation', 'codex')
    assert [c[:2] for c in worker.calls] == [('configure', 'codex'), ('work', 'codex'), ('configure', 'machx')]
    assert worker.calls[0][2:] == ('gpt-6-astra', 'high')
    assert worker.calls[1][3:] == ('auto', worker.workspace)
    assert 'Polish the existing animation' in worker.calls[1][2]
    assert worker.engine._moe == cfg and worker.engine.model == 'local'
    assert worker.provider == 'machx'
    assert result['completed'] == ['codex']


async def test_team_relay_serializes_turns_and_restores_main(worker):
    result = await worker._work_council('Improve and verify the saved result')
    assert [c[1] for c in worker.calls if c[0] == 'work'] == ['codex', 'gemini']
    assert result['completed'] == ['codex', 'gemini']
    assert worker.calls[-1][:3] == ('configure', 'machx', 'local')


@pytest.mark.parametrize('failure', ['exception', 'incomplete', 'interrupted'])
async def test_failure_never_starts_next_worker_and_restores_main(worker, failure):
    async def fail(prompt):
        worker.calls.append(('work', worker.engine.provider.key))
        if failure == 'exception':
            raise RuntimeError('provider failed')
        worker.interrupted = failure == 'interrupted'
        return False
    worker._ask = fail
    with pytest.raises((ValueError, RuntimeError)):
        await worker._work_council('Polish')
    assert [c[1] for c in worker.calls if c[0] == 'work'] == ['codex']
    assert worker.engine.provider.key == 'machx'


@pytest.mark.parametrize('question,member', [('', 'codex'), ('x'*8001, 'codex'), ('Polish', 'unknown')])
async def test_invalid_work_rejected_before_handoff(worker, question, member):
    with pytest.raises(ValueError):
        await worker._work_council(question, member)
    assert not worker.calls


async def test_restore_failure_explicit_no_replay(worker):
    configure = worker.engine.configure_council
    async def reject_restore(cfg, model=None):
        if cfg.orchestrator == 'machx':
            raise RuntimeError('reconnect failed')
        await configure(cfg, model)
    worker.engine.configure_council = reject_restore
    with pytest.raises(RuntimeError, match='restore|return|reconnect'):
        await worker._work_council('Polish', 'codex')
    assert len([c for c in worker.calls if c[0] == 'work']) == 1


async def test_stream_reports_terminal_failure_without_workflow():
    from dream.tui.app import App
    from dream.core.backends.base import Event
    obj = App.__new__(App)
    obj._render_event = lambda event: None
    async def failed(prompt):
        yield Event('result', {'subtype': 'error', 'is_error': True})
    obj.engine = SimpleNamespace(ask=failed)
    assert await obj._stream('task') is False
    async def success(prompt):
        yield Event('result', {'subtype': 'success'})
    obj.engine.ask = success
    assert await obj._stream('task') is True
    async def missing(prompt):
        yield Event('text', 'not a terminal receipt')
    obj.engine.ask = missing
    assert await obj._stream('task') is False


async def test_restore_normalizes_legacy_effort_and_default_model(worker):
    worker.engine.effort = 'med'
    worker.engine.model = None
    await worker._work_council('Polish', 'codex')
    assert worker.calls[-1] == ('configure', 'machx', '', 'medium')


async def test_cancellation_restores_main_on_lifecycle_task(worker):
    async def cancel(prompt):
        raise asyncio.CancelledError()
    worker._ask = cancel
    with pytest.raises(asyncio.CancelledError):
        await worker._work_council('Polish', 'codex')
    assert worker.engine.provider.key == 'machx'

async def test_work_activity_has_stable_task_identity_and_lifecycle(worker):
    await worker._work_council('Polish existing output','codex')
    rows=[event.data for event in worker.events if event.kind=='council_activity']
    assert [row['state'] for row in rows]==['queued','running','completed']
    assert len({row['task_id'] for row in rows})==1
    assert rows[-1]['member']=='codex'
    assert rows[0]['model']=='gpt-6-astra'
    assert rows[-1]['workspace']==str(worker.workspace)
    assert all(row['observed_at'] for row in rows)

async def test_failed_worker_activity_is_terminal(worker):
    async def fail(prompt):
        raise RuntimeError('fixture provider failed')
    worker._ask=fail
    with pytest.raises(RuntimeError):
        await worker._work_council('Polish','codex')
    rows=[event.data for event in worker.events if event.kind=='council_activity']
    assert rows[-1]['state']=='failed'
    assert 'fixture provider failed' in rows[-1]['detail']
