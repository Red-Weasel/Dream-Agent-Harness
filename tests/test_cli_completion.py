"""Terminal evidence through real CLI parsing, cleanup, Engine timing and Loop.

Only process I/O and the independent reviewer are fake; no installed CLI runs.
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
from collections import deque

import pytest

from dream.core.backends import cli_agent
from dream.core.backends.base import Event
from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter
from dream.core.loop import AutonomousLoop
from test_cli_exit_reconciliation import deny_live, engine


def _record(obj):
    return (json.dumps(obj) + '\n').encode()


_PARTIAL = _record({'type': 'item.completed', 'item': {
    'type': 'agent_message', 'text': 'STATUS: DONE\nNEXT: inspect the existing artifact',
}})
_SUCCESS = _record({'type': 'turn.completed', 'usage': {
    'input_tokens': 11, 'output_tokens': 7,
}})
_SESSION = _record({'type': 'thread.started', 'thread_id': 'fixture-thread'})


class _Chunks:
    def __init__(self, chunks, *, eof=True):
        self.chunks = deque(chunks)
        self.eof = eof
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()

    async def read(self, limit):
        await asyncio.sleep(0)
        if self.chunks:
            chunk = self.chunks.popleft()
            if len(chunk) > limit:
                self.chunks.appendleft(chunk[limit:])
            return chunk[:limit]
        if not self.eof:
            self.blocked.set()
            await self.release.wait()
        return b''


class _Process:
    def __init__(self, chunks, *, rc=0, stderr=b'', eof=True, hold_cleanup=False):
        self.stdout = _Chunks(chunks, eof=eof)
        self.stderr = _Chunks([stderr] if stderr else [])
        self.returncode = None
        self.rc = rc
        self.wait_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        if not hold_cleanup:
            self.release_cleanup.set()
        self.cleaned = False
        self.terminations = 0
        self.kills = 0

    async def wait(self):
        self.wait_started.set()
        await self.release_cleanup.wait()
        self.returncode = self.rc
        self.cleaned = True
        return self.rc

    def terminate(self):
        self.terminations += 1
        self.rc = -15
        self.stdout.release.set()
        self.release_cleanup.set()

    def kill(self):
        self.kills += 1
        self.rc = -9
        self.stdout.release.set()
        self.release_cleanup.set()


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    pending = deque()
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError('Completion fixtures must not launch processes or connect to a provider')

    monkeypatch.setattr(subprocess.Popen, '__init__', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(cli_agent, 'supervised_command', lambda argv: argv)

    async def spawn(*argv, **kwargs):
        assert argv[0] == '/fixture/no-real-cli'
        assert kwargs['start_new_session'] is True
        calls.append(argv)
        return pending.popleft()

    monkeypatch.setattr(cli_agent.asyncio, 'create_subprocess_exec', spawn)

    def make(chunks, *, adapter=None, idle_timeout=1, **kwargs):
        proc = _Process(chunks, **kwargs)
        pending.append(proc)
        adapter = adapter or CodexAdapter()
        adapter.cmd = '/fixture/no-real-cli'
        backend = CliAgentBackend(adapter, system_prompt='fixture system', cwd=str(tmp_path),
                                  idle_timeout=idle_timeout)
        return backend, proc

    yield make, calls
    assert not pending, 'Every declared fake process must be consumed'


@pytest.mark.parametrize('wire,preserved', [
    (b'', []),
    (_SESSION + _PARTIAL, ['assistant_done']),
    (b'{broken json\n[]\nnull\n', []),
    (_record({'type': 'turn.failed', 'error': {'message': 'fixture failure'}}), []),
    (_record({'type': 'unrecognized.provider.event'}), []),
    (_record({'type': 'item.started', 'item': {
        'type': 'command_execution', 'id': 'tool-1', 'command': 'fixture-only',
    }}) + _record({'type': 'item.completed', 'item': {
        'type': 'command_execution', 'id': 'tool-1', 'aggregated_output': 'partial evidence',
        'exit_code': 0,
    }}) + _PARTIAL, ['tool_use', 'tool_result', 'assistant_done']),
])
async def test_zero_exit_without_terminal_evidence_is_error(fake_cli, wire, preserved):
    make, calls = fake_cli
    backend, proc = make([wire] if wire else [])
    events = []
    async for event in backend.ask('fixture request'):
        if event.kind == 'error':
            assert proc.cleaned and backend._proc is None
        events.append(event)
    assert [event.kind for event in events] == preserved + ['error']
    assert 'terminal result' in events[-1].data.lower()
    assert 'inspect' in events[-1].data.lower()
    assert len(calls) == 1
    assert proc.terminations == proc.kills == 0
    if wire.startswith(_SESSION):
        assert backend._session_id == 'fixture-thread'
        assert events[0].data.startswith('STATUS: DONE')


@pytest.mark.parametrize('chunk_size,newline', [(1, True), (7, True), (19, False)])
async def test_split_success_preserves_text_usage_and_session(fake_cli, chunk_size, newline):
    make, calls = fake_cli
    wire = _SESSION + _PARTIAL + (_SUCCESS if newline else _SUCCESS.rstrip(b'\n'))
    backend, proc = make([wire[i:i + chunk_size] for i in range(0, len(wire), chunk_size)])
    events = [event async for event in backend.ask('fixture request')]
    assert [event.kind for event in events] == ['assistant_done', 'result']
    assert events[-1].data['is_error'] is False
    assert events[-1].data['usage']['prompt_tokens'] == 11
    assert events[-1].data['usage']['completion_tokens'] == 7
    assert backend._session_id == 'fixture-thread'
    assert proc.cleaned and backend._proc is None and len(calls) == 1


@pytest.mark.parametrize('is_error', [False, True])
async def test_translated_result_keeps_its_explicit_outcome(fake_cli, is_error):
    class ResultAdapter(CodexAdapter):
        def translate(self, obj, state):
            return [Event('result', {'is_error': obj['is_error'], 'subtype': 'fixture'})]

    make, calls = fake_cli
    backend, proc = make([_record({'is_error': is_error})], adapter=ResultAdapter())
    events = [event async for event in backend.ask('fixture request')]
    assert [(event.kind, event.data['is_error']) for event in events] == [('result', is_error)]
    assert proc.cleaned and len(calls) == 1


@pytest.mark.parametrize('stderr', [b'fixture failure detail', b'old-prefix' + b'x' * 70000 + b'last-diagnostic'])
async def test_nonzero_without_result_retains_bounded_stderr(fake_cli, stderr):
    make, calls = fake_cli
    backend, proc = make([_PARTIAL], rc=3, stderr=stderr)
    events = [event async for event in backend.ask('fixture request')]
    assert [event.kind for event in events] == ['assistant_done', 'error']
    assert events[-1].data == 'ChatGPT · Codex exited 3: ' + stderr.decode()[-800:]
    assert proc.cleaned and len(calls) == 1


async def test_missing_result_error_waits_for_owned_cleanup(fake_cli):
    make, calls = fake_cli
    backend, proc = make([_PARTIAL], hold_cleanup=True)
    delivered = []

    async def collect():
        async for event in backend.ask('fixture request'):
            delivered.append(event)

    task = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(proc.wait_started.wait(), 1)
        assert [event.kind for event in delivered] == ['assistant_done']
        assert backend._proc is proc and not proc.cleaned
        proc.release_cleanup.set()
        await asyncio.wait_for(task, 1)
    finally:
        proc.release_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)
    assert [event.kind for event in delivered] == ['assistant_done', 'error']
    assert backend._proc is None and proc.cleaned and len(calls) == 1


async def test_idle_timeout_preserves_its_specific_failure(fake_cli):
    make, calls = fake_cli
    backend, proc = make([], eof=False, idle_timeout=.01)
    events = [event async for event in backend.ask('fixture request')]
    assert [event.kind for event in events] == ['error']
    assert 'idle timeout' in events[0].data
    assert 'terminal result' not in events[0].data
    assert proc.cleaned and proc.terminations == 1 and len(calls) == 1


async def test_cancellation_cleans_owned_process_without_completion(fake_cli):
    make, calls = fake_cli
    backend, proc = make([], eof=False)
    delivered = []

    async def collect():
        async for event in backend.ask('fixture request'):
            delivered.append(event)

    task = asyncio.create_task(collect())
    await asyncio.wait_for(proc.stdout.blocked.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert delivered == []
    assert proc.cleaned and proc.terminations == 1 and len(calls) == 1
    assert backend._proc is None


@pytest.mark.parametrize('terminal,expected_status,expected_review', [
    (False, 'error', 0), (True, 'done', 1),
])
async def test_loop_requires_cli_terminal_evidence_before_review(
    fake_cli, tmp_path, engine, terminal, expected_status, expected_review,
):
    make, calls = fake_cli
    backend, proc = make([_SESSION + _PARTIAL + (_SUCCESS if terminal else b'')])

    engine.backend = backend
    reviews = []

    class Reviewer:
        async def connect(self):
            pass

        async def disconnect(self):
            pass

        async def ask(self, prompt):
            reviews.append(prompt)
            yield Event('assistant_done', 'VERDICT: PASS\nGAPS: none')

    loop = AutonomousLoop(engine, state_dir=tmp_path / 'runs',
                          evaluator_backend_factory=lambda *args: Reviewer())
    result = await loop.run('fixture goal', acceptance_criteria='- inspect the existing artifact')
    ledger = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
    assert result.status == expected_status
    assert len(reviews) == expected_review
    assert any(record['event'] == 'worker_finished' for record in ledger) is terminal
    assert ledger[-1]['state']['uncertain'] is (not terminal)
    # Closing after an observed protocol error preserves that error; it does
    # not turn the missing-terminal failure into a cancellation.
    assert engine.turn_timing.summary()['outcome'] == ('completed' if terminal else 'error')
    assert engine.turn_timing.summary()['configuration']['turn'] == 1
    assert proc.cleaned and backend._proc is None and len(calls) == 1
    if not terminal:
        blocked = await loop.run('fixture goal', resume_run_id=result.run_id)
        assert blocked.status == 'need_input' and len(calls) == 1 and reviews == []
