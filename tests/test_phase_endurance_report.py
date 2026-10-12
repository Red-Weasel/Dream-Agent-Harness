"""Read-only schedule evidence from actual synthetic managed phase workflows."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from dream.core import endurance_report
from dream.core.run_state import RunState
from test_managed_loop import worker
from test_responses_phase_workflow import workflow, children


def report_api():
    value = getattr(endurance_report, 'build_phase_endurance_report', None)
    assert callable(value), 'Schedule-aware endurance report API is absent.'
    return value


def test_schedule_aware_report_api_is_explicit():
    report_api()


async def completed(worker, tmp_path, *, phases=1, mode='pass'):
    report_api()
    runner, seen, inference = workflow(worker, tmp_path, phases=phases, mode=mode)
    result = await runner.run()
    if mode == 'pass': assert result.status == 'done' and result.completed_phases == phases
    else: assert result.status != 'done'
    schedule, states = children(runner, result)
    return runner, result, schedule, states, seen, inference


def report(runner, result, worker, **kwargs):
    return report_api()(runner.root, result.schedule_id, task_lookup=worker.tasks.get, **kwargs)


def assert_unqualified(value):
    assert value['independently_verified_build'] is None
    assert value['metrics']['cost'] is None and value['metrics']['provider_total_tokens'] is None
    assert [gate['hours'] for gate in value['gates']] == [1, 4, 12]
    assert all(gate['qualified'] is False for gate in value['gates'])


def snapshot(runner, result, states):
    folders = [runner.root / result.schedule_id] + [runner.children / state['run_id'] for state in states]
    return {str(path): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for folder in folders for path in folder.iterdir() if path.is_file()}


def alter_child(runner, state, name, value):
    journal = RunState(runner.children, state['run_id'])
    try: journal.record('fixture_evidence_changed', incremental=True, **{name: value})
    finally: journal.close()


def rejected_or_unaccepted(runner, result, worker):
    try: value = report(runner, result, worker)
    except (ValueError, RuntimeError, OSError): return
    assert value['accepted_work_count'] == 0
    assert_unqualified(value)


async def test_two_actual_checked_phases_join_parent_receipts_and_advisor_subset(worker, tmp_path):
    runner, result, schedule, states, seen, inference = await completed(worker, tmp_path, phases=2)
    before = snapshot(runner, result, states)
    counts = (len(seen.calls), len(seen.changed), len(seen.reviews), len(seen.checks), len(inference.requests))
    value = report(runner, result, worker)
    assert value['accepted_work_count'] == 2
    assert value['schedule']['schedule_id'] == result.schedule_id
    assert value['schedule']['manifest_sha256'] == schedule['manifest_sha256']
    assert value['recorded_runtime_sources'] == schedule['manifest']['runtime_sources']
    assert {row['run_id'] for row in value['runs']} == {state['run_id'] for state in states}
    assert all(row['completion']['accepted'] and row['completion']['task_projection'] == 'linked' for row in value['runs'])
    assert all(row['host_checks'] == {'passed': 1, 'failed': 0} for row in value['runs'])
    usage = value['advisor_usage']
    assert usage['requests'] == 2 and usage['input_tokens'] == 74 and usage['output_tokens'] == 16
    assert isinstance(usage['coverage'], str) and usage['coverage']
    assert_unqualified(value)
    assert snapshot(runner, result, states) == before
    assert counts == (len(seen.calls), len(seen.changed), len(seen.reviews), len(seen.checks), len(inference.requests))
    encoded = json.dumps(value)
    assert str(tmp_path) not in encoded and worker._assembled_system_prompt not in encoded
    assert schedule['manifest']['goal'] not in encoded
    for state in states:
        assert state['frontier_help']['outcome']['advice'] not in encoded
    again = report(runner, result, worker)
    assert again['advisor_usage'] == usage and again['accepted_work_count'] == 2


async def test_readonly_cli_style_report_without_task_authority_never_accepts_work(worker, tmp_path):
    runner, result, _, _, _, _ = await completed(worker, tmp_path)
    value = report_api()(runner.root, result.schedule_id)
    assert value['accepted_work_count'] == 0
    assert value['runs'][0]['completion']['task_projection'] == 'unverified'
    assert_unqualified(value)


async def test_runtime_aggregate_and_preflight_count_are_not_added_to_advisor_usage(worker, tmp_path):
    runner, result, _, states, _, _ = await completed(worker, tmp_path)
    journal = RunState(runner.children, states[0]['run_id'])
    try:
        journal.record('runtime_budget', data={'usage': {'prompt_tokens': 999, 'output_tokens': 333,
            'cached_tokens': 0, 'tools': 7, 'failures': 0}})
    finally: journal.close()
    value = report(runner, result, worker)
    assert value['accepted_work_count'] == 1
    assert value['advisor_usage']['requests'] == 1
    assert value['advisor_usage']['input_tokens'] == 37 and value['advisor_usage']['output_tokens'] == 8
    assert value['metrics']['reported_prompt_tokens'] == 999
    assert_unqualified(value)


@pytest.mark.parametrize('mode', ['count_fault', 'inference_fault'])
async def test_unsettled_advisor_request_stays_unknown_not_zero_or_accepted(worker, tmp_path, mode):
    runner, result, _, _, _, _ = await completed(worker, tmp_path, mode=mode)
    value = report(runner, result, worker)
    assert value['accepted_work_count'] == 0
    assert value['advisor_usage']['requests'] is None
    assert value['advisor_usage']['input_tokens'] is None and value['advisor_usage']['output_tokens'] is None
    assert_unqualified(value)


@pytest.mark.parametrize('mutation', ['phase_binding', 'check_version', 'boundary', 'receipt', 'help_digest', 'count_tokens'])
async def test_changed_child_evidence_cannot_be_promoted_by_parent_completion(worker, tmp_path, mutation):
    runner, result, _, states, _, _ = await completed(worker, tmp_path)
    state = states[0]
    if mutation == 'phase_binding':
        key = 'phase_binding'; changed = deepcopy(state[key]); changed['phase_id'] = 'another-phase'
    elif mutation == 'check_version':
        key = 'phase_check'; changed = deepcopy(state[key]); changed['check_version'] = 'f' * 64
    elif mutation in {'boundary', 'receipt'}:
        key = 'managed'; changed = deepcopy(state[key])
        if mutation == 'boundary': changed['boundary']['status'] = 'prepared'
        else: changed['receipt']['action_revision'] += 1
    elif mutation == 'help_digest':
        key = 'frontier_help'; changed = deepcopy(state[key]); changed['outcome']['advice'] = 'Altered generic advisory.'
    else:
        key = 'responses_help_preflight'; changed = deepcopy(state[key]); changed['input_tokens'] = 38
    alter_child(runner, state, key, changed)
    rejected_or_unaccepted(runner, result, worker)


@pytest.mark.parametrize('mutation', ['manifest', 'swapped_children', 'completion_receipt'])
async def test_changed_parent_schedule_cannot_select_or_accept_other_work(worker, tmp_path, mutation):
    runner, result, schedule, _, _, _ = await completed(worker, tmp_path, phases=2)
    changed = deepcopy(schedule)
    if mutation == 'manifest': changed['manifest']['phases'][0]['check_version'] = 'f' * 64
    elif mutation == 'swapped_children':
        first, second = changed['phases']
        first['run_id'], second['run_id'] = second['run_id'], first['run_id']
    else: changed['phases'][0]['completion']['receipt_id'] = 'forged-completion'
    journal = RunState(runner.root, result.schedule_id)
    try: journal.record('fixture_parent_changed', incremental=True, phase_schedule=changed)
    finally: journal.close()
    rejected_or_unaccepted(runner, result, worker)


async def test_wrong_task_projection_does_not_satisfy_host_check_linkage(worker, tmp_path):
    runner, result, _, _, _, _ = await completed(worker, tmp_path)
    def wrong(task_id):
        row = deepcopy(worker.tasks.get(task_id)); row['managed_revision'] += 1
        return row
    value = report_api()(runner.root, result.schedule_id, task_lookup=wrong)
    assert value['accepted_work_count'] == 0 and value['runs'][0]['completion']['task_projection'] != 'linked'
    assert_unqualified(value)


async def test_selected_run_limit_refuses_before_task_lookup(worker, tmp_path):
    runner, result, _, _, _, _ = await completed(worker, tmp_path, phases=2)
    def forbidden(task_id): raise AssertionError('Run bounds must be checked before task lookup.')
    with pytest.raises(ValueError):
        report_api()(runner.root, result.schedule_id, task_lookup=forbidden,
            limits=endurance_report.ReportLimits(max_runs=1))


def cli_module():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'report_endurance.py'
    spec = importlib.util.spec_from_file_location('phase_report_cli_fixture', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


async def test_existing_report_cli_schedule_mode_is_readonly_and_has_no_task_authority(worker, tmp_path, capsys):
    runner, result, _, states, _, _ = await completed(worker, tmp_path)
    before = snapshot(runner, result, states)
    assert cli_module().main([str(runner.root), '--schedule', result.schedule_id]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['schedule']['schedule_id'] == result.schedule_id and value['accepted_work_count'] == 0
    assert value['advisor_usage']['input_tokens'] == 37
    assert_unqualified(value)
    assert snapshot(runner, result, states) == before


def test_schedule_cli_refuses_mixed_child_selection(tmp_path, monkeypatch):
    report_api(); module = cli_module(); calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Mixed schedule and child selection cannot reach the reporter.')
    monkeypatch.setattr(module, 'build_endurance_report', forbidden)
    monkeypatch.setattr(module, 'build_phase_endurance_report', forbidden, raising=False)
    try: code = module.main([str(tmp_path), 'child-run', '--schedule', 'schedule-run'])
    except SystemExit as exc: code = exc.code
    assert code == 2 and calls == []


async def ordinary_completed(worker, tmp_path):
    from dream.core.phase_runner import PhaseRunner
    from test_phase_runner import configured, phases
    factory = configured(worker, tmp_path)
    runner = PhaseRunner(worker, phases(tmp_path)[:1], goal='Generic evidence fixture',
        state_dir=tmp_path/'schedules', evaluator_backend_factory=factory)
    result = await runner.run()
    assert result.status == 'done', result
    schedule, states = children(runner, result)
    return runner, result, schedule, states


async def test_actual_no_advisor_schedule_does_not_invent_token_zeroes(worker, tmp_path):
    runner, result, _, states = await ordinary_completed(worker, tmp_path)
    assert all(state.get('frontier_help') is None for state in states)
    before = snapshot(runner, result, states)
    value = report(runner, result, worker)
    assert value['accepted_work_count'] == 1
    assert value['advisor_usage']['requests'] == 0
    assert value['advisor_usage']['input_tokens'] is None
    assert value['advisor_usage']['output_tokens'] is None
    assert_unqualified(value)
    assert snapshot(runner, result, states) == before


async def test_busy_parent_refuses_before_task_lookup_without_writes(worker, tmp_path):
    runner, result, _, states = await ordinary_completed(worker, tmp_path)
    parent = RunState(runner.root, result.schedule_id)
    before = snapshot(runner, result, states)
    calls = []
    try:
        with pytest.raises((ValueError, RuntimeError, OSError)):
            report_api()(runner.root, result.schedule_id, task_lookup=lambda task: calls.append(task))
        assert calls == [] and snapshot(runner, result, states) == before
    finally:
        parent.close()


async def test_parent_change_during_task_lookup_refuses_whole_report(worker, tmp_path):
    runner, result, _, _ = await ordinary_completed(worker, tmp_path)
    calls = []
    def changing_lookup(task_id):
        calls.append(task_id)
        parent = RunState(runner.root, result.schedule_id)
        try:
            parent.record('fixture_concurrent_parent_observation', data={'observed': True})
        finally:
            parent.close()
        return worker.tasks.get(task_id)
    with pytest.raises(ValueError):
        report_api()(runner.root, result.schedule_id, task_lookup=changing_lookup)
    assert len(calls) == 1


async def test_total_read_limit_charges_parent_twice_and_child_once(worker, tmp_path):
    runner, result, _, states = await ordinary_completed(worker, tmp_path)
    parent_bytes = (runner.root/result.schedule_id/'ledger.jsonl').stat().st_size
    child_bytes = sum((runner.children/state['run_id']/'ledger.jsonl').stat().st_size for state in states)
    exact = parent_bytes * 2 + child_bytes
    calls = []
    with pytest.raises(ValueError):
        report_api()(runner.root, result.schedule_id, task_lookup=lambda task: calls.append(task),
            limits=endurance_report.ReportLimits(max_total_bytes=exact-1))
    assert calls == []
    value = report(runner, result, worker,
        limits=endurance_report.ReportLimits(max_total_bytes=exact))
    assert value['accepted_work_count'] == 1
    assert_unqualified(value)


async def test_linked_child_directory_symlink_is_not_followed(worker, tmp_path):
    runner, result, _, states = await ordinary_completed(worker, tmp_path)
    selected = runner.children/states[0]['run_id']
    moved = tmp_path/'moved-generic-child'
    selected.rename(moved)
    selected.symlink_to(moved, target_is_directory=True)
    calls = []
    with pytest.raises((ValueError, RuntimeError, OSError)):
        report_api()(runner.root, result.schedule_id, task_lookup=lambda task: calls.append(task))
    assert calls == []


async def test_matching_parent_and_child_sequence_must_name_real_passing_check(worker, tmp_path):
    runner, result, schedule, states = await ordinary_completed(worker, tmp_path)
    state = states[0]
    records = [json.loads(line) for line in (runner.children/state['run_id']/'ledger.jsonl').read_text().splitlines()]
    unrelated = next(record['seq'] for record in records if record['event'] != 'recovery_check_executed')
    assert unrelated != state['phase_check']['observation_sequence']
    changed = deepcopy(state['phase_check']); changed['observation_sequence'] = unrelated
    alter_child(runner, state, 'phase_check', changed)
    changed_parent = deepcopy(schedule)
    changed_parent['phases'][0]['completion']['host_check_sequence'] = unrelated
    parent = RunState(runner.root, result.schedule_id)
    try:
        parent.record('fixture_consistent_but_unobserved_check_sequence', incremental=True,
            phase_schedule=changed_parent)
    finally:
        parent.close()
    rejected_or_unaccepted(runner, result, worker)


@pytest.mark.parametrize('point', ['returned_before_authorize', 'checked_before_ack_stop'])
async def test_interrupted_advisor_usage_is_readable_without_execution_admission(worker, tmp_path, monkeypatch, point):
    import asyncio
    from types import SimpleNamespace
    from dream.core.task_checkpoints import ManagedTaskCoordinator
    from test_responses_phase_workflow import observed, assert_no_replay
    runner, seen, inference = workflow(worker, tmp_path, cap=6)
    captured = {}
    class Crash(BaseException): pass
    original = RunState.record
    def after_return(journal, event, *args, **kwargs):
        value = original(journal, event, *args, **kwargs)
        if event == 'frontier_help_returned':
            captured.update(schedule_id=journal.state['phase_binding']['schedule_id'])
            raise Crash('Synthetic interruption after actual advisory receipt, before authorization.')
        return value
    def stop_before_ack(coordinator, *args, **kwargs):
        assert coordinator._state['completed_receipt']
        assert coordinator._state['boundary']['status'] == 'prepared'
        captured.update(schedule_id=coordinator.journal.state['phase_binding']['schedule_id'])
        coordinator.stop()
        raise asyncio.CancelledError()
    with monkeypatch.context() as patch:
        if point == 'returned_before_authorize':
            patch.setattr(RunState, 'record', after_return)
            with pytest.raises(Crash): await runner.run()
        else:
            patch.setattr(ManagedTaskCoordinator, 'acknowledge_boundary', stop_before_ack)
            with pytest.raises(asyncio.CancelledError): await runner.run()
    result = SimpleNamespace(schedule_id=captured['schedule_id'])
    schedule, states = children(runner, result)
    assert len(seen.calls) == 2 and states[0]['frontier_help']['status'] == 'returned'
    assert schedule['cursor'] == 0 and schedule['phases'][0]['completion'] is None
    if point == 'returned_before_authorize':
        assert states[0].get('help_continuation') is None
        assert seen.changed == seen.reviews == seen.checks == []
    else:
        assert states[0]['managed']['recovery']['pending']['kind'] == 'stopped'
        assert len(seen.changed) == len(seen.reviews) == len(seen.checks) == 1
    before = snapshot(runner, result, states)
    counts = observed(runner, seen, inference, result.schedule_id)
    value = report(runner, result, worker)
    assert value['accepted_work_count'] == 0
    # Actual returned inference usage survives independently of permission to
    # continue the stopped/interrupted worker; no preflight tokens are added.
    assert value['advisor_usage']['requests'] == 1
    assert value['advisor_usage']['input_tokens'] == 37
    assert value['advisor_usage']['output_tokens'] == 8
    assert_unqualified(value)
    assert snapshot(runner, result, states) == before
    assert_no_replay(counts, runner, seen, inference, result.schedule_id)
