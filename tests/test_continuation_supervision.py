"""Continuation and ownership fixtures; no model, network, or owner process."""
import asyncio
import json

import pytest

from dream.core import run_state
from dream.core.backends.base import Event
from dream.core.loop import AutonomousLoop, _parse_status
from dream.core.run_state import RunState


class Worker:
    def __init__(self, workspace, replies):
        self.workspace = workspace
        self.replies = iter(replies)
        self.prompts = []

    async def ask(self, prompt):
        self.prompts.append(prompt)
        reply = next(self.replies)
        yield Event('assistant_done', reply)

    async def interrupt(self):
        pass


@pytest.mark.parametrize('text', [
    'The old report said STATUS: NEED_INPUT, but I can proceed.',
    'STATUS: DONE\nNEXT: nothing\nThe actual artifact still needs work.',
    'STATUS: CONTINUE_THIS_IS_NOT_A_STATUS\nNEXT: inspect',
    '```\nSTATUS: NEED_INPUT\nNEXT: obsolete question\n```',
    'Example:\n```text\nSTATUS: DONE',
    'Example:\n~~~text\nSTATUS: NEED_INPUT\nNEXT: not a request',
    '````text\n```\nSTATUS: DONE',
])
def test_status_in_prose_or_old_block_cannot_end_continuation(text):
    assert _parse_status(text) == (None, '')


async def test_clean_resume_delivers_saved_next_action_to_fresh_worker(tmp_path):
    action = 'Verify the 17th fixture frame before editing the remaining segment.'
    first = AutonomousLoop(Worker(tmp_path, [f'STATUS: CONTINUE\nNEXT: {action}']),
                           state_dir=tmp_path / 'runs', max_iterations=1)
    saved = await first.run('finish fixture', acceptance_criteria='- all frames verified')
    assert saved.status == 'budget'
    fresh = Worker(tmp_path, ['STATUS: NEED_INPUT\nNEXT: Select the final color.'])
    resumed = AutonomousLoop(fresh, state_dir=tmp_path / 'runs')
    result = await resumed.run('finish fixture', resume_run_id=saved.run_id)
    assert result.status == 'need_input'
    assert action in fresh.prompts[0]
    assert 'Do not replay completed actions' in fresh.prompts[0]


async def test_continue_sends_checkpoint_next_and_stays_in_same_run(tmp_path):
    action = 'Inspect fixture B after finishing fixture A.'
    worker = Worker(tmp_path, [f'STATUS: CONTINUE\nNEXT: {action}',
                              'STATUS: NEED_INPUT\nNEXT: Pick an output format.'])
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs')
    result = await loop.run('finish fixture', acceptance_criteria='- fixtures complete')
    assert result.iterations == 2
    assert action in worker.prompts[1]
    records = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
    first_finish = next(record for record in records if record['event'] == 'worker_finished')
    assert first_finish['state']['next'] == action
    assert first_finish['state']['phase'] == 'ready'


def checkpoint(root, phase='ready', **changes):
    journal = RunState(root)
    journal.record('created', status='running', phase=phase, uncertain=False,
                   goal='finish fixture', worker_workspace=str(root), iterations=1,
                   contract='- fixture exists', result=None, **changes)
    return journal


def test_inspection_distinguishes_owned_from_abandoned_turn(tmp_path):
    journal = checkpoint(tmp_path, 'worker_running')
    try:
        before = (journal.path / 'ledger.jsonl').read_bytes()
        live = run_state.inspect_run(tmp_path, journal.run_id)
        assert live['ownership'] == 'held'
        assert live['status'] == 'running'
        assert live['continuation'] == 'owned'
        assert not live['requires_reconciliation']
        assert (journal.path / 'ledger.jsonl').read_bytes() == before
    finally:
        journal.close()
    abandoned = run_state.inspect_run(tmp_path, journal.run_id)
    assert abandoned['ownership'] == 'free'
    assert abandoned['recorded_status'] == 'running'
    assert abandoned['status'] == 'stopped'
    assert abandoned['continuation'] == 'needs_reconciliation'
    assert abandoned['requires_reconciliation']
    assert (journal.path / 'ledger.jsonl').read_bytes() == before


@pytest.mark.parametrize(('phase', 'want'), [
    ('contract_needed', 'resume_available'), ('ready', 'resume_available'),
    ('review_pending', 'resume_available'), ('contract_running', 'needs_reconciliation'),
])
def test_clean_checkpoints_can_resume_but_interrupted_turns_need_reconciliation(tmp_path, phase, want):
    journal = checkpoint(tmp_path, phase)
    journal.close()
    observed = run_state.inspect_run(tmp_path, journal.run_id)
    assert observed['status'] == 'stopped'
    assert observed['continuation'] == want


@pytest.mark.parametrize('status', ['need_input', 'budget', 'stopped', 'error', 'done', 'unverified'])
def test_final_outcomes_are_not_automatically_resumed(tmp_path, status):
    journal = checkpoint(tmp_path)
    journal.record('outcome', status=status)
    journal.close()
    observed = run_state.inspect_run(tmp_path, journal.run_id)
    assert observed['status'] == status
    assert observed['continuation'] == {'need_input': 'waiting_input', 'done': 'complete'}.get(status, status)


def test_missing_lock_is_unknown_without_creating_one(tmp_path):
    journal = checkpoint(tmp_path)
    journal.close()
    (journal.path / '.lock').unlink()
    observed = run_state.inspect_run(tmp_path, journal.run_id)
    assert observed['ownership'] == 'unknown'
    assert observed['status'] == 'unknown'
    assert observed['continuation'] == 'unknown'
    assert not (journal.path / '.lock').exists()


def test_inspection_uses_ledger_despite_stale_snapshot_and_rejects_corruption(tmp_path):
    journal = checkpoint(tmp_path)
    journal.close()
    (journal.path / 'state.json').write_text('{broken snapshot')
    assert run_state.inspect_run(tmp_path, journal.run_id)['state']['phase'] == 'ready'
    with (journal.path / 'ledger.jsonl').open('a') as stream:
        stream.write('{incomplete')
    with pytest.raises(ValueError):
        run_state.inspect_run(tmp_path, journal.run_id)


def test_inspection_refuses_run_and_ledger_symlinks(tmp_path):
    runs = tmp_path / 'runs'
    journal = checkpoint(runs)
    journal.close()
    (runs / 'alias').symlink_to(journal.path, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        run_state.inspect_run(runs, 'alias')
    original = journal.path / 'ledger.jsonl'
    target = tmp_path / 'saved-ledger'
    original.rename(target)
    original.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        run_state.inspect_run(runs, journal.run_id)


def _crash_driver(root, connection):
    """Own only this fixture's directory; exit without Python cleanup."""
    import os
    from pathlib import Path

    workspace = Path(root)
    journal = RunState(workspace / 'runs')
    journal.record('worker_started', status='running', phase='worker_running',
                   uncertain=False, goal='finish fixture', worker_workspace=str(workspace),
                   iterations=1, contract='- fixture exists', result=None)
    (workspace / 'effect.txt').write_text('executed once')
    connection.send(journal.run_id)
    connection.recv()
    os._exit(7)


async def test_dead_process_is_stopped_and_restart_cannot_replay_uncertain_effect(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    process = context.Process(target=_crash_driver, args=(str(tmp_path), child))
    process.start()
    child.close()
    try:
        assert parent.poll(10), 'fixture driver did not create its checkpoint'
        run_id = parent.recv()
        assert run_state.inspect_run(tmp_path / 'runs', run_id)['ownership'] == 'held'
        parent.send('exit without cleanup')
        process.join(10)
        assert process.exitcode == 7
        observed = run_state.inspect_run(tmp_path / 'runs', run_id)
        assert observed['ownership'] == 'free'
        assert observed['status'] == 'stopped'
        assert observed['continuation'] == 'needs_reconciliation'
        worker = Worker(tmp_path, [])
        recovery = AutonomousLoop(worker, state_dir=tmp_path / 'runs')
        result = await recovery.run('finish fixture', resume_run_id=run_id)
        assert result.status == 'need_input'
        assert worker.prompts == []
        assert (tmp_path / 'effect.txt').read_text() == 'executed once'
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()


async def test_long_permission_wait_retains_ownership_and_cancel_is_durable(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    class AncientClock:
        @staticmethod
        def now(*args):
            return datetime(1970, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr(run_state, 'datetime', AncientClock)
    waiting, released = asyncio.Event(), asyncio.Event()

    class PermissionWorker(Worker):
        async def ask(self, prompt):
            self.prompts.append(prompt)
            waiting.set()
            await released.wait()
            yield Event('assistant_done', 'STATUS: DONE')

        async def interrupt(self):
            released.set()

    worker = PermissionWorker(tmp_path, [])
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs', evaluate=False)
    task = asyncio.create_task(loop.run('finish fixture', acceptance_criteria='- fixture exists'))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        observed = run_state.inspect_run(tmp_path / 'runs', loop._journal.run_id)
        assert observed['updated_at'].startswith('1970-')
        assert observed['ownership'] == 'held'
        assert observed['status'] == 'running'
        assert observed['continuation'] == 'owned'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        stopped = run_state.inspect_run(tmp_path / 'runs', loop._journal.run_id)
        assert stopped['status'] == 'stopped'
        assert stopped['ownership'] == 'free'
        assert stopped['requires_reconciliation']
        assert len(worker.prompts) == 1
    finally:
        released.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
