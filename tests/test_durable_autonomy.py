"""Durable loop/provider boundary fixtures. No network, model, or Engine startup."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dream.core import loop as loop_module
from dream.core.backends.base import Event
from dream.core.evaluator import IsolatedOpenAIBackend, ReviewSettings, ScopedReader, collect_review, review_backend
from dream.core.loop import AutonomousLoop
from dream.core.providers import get_provider
from dream.core.run_state import RunState, atomic_write


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for key in ('DREAM_EVALUATOR_PROVIDER', 'DREAM_EVALUATOR_MODEL', 'DREAM_EVALUATOR_TIMEOUT'):
        monkeypatch.delenv(key, raising=False)


class Worker:
    def __init__(self, workspace, replies=()):
        self.workspace = workspace
        self.provider = get_provider('machx')
        self.model = 'fixture-local-model'
        self.replies = list(replies)
        self.prompts = []
        self.interrupted = False

    async def ask(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if callable(reply):
            async for event in reply():
                yield event
        else:
            yield Event('assistant_done', reply)

    async def interrupt(self):
        self.interrupted = True


class Reviewer:
    def __init__(self, response='VERDICT: PASS\nGAPS: none', error=None):
        self.response, self.error = response, error
        self.disconnected = False

    async def connect(self):
        pass

    async def ask(self, prompt):
        if self.error:
            yield Event('error', self.error)
        yield Event('assistant_done', self.response)

    async def disconnect(self):
        self.disconnected = True


def build(worker, root, **kwargs):
    return AutonomousLoop(worker, state_dir=root / 'runs', **kwargs)


def records(root, result):
    return [json.loads(line) for line in (root / 'runs' / result.run_id / 'ledger.jsonl').read_text().splitlines()]


def test_provider_defaults_and_explicit_cross_provider_models(tmp_path, monkeypatch):
    worker = Worker(tmp_path)
    settings = ReviewSettings.resolve(worker)
    assert settings.provider.key == 'machx'
    assert settings.model == worker.model
    monkeypatch.setenv('DREAM_EVALUATOR_PROVIDER', 'xai')
    monkeypatch.setenv('DREAM_EVALUATOR_MODEL', 'configured-review-model')
    assert ReviewSettings.resolve(worker).model == 'configured-review-model'
    selected = ReviewSettings.resolve(worker, provider='openai', model='explicit')
    assert (selected.provider.key, selected.model) == ('openai', 'explicit')
    monkeypatch.delenv('DREAM_EVALUATOR_MODEL')
    assert ReviewSettings.resolve(worker).model == get_provider('xai').default_model
    assert ReviewSettings.resolve(worker).model != worker.model


async def test_local_http_reviewer_reads_worker_artifact_without_global_context(tmp_path, monkeypatch):
    from dream.tools import context
    sentinel = object()
    monkeypatch.setattr(context, '_CTX', sentinel)
    worker_dir = tmp_path / 'project'
    worker_dir.mkdir()
    artifact = worker_dir / 'result.txt'
    artifact.write_text('fixture acceptance evidence')
    requests, created = [], []

    def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            delta = {'tool_calls': [{'index': 0, 'id': 'read-artifact', 'type': 'function', 'function': {'name': 'read_file', 'arguments': json.dumps({'path': str(artifact)})}}]}
        else:
            assert 'fixture acceptance evidence' in payload['messages'][-1]['content']
            delta = {'content': 'VERDICT: PASS\nGAPS: none'}
        body = 'data: ' + json.dumps({'choices': [{'delta': delta}]}) + '\n\ndata: [DONE]\n\n'
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, text=body)

    class FixtureHTTP(IsolatedOpenAIBackend):
        async def connect(self):
            self._client = httpx.AsyncClient(base_url='http://fixture.invalid/v1', transport=httpx.MockTransport(transport))

    def factory(settings, tools, system, cwd):
        assert settings.provider.key == 'machx' and settings.model == 'fixture-local-model'
        assert cwd == worker_dir
        backend = FixtureHTTP(provider=settings.provider, model=settings.model, system_prompt=system, tools=tools, permission_cb=None)
        created.append(backend)
        return backend

    worker = Worker(worker_dir, ['STATUS: DONE'])
    loop = build(worker, tmp_path, evaluator_backend_factory=factory)
    result = await loop.run('produce evidence', acceptance_criteria='- result.txt contains fixture acceptance evidence')
    assert result.status == 'done'
    assert loop.workspace != worker_dir and loop.worker_workspace == worker_dir
    assert str(worker_dir) in worker.prompts[0] and str(loop.workspace) in worker.prompts[0]
    assert context._CTX is sentinel
    assert len(requests) == 2
    assert {tool['function']['name'] for tool in requests[0]['tools']} == {'read_file', 'list_files'}
    assert str(artifact) in loop.last_review['inspected']
    assert (await created[0]._exec_tool('remember', {}))[1]
    assert created[0]._note_elided() is None and created[0]._tool_uses() is None
    assert records(tmp_path, result)[-1]['state']['status'] == 'done'


async def test_unavailable_review_resume_rechecks_without_replaying_worker(tmp_path):
    worker = Worker(tmp_path, ['STATUS: DONE'])
    failing = build(worker, tmp_path, evaluator_backend_factory=lambda *a: Reviewer(error='provider unavailable'))
    result = await failing.run('goal', acceptance_criteria='- fixture criterion')
    assert result.status == 'unverified'
    assert records(tmp_path, result)[-1]['state']['phase'] == 'review_pending'
    fresh_worker = Worker(tmp_path)
    passing = build(fresh_worker, tmp_path, evaluator_backend_factory=lambda *a: Reviewer())
    resumed = await passing.run('goal', resume_run_id=result.run_id)
    assert resumed.status == 'done' and resumed.run_id == result.run_id
    assert resumed.iterations == 1 and not fresh_worker.prompts
    before = (passing.workspace / 'ledger.jsonl').read_bytes()
    again = await passing.run('goal', resume_run_id=resumed.run_id)
    assert again == resumed
    assert (passing.workspace / 'ledger.jsonl').read_bytes() == before


async def test_interrupt_after_side_effect_requires_reconciliation(tmp_path):
    reached = asyncio.Event()
    stopped = asyncio.Event()
    settled = asyncio.Event()
    class InterruptibleWorker(Worker):
        async def interrupt(self):
            await super().interrupt()
            stopped.set()

    effects = tmp_path / 'effect.txt'
    async def act():
        yield Event('tool_use', {'id': 'action1', 'name': 'write_file', 'input': {'path': str(effects)}})
        effects.write_text('executed once')
        yield Event('tool_result', {'id': 'action1', 'name': 'write_file', 'content': 'saved'})
        reached.set()
        try:
            await stopped.wait()
        finally:
            settled.set()
    worker = InterruptibleWorker(tmp_path, [act])
    loop = build(worker, tmp_path)
    task = asyncio.create_task(loop.run('goal', acceptance_criteria='- create effect.txt once'))
    await reached.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.interrupted
    assert settled.is_set()
    run_id = loop._journal.run_id
    ledger = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
    assert any(r['event'] == 'tool_result' for r in ledger)
    assert ledger[-1]['state']['uncertain']
    recovery_worker = Worker(tmp_path, ['STATUS: DONE'])
    recovery = build(recovery_worker, tmp_path, evaluator_backend_factory=lambda *a: Reviewer())
    paused = await recovery.run('goal', resume_run_id=run_id)
    assert paused.status == 'need_input' and not recovery_worker.prompts
    assert effects.read_text() == 'executed once'
    completed = await recovery.run('goal', resume_run_id=run_id, resume_note='Inspected effect.txt: action1 completed. Do not execute it again; verify only.')
    assert completed.status == 'done'
    assert 'action1 completed' in recovery_worker.prompts[0]
    assert effects.read_text() == 'executed once'


async def test_explicit_budget_resume_uses_ledger_not_stale_snapshot(tmp_path):
    worker = Worker(tmp_path, ['STATUS: CONTINUE\nNEXT: finish'])
    loop = build(worker, tmp_path, max_iterations=1)
    result = await loop.run('goal', acceptance_criteria='- fixture criterion')
    assert result.status == 'budget'
    (loop.workspace / 'state.json').write_text('{corrupt convenience snapshot')
    other = build(Worker(tmp_path, ['STATUS: DONE']), tmp_path, evaluator_backend_factory=lambda *a: Reviewer())
    resumed = await other.run('goal', resume_run_id=result.run_id)
    assert resumed.status == 'done' and resumed.iterations == 2


async def test_resume_rejects_wrong_workspace_and_goal_without_changes(tmp_path):
    loop = build(Worker(tmp_path, ['STATUS: NEED_INPUT']), tmp_path)
    result = await loop.run('goal', acceptance_criteria='- fixture criterion')
    before = (loop.workspace / 'ledger.jsonl').read_bytes()
    other = build(Worker(tmp_path / 'other'), tmp_path)
    assert (await other.run('goal', resume_run_id=result.run_id)).status == 'error'
    assert (await loop.run('different', resume_run_id=result.run_id)).status == 'error'
    assert (loop.workspace / 'ledger.jsonl').read_bytes() == before


async def test_contract_change_cannot_make_worker_pass(tmp_path):
    loop = build(Worker(tmp_path), tmp_path, evaluator_backend_factory=lambda *a: Reviewer())
    async def replace_contract():
        (loop.workspace / 'contract.md').write_text('- easier criterion')
        yield Event('assistant_done', 'STATUS: DONE')
    loop.engine.replies = [replace_contract]
    result = await loop.run('goal', acceptance_criteria='- original criterion')
    assert result.status == 'unverified'
    assert 'contract changed' in result.message


async def test_evaluation_disabled_is_honestly_unverified(tmp_path):
    result = await build(Worker(tmp_path, ['STATUS: DONE']), tmp_path, evaluate=False).run('goal', acceptance_criteria='- fixture')
    assert result.status == 'unverified'


async def test_reviewer_error_after_pass_is_not_a_pass(tmp_path):
    class Partial(Reviewer):
        async def ask(self, prompt):
            yield Event('assistant_done', self.response)
            yield Event('result', {'is_error': True})
    backend = Partial()
    with pytest.raises(RuntimeError):
        await collect_review(backend, 'review', 1)
    assert backend.disconnected


async def test_reviewer_timeout_disconnects():
    class Hung(Reviewer):
        async def connect(self):
            await asyncio.Event().wait()
    backend = Hung()
    with pytest.raises(TimeoutError):
        await collect_review(backend, 'review', 0.01)
    assert backend.disconnected


async def test_artifact_changes_during_review_are_unverified(tmp_path):
    loop = build(Worker(tmp_path, ['STATUS: DONE']), tmp_path)
    class Changing(Reviewer):
        async def ask(self, prompt):
            (loop.workspace / 'progress.md').write_text('changed after snapshot')
            yield Event('assistant_done', self.response)
    loop.evaluator_backend_factory = lambda *a: Changing()
    result = await loop.run('goal', acceptance_criteria='- fixture')
    assert result.status == 'unverified'


def test_reader_denies_outside_and_symlink_reads(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'secret.txt'
    outside.write_text('do not disclose')
    (workspace / 'link').symlink_to(outside)
    (workspace / 'dirlink').symlink_to(tmp_path, target_is_directory=True)
    reader = ScopedReader(workspace)
    for path in ('../secret.txt', 'link', 'dirlink/secret.txt'):
        with pytest.raises((OSError, ValueError)):
            reader.read(path)


def test_run_lock_unique_ids_and_truncated_ledger_fail_closed(tmp_path):
    first = RunState(tmp_path)
    first.record('created', phase='worker_running')
    with pytest.raises(BlockingIOError):
        RunState(tmp_path, first.run_id)
    second = RunState(tmp_path)
    assert second.run_id != first.run_id
    second.close()
    first.close()
    with (first.path / 'ledger.jsonl').open('a') as stream:
        stream.write('{"seq":2')
    with pytest.raises(ValueError):
        RunState(tmp_path, first.run_id)


def test_cli_review_does_not_fallback(tmp_path):
    from dream.core.cli_review import CLIReviewBackend
    settings = ReviewSettings(get_provider('codex'), 'explicit-model')
    backend = review_backend(settings, [], 'review', tmp_path)
    assert isinstance(backend, CLIReviewBackend)
    assert backend.consultation.provider.key == 'codex'
    assert backend.consultation.model == 'explicit-model'


def test_conflicting_or_quoted_verdict_cannot_pass():
    from dream.core.loop import _parse_verdict
    assert _parse_verdict('VERDICT: PASS\nGAPS: none\nVERDICT: FAIL\nGAPS: criterion missing')[0] == 'FAIL'
    assert _parse_verdict('The worker said VERDICT: PASS, but there is no evidence.')[0] == 'FAIL'
    assert _parse_verdict('VERDICT: PASS\nGAPS: missing artifact')[0] == 'FAIL'


def test_closed_writer_cannot_append_after_another_driver_resumes(tmp_path):
    stale = RunState(tmp_path)
    stale.record('created', phase='ready')
    stale.close()
    active = RunState(tmp_path, stale.run_id)
    try:
        before = (active.path / 'ledger.jsonl').read_bytes()
        with pytest.raises(RuntimeError, match='closed'):
            stale.record('bad-stale-write', phase='worker_running')
        assert (active.path / 'ledger.jsonl').read_bytes() == before
        active.record('resumed', phase='review_pending')
    finally:
        active.close()


async def test_rejected_resume_contract_override_does_not_clobber_run(tmp_path):
    loop = build(Worker(tmp_path, ['STATUS: DONE']), tmp_path, evaluator_backend_factory=lambda *a: Reviewer())
    result = await loop.run('goal', acceptance_criteria='- original criterion')
    before = (loop.workspace / 'ledger.jsonl').read_bytes()
    rejected = await loop.run('goal', resume_run_id=result.run_id, acceptance_criteria='- replacement criterion')
    assert rejected.status == 'error'
    assert (loop.workspace / 'ledger.jsonl').read_bytes() == before
    assert (await loop.run('goal', resume_run_id=result.run_id)).status == 'done'


def test_committed_ledger_survives_atomic_snapshot_failure(tmp_path, monkeypatch):
    from dream.core import run_state
    journal = RunState(tmp_path)
    journal.record('created', phase='ready')
    def fail(*args):
        raise OSError('fixture snapshot write failure')
    monkeypatch.setattr(run_state, 'atomic_write', fail)
    with pytest.raises(OSError):
        journal.record('worker_started', phase='worker_running')
    journal.close()
    recovered = RunState(tmp_path, journal.run_id)
    assert recovered.state['phase'] == 'worker_running'
    assert recovered.seq == 2
    recovered.close()
