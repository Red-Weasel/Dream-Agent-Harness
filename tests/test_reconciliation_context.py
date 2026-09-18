"""Canonical recovery notes, actual disposable journals and fake model dispatch."""
import asyncio
import copy
from dataclasses import FrozenInstanceError
import json
import socket
import subprocess
from types import SimpleNamespace

import pytest

from dream.core import run_state as state_module
from dream.core.backends.base import Event
from dream.core.loop import AutonomousLoop
from dream.core.run_state import RunState, digest
from dream.telemetry.runtime import RunLimit
from test_durable_autonomy import Reviewer
from test_run_state_validation import BudgetWorker, checkpoint, rows, rewrite, contents, assert_unlocked
from test_run_state_write_failure import AppendFault


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Reconciliation fixtures cannot execute external operations')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(subprocess.Popen, '__init__', blocked)


def reports(prompt):
    marker = 'Run reconciliation caller reports (JSON):\n'
    assert prompt.count(marker) == 1
    return json.loads(prompt.split(marker, 1)[1])


def expected_reports(path, *, current=None):
    return [dict(run_id=path.name, seq=r['seq'], at=r['at'], text=r['data']['note'],
                 submission='current invocation' if r['seq'] == current else 'historical')
            for r in rows(path) if r['event'] == 'resume_requested' and r['data'].get('note', '').strip()]


def legacy_notes(path, texts):
    """Write old-format canonical events without depending on the new projection."""
    records = rows(path)
    for text in texts:
        record = copy.deepcopy(records[-1])
        record.update(seq=len(records) + 1, at='legacy recorded time', event='resume_requested', data={'note': text})
        records.append(record)
    rewrite(path, records)
    (path / 'state.json').write_text('damaged convenience snapshot')


class CapturedReviewer(Reviewer):
    def __init__(self, captured, response='VERDICT: PASS\nGAPS: none'):
        super().__init__(response)
        self.captured = captured

    async def ask(self, prompt):
        self.captured.append(prompt)
        async for event in super().ask(prompt):
            yield event


def test_legacy_projection_is_ordered_exact_readonly_and_does_not_duplicate_state(tmp_path):
    path = checkpoint(tmp_path)
    notes = ['  action1 completed\nDo not repeat.  ', 'Unrelated constraint', 'Unrelated constraint']
    legacy_notes(path, notes)
    before = contents(path)
    journal = RunState(path.parent, path.name)
    try:
        observed = journal.reconciliation_notes
        assert [(n.run_id, n.seq, n.at, n.text) for n in observed] == [
            (path.name, 2, 'legacy recorded time', notes[0]),
            (path.name, 3, 'legacy recorded time', notes[1]),
            (path.name, 4, 'legacy recorded time', notes[2])]
        assert isinstance(observed, tuple)
        with pytest.raises((FrozenInstanceError, AttributeError)):
            observed[0].text = 'rewrite'
        assert journal.reconciliation_notes == observed
        assert contents(path) == before
        assert 'reconciliation_notes' not in journal.state
    finally:
        journal.close()


@pytest.mark.parametrize('value', [None, False, 0, 1.5, [], {}])
def test_note_validation_rejects_direct_write_before_bytes_and_keeps_writer_usable(tmp_path, value):
    journal = RunState(tmp_path)
    journal.record('created', phase='ready')
    before = contents(journal.path)
    try:
        with pytest.raises(ValueError, match='note'):
            journal.record('resume_requested', data={'note': value})
        assert contents(journal.path) == before and journal.write_error is None and journal.seq == 1
        journal.record('resume_requested', data={'note': 'valid afterwards'})
        assert [n.text for n in journal.reconciliation_notes] == ['valid afterwards']
    finally:
        journal.close()


@pytest.mark.parametrize('value', [None, False, 0, 1.5, [], {}])
@pytest.mark.parametrize('valid_tail', [False, True])
async def test_malformed_legacy_note_rejects_before_hooks_and_preserves_bytes(tmp_path, value, valid_tail):
    path = checkpoint(tmp_path)
    records = rows(path)
    original = copy.deepcopy(records[0])
    records[0].update(event='resume_requested', data={'note': value})
    if valid_tail:
        original['seq'] = 2
        records.append(original)
    rewrite(path, records)
    before = contents(path)
    with pytest.raises(ValueError, match='note'):
        RunState(path.parent, path.name)
    worker = BudgetWorker(tmp_path)
    result = await AutonomousLoop(worker, state_dir=path.parent).run('goal', resume_run_id=path.name)
    assert result.status == 'error' and result.run_id == path.name
    assert worker.hooks == [] and worker.prompts == [] and contents(path) == before
    assert_unlocked(path)


def test_missing_blank_and_unrelated_notes_preserve_generic_journals(tmp_path):
    journal = RunState(tmp_path)
    try:
        for data in ({}, {'note': ''}, {'note': ' \n\t '}):
            journal.record('resume_requested', data=data, phase='ready')
        journal.record('unrelated_event', data={'note': {'opaque': True}})
        assert journal.reconciliation_notes == ()
        run_id = journal.run_id
    finally:
        journal.close()
    loaded = RunState(tmp_path, run_id)
    try:
        assert loaded.reconciliation_notes == ()
    finally:
        loaded.close()


@pytest.mark.parametrize('stage', ['open', 'write', 'partial', 'flush', 'fsync', 'close'])
def test_projection_advances_at_owned_fsync_boundary(tmp_path, monkeypatch, stage):
    journal = RunState(tmp_path)
    journal.record('created', phase='ready')
    fault = AppendFault(monkeypatch, tmp_path, stage, event='resume_requested')
    try:
        assert journal.reconciliation_notes == ()
        with pytest.raises(OSError):
            journal.record('resume_requested', data={'note': 'retained on commit only'})
        assert [n.text for n in journal.reconciliation_notes] == (['retained on commit only'] if stage == 'close' else [])
        assert journal.seq == (2 if stage == 'close' else 1)
        assert journal.write_error is not None
        with pytest.raises(RuntimeError, match='failed'):
            journal.record('must_not_append', data={'note': 'not accepted'})
        assert contents(journal.path)['ledger.jsonl'] == fault.bytes_at_failure
        run_id = journal.run_id
    finally:
        journal.close()
    if stage == 'partial':
        with pytest.raises(ValueError):
            RunState(tmp_path, run_id)
    else:
        loaded = RunState(tmp_path, run_id)
        try:
            # Surviving complete canonical records are authoritative on explicit reopen.
            assert [n.text for n in loaded.reconciliation_notes] == ([] if stage in ('open', 'write') else ['retained on commit only'])
        finally:
            loaded.close()


def test_projection_is_retained_before_snapshot_failure(tmp_path, monkeypatch):
    journal = RunState(tmp_path)
    journal.record('created', phase='ready')
    original_error = OSError('snapshot failed after fsync')
    def failed(*args):
        raise original_error
    with monkeypatch.context() as patch:
        patch.setattr(state_module, 'atomic_write', failed)
        with pytest.raises(OSError) as caught:
            journal.record('resume_requested', data={'note': 'exact committed note'})
    assert caught.value is original_error and journal.write_error is None
    assert [n.text for n in journal.reconciliation_notes] == ['exact committed note']
    journal.close()
    loaded = RunState(tmp_path, journal.run_id)
    try:
        assert [n.text for n in loaded.reconciliation_notes] == ['exact committed note']
    finally:
        loaded.close()


@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('note', [False, 0, 1.5, [], {}])
async def test_wrong_note_input_rejects_before_hooks_or_creation(tmp_path, resume, note):
    path = checkpoint(tmp_path) if resume else None
    before = contents(path) if path else None
    worker = BudgetWorker(tmp_path)
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs')
    result = await loop.run('goal', resume_run_id=path.name if path else None, resume_note=note)
    assert result.status == 'error' and result.run_id == (path.name if path else None)
    assert worker.hooks == [] and worker.prompts == []
    if path:
        assert contents(path) == before
        assert_unlocked(path)
    else:
        assert not (tmp_path / 'runs').exists()


async def test_fresh_meaningful_note_requires_run_identity(tmp_path):
    worker = BudgetWorker(tmp_path)
    result = await AutonomousLoop(worker, state_dir=tmp_path / 'runs').run('goal', resume_note='do not silently discard me')
    assert result.status == 'error' and worker.hooks == [] and worker.prompts == []
    assert not (tmp_path / 'runs').exists()


@pytest.mark.parametrize('blank', [None, '', ' \n '])
async def test_blank_note_is_absent_for_new_and_need_input_runs(tmp_path, blank):
    worker = BudgetWorker(tmp_path, ['STATUS: NEED_INPUT\nNEXT: clarify'])
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs')
    paused = await loop.run('goal', acceptance_criteria='- criterion', resume_note=blank)
    assert paused.status == 'need_input' and worker.hooks == ['begin', 'end']
    before = contents(loop.workspace)
    again = await loop.run('goal', resume_run_id=paused.run_id, resume_note=blank)
    assert again == paused and contents(loop.workspace) == before
    assert len(worker.prompts) == 1


async def test_contract_restart_delivers_reconciliation_before_synthetic_effect(tmp_path):
    effects = []
    class ContractWorker(BudgetWorker):
        async def ask(self, prompt):
            self.prompts.append(prompt)
            if 'before doing any work' in prompt:
                if 'action1 already completed' not in prompt:
                    effects.append('action1')
                if len(effects) == 1 and len(self.prompts) == 1:
                    raise RuntimeError('interrupted negotiation')
                yield Event('assistant_done', '- criterion')
            else:
                yield Event('assistant_done', 'STATUS: DONE')
    worker = ContractWorker(tmp_path)
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs', evaluate=False)
    first = await loop.run('goal')
    assert first.status == 'error' and effects == ['action1']
    note = 'action1 already completed; do not execute again'
    resumed = await loop.run('goal', resume_run_id=first.run_id, resume_note=note)
    assert resumed.status == 'unverified' and effects == ['action1']
    current = next(r['seq'] for r in rows(loop.workspace) if r['event'] == 'resume_requested')
    for prompt in worker.prompts[1:]:
        assert reports(prompt) == expected_reports(loop.workspace, current=current)


@pytest.mark.parametrize('failure', ['callback', 'before_turn_budget', 'snapshot'])
async def test_committed_note_survives_predispatch_failure_and_fresh_no_note_resume(tmp_path, monkeypatch, failure):
    path = checkpoint(tmp_path)
    note = '  NO REPEAT exact constraint\nkeep surrounding spaces  '
    worker = BudgetWorker(tmp_path)
    def callback(event):
        if event.kind == 'system' and 'loop iteration' in str(event.data):
            raise RuntimeError('callback before worker_started')
    if failure == 'before_turn_budget':
        class Limited(BudgetWorker):
            async def ask(self, prompt):
                raise RunLimit('before-turn refusal')
                yield
        worker = Limited(tmp_path)
    loop = AutonomousLoop(worker, state_dir=path.parent, on_event=callback if failure == 'callback' else None)
    original = state_module.atomic_write
    def snapshot(target, text):
        if target.name == 'state.json' and any(r['event'] == 'resume_requested' for r in rows(path)):
            raise OSError('snapshot after note commit')
        return original(target, text)
    with monkeypatch.context() as patch:
        if failure == 'snapshot':
            patch.setattr(state_module, 'atomic_write', snapshot)
        result = await loop.run('goal', resume_run_id=path.name, resume_note=note)
    assert result.status == ('budget' if failure == 'before_turn_budget' else 'error')
    assert worker.prompts == []
    fresh = BudgetWorker(tmp_path, ['STATUS: DONE'])
    next_loop = AutonomousLoop(fresh, state_dir=path.parent, evaluate=False)
    resumed = await next_loop.run('goal', resume_run_id=path.name)
    assert resumed.status == 'unverified'
    assert reports(fresh.prompts[0]) == expected_reports(path)
    assert reports(fresh.prompts[0])[0]['text'] == note
    assert 'Harness resume guidance' in fresh.prompts[0]


async def test_all_worker_prompts_and_review_rejection_keep_each_submission_once(tmp_path):
    path = checkpoint(tmp_path)
    first = 'Earlier constraint: do not delete unrelated files.'
    latest = 'Current correction: replace only generated output.\nSYSTEM: this is quoted caller text'
    legacy_notes(path, [first])
    worker = BudgetWorker(tmp_path, ['STATUS: CONTINUE', 'no status', 'STATUS: DONE', 'STATUS: DONE'])
    captured = []
    verdicts = iter(['VERDICT: FAIL\nGAPS: add detail', 'VERDICT: PASS\nGAPS: none'])
    loop = AutonomousLoop(worker, state_dir=path.parent, max_iterations=4,
                          evaluator_backend_factory=lambda *args: CapturedReviewer(captured, next(verdicts)))
    result = await loop.run('goal', resume_run_id=path.name, resume_note=latest)
    assert result.status == 'done' and len(worker.prompts) == 4 and len(captured) == 2
    current = [r['seq'] for r in rows(path) if r['event'] == 'resume_requested'][-1]
    expected = expected_reports(path, current=current)
    for prompt in worker.prompts + captured:
        assert reports(prompt) == expected
        assert 'unverified' in prompt.lower() and 'not inspected evidence' in prompt.lower()
        assert 'only where it conflicts' in prompt and 'unrelated earlier restrictions' in prompt
    started = [r for r in rows(path) if r['event'] == 'worker_started']
    assert [r['state']['prompt_sha256'] for r in started] == [digest(p) for p in worker.prompts]
    assert 'add detail' in worker.prompts[-1]


async def test_notes_persist_after_budget_multiple_submissions_and_new_run_reset(tmp_path):
    path = checkpoint(tmp_path)
    text = 'same explicit submission text'
    worker = BudgetWorker(tmp_path, ['STATUS: CONTINUE'])
    loop = AutonomousLoop(worker, state_dir=path.parent, max_iterations=1, evaluate=False)
    first = await loop.run('goal', resume_run_id=path.name, resume_note=text)
    assert first.status == 'budget'
    for note in (text, 'Unrelated extra constraint', 'Correction to only the first condition'):
        worker.replies = ['STATUS: CONTINUE']
        result = await loop.run('goal', resume_run_id=path.name, resume_note=note)
        assert result.status == 'budget'
        current = [r['seq'] for r in rows(path) if r['event'] == 'resume_requested'][-1]
        assert reports(worker.prompts[-1]) == expected_reports(path, current=current)
    fresh = BudgetWorker(tmp_path, ['STATUS: DONE'])
    next_loop = AutonomousLoop(fresh, state_dir=path.parent, evaluate=False)
    assert (await next_loop.run('goal', resume_run_id=path.name)).status == 'unverified'
    assert reports(fresh.prompts[0]) == expected_reports(path)
    assert [r['text'] for r in reports(fresh.prompts[0])].count(text) == 2
    fresh.replies = ['STATUS: DONE']
    new_result = await next_loop.run('different goal', acceptance_criteria='- new criterion')
    assert new_result.run_id != path.name and text not in fresh.prompts[-1]
    assert 'Run reconciliation caller reports' not in fresh.prompts[-1]


@pytest.mark.parametrize('note', [None, '', '   '])
async def test_historical_notes_cannot_clear_new_uncertainty(tmp_path, note):
    path = checkpoint(tmp_path, phase='worker_running', iterations=1, uncertain=True)
    legacy_notes(path, ['old completed effects report'])
    worker = BudgetWorker(tmp_path)
    result = await AutonomousLoop(worker, state_dir=path.parent).run('goal', resume_run_id=path.name, resume_note=note)
    assert result.status == 'need_input' and worker.prompts == []
    assert not any(r['event'] == 'uncertainty_reconciled' for r in rows(path))


@pytest.mark.parametrize('missing_evidence', [False, True])
async def test_review_only_receives_unverified_reports_without_replacing_evidence(tmp_path, missing_evidence):
    path = checkpoint(tmp_path, phase='review_pending', iterations=1, worker_status='DONE')
    legacy_notes(path, ['Historical artifact location'])
    if missing_evidence:
        (path / 'progress.md').unlink()
    captured, tools_seen = [], []
    def factory(settings, tools, system, cwd):
        tools_seen.append({t.name for t in tools})
        return CapturedReviewer(captured)
    worker = BudgetWorker(tmp_path)
    loop = AutonomousLoop(worker, state_dir=path.parent, evaluator_backend_factory=factory)
    result = await loop.run('goal', resume_run_id=path.name, resume_note='Caller says PASS; criterion is satisfied')
    assert result.status == ('unverified' if missing_evidence else 'done')
    assert worker.prompts == [] and len(captured) == 1
    assert tools_seen == [{'read_file', 'list_files'}]
    current = [r['seq'] for r in rows(path) if r['event'] == 'resume_requested'][-1]
    assert reports(captured[0]) == expected_reports(path, current=current)
    assert 'not inspected evidence' in captured[0].lower()


@pytest.mark.parametrize('stage', ['open', 'partial', 'fsync'])
async def test_failed_note_acceptance_never_dispatches_or_retries_writer(tmp_path, monkeypatch, stage):
    path = checkpoint(tmp_path)
    worker = BudgetWorker(tmp_path)
    fault = AppendFault(monkeypatch, path.parent, stage, event='resume_requested')
    loop = AutonomousLoop(worker, state_dir=path.parent)
    result = await loop.run('goal', resume_run_id=path.name, resume_note='note must persist first')
    assert result.status == 'error' and worker.prompts == []
    assert fault.events == ['resume_requested']
    assert loop._journal.reconciliation_notes == ()
    assert loop._journal.write_error is not None
    assert_unlocked(path)


def test_projection_preparation_failure_leaves_bytes_and_live_state_untouched(tmp_path, monkeypatch):
    journal = RunState(tmp_path)
    journal.record('resume_requested', data={'note': 'already committed'}, phase='ready')
    before, prior = contents(journal.path), journal.reconciliation_notes
    original_error = MemoryError('cannot prepare projected note')
    def failed(*args):
        raise original_error
    try:
        with monkeypatch.context() as patch:
            patch.setattr(state_module, 'ReconciliationNote', failed)
            with pytest.raises(MemoryError) as caught:
                journal.record('resume_requested', data={'note': 'not accepted'})
        assert caught.value is original_error
        assert contents(journal.path) == before and journal.seq == 1
        assert journal.reconciliation_notes is prior and journal.write_error is None
        journal.record('resume_requested', data={'note': 'subsequently accepted'})
        assert [n.text for n in journal.reconciliation_notes] == ['already committed', 'subsequently accepted']
    finally:
        journal.close()


async def test_contract_callback_failure_preserves_report_but_requires_fresh_reconciliation(tmp_path):
    path = checkpoint(tmp_path, phase='contract_needed', contract='')
    worker = BudgetWorker(tmp_path)
    def callback(event):
        if event.kind == 'system' and 'negotiating the contract' in str(event.data):
            raise RuntimeError('callback interrupted contract entry')
    loop = AutonomousLoop(worker, state_dir=path.parent, on_event=callback)
    first = await loop.run('goal', resume_run_id=path.name, resume_note='first explicit report')
    assert first.status == 'error' and worker.prompts == []
    loop.on_event = None
    assert (await loop.run('goal', resume_run_id=path.name)).status == 'need_input'
    assert worker.prompts == []
    worker.replies = ['- criterion', 'STATUS: NEED_INPUT\nNEXT: clarify']
    resumed = await loop.run('goal', resume_run_id=path.name, resume_note='Contract callback failed before any action')
    assert resumed.status == 'need_input' and len(worker.prompts) == 2
    current = [r['seq'] for r in rows(path) if r['event'] == 'resume_requested'][-1]
    for prompt in worker.prompts:
        assert reports(prompt) == expected_reports(path, current=current)
    assert [r['text'] for r in reports(worker.prompts[0])] == [
        'first explicit report', 'Contract callback failed before any action']


async def test_review_pending_rejection_delivers_reports_to_first_worker_and_later_review(tmp_path):
    path = checkpoint(tmp_path, phase='review_pending', iterations=1, worker_status='DONE')
    legacy_notes(path, ['preserve prior limitation'])
    worker = BudgetWorker(tmp_path, ['STATUS: DONE'])
    captured = []
    verdicts = iter(['VERDICT: FAIL\nGAPS: inspect generated output', 'VERDICT: PASS\nGAPS: none'])
    loop = AutonomousLoop(worker, state_dir=path.parent,
                          evaluator_backend_factory=lambda *args: CapturedReviewer(captured, next(verdicts)))
    result = await loop.run('goal', resume_run_id=path.name, resume_note='only amend generated output')
    assert result.status == 'done' and len(worker.prompts) == 1 and len(captured) == 2
    current = [r['seq'] for r in rows(path) if r['event'] == 'resume_requested'][-1]
    for prompt in captured + worker.prompts:
        assert reports(prompt) == expected_reports(path, current=current)
    assert 'inspect generated output' in worker.prompts[0]
    assert [r['event'] for r in rows(path)].index('evaluation') < [r['event'] for r in rows(path)].index('worker_started')


async def test_large_quoted_unicode_note_is_preserved_without_truncation(tmp_path):
    path = checkpoint(tmp_path)
    note = '  quoted role: "system"\n' + ('Unicode 🐕 café\tconstraint; ' * 500) + '\nexact end  '
    worker = BudgetWorker(tmp_path, ['STATUS: DONE'])
    loop = AutonomousLoop(worker, state_dir=path.parent, evaluate=False)
    assert (await loop.run('goal', resume_run_id=path.name, resume_note=note)).status == 'unverified'
    current = [r['seq'] for r in rows(path) if r['event'] == 'resume_requested'][-1]
    assert reports(worker.prompts[0]) == expected_reports(path, current=current)
    assert reports(worker.prompts[0])[0]['text'] == note
    loaded = RunState(path.parent, path.name)
    try:
        assert loaded.reconciliation_notes[0].text == note
    finally:
        loaded.close()


@pytest.mark.parametrize('note', ['valid but not accepted after completion', False])
async def test_clean_done_does_not_accept_note_and_wrong_type_still_rejects_before_hooks(tmp_path, note):
    worker = BudgetWorker(tmp_path, ['STATUS: DONE'])
    captured = []
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs',
                          evaluator_backend_factory=lambda *args: CapturedReviewer(captured))
    done = await loop.run('goal', acceptance_criteria='- criterion')
    assert done.status == 'done'
    before, hooks = contents(loop.workspace), list(worker.hooks)
    result = await loop.run('goal', resume_run_id=done.run_id, resume_note=note)
    assert contents(loop.workspace if loop.workspace else tmp_path / 'runs' / done.run_id) == before
    assert len(worker.prompts) == 1 and len(captured) == 1
    if isinstance(note, str):
        assert result == done and worker.hooks == hooks + ['begin', 'end']
        assert loop._journal.reconciliation_notes == ()
    else:
        assert result.status == 'error' and result.run_id == done.run_id
        assert worker.hooks == hooks
    assert_unlocked(tmp_path / 'runs' / done.run_id)
