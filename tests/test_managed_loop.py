"""Real loop/registry/task-store boundaries with synthetic providers and handlers."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from claude_agent_sdk import SdkMcpTool

from dream.core.backends.base import Event
from dream.core.loop import AutonomousLoop
from dream.core.providers import get_provider
from dream.core.run_state import RunState
from dream.core.task_checkpoints import current
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
from dream.tools import registry


class Reviewer:
    async def connect(self):
        pass

    async def ask(self, prompt):
        yield Event('assistant_done', 'VERDICT: PASS\nGAPS: none')

    async def disconnect(self):
        pass


@pytest.fixture
def worker(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / 'tasks.db')
    tasks = TaskStore(store)
    monkeypatch.setattr(registry, '_BASE_TOOLS', [])
    monkeypatch.setattr(registry, '_load_custom_tools', lambda: ([], []))
    class Worker:
        workspace = tmp_path
        provider = get_provider('machx')
        model = 'fixture'
        _assembled_system_prompt = 'original full standing head'
        backend = None
        stopped = False
        calls = 0
        action = None
        def __init__(self):
            self.tasks = tasks
        async def ask(self, prompt):
            self.calls += 1
            assert current() is not None
            if self.action:
                await self.action()
            yield Event('assistant_done', 'STATUS: DONE')
        async def interrupt(self):
            self.stopped = True
    yield Worker()
    store.close()


def loop(worker, tmp_path):
    return AutonomousLoop(worker, state_dir=tmp_path / 'runs', evaluator_backend_factory=lambda *a: Reviewer())


def tool_bundle(handler, wrap=None):
    return registry.build(extra_tools=[SdkMcpTool(name='fixture_action', description='', input_schema={}, handler=handler)], wrap_tool=wrap)['tools'][0]


async def test_loop_registry_receipt_completion_and_adapter_hook(worker, tmp_path):
    observed = []
    async def handler(args):
        c = current()
        assert c.unresolved_actions
        observed.append('actual handler')
        return {'content': [{'type': 'text', 'text': 'saved'}]}
    tool = tool_bundle(handler)
    async def act():
        await tool.handler({})
    worker.action = act
    async def checked(c):
        assert current() is c
        assert c.task['status'] == 'done'
        assert c.boundary_eligibility()['eligible']
        token = c.prepare_boundary(c.boundary_eligibility()['receipt_id'])
        c.acknowledge_boundary(token['id'], generation=token['generation'], settled=True)
        observed.append('checked reset')
    worker.backend = SimpleNamespace(checked_task_boundary=checked, managed_action_receipts=True)
    driver = loop(worker, tmp_path)
    result = await driver.run('retain full original goal', acceptance_criteria='- criterion')
    assert result.status == 'done' and observed == ['actual handler', 'checked reset']
    assert current() is None
    assert driver.checkpoints.task['managed_state'] == 'checked_complete'
    with RunReader(driver) as journal:
        assert journal.state['managed']['generation'] == 1
        assert journal.state['standing_rules'] == 'original full standing head'
        events = [json.loads(line)['event'] for line in (journal.path / 'ledger.jsonl').read_text().splitlines()]
        assert events.index('action_intent') < events.index('action_returned') < events.index('verification_checked') < events.index('task_checked_complete') < events.index('boundary_prepared') < events.index('boundary_acknowledged')


class RunReader:
    def __init__(self, loop):
        self.loop = loop
    def __enter__(self):
        self.journal = RunState(self.loop.state_dir, self.loop._journal.run_id)
        return self.journal
    def __exit__(self, *a):
        self.journal.close()


async def test_permission_hook_refusal_is_not_an_actual_handler_receipt(worker, tmp_path):
    called = []
    async def handler(args):
        called.append(1)
        return {}
    def refusing(tool):
        async def handler(args):
            return {'is_error': True, 'content': [{'type': 'text', 'text': 'permission refused'}]}
        return SdkMcpTool(name=tool.name, description='', input_schema={}, handler=handler)
    tool = tool_bundle(handler, refusing)
    async def act():
        await tool.handler({})
    worker.action = act
    driver = loop(worker, tmp_path)
    result = await driver.run('goal', acceptance_criteria='- criterion')
    assert result.status == 'done' and called == []
    assert 'action_intent' not in (driver.workspace / 'ledger.jsonl').read_text()
    assert 'boundary_unsupported' in (driver.workspace / 'ledger.jsonl').read_text()


async def test_returned_error_blocks_pass_and_resume_never_replays(worker, tmp_path):
    async def handler(args):
        return {'is_error': True, 'content': [{'type': 'text', 'text': 'failed after effect'}]}
    tool = tool_bundle(handler)
    async def act():
        await tool.handler({})
    worker.action = act
    driver = loop(worker, tmp_path)
    result = await driver.run('goal', acceptance_criteria='- criterion')
    assert result.status == 'need_input'
    assert len(driver.checkpoints.unresolved_actions) == 1
    again = await driver.run('goal', resume_run_id=result.run_id, resume_note='do not replay')
    assert again.status == 'need_input' and worker.calls == 1
    actions = {a: 'Caller inspected effect and reconciled the failed return' for a in driver.checkpoints.unresolved_actions}
    done = await driver.run('goal', resume_run_id=result.run_id, resume_note='review only', reconciled_actions=actions)
    assert done.status == 'done' and worker.calls == 1


async def test_pending_background_handler_cannot_race_review(worker, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(args):
        entered.set()
        await release.wait()
        return {}
    tool = tool_bundle(handler)
    pending = []
    async def act():
        pending.append(asyncio.create_task(tool.handler({})))
        await entered.wait()
    worker.action = act
    driver = loop(worker, tmp_path)
    result = await driver.run('goal', acceptance_criteria='- criterion')
    assert result.status == 'need_input' and driver.checkpoints.unresolved_actions
    # A detached tool cannot silently obtain a receipt once ownership closes.
    release.set()
    with pytest.raises(RuntimeError, match='closed'):
        await pending[0]
    with RunReader(driver) as journal:
        assert journal.state['managed']['unresolved']


async def test_stop_settles_awaited_handler_before_closing_ledger(worker, tmp_path):
    entered, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def handler(args):
        entered.set()
        await release.wait()
        settled.set()
        return {}
    tool = tool_bundle(handler)
    async def act():
        await tool.handler({})
    async def interrupt():
        worker.stopped = True
        release.set()
    worker.action, worker.interrupt = act, interrupt
    driver = loop(worker, tmp_path)
    running = asyncio.create_task(driver.run('goal', acceptance_criteria='- criterion'))
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert settled.is_set() and worker.stopped
    assert driver.checkpoints.task['status'] != 'done'
    with RunReader(driver) as journal:
        assert journal.state['status'] == 'stopped'
        assert journal.state['uncertain']


async def test_adapter_failure_keeps_receipt_and_resume_does_not_rerun_worker(worker, tmp_path):
    calls = []
    async def checked(c):
        calls.append(1)
        if len(calls) == 1:
            c.prepare_boundary(c.boundary_eligibility()['receipt_id'])
            raise OSError('reset interrupted')
        token = c.prepare_boundary(c.boundary_eligibility()['receipt_id'])
        c.acknowledge_boundary(token['id'], generation=token['generation'], settled=True)
    worker.backend = SimpleNamespace(checked_task_boundary=checked, managed_action_receipts=True)
    driver = loop(worker, tmp_path)
    result = await driver.run('goal', acceptance_criteria='- criterion')
    assert result.status == 'error' and driver.checkpoints.task['status'] == 'done'
    resumed = await driver.run('goal', resume_run_id=result.run_id)
    assert resumed.status == 'done' and worker.calls == 1 and len(calls) == 2


async def test_stop_closes_admission_before_provider_cleanup(worker, tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls, refused = [], []
    async def handler(args):
        calls.append(args['step'])
        if args['step'] == 1:
            entered.set()
            await release.wait()
        return {}
    tool = tool_bundle(handler)
    async def act():
        await tool.handler({'step': 1})
        try:
            await tool.handler({'step': 2})
        except PermissionError:
            refused.append(True)
    async def interrupt():
        release.set()
    worker.action, worker.interrupt = act, interrupt
    driver = loop(worker, tmp_path)
    running = asyncio.create_task(driver.run('goal', acceptance_criteria='- criterion'))
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert calls == [1] and refused == [True]


async def test_managed_resume_done_revalidates_evidence_without_new_worker(worker, tmp_path):
    driver = loop(worker, tmp_path)
    result = await driver.run('goal', acceptance_criteria='- criterion')
    assert result.status == 'done'
    original = (driver.workspace / 'ledger.jsonl').read_bytes()
    assert (await driver.run('goal', resume_run_id=result.run_id)).status == 'done'
    assert (driver.workspace / 'ledger.jsonl').read_bytes() == original
    (driver.workspace / 'progress.md').write_text('post-completion drift')
    failed = await driver.run('goal', resume_run_id=result.run_id)
    assert failed.status == 'error' and worker.calls == 1


async def test_contract_generation_refuses_handler_before_any_effect(worker, tmp_path):
    effects = []
    async def handler(arguments):
        effects.append('untracked effect')
        return {'is_error': True, 'content': [{'type': 'text', 'text': 'failed after effect'}]}
    tool = tool_bundle(handler)
    denied = []
    async def ask(prompt):
        worker.calls += 1
        if worker.calls == 1:
            denied.append(await tool.handler({}))
            yield Event('assistant_done', '- criterion')
        else:
            yield Event('assistant_done', 'STATUS: DONE')
    worker.ask = ask
    driver = loop(worker, tmp_path)
    result = await driver.run('goal without a supplied contract')
    assert effects == []
    assert denied[0]['is_error'] and 'contract' in denied[0]['content'][0]['text'].lower()
    assert result.status == 'done'
    assert driver.checkpoints.unresolved_actions == {}


async def test_managed_sdk_refused_before_contract_or_worker_dispatch(worker, tmp_path):
    from dream.core.backends.anthropic import AnthropicBackend
    worker.backend = object.__new__(AnthropicBackend)
    result = await loop(worker, tmp_path).run('protected work')
    assert result.status == 'unverified'
    assert 'registered' in result.message.lower() and 'unsupported' in result.message.lower()
    assert worker.calls == 0 and result.workspace is None
    assert not (tmp_path / 'runs').exists()
