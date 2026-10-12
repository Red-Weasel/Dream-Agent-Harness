"""Deterministic, provider-free recovery decisions after trusted observations.

This is a harness API, never a model tool. Only the owner of the RunState lock
may advance a controller. Observe AFTER a handler/check returns or a generation
stream closes; distinguish
actual outcomes from refused or uncertain operations at that boundary. A check
is an independently executed named check, not the assistant's claimed verdict.
The caller must persist ``to_snapshot()`` in the existing journal before acting
on a decision. A recovery decision does not authorize replay of side effects.

EvidenceAuthority belongs to the trusted receipt verifier. It must not issue a
receipt for model prose, a changed query, file mtime, or an unverified success.
Its capability is an in-process boundary, not a sandbox against arbitrary Python.
Receipts identify task-relevant evidence content/checkpoints, not request IDs.
Research evidence counts without any file write. No decision declares completion.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import re
from typing import Literal

_VERSION = 1
_MAX_COUNTER = 1_000_000
_MAX_KEYS = 64
_MAX_EVIDENCE = 256
_REASONS = frozenset({'', 'repeated_call', 'repeated_error', 'repeated_check',
                      'generation_repetition', 'no_progress', 'phase_budget',
                      'tracking_capacity', 'stopped'})
_STATUSES = frozenset({'active', 'needs_help', 'blocked', 'stopped'})


def _integer(value: object, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid recovery counter or limit')


def _digest(value: object, *, optional: bool = False) -> None:
    if optional and value == '':
        return
    if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        raise ValueError('Expected a SHA-256 recovery digest')


def payload_digest(payload: object) -> str:
    """Hash JSON data without retaining it; reject non-JSON/non-finite inputs.

    Caller should normalize the actual call (tool name + arguments), error, or
    evidence content, omitting volatile delivery IDs. No raw payload is stored.
    """
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('Recovery payload must be finite JSON data') from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RecoveryPolicy:
    repeat_call_limit: int = 3
    repeat_error_limit: int = 3
    no_progress_limit: int = 12
    phase_step_budget: int = 48
    max_recovery_attempts: int = 2
    help_request_limit: int = 1

    def __post_init__(self) -> None:
        _integer(self.repeat_call_limit, 2, _MAX_COUNTER)
        _integer(self.repeat_error_limit, 2, _MAX_COUNTER)
        _integer(self.no_progress_limit, 1, _MAX_COUNTER)
        _integer(self.phase_step_budget, 1, _MAX_COUNTER)
        _integer(self.max_recovery_attempts, 0, 2)
        _integer(self.help_request_limit, 0, 16)


@dataclass(frozen=True)
class EvidenceReceipt:
    task_digest: str
    phase_digest: str
    evidence_digest: str
    kind: Literal['evidence', 'checkpoint']
    _authority: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        for value in (self.task_digest, self.phase_digest, self.evidence_digest):
            _digest(value)
        if self.kind not in ('evidence', 'checkpoint'):
            raise ValueError('Invalid recovery evidence kind')


class EvidenceAuthority:
    """Capability owned by the harness's independent evidence verifier."""

    def __init__(self) -> None:
        self._token = object()

    def issue(self, task_digest: str, phase_digest: str, evidence_digest: str,
              *, kind: Literal['evidence', 'checkpoint'] = 'evidence') -> EvidenceReceipt:
        return EvidenceReceipt(task_digest, phase_digest, evidence_digest, kind, self._token)


@dataclass(frozen=True)
class RecoveryObservation:
    sequence: int
    call_digest: str = ''
    error_digest: str = ''
    check_digest: str = ''
    check_passed: bool | None = None
    evidence: EvidenceReceipt | None = None
    stopped: bool = False
    generation_cut: bool = False

    def __post_init__(self) -> None:
        _integer(self.sequence, 1, _MAX_COUNTER)
        for value in (self.call_digest, self.error_digest, self.check_digest):
            _digest(value, optional=True)
        if type(self.stopped) is not bool:
            raise ValueError('Invalid recovery Stop observation')
        if type(self.generation_cut) is not bool:
            raise ValueError('Invalid generation-cut observation')
        if self.generation_cut and (self.call_digest or self.check_digest or self.evidence is not None):
            raise ValueError('A generation cut is not a handler, check or evidence receipt')
        if self.check_passed is not None and type(self.check_passed) is not bool:
            raise ValueError('Invalid independent check outcome')
        if bool(self.check_digest) != (self.check_passed is not None):
            raise ValueError('A named check requires an actual outcome')
        if self.evidence is not None and type(self.evidence) is not EvidenceReceipt:
            raise ValueError('Expected a trusted evidence receipt')


@dataclass(frozen=True)
class HelpRequest:
    """Minimal local envelope; caller reviews it before any external dispatch.

    Digests can still correlate private material: this object is not permission
    to transmit them. Task text, queries, errors, paths and results are absent.
    """
    task_digest: str
    phase_digest: str
    signal_digest: str
    reason: str
    attempts: int

    def __post_init__(self) -> None:
        for value in (self.task_digest, self.phase_digest, self.signal_digest):
            _digest(value)
        if (not isinstance(self.reason, str)
                or self.reason not in _REASONS - {'', 'stopped', 'tracking_capacity'}):
            raise ValueError('Invalid help reason')
        _integer(self.attempts, 0, 2)

    def to_dict(self) -> dict:
        return {'version': _VERSION, **asdict(self), 'requires_review': True}


@dataclass(frozen=True)
class RecoveryAction:
    kind: Literal['continue', 'recover', 'needs_help', 'blocked', 'stopped']
    reason: str = ''
    attempts: int = 0
    help_request: HelpRequest | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.kind, str)
                or self.kind not in {'continue', 'recover', 'needs_help', 'blocked', 'stopped'}):
            raise ValueError('Invalid recovery action')
        if not isinstance(self.reason, str) or self.reason not in _REASONS:
            raise ValueError('Invalid recovery reason')
        _integer(self.attempts, 0, 2)
        if self.help_request is not None and (type(self.help_request) is not HelpRequest
                                             or self.kind != 'needs_help'):
            raise ValueError('Help envelope requires needs_help')


class RecoveryController:
    """One task/phase scope, serialized by its owning run's existing lock.

    Terminal states never resume implicitly. A new phase/controller requires a
    harness-verified transition; carry the remaining RUN help budget into its
    policy. This class enforces the passed budget for this scope and cannot infer
    budgets consumed by other controllers. All counts and digest collections are
    bounded. Capacity exhaustion blocks rather than evicting replay protection.
    Error history is retained for this scope: ordinary evidence/checkpoint receipts
    do not prove resolution of a specific error. Starting a fresh scope requires
    the caller's independently verified transition, never a model clearance claim.
    """

    def __init__(self, task_digest: str, phase_digest: str, *,
                 policy: RecoveryPolicy | None = None,
                 evidence_authority: EvidenceAuthority) -> None:
        _digest(task_digest)
        _digest(phase_digest)
        if type(evidence_authority) is not EvidenceAuthority:
            raise ValueError('Expected the trusted evidence authority')
        if policy is None:
            policy = RecoveryPolicy()
        if type(policy) is not RecoveryPolicy:
            raise ValueError('Invalid recovery policy')
        self._authority = evidence_authority
        self._policy = policy
        self._state = {
            'version': _VERSION, 'task_digest': task_digest, 'phase_digest': phase_digest,
            'policy': asdict(policy), 'status': 'active', 'reason': '',
            'steps': 0, 'no_progress': 0, 'attempts': 0, 'help_used': 0,
            'last_sequence': 0, 'last_observation': '',
            'calls': {}, 'errors': {}, 'checks': {}, 'evidence': [],
        }

    def to_snapshot(self) -> dict:
        """Detached JSON-compatible state for the existing RunState journal."""
        return deepcopy(self._state)

    @classmethod
    def from_snapshot(cls, snapshot: dict, *,
                      evidence_authority: EvidenceAuthority) -> RecoveryController:
        """Strictly restore counters; corrupt/unknown formats never reset them."""
        if type(snapshot) is not dict or type(snapshot.get('policy')) is not dict:
            raise ValueError('Invalid recovery snapshot')
        try:
            policy = RecoveryPolicy(**snapshot['policy'])
            instance = cls(snapshot['task_digest'], snapshot['phase_digest'], policy=policy,
                           evidence_authority=evidence_authority)
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid recovery snapshot fields') from exc
        if set(snapshot) != set(instance._state) or set(snapshot['policy']) != set(asdict(policy)):
            raise ValueError('Unknown or missing recovery snapshot fields')
        _integer(snapshot['version'], _VERSION, _VERSION)
        if (not isinstance(snapshot['status'], str) or snapshot['status'] not in _STATUSES
                or not isinstance(snapshot['reason'], str) or snapshot['reason'] not in _REASONS):
            raise ValueError('Invalid recovery snapshot state')
        for name in ('steps', 'no_progress', 'last_sequence'):
            _integer(snapshot[name], 0, _MAX_COUNTER)
        _integer(snapshot['attempts'], 0, policy.max_recovery_attempts)
        _integer(snapshot['help_used'], 0, policy.help_request_limit)
        _digest(snapshot['last_observation'], optional=True)
        if (snapshot['no_progress'] > snapshot['steps']
                or snapshot['steps'] > snapshot['last_sequence']
                or bool(snapshot['last_sequence']) != bool(snapshot['last_observation'])):
            raise ValueError('Inconsistent recovery counters')
        for name, maximum in (('calls', policy.repeat_call_limit),
                              ('errors', policy.repeat_error_limit), ('checks', 2)):
            values = snapshot[name]
            if type(values) is not dict or len(values) > _MAX_KEYS:
                raise ValueError('Invalid recovery digest counters')
            for key, value in values.items():
                _digest(key)
                _integer(value, 1, maximum)
        evidence = snapshot['evidence']
        if type(evidence) is not list or len(evidence) > _MAX_EVIDENCE:
            raise ValueError('Invalid recovery evidence history')
        for key in evidence:
            _digest(key)
        if len(set(evidence)) != len(evidence):
            raise ValueError('Duplicate recovery evidence history')
        if (snapshot['status'] == 'stopped') != (snapshot['reason'] == 'stopped'):
            raise ValueError('Inconsistent recovery Stop state')
        if snapshot['status'] in {'needs_help', 'blocked'} and not snapshot['reason']:
            raise ValueError('Terminal recovery state requires a reason')
        if snapshot['status'] == 'needs_help' and snapshot['help_used'] == 0:
            raise ValueError('Help must reserve budget before dispatch')
        if snapshot['reason'] == 'tracking_capacity' and snapshot['status'] != 'blocked':
            raise ValueError('Tracking capacity must stop the controller')
        if snapshot['status'] == 'active' and snapshot['help_used']:
            raise ValueError('Help reservation is a terminal transition')
        if snapshot['status'] in {'needs_help', 'blocked'} and snapshot['reason'] != 'tracking_capacity':
            if snapshot['attempts'] != policy.max_recovery_attempts:
                raise ValueError('Recovery attempts must precede help or budget blocking')
            if snapshot['status'] == 'blocked' and snapshot['help_used'] != policy.help_request_limit:
                raise ValueError('Inconsistent exhausted help budget')
        instance._state = deepcopy(snapshot)
        return instance

    def observe(self, observation: RecoveryObservation) -> RecoveryAction:
        """Advance atomically in memory; journal before consuming the action.

        Exact redelivery of the last observation is a no-op, without another
        help envelope/recovery action. Older/conflicting delivery fails closed.
        Stop dominates every state, including late Stop with an older sequence.
        """
        if type(observation) is not RecoveryObservation:
            raise ValueError('Expected a typed recovery observation')
        current = self._state
        if observation.stopped:
            self._state = {**current, 'status': 'stopped', 'reason': 'stopped'}
            return RecoveryAction('stopped', 'stopped', current['attempts'])
        if current['status'] != 'active':
            return RecoveryAction(current['status'], current['reason'], current['attempts'])
        receipt = observation.evidence
        if receipt is not None:
            if (receipt._authority is not self._authority._token
                    or receipt.task_digest != current['task_digest']
                    or receipt.phase_digest != current['phase_digest']):
                raise ValueError('Untrusted or out-of-scope recovery receipt')
        fingerprint_fields = {
            'sequence': observation.sequence, 'call': observation.call_digest,
            'error': observation.error_digest, 'check': observation.check_digest,
            'passed': observation.check_passed,
            'evidence': receipt.evidence_digest if receipt else '',
            'evidence_kind': receipt.kind if receipt else '',
        }
        # Preserve old observation identities exactly when no new signal exists.
        if observation.generation_cut:
            fingerprint_fields['generation_cut'] = True
        fingerprint = payload_digest(fingerprint_fields)
        if observation.sequence <= current['last_sequence']:
            if (observation.sequence == current['last_sequence']
                    and fingerprint == current['last_observation']):
                return RecoveryAction('continue', attempts=current['attempts'])
            raise ValueError('Stale or conflicting recovery observation')
        state = deepcopy(current)
        state['last_sequence'] = observation.sequence
        state['last_observation'] = fingerprint
        state['steps'] = min(_MAX_COUNTER, state['steps'] + 1)
        if observation.sequence == _MAX_COUNTER:
            return self._block_capacity(state)
        novel = receipt is not None and receipt.evidence_digest not in state['evidence']
        if novel:
            if len(state['evidence']) == _MAX_EVIDENCE:
                return self._block_capacity(state)
            state['evidence'].append(receipt.evidence_digest)
            state['no_progress'] = 0
            # Progress cannot replenish a spent phase budget or hide unresolved
            # errors/check failures. Only a pass of the same named check clears
            # that check; no generic progress receipt clears error-specific debt.
            if (state['steps'] <= self._policy.phase_step_budget
                    and not state['checks'] and not state['errors']):
                state['attempts'] = 0
                state['calls'], state['errors'], state['checks'] = {}, {}, {}
                state['reason'] = ''
        else:
            state['no_progress'] = min(_MAX_COUNTER, state['no_progress'] + 1)
        signals = {}
        # A new verified fact from the same research call is progress, not a loop.
        for name, key, limit in (
            ('calls', observation.call_digest if not novel else '', self._policy.repeat_call_limit),
            ('errors', observation.error_digest, self._policy.repeat_error_limit),
            ('checks', observation.check_digest if observation.check_passed is False else '', 2),
        ):
            if not key:
                continue
            if key not in state[name] and len(state[name]) == _MAX_KEYS:
                return self._block_capacity(state)
            state[name][key] = min(limit, state[name].get(key, 0) + 1)
            signals[name] = state[name][key] >= limit
        # Passing one independently observed check clears only that check's failures.
        if observation.check_passed:
            state['checks'].pop(observation.check_digest, None)
        reason = (
            'phase_budget' if state['steps'] > self._policy.phase_step_budget else
            'generation_repetition' if observation.generation_cut else
            'repeated_check' if signals.get('checks') else
            'repeated_error' if signals.get('errors') else
            'repeated_call' if signals.get('calls') else
            'no_progress' if state['no_progress'] >= self._policy.no_progress_limit else ''
        )
        action = RecoveryAction('continue', attempts=state['attempts'])
        if reason:
            state['reason'] = reason
            if state['attempts'] < self._policy.max_recovery_attempts:
                state['attempts'] += 1
                action = RecoveryAction('recover', reason, state['attempts'])
            elif state['help_used'] < self._policy.help_request_limit:
                state['help_used'] += 1
                state['status'] = 'needs_help'
                action = RecoveryAction('needs_help', reason, state['attempts'], HelpRequest(
                    state['task_digest'], state['phase_digest'], fingerprint, reason, state['attempts']))
            else:
                state['status'] = 'blocked'
                action = RecoveryAction('blocked', reason, state['attempts'])
        self._state = state
        return action

    def _block_capacity(self, state: dict) -> RecoveryAction:
        state['status'], state['reason'] = 'blocked', 'tracking_capacity'
        self._state = state
        return RecoveryAction('blocked', 'tracking_capacity', state['attempts'])


def assisted_projection(original: dict, observations: list[dict]) -> dict:
    """Replay at most sixteen host observations without reopening the controller.

    The terminal controller remains authoritative and unchanged. This separate
    projection retains its debt and adds actual observations; evidence never
    refunds attempts, help, steps or no-progress debt. A further signal seals
    assistance instead of reserving another intervention/help request.
    """
    authority = EvidenceAuthority()
    base = RecoveryController.from_snapshot(original, evidence_authority=authority).to_snapshot()
    if base['status'] != 'needs_help' or base['help_used'] != 1:
        raise ValueError('Assistance requires the original single-help terminal state')
    if type(observations) is not list or len(observations) > 16:
        raise ValueError('Invalid assisted observations')
    result = deepcopy(base)
    result.update(status='assisted', reason='')
    for raw in observations:
        if result['reason']:
            raise ValueError('Observation after assisted recovery closed')
        if type(raw) is not dict or set(raw) != {'call_digest','error_digest','evidence_digest','check_digest','check_passed'}:
            raise ValueError('Invalid assisted observation fields')
        _digest(raw['evidence_digest'], optional=True)
        RecoveryObservation(result['last_sequence'] + 1,
            call_digest=raw['call_digest'], error_digest=raw['error_digest'],
            check_digest=raw['check_digest'], check_passed=raw['check_passed'])
        result['last_sequence'] += 1
        result['steps'] += 1
        result['last_observation'] = payload_digest(raw)
        novel = bool(raw['evidence_digest']) and raw['evidence_digest'] not in result['evidence']
        if novel:
            if len(result['evidence']) == _MAX_EVIDENCE:
                result['reason'] = 'tracking_capacity'
            else:
                result['evidence'].append(raw['evidence_digest'])
        else:
            result['no_progress'] += 1
        signals = {}
        for name, key, maximum in (
            ('calls', raw['call_digest'] if not novel else '', base['policy']['repeat_call_limit']),
            ('errors', raw['error_digest'], base['policy']['repeat_error_limit']),
            ('checks', raw['check_digest'] if raw['check_passed'] is False else '', 2),
        ):
            if not key:
                continue
            if key not in result[name] and len(result[name]) == _MAX_KEYS:
                result['reason'] = 'tracking_capacity'
                break
            result[name][key] = min(maximum, result[name].get(key, 0) + 1)
            signals[name] = result[name][key] >= maximum
        # Only a real pass of this same named check can resolve its own debt.
        if raw['check_passed'] is True:
            result['checks'].pop(raw['check_digest'], None)
        result['reason'] = result['reason'] or (
            'phase_budget' if result['steps'] > base['policy']['phase_step_budget'] else
            'repeated_check' if signals.get('checks') else
            'repeated_error' if signals.get('errors') else
            'repeated_call' if signals.get('calls') else
            'no_progress' if not novel and result['no_progress'] >= base['policy']['no_progress_limit'] else '')
    if result['reason']:
        result['status'] = 'blocked'
    return result
