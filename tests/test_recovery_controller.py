"""Behavioral acceptance for the harness-owned recovery boundary (no providers)."""
import json
from contextlib import closing
from dataclasses import FrozenInstanceError

import pytest

from dream.core.recovery import (
    EvidenceAuthority, RecoveryController, RecoveryObservation, RecoveryPolicy,
    payload_digest,
)
from dream.core.run_state import RunState

TASK = payload_digest('research task')
PHASE = payload_digest('investigate')


def controller(**policy):
    authority = EvidenceAuthority()
    return RecoveryController(TASK, PHASE, policy=RecoveryPolicy(**policy),
                              evidence_authority=authority), authority


def observe(c, seq, call='search', error=None, **kwargs):
    return c.observe(RecoveryObservation(seq, call_digest=payload_digest(call),
                     error_digest=payload_digest(error) if error else '', **kwargs))


def receipt(authority, payload, **kwargs):
    return authority.issue(TASK, PHASE, payload_digest(payload), **kwargs)


def test_repeated_calls_bounded_and_restart_does_not_forgive(tmp_path):
    c, authority = controller()
    assert [observe(c, n).kind for n in (1, 2, 3)] == ['continue', 'continue', 'recover']
    with closing(RunState(tmp_path)) as journal:
        journal.record('recovery_observed', recovery=c.to_snapshot())
        run_id = journal.run_id
    with closing(RunState(tmp_path, run_id)) as resumed:
        c = RecoveryController.from_snapshot(resumed.state['recovery'], evidence_authority=authority)
    assert observe(c, 4).kind == 'recover'
    action = observe(c, 5)
    assert action.kind == 'needs_help'
    assert action.attempts == 2
    assert action.help_request is not None
    assert observe(c, 6).help_request is None
    assert observe(c, 7).kind == 'needs_help'


def test_distinct_calls_remain_distinct_but_same_error_is_detected():
    c, _ = controller()
    assert all(observe(c, n, f'query-{n}').kind == 'continue' for n in range(1, 9))
    c, _ = controller()
    for n in (1, 2):
        assert observe(c, n, f'query-{n}', 'permission denied').kind == 'continue'
    assert observe(c, 3, 'different again', 'permission denied').reason == 'repeated_error'


def test_named_independent_check_fails_twice_even_interleaved():
    c, _ = controller()
    check = payload_digest('acceptance: sources attributed')
    assert observe(c, 1, 'check A', check_digest=check, check_passed=False).kind == 'continue'
    observe(c, 2, 'other check', check_digest=payload_digest('other'), check_passed=False)
    a = observe(c, 3, 'check A changed args', check_digest=check, check_passed=False)
    assert (a.kind, a.reason) == ('recover', 'repeated_check')


def test_research_progress_is_novel_task_bound_evidence_not_file_writes():
    c, authority = controller(no_progress_limit=3)
    for seq in range(1, 25):
        assert observe(c, seq, evidence=receipt(authority, f'source fact {seq}')).kind == 'continue'
    assert c.to_snapshot()['attempts'] == 0
    c, _ = controller(no_progress_limit=3)
    for seq in (1, 2):
        observe(c, seq, f'write file {seq}')
    assert observe(c, 3, 'write different file').reason == 'no_progress'


def test_receipt_replay_and_new_receipt_for_old_content_do_not_reset():
    c, authority = controller(no_progress_limit=2)
    r = receipt(authority, 'same source')
    assert observe(c, 1, evidence=r).kind == 'continue'
    observe(c, 2, 'second', evidence=r)
    assert observe(c, 3, 'third', evidence=receipt(authority, 'same source')).reason == 'no_progress'
    assert observe(c, 4, evidence=receipt(authority, 'new finding')).kind == 'continue'
    assert c.to_snapshot()['attempts'] == 0


def test_foreign_or_wrong_task_receipts_are_rejected_atomically():
    c, authority = controller()
    for bad in (receipt(EvidenceAuthority(), 'foreign'),
                authority.issue(payload_digest('other task'), PHASE, payload_digest('evidence')),
                authority.issue(TASK, payload_digest('other phase'), payload_digest('evidence')),
                {'verified': True, 'kind': 'checkpoint'}):
        before = c.to_snapshot()
        with pytest.raises(ValueError):
            observe(c, 1, evidence=bad)
        assert c.to_snapshot() == before


def test_phase_budget_not_reset_by_progress_and_zero_help_budget_blocks():
    c, authority = controller(phase_step_budget=2, help_request_limit=0)
    for seq in (1, 2):
        assert observe(c, seq, evidence=receipt(authority, seq)).kind == 'continue'
    assert observe(c, 3, evidence=receipt(authority, 3)).reason == 'phase_budget'
    assert observe(c, 4, evidence=receipt(authority, 4)).kind == 'recover'
    assert observe(c, 5, evidence=receipt(authority, 5)).kind == 'blocked'
    assert c.to_snapshot()['help_used'] == 0


def test_stop_is_terminal_even_after_help_and_restart():
    c, authority = controller(no_progress_limit=1, max_recovery_attempts=0)
    assert observe(c, 1).kind == 'needs_help'
    assert c.observe(RecoveryObservation(1, stopped=True)).kind == 'stopped'
    c = RecoveryController.from_snapshot(c.to_snapshot(), evidence_authority=authority)
    assert observe(c, 2, evidence=receipt(authority, 'new verified fact')).kind == 'stopped'


def test_duplicate_delivery_is_idempotent_conflicting_or_old_delivery_rejected():
    c, _ = controller()
    observe(c, 1)
    before = c.to_snapshot()
    assert observe(c, 1).kind == 'continue'
    assert c.to_snapshot() == before
    with pytest.raises(ValueError):
        observe(c, 1, 'changed')
    observe(c, 2)
    before = c.to_snapshot()
    with pytest.raises(ValueError):
        observe(c, 1)
    assert c.to_snapshot() == before


def test_help_envelope_contains_only_digests_and_fixed_metadata():
    c, _ = controller(max_recovery_attempts=0)
    private = 'private secret query with name and credential'
    for seq in (1, 2, 3):
        action = observe(c, seq, private, private)
    assert action.kind == 'needs_help'
    encoded = json.dumps(action.help_request.to_dict())
    assert private not in encoded
    assert 'private' not in json.dumps(c.to_snapshot())
    assert len(encoded) < 600
    assert action.help_request.to_dict()['requires_review'] is True
    with pytest.raises(FrozenInstanceError):
        action.kind = 'done'


@pytest.mark.parametrize('change', [
    {'version': True}, {'version': 99}, {'attempts': 3}, {'help_used': -1},
    {'steps': 10**100}, {'last_sequence': True}, {'status': 'done'},
    {'reason': 'private text'}, {'extra': 'private'}, {'task_digest': 'bad'},
    {'calls': {'bad': 3}}, {'evidence': ['0'*64, '0'*64]},
])
def test_malformed_snapshots_fail_closed(change):
    c, authority = controller()
    snapshot = {**c.to_snapshot(), **change}
    with pytest.raises(ValueError):
        RecoveryController.from_snapshot(snapshot, evidence_authority=authority)


def test_snapshot_is_detached_json_and_limits_tracking_without_eviction():
    c, authority = controller(no_progress_limit=1000, phase_step_budget=1000)
    for seq in range(1, 66):
        action = observe(c, seq, f'distinct-{seq}')
    assert action.kind == 'blocked'
    assert action.reason == 'tracking_capacity'
    snapshot = json.loads(json.dumps(c.to_snapshot()))
    assert len(snapshot['calls']) <= 64
    assert len(json.dumps(snapshot)) < 30000
    snapshot['calls'].clear()
    assert c.to_snapshot()['calls']


def test_digest_canonicalization_and_input_validation():
    assert payload_digest({'a': 1, 'b': 2}) == payload_digest({'b': 2, 'a': 1})
    for bad in (float('nan'), object(), {'bad': float('inf')}):
        with pytest.raises(ValueError):
            payload_digest(bad)
    for kwargs in ({'sequence': True}, {'sequence': 0}, {'sequence': 10**100},
                   {'sequence': 1, 'call_digest': 'raw secret'},
                   {'sequence': 1, 'check_passed': False},
                   {'sequence': 1, 'stopped': 1}):
        with pytest.raises(ValueError):
            RecoveryObservation(**kwargs)
    with pytest.raises(ValueError):
        RecoveryPolicy(max_recovery_attempts=3)


def test_new_authority_after_process_restart_preserves_consumed_receipts():
    c, authority = controller(no_progress_limit=2)
    observe(c, 1, evidence=receipt(authority, 'same content'))
    restarted_authority = EvidenceAuthority()
    c = RecoveryController.from_snapshot(c.to_snapshot(), evidence_authority=restarted_authority)
    observe(c, 2, 'another read', evidence=receipt(restarted_authority, 'same content'))
    action = observe(c, 3, 'read again', evidence=receipt(restarted_authority, 'same content', kind='checkpoint'))
    assert (action.kind, action.reason) == ('recover', 'no_progress')


def test_passing_different_check_does_not_clear_attempts_or_other_check_failures():
    c, _ = controller()
    failed = payload_digest('must cite evidence')
    observe(c, 1, 'check', check_digest=failed, check_passed=False)
    observe(c, 2, 'check', check_digest=failed, check_passed=False)
    observe(c, 3, 'pass irrelevant check', check_digest=payload_digest('unrelated'), check_passed=True)
    assert observe(c, 4, 'check', check_digest=failed, check_passed=False).attempts == 2
    assert observe(c, 5, 'check', check_digest=failed, check_passed=False).kind == 'needs_help'


def test_recovery_redelivery_never_issues_another_attempt_after_restart():
    c, authority = controller(no_progress_limit=1)
    assert observe(c, 1).kind == 'recover'
    c = RecoveryController.from_snapshot(c.to_snapshot(), evidence_authority=authority)
    before = c.to_snapshot()
    assert observe(c, 1).kind == 'continue'
    assert c.to_snapshot() == before
    assert observe(c, 2).attempts == 2
    assert observe(c, 3).kind == 'needs_help'
    authority = EvidenceAuthority()
    c = RecoveryController.from_snapshot(c.to_snapshot(), evidence_authority=authority)
    assert observe(c, 4).help_request is None
    assert c.to_snapshot()['help_used'] == 1


def test_evidence_capacity_blocks_without_forgetting_old_progress():
    c, authority = controller(phase_step_budget=1000)
    for seq in range(1, 257):
        assert observe(c, seq, evidence=receipt(authority, seq)).kind == 'continue'
    action = observe(c, 257, evidence=receipt(authority, 257))
    assert (action.kind, action.reason) == ('blocked', 'tracking_capacity')
    assert len(c.to_snapshot()['evidence']) == 256


def test_error_and_check_capacities_are_bounded_independently():
    for field in ('error', 'check'):
        c, _ = controller(no_progress_limit=1000, phase_step_budget=1000)
        for seq in range(1, 66):
            obs = RecoveryObservation(seq, **(
                {'error_digest': payload_digest(seq)} if field == 'error' else
                {'check_digest': payload_digest(seq), 'check_passed': False}))
            action = c.observe(obs)
        assert (action.kind, action.reason) == ('blocked', 'tracking_capacity')
        assert len(c.to_snapshot()[field + 's']) == 64


@pytest.mark.parametrize('change', [
    {'status': []}, {'reason': {}}, {'calls': []}, {'policy': {'repeat_call_limit': 1}},
    {'checks': {'0'*64: 3}}, {'errors': {'0'*64: True}}, {'evidence': [None]},
    {'last_observation': '0'*64}, {'no_progress': 1},
    {'status': 'needs_help', 'reason': 'no_progress'},
    {'status': 'active', 'reason': 'stopped'},
    {'status': 'active', 'reason': 'tracking_capacity'},
    {'status': 'active', 'help_used': 1},
    {'status': 'blocked', 'reason': 'repeated_error'},
])
def test_inconsistent_snapshot_states_fail_with_value_error(change):
    c, authority = controller()
    with pytest.raises(ValueError):
        RecoveryController.from_snapshot({**c.to_snapshot(), **change}, evidence_authority=authority)


def test_stop_before_first_observation_roundtrips_without_incrementing_budget():
    c, authority = controller()
    c.observe(RecoveryObservation(1, stopped=True))
    c = RecoveryController.from_snapshot(c.to_snapshot(), evidence_authority=authority)
    assert c.to_snapshot()['steps'] == 0
    assert c.observe(RecoveryObservation(1)).kind == 'stopped'


def test_snapshot_is_small_even_for_large_private_payloads():
    c, _ = controller()
    private = 'private query ' * 100000
    observe(c, 1, private, private)
    assert len(json.dumps(c.to_snapshot())) < 1500
    assert private[:20] not in json.dumps(c.to_snapshot())


def test_novel_research_does_not_hide_persistently_failing_independent_check():
    c, authority = controller()
    check = payload_digest('required independent acceptance')
    for seq in range(1, 5):
        action = observe(c, seq, f'research {seq}', check_digest=check, check_passed=False,
                         evidence=receipt(authority, f'novel partial finding {seq}'))
    assert (action.kind, action.reason, action.attempts) == ('needs_help', 'repeated_check', 2)


def test_sequence_space_exhaustion_blocks_without_counter_wraparound():
    c, authority = controller()
    action = observe(c, 1000000, evidence=receipt(authority, 'one finding'))
    assert (action.kind, action.reason) == ('blocked', 'tracking_capacity')
    restored = RecoveryController.from_snapshot(c.to_snapshot(), evidence_authority=authority)
    assert restored.to_snapshot() == c.to_snapshot()


@pytest.mark.parametrize('kind', ['evidence', 'checkpoint'])
def test_partial_progress_cannot_hide_simultaneous_repeated_errors(kind):
    c, authority = controller()
    actions = [observe(c, seq, f'partial research {seq}', 'permission denied',
                       evidence=receipt(authority, f'new partial fact {seq}', kind=kind))
               for seq in range(1, 7)]
    assert [a.kind for a in actions] == [
        'continue', 'continue', 'recover', 'recover', 'needs_help', 'needs_help']
    assert [a.attempts for a in actions] == [0, 0, 1, 2, 2, 2]
    assert all(a.reason == 'repeated_error' for a in actions[2:])
    assert sum(a.help_request is not None for a in actions) == 1
    assert c.to_snapshot()['help_used'] == 1
    assert c.to_snapshot()['errors'] == {payload_digest('permission denied'): 3}


@pytest.mark.parametrize('kind', ['evidence', 'checkpoint'])
def test_interleaved_progress_and_restart_preserve_unresolved_error_debt(tmp_path, kind):
    c, authority = controller()
    for seq in (1, 2, 3):
        action = observe(c, seq, f'failing query {seq}', 'permission denied')
    assert (action.kind, action.attempts) == ('recover', 1)

    # A real independent success on another check plus novel partial work is
    # useful evidence, but provides no proof that this error was resolved.
    action = observe(c, 4, 'different successful research',
                     check_digest=payload_digest('unrelated independent check'), check_passed=True,
                     evidence=receipt(authority, 'new partial fact', kind=kind))
    assert (action.kind, action.attempts) == ('continue', 1)
    assert c.to_snapshot()['errors'] == {payload_digest('permission denied'): 3}
    with closing(RunState(tmp_path)) as journal:
        journal.record('recovery_observed', recovery=c.to_snapshot())
        run_id = journal.run_id
    authority = EvidenceAuthority()
    with closing(RunState(tmp_path, run_id)) as resumed:
        c = RecoveryController.from_snapshot(resumed.state['recovery'], evidence_authority=authority)

    action = observe(c, 5, 'retry with other arguments', 'permission denied')
    assert (action.kind, action.reason, action.attempts) == ('recover', 'repeated_error', 2)
    action = observe(c, 6, 'another successful research',
                     evidence=receipt(authority, 'another new partial fact', kind=kind))
    assert (action.kind, action.attempts) == ('continue', 2)
    c = RecoveryController.from_snapshot(c.to_snapshot(), evidence_authority=authority)
    action = observe(c, 7, 'third recovery would be unsafe', 'permission denied')
    assert (action.kind, action.reason, action.attempts) == ('needs_help', 'repeated_error', 2)
    assert action.help_request is not None
    assert c.to_snapshot()['help_used'] == 1
    c = RecoveryController.from_snapshot(c.to_snapshot(), evidence_authority=authority)
    assert observe(c, 8, evidence=receipt(authority, 'more progress')).help_request is None
    assert c.to_snapshot()['help_used'] == 1
