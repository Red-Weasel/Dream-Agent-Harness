"""Disposable journal I/O faults; no real disk fault, process, or provider calls."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core import run_state
from dream.core.backends.base import Event
from dream.core.loop import AutonomousLoop
from dream.core.providers import get_provider
from dream.core.run_state import RunState
from dream.telemetry.runtime import RunLimit


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Journal fixtures must not launch processes or connect to providers')
    monkeypatch.setattr(subprocess.Popen, '__init__', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


class AppendFault:
    """Fail one real disposable-file operation; later attempts would succeed."""
    def __init__(self, monkeypatch, root, stage, *, event=None, secondary_close=False):
        self.root = root.resolve()
        self.stage = stage
        self.event = event
        self.active = event is None
        self.secondary_close = secondary_close
        self.error = OSError(f'fixture {stage} failure')
        self.secondary_error = OSError('fixture secondary close failure')
        self.fired = False
        self.path = None
        self.bytes_at_failure = None
        self.opens = 0
        self.events = []
        self.descriptors = set()
        raw_open, raw_fdopen, raw_fsync, raw_close = os.open, os.fdopen, os.fsync, os.close
        raw_record = RunState.record
        fault = self

        def opened(path, flags, *args, **kwargs):
            path = Path(path)
            ledger = path.name == 'ledger.jsonl' and path.is_relative_to(self.root)
            if ledger:
                self.path = path
                self.opens += 1
                if self.active and stage == 'open' and not self.fired:
                    self.fail()
            fd = raw_open(path, flags, *args, **kwargs)
            if ledger:
                self.descriptors.add(fd)
            return fd

        class Stream:
            def __init__(self, stream):
                self.stream = stream
                self.fd = stream.fileno()

            def write(self, payload):
                if fault.active and not fault.fired:
                    if stage == 'write':
                        fault.fail()
                    if stage == 'zero':
                        fault.fired = True
                        fault.bytes_at_failure = fault.path.read_bytes()
                        return 0
                    if stage in ('partial', 'short'):
                        count = self.stream.write(payload[:len(payload) // 2])
                        self.stream.flush()
                        if stage == 'partial':
                            fault.fail()
                        fault.fired = True
                        fault.bytes_at_failure = fault.path.read_bytes()
                        return count
                return self.stream.write(payload)

            def flush(self):
                if fault.active and stage == 'flush' and not fault.fired:
                    fault.fail()
                return self.stream.flush()

            def fileno(self):
                return self.stream.fileno()

            def close(self):
                try:
                    self.stream.close()
                finally:
                    fault.descriptors.discard(self.fd)
                if fault.active and stage == 'close' and not fault.fired:
                    fault.fail()
                if fault.fired and fault.secondary_close:
                    raise fault.secondary_error

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        def fdopened(fd, *args, **kwargs):
            if fd in self.descriptors and self.active and stage == 'fdopen' and not self.fired:
                self.fail()
            stream = raw_fdopen(fd, *args, **kwargs)
            return Stream(stream) if fd in self.descriptors else stream

        def synced(fd):
            if fd in self.descriptors and self.active and stage == 'fsync' and not self.fired:
                self.fail()
            return raw_fsync(fd)

        def recorded(journal, name, **kwargs):
            self.events.append(name)
            if name == event:
                self.active = True
            return raw_record(journal, name, **kwargs)

        def closed(fd):
            try:
                return raw_close(fd)
            finally:
                self.descriptors.discard(fd)

        monkeypatch.setattr(run_state.os, 'close', closed)
        monkeypatch.setattr(run_state.os, 'open', opened)
        monkeypatch.setattr(run_state.os, 'fdopen', fdopened)
        monkeypatch.setattr(run_state.os, 'fsync', synced)
        monkeypatch.setattr(RunState, 'record', recorded)

    def fail(self):
        self.fired = True
        self.bytes_at_failure = self.path.read_bytes() if self.path.exists() else b''
        raise self.error


@pytest.mark.parametrize('stage', ['open', 'fdopen', 'write', 'partial', 'zero', 'flush', 'fsync', 'close'])
def test_failed_append_refuses_all_later_writes_and_retains_lock(tmp_path, monkeypatch, stage):
    journal = RunState(tmp_path)
    journal.record('created', phase='ready')
    fault = AppendFault(monkeypatch, tmp_path, stage)
    try:
        with pytest.raises(OSError) as first:
            journal.record('worker_started', phase='worker_running')
        after = (journal.path / 'ledger.jsonl').read_bytes()
        assert after == fault.bytes_at_failure
        assert not fault.descriptors
        assert journal.write_error is first.value
        if stage != 'zero':
            assert first.value is fault.error
        assert journal.seq == (2 if stage == 'close' else 1)
        assert journal.state['phase'] == ('worker_running' if stage == 'close' else 'ready')
        opens = fault.opens
        for _ in range(3):
            with pytest.raises(RuntimeError) as rejected:
                journal.record('must_not_append', phase='ready')
            assert rejected.value.__cause__ is first.value
        assert fault.opens == opens
        assert (journal.path / 'ledger.jsonl').read_bytes() == after
        with pytest.raises(BlockingIOError):
            RunState(tmp_path, journal.run_id)
    finally:
        journal.close()
    if stage == 'partial':
        with pytest.raises(ValueError):
            RunState(tmp_path, journal.run_id)
    else:
        recovered = RunState(tmp_path, journal.run_id)
        try:
            assert recovered.seq == (1 if stage in ('open', 'fdopen', 'write', 'zero') else 2)
        finally:
            recovered.close()



def test_known_short_write_progress_completes_one_record(tmp_path, monkeypatch):
    journal = RunState(tmp_path)
    journal.record('created', phase='ready')
    fault = AppendFault(monkeypatch, tmp_path, 'short')
    try:
        journal.record('worker_started', phase='worker_running', label='Unicode café')
        assert fault.fired and journal.seq == 2
        assert journal.write_error is None
        rows = [json.loads(line) for line in (journal.path / 'ledger.jsonl').read_text().splitlines()]
        assert [row['seq'] for row in rows] == [1, 2]
        assert rows[-1]['state']['label'] == 'Unicode café'
    finally:
        journal.close()

def test_cleanup_failure_does_not_replace_original_write_error(tmp_path, monkeypatch):
    journal = RunState(tmp_path)
    journal.record('created', phase='ready')
    fault = AppendFault(monkeypatch, tmp_path, 'write', secondary_close=True)
    try:
        with pytest.raises(OSError) as failed:
            journal.record('worker_started', phase='worker_running')
        assert failed.value is fault.error
        assert journal.write_error is fault.error
        assert (journal.path / 'ledger.jsonl').read_bytes() == fault.bytes_at_failure
    finally:
        journal.close()


@pytest.mark.parametrize('value', [object(), float('nan')])
def test_serialization_rejection_leaves_writer_usable(tmp_path, monkeypatch, value):
    journal = RunState(tmp_path)
    journal.record('created', phase='ready')
    fault = AppendFault(monkeypatch, tmp_path, 'fsync', event='never')
    try:
        before = (journal.path / 'ledger.jsonl').read_bytes()
        with pytest.raises((TypeError, ValueError)):
            journal.record('invalid', data={'value': value})
        assert fault.opens == 0 and journal.seq == 1
        assert journal.write_error is None
        assert (journal.path / 'ledger.jsonl').read_bytes() == before
        journal.record('valid', phase='worker_running')
        assert journal.seq == 2 and journal.write_error is None
    finally:
        journal.close()


@pytest.mark.parametrize('stage', ['snapshot_replace', 'snapshot_directory_sync'])
def test_snapshot_failure_keeps_committed_state_and_allows_next_record(tmp_path, monkeypatch, stage):
    journal = RunState(tmp_path)
    journal.record('created', phase='ready')
    original_replace, original_sync = os.replace, os.fsync
    error = OSError('fixture snapshot failure')
    fired = False

    def replace(source, target):
        nonlocal fired
        if stage == 'snapshot_replace' and not fired and Path(target) == journal.path / 'state.json':
            fired = True
            raise error
        return original_replace(source, target)

    def synced(fd):
        nonlocal fired
        if stage == 'snapshot_directory_sync' and not fired and stat.S_ISDIR(os.fstat(fd).st_mode):
            fired = True
            raise error
        return original_sync(fd)

    monkeypatch.setattr(run_state.os, 'replace', replace)
    monkeypatch.setattr(run_state.os, 'fsync', synced)
    try:
        with pytest.raises(OSError) as failed:
            journal.record('worker_started', phase='worker_running')
        assert failed.value is error and fired
        assert journal.write_error is None
        assert journal.seq == 2 and journal.state['phase'] == 'worker_running'
        journal.record('worker_finished', phase='ready')
        assert journal.seq == 3
        rows = [json.loads(line) for line in (journal.path / 'ledger.jsonl').read_text().splitlines()]
        assert [row['seq'] for row in rows] == [1, 2, 3]
        assert json.loads((journal.path / 'state.json').read_text()) == journal.state
    finally:
        journal.close()


class Worker:
    def __init__(self, workspace, *, failure=None, hold_cleanup=False, hanging=False):
        self.workspace = workspace
        self.provider = get_provider('machx')
        self.model = 'fixture-unused-model'
        self.prompts = []
        self.failure = failure
        self.hanging = hanging
        self.entered = asyncio.Event()
        self.interrupted = asyncio.Event()
        self.cleanup_entered = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        if not hold_cleanup:
            self.release_cleanup.set()
        self.closed = False
        self.ends = 0

    async def ask(self, prompt):
        self.prompts.append(prompt)
        self.entered.set()
        try:
            if self.hanging:
                await self.interrupted.wait()
            if self.failure:
                raise self.failure
            yield Event('tool_use', {'id': 'fixture-effect', 'name': 'fixture-only'})
            yield Event('assistant_done', 'STATUS: DONE')
        finally:
            self.cleanup_entered.set()
            await self.release_cleanup.wait()
            self.closed = True

    async def interrupt(self):
        self.interrupted.set()

    def begin_run_budget(self):
        pass

    def end_run_budget(self):
        self.ends += 1


def make_loop(worker, root):
    reviews = []
    class Reviewer:
        async def connect(self):
            pass
        async def disconnect(self):
            pass
        async def ask(self, prompt):
            reviews.append(prompt)
            yield Event('assistant_done', 'VERDICT: PASS\nGAPS: none')
    return AutonomousLoop(worker, state_dir=root / 'runs',
                          evaluator_backend_factory=lambda *args: Reviewer()), reviews


@pytest.mark.parametrize('event,worker_calls,review_calls', [
    ('created', 0, 0), ('contract_started', 0, 0), ('contract_established', 0, 0),
    ('worker_started', 0, 0), ('tool_use', 1, 0), ('worker_finished', 1, 0),
    ('evaluation', 1, 1), ('outcome', 1, 1), ('runtime_budget', 1, 1),
    ('error', 1, 0), ('runtime_budget_exhausted', 1, 0),
])
async def test_loop_reports_unpersisted_failure_without_later_appends(
    tmp_path, monkeypatch, event, worker_calls, review_calls,
):
    failure = RuntimeError('fixture worker failure') if event == 'error' else (
        RunLimit('fixture run limit') if event == 'runtime_budget_exhausted' else None)
    worker = Worker(tmp_path, failure=failure)
    if event == 'runtime_budget':
        worker.runtime_meter = SimpleNamespace(check=lambda: None, summary=lambda: {'fixture': 1})
    loop, reviews = make_loop(worker, tmp_path)
    fault = AppendFault(monkeypatch, tmp_path, 'fsync', event=event)
    result = await loop.run('fixture goal', acceptance_criteria='- inspect fixture')
    assert fault.fired and result.status == 'error'
    assert 'not durably recorded' in result.message
    assert 'writer was disabled' in result.message
    assert 'fixture fsync failure' in result.message
    assert result.run_id == loop._journal.run_id
    assert result.workspace == str(loop._journal.path)
    assert fault.events[-1] == event
    assert len(worker.prompts) == worker_calls and len(reviews) == review_calls
    assert worker.ends == 1 and not loop._active and loop._journal._lock is None
    assert not worker_calls or worker.closed
    assert (loop._journal.path / 'ledger.jsonl').read_bytes() == fault.bytes_at_failure
    rows = [json.loads(line) for line in fault.bytes_at_failure.decode().splitlines()]
    assert [row['seq'] for row in rows] == list(range(1, len(rows) + 1))
    recovered = RunState(tmp_path / 'runs', result.run_id)
    recovered.close()


async def test_failed_tool_record_retains_lock_until_worker_cleanup(tmp_path, monkeypatch):
    worker = Worker(tmp_path, hold_cleanup=True)
    loop, reviews = make_loop(worker, tmp_path)
    fault = AppendFault(monkeypatch, tmp_path, 'fsync', event='tool_use')
    task = asyncio.create_task(loop.run('fixture goal', acceptance_criteria='- inspect fixture'))
    try:
        await asyncio.wait_for(worker.cleanup_entered.wait(), 1)
        assert fault.fired and not task.done()
        with pytest.raises(BlockingIOError):
            RunState(tmp_path / 'runs', loop._journal.run_id)
        assert reviews == []
        worker.release_cleanup.set()
        result = await asyncio.wait_for(task, 1)
        assert result.status == 'error' and 'not durably recorded' in result.message
    finally:
        worker.release_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)
    assert worker.closed and loop._journal._lock is None
    assert fault.events[-1] == 'tool_use'


@pytest.mark.parametrize('failed_event', ['tool_use', 'interrupted'])
@pytest.mark.parametrize('error_type', [OSError, ValueError])
async def test_cancellation_preserves_storage_cause_without_recording_outcome(
    tmp_path, monkeypatch, failed_event, error_type,
):
    worker = Worker(tmp_path, hold_cleanup=True, hanging=failed_event == 'interrupted')
    loop, reviews = make_loop(worker, tmp_path)
    fault = AppendFault(monkeypatch, tmp_path, 'fsync', event=failed_event)
    fault.error = error_type('fixture append failure')
    task = asyncio.create_task(loop.run('fixture goal', acceptance_criteria='- inspect fixture'))
    try:
        if failed_event == 'tool_use':
            await asyncio.wait_for(worker.cleanup_entered.wait(), 1)
        else:
            await asyncio.wait_for(worker.entered.wait(), 1)
        task.cancel()
        await asyncio.wait_for(worker.cleanup_entered.wait(), 1)
        assert not task.done()
        with pytest.raises(BlockingIOError):
            RunState(tmp_path / 'runs', loop._journal.run_id)
        worker.release_cleanup.set()
        with pytest.raises(asyncio.CancelledError) as stopped:
            await asyncio.wait_for(task, 1)
        assert any('not durably recorded' in note and loop._journal.run_id in note
                   for note in getattr(stopped.value, '__notes__', []))
    finally:
        worker.release_cleanup.set()
        worker.interrupted.set()
        await asyncio.gather(task, return_exceptions=True)
    assert fault.fired and fault.events[-1] == failed_event
    assert loop._journal.write_error is fault.error
    assert worker.closed and worker.ends == 1 and reviews == []
    assert loop._journal._lock is None and not loop._active
    assert (loop._journal.path / 'ledger.jsonl').read_bytes() == fault.bytes_at_failure


@pytest.mark.parametrize('event', ['error', 'runtime_budget_exhausted'])
async def test_handler_preserves_non_os_append_failure(tmp_path, monkeypatch, event):
    worker_failure = RuntimeError('fixture worker error') if event == 'error' else RunLimit('fixture limit')
    worker = Worker(tmp_path, failure=worker_failure)
    loop, reviews = make_loop(worker, tmp_path)
    fault = AppendFault(monkeypatch, tmp_path, 'fsync', event=event)
    fault.error = ValueError('fixture append failure')
    result = await loop.run('fixture goal', acceptance_criteria='- inspect fixture')
    assert result.status == 'error' and 'not durably recorded' in result.message
    assert 'ValueError: fixture append failure' in result.message
    assert loop._journal.write_error is fault.error and fault.events[-1] == event
    assert reviews == [] and worker.closed and worker.ends == 1


async def test_loop_normal_success_keeps_durable_result(tmp_path):
    worker = Worker(tmp_path)
    loop, reviews = make_loop(worker, tmp_path)
    result = await loop.run('fixture goal', acceptance_criteria='- inspect fixture')
    assert result.status == 'done' and len(reviews) == 1 and worker.closed
    rows = [json.loads(line) for line in (loop.workspace / 'ledger.jsonl').read_text().splitlines()]
    assert [row['seq'] for row in rows] == list(range(1, len(rows) + 1))
    assert rows[-1]['event'] == 'outcome' and rows[-1]['state']['result'] == result.__dict__


async def test_first_snapshot_failure_reports_committed_run_identity(tmp_path, monkeypatch):
    worker = Worker(tmp_path)
    loop, reviews = make_loop(worker, tmp_path)
    error = OSError('fixture first snapshot failure')

    def snapshot_fails(*args):
        raise error

    monkeypatch.setattr(run_state, 'atomic_write', snapshot_fails)
    result = await loop.run('fixture goal', acceptance_criteria='- inspect fixture')
    assert result.status == 'error' and 'not durably recorded' in result.message
    assert 'Committed ledger records' in result.message and 'disabled' not in result.message
    assert result.run_id == loop._journal.run_id
    assert result.workspace == str(loop._journal.path)
    assert loop._journal.write_error is None and loop._journal.seq == 1
    assert worker.prompts == reviews == [] and worker.ends == 1
    assert loop._journal._lock is None
    rows = [json.loads(line) for line in (loop._journal.path / 'ledger.jsonl').read_text().splitlines()]
    assert [row['event'] for row in rows] == ['created']
