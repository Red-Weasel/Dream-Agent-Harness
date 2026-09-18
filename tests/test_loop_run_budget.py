"""Invocation-wide Engine budget contract, with real meters and fixture workers."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from dream.core.backends.base import Event
from dream.core.loop import AutonomousLoop
from dream.core.providers import get_provider
from dream.telemetry.runtime import RunLimit, RunMeter


class Worker:
    def __init__(self, workspace, replies):
        self.workspace = workspace
        self.provider = get_provider('machx')
        self.model = 'fixture-model'
        self.replies = list(replies)
        self.profile = SimpleNamespace(max_run_tokens=10, max_run_tools=10, max_run_seconds=30)
        self.runtime_meter = self.shared_meter = None
        self.begins = self.ends = 0
        self.prompts = []

    def begin_run_budget(self):
        if self.shared_meter is not None:
            raise RuntimeError('Engine already owned by another run')
        self.begins += 1
        self.shared_meter = self.runtime_meter = RunMeter('fixture', self.begins, self.profile)

    def end_run_budget(self):
        self.ends += 1
        self.shared_meter = None

    async def ask(self, prompt):
        self.runtime_meter.check()
        self.prompts.append(prompt)
        self.runtime_meter.usage({'input_tokens': 8, 'output_tokens': 2})
        yield Event('assistant_done', self.replies.pop(0))

    async def interrupt(self):
        pass


class Reviewer:
    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def ask(self, prompt):
        yield Event('assistant_done', 'VERDICT: PASS\nGAPS: none')


def loop_for(worker, tmp_path):
    return AutonomousLoop(worker, state_dir=tmp_path / 'runs', evaluator_backend_factory=lambda *a: Reviewer())


def ledger(loop):
    return [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]


async def test_multiple_asks_share_budget_and_explicit_resume_starts_fresh(tmp_path):
    worker = Worker(tmp_path, ['STATUS: CONTINUE\nNEXT: finish', 'STATUS: DONE'])
    loop = loop_for(worker, tmp_path)
    first = await loop.run('goal', acceptance_criteria='- inspect artifact')
    assert first.status == 'budget' and first.iterations == 1
    assert worker.begins == worker.ends == 1 and worker.shared_meter is None
    first_meter = worker.runtime_meter
    assert first_meter.summary()['prompt_tokens'] == 8
    assert ledger(loop)[-1]['state']['uncertain'] is False

    second = await loop.run('goal', resume_run_id=first.run_id)
    assert second.status == 'done' and second.iterations == 2
    assert worker.begins == worker.ends == 2 and worker.runtime_meter is not first_meter
    usages = [record['data']['usage'] for record in ledger(loop) if record['event'] == 'runtime_budget']
    assert [usage['prompt_tokens'] + usage['output_tokens'] for usage in usages] == [10, 10]
    assert len(worker.prompts) == 2


async def test_contract_negotiation_consumes_same_invocation_budget(tmp_path):
    worker = Worker(tmp_path, ['- inspect artifact', 'STATUS: DONE'])
    loop = loop_for(worker, tmp_path)
    first = await loop.run('goal')
    assert first.status == 'budget' and first.iterations == 0 and len(worker.prompts) == 1
    second = await loop.run('goal', resume_run_id=first.run_id)
    assert second.status == 'done' and len(worker.prompts) == 2
    assert worker.begins == worker.ends == 2


async def test_engine_before_turn_limit_is_budget_without_uncertain_effects(tmp_path):
    class BeforeTurnWorker(Worker):
        async def ask(self, prompt):
            if self.begins == 1:
                raise RunLimit('Before-turn budget check')
            async for event in super().ask(prompt):
                yield event
    worker = BeforeTurnWorker(tmp_path, ['STATUS: DONE'])
    loop = loop_for(worker, tmp_path)
    first = await loop.run('goal', acceptance_criteria='- inspect artifact')
    assert first.status == 'budget' and not worker.prompts
    assert ledger(loop)[-1]['state']['phase'] == 'ready'
    assert ledger(loop)[-1]['state']['uncertain'] is False
    assert (await loop.run('goal', resume_run_id=first.run_id)).status == 'done'
    assert worker.begins == worker.ends == 2


async def test_mid_turn_limit_preserves_uncertainty_until_explicit_reconciliation(tmp_path):
    class DuringTurnWorker(Worker):
        async def ask(self, prompt):
            if self.begins == 1:
                yield Event('tool_use', {'name': 'fixture_effect', 'id': 'effect-1'})
                raise RunLimit('Mid-turn budget check')
            async for event in super().ask(prompt):
                yield event
    worker = DuringTurnWorker(tmp_path, ['STATUS: DONE'])
    loop = loop_for(worker, tmp_path)
    first = await loop.run('goal', acceptance_criteria='- inspect artifact')
    assert first.status == 'budget' and ledger(loop)[-1]['state']['uncertain'] is True
    blocked = await loop.run('goal', resume_run_id=first.run_id)
    assert blocked.status == 'need_input' and not worker.prompts
    resumed = await loop.run('goal', resume_run_id=first.run_id, resume_note='Inspected fixture effect; already completed.')
    assert resumed.status == 'done' and len(worker.prompts) == 1
    assert worker.begins == worker.ends == 3


async def test_cancelled_run_releases_shared_budget_and_keeps_final_meter(tmp_path):
    entered = asyncio.Event()
    interrupted = asyncio.Event()
    class HangingWorker(Worker):
        async def ask(self, prompt):
            entered.set()
            await interrupted.wait()
            yield Event('assistant_done', 'unreachable')
        async def interrupt(self):
            interrupted.set()
    worker = HangingWorker(tmp_path, [])
    loop = loop_for(worker, tmp_path)
    task = asyncio.create_task(loop.run('goal', acceptance_criteria='- inspect artifact'))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.begins == worker.ends == 1
    assert worker.shared_meter is None and worker.runtime_meter is not None
    assert interrupted.is_set()
    assert ledger(loop)[-1]['state']['status'] == 'stopped'


async def test_failed_begin_does_not_release_another_runs_meter(tmp_path):
    worker = Worker(tmp_path, [])
    worker.begin_run_budget()
    owned = worker.shared_meter
    result = await loop_for(worker, tmp_path).run('goal')
    assert result.status == 'error'
    assert worker.shared_meter is owned and worker.ends == 0
