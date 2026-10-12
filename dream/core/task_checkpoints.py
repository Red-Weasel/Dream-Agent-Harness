"""Checked lifecycle on the existing RunState authority (DREAM-261).

One autonomous run is one managed task. These receipts report handler returns and
an independent *model review*, not arbitrary-effect exactly-once or executed tests.
The ordinary tool API cannot supply verifier authority or edit managed fields.
Same-user shell/SQL, native tools and trusted Python extensions remain outside
that boundary. Display tool events are not handler execution receipts.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import json
from pathlib import Path
import re
from uuid import uuid4

from .run_state import RunState, digest, validate_child_delegations
from .recovery_runtime import (RecoveryPending, RecoveryRuntime, research_digest, reported_handles,
                               file_observation, file_evidence)

_CURRENT: ContextVar[ManagedTaskCoordinator | None] = ContextVar('dream_managed_task', default=None)
_ALLOW_TOOLS: ContextVar[bool] = ContextVar('dream_managed_tool_admission', default=True)
_CHILD: ContextVar[tuple | None] = ContextVar('dream_managed_child_lease', default=None)
_DISPOSITIONS = {'returned-success', 'error', 'refused', 'interrupted', 'unknown'}


def tools_admitted() -> bool:
    return _ALLOW_TOOLS.get()


def current() -> ManagedTaskCoordinator | None:
    return _CURRENT.get()


@contextmanager
def bind(coordinator: ManagedTaskCoordinator | None, *, allow_tools: bool = True):
    token = _CURRENT.set(coordinator)
    admission = _ALLOW_TOOLS.set(allow_tools)
    try:
        yield coordinator
    finally:
        _ALLOW_TOOLS.reset(admission)
        _CURRENT.reset(token)


def _hash(value) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                             allow_nan=False))


class ManagedTaskCoordinator:
    def __init__(self, journal: RunState, tasks, *, verification_authority=None,
                 recovery_policy=None, recovery_check_authority=None,
                 assistance_policy=None, assistance_authority=None,
                 assistance_budget=None, assistance_clock=None):
        self.journal, self.tasks = journal, tasks
        self.__child_lease = None
        # Deliberately not a serialized/model-supplied capability. Python host
        # code is trusted; only the loop's evaluator retains this exact object.
        self.__verification_authority = verification_authority or object()
        self.__recovery_check_authority = recovery_check_authority or object()
        self._recovery_policy = recovery_policy
        self._assistance_policy = assistance_policy
        self.__assistance_authority = assistance_authority or object()
        if assistance_budget is not None and not callable(assistance_budget):
            raise ValueError('Assistance requires a trusted current-budget reader')
        self._assistance_budget = assistance_budget
        self._assistance_clock = assistance_clock
        self.__assistance = None
        self.__assisted_stage = None
        self.__assisted_actions = {}
        self._accepting = True
        self._stopped = False
        self._storage_failed = False

    @property
    def _state(self):
        return deepcopy(self.journal.state['managed'])

    @property
    def task(self):
        return self.tasks.get(self._state['task_id'])

    @property
    def unresolved_actions(self) -> dict:
        return self._state['unresolved']

    @property
    def pending_children(self) -> dict:
        validate_child_delegations(self.journal.state)
        return {k: v for k, v in self._state.get('children', {}).items() if v['status'] == 'reserved'}

    def _child_parent_scope(self, worker_id, session_id):
        from .tool_artifacts import ArtifactStore
        store = ArtifactStore(self.journal.state['worker_workspace'], session_id, worker_id)
        return _hash(dict(goal=self.journal.state['goal'],
                          standing_rules=self.journal.state.get('standing_rules', ''),
                          workspace_key=store.workspace_key))

    def _child_binding(self, binding):
        from .worker_checkpoints import _encode
        validate_child_delegations(self.journal.state)
        if (type(binding) is not dict or not isinstance(binding.get('delegation_id'), str)
                or re.fullmatch(r'[0-9a-f]{32}', binding['delegation_id']) is None):
            raise ValueError('Invalid child binding')
        record = self._state.get('children', {}).get(binding.get('delegation_id'))
        if record is None or _encode(record['binding']) != _encode(binding):
            raise ValueError('Mismatched child binding')
        self._validate_task()
        if self._child_parent_scope(binding['worker_id'], binding['session_id']) != binding['parent_scope_sha256']:
            raise ValueError('Child parent instructions or workspace identity changed')
        from .evaluator import ScopedReader
        try:
            _, contract_sha = ScopedReader(Path(self.journal.state['worker_workspace']), self.journal.path).read(
                str(self.journal.path / 'contract.md'))
        except (OSError, ValueError) as exc:
            raise ValueError('Child parent contract is unavailable') from exc
        if contract_sha != binding['contract_sha256']:
            raise ValueError('Child parent contract changed')
        return record

    def _child_admitted(self):
        lease = _CHILD.get()
        return bool(lease is not None and lease is self.__child_lease
                    and lease[0] is self and lease[1] in self.pending_children)

    def _child_execution_admitted(self):
        if self._state.get('children') and self.journal.state.get('status') != 'running':
            return False
        # A revoked inherited context is not a fresh parent context, even when
        # its child has returned and no pending ownership remains.
        if _CHILD.get() is not None:
            return self._child_admitted()
        return not self.pending_children

    def _unreconciled_parent_turn(self):
        # Loop marks every active ask uncertain before entering it. Its bound
        # driver may delegate during that turn; a reopened, unbound host cannot.
        return bool(self.journal.state.get('uncertain')
                    and (current() is not self or self.journal.state.get('phase') != 'worker_running'))

    def begin_child(self, *, worker_id, session_id, call_id, arguments, execution_binding) -> dict:
        """Host-only ownership reservation, before child publication or dispatch."""
        from .worker_checkpoints import _encode
        if (not self.actions_admitted or self.unresolved_actions or self.__child_lease is not None
                or self.journal.state.get('status') != 'running'
                or self._unreconciled_parent_turn() or self.journal.state.get('help_continuation') is not None):
            raise PermissionError('Child delegation admission is closed')
        if (not isinstance(worker_id, str) or re.fullmatch(r'[0-9a-f]{32}', worker_id) is None
                or any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in (session_id, call_id))
                or type(arguments) is not dict or type(execution_binding) is not dict or not execution_binding):
            raise ValueError('Invalid child delegation')
        args_raw, execution_raw = _encode(arguments), _encode(execution_binding)
        if len(args_raw) > 65536 or len(execution_raw) > 8192:
            raise ValueError('Child delegation binding exceeds capacity')
        self._validate_task()
        state = self._state
        children = state.setdefault('children', {})
        if len(children) >= 64 or any(c['binding']['worker_id'] == worker_id for c in children.values()):
            raise ValueError('Child worker identity reused or delegation capacity exhausted')
        binding = dict(version=1, delegation_id=uuid4().hex, parent_run_id=self.journal.run_id,
                       task_id=state['task_id'], project=state['project'], revision=state['revision'],
                       contract_sha256=state['contract_sha256'], worker_id=worker_id,
                       session_id=session_id, call_id=call_id,
                       arguments_sha256=digest(args_raw.decode()), execution_sha256=digest(execution_raw.decode()),
                       parent_scope_sha256=self._child_parent_scope(worker_id, session_id))
        children[binding['delegation_id']] = dict(binding=binding, status='reserved', completion=None)
        state['action_revision'] += 1
        state['receipt'] = None
        state['evidence_chain'] = _hash([state['evidence_chain'], 'child_reserved', binding])
        self._record('child_reserved', state, delegation_id=binding['delegation_id'])
        return deepcopy(binding)

    @contextmanager
    def claim_child(self, binding, *, execution_binding):
        """An exclusive in-process host lease; serialized/model values cannot grant it."""
        from .worker_checkpoints import _encode
        record = self._child_binding(binding)
        if (not self._base_actions_admitted or record['status'] != 'reserved'
                or self.journal.state.get('status') != 'running' or _CHILD.get() is not None
                or self.unresolved_actions or self._unreconciled_parent_turn()
                or self.journal.state.get('help_continuation') is not None or self.__child_lease is not None
                or type(execution_binding) is not dict or not execution_binding
                or digest(_encode(execution_binding).decode()) != binding['execution_sha256']):
            raise PermissionError('Child execution claim refused')
        lease = (self, binding['delegation_id'], object())
        token = _CHILD.set(lease)
        self.__child_lease = lease
        try:
            yield deepcopy(binding)
        finally:
            self.__child_lease = None
            _CHILD.reset(token)

    def record_child_return(self, binding, worker_journal) -> dict:
        """Save verified terminal delivery before child acknowledgement; never run work."""
        from .worker_checkpoints import WorkerJournal, WorkerCheckpointError, _encode
        record = self._child_binding(binding)
        if type(worker_journal) is not WorkerJournal:
            raise ValueError('An open host worker journal is required')
        if (worker_journal.run_id != binding['worker_id']
                or worker_journal.store.session_key != digest(binding['session_id'])
                or worker_journal.store.workspace != str(Path(self.journal.state['worker_workspace']).resolve())):
            raise ValueError('Worker journal scope does not match parent delegation')
        try:
            saved = worker_journal.inspect()
        except (WorkerCheckpointError, OSError):
            self._storage_failed = True
            self.seal_actions()
            raise
        if _encode(saved['binding']) != _encode(binding) or saved['outcome'] not in ('completed', 'incomplete'):
            raise ValueError('Worker journal has no matching terminal result')
        result = saved['snapshot'].get('parent_result')
        if (type(result) is not dict or set(result) != {'text', 'is_error'}
                or not isinstance(result['text'], str) or type(result['is_error']) is not bool
                or result['is_error'] != (saved['outcome'] == 'incomplete')):
            raise ValueError('Invalid terminal parent result')
        completion = dict(completion_seq=saved['completion_seq'], outcome=saved['outcome'],
                          snapshot_receipt=saved['snapshot_receipt'], result_sha256=_hash(result))
        if record['status'] == 'returned':
            if record['completion'] != completion:
                raise ValueError('Conflicting child terminal delivery')
            return deepcopy(completion)
        if self._storage_failed or self.journal.write_error is not None:
            raise PermissionError('Failed parent driver cannot append child delivery')
        if saved['outcome'] == 'completed' and self.unresolved_actions:
            raise PermissionError('Completed child delivery cannot erase unresolved handler effects')
        state = self._state
        state['children'][binding['delegation_id']].update(status='returned', completion=completion)
        state['action_revision'] += 1
        state['receipt'] = None
        state['evidence_chain'] = _hash([state['evidence_chain'], 'child_returned', binding['delegation_id'], completion])
        self._record('child_returned', state, delegation_id=binding['delegation_id'])
        return deepcopy(completion)

    def _runtime(self, state=None):
        options = {'assistance_policy': self._assistance_policy} if self._assistance_policy is not None else {}
        return RecoveryRuntime(self.journal.state, state if state is not None else self._state,
                               policy=self._recovery_policy, **options)

    def _assistance(self):
        if self.__assistance is None:
            from .help_continuation import HelpContinuation
            self.__assistance = HelpContinuation(self.journal, authority=self.__assistance_authority,
                clock=self._assistance_clock, check_checkpoint=self.__commit_assisted_check)
        return self.__assistance

    def assistance_state(self):
        """Detached host lease projection, never model permission or a stage token."""
        return self._assistance().inspect() if self.journal.state.get('help_continuation') is not None else None

    def _assistance_host(self, authority):
        if authority is not self.__assistance_authority:
            raise PermissionError('Only the trusted host may admit assisted stages')
        if self._stopped or self._storage_failed or self.journal.write_error is not None:
            raise PermissionError('Assisted task admission is closed')
        if self._assistance_budget is None:
            raise PermissionError('No trusted parent budget reader was configured')

    def authorize_assistance(self, *, authority):
        self._assistance_host(authority)
        return self._assistance().authorize(self._assistance_budget(), authority=authority)

    def begin_assisted_stage(self, kind, *, authority):
        self._assistance_host(authority)
        if self.__assisted_stage is not None:
            raise PermissionError('An assisted stage is already owned')
        self.seal_actions()
        self.__assisted_stage = self._assistance().begin_stage(kind, self._assistance_budget(), authority=authority)

    def finish_assisted_stage(self, *, passed, authority):
        self._assistance_host(authority)
        self.seal_actions()
        if self.__assisted_stage is None:
            raise PermissionError('A currently owned assisted stage is required')
        result = self._assistance().finish_stage(self.__assisted_stage, self._assistance_budget(),
                                                 authority=authority, passed=passed)
        self.__assisted_stage = None
        return result

    def execute_assisted_check(self, name, check, *, check_version, authority):
        """The dedicated final host check; ordinary check admission stays closed."""
        self._assistance_host(authority)
        self.seal_actions()
        if self.__assisted_stage is None:
            raise PermissionError('A currently owned assisted check stage is required')
        phase_check = self.journal.state.get('phase_check')
        state = self._state
        if phase_check is not None and (not isinstance(phase_check, dict) or phase_check.get('status') != 'started'
                or phase_check.get('check_name') != name or phase_check.get('check_version') != check_version
                or phase_check.get('action_revision') != state['action_revision']
                or phase_check.get('action_chain') != state['evidence_chain']):
            raise ValueError('Assisted host check requires its exact current phase intent')
        try:
            result = self._assistance().execute_check(self.__assisted_stage, check, self._assistance_budget(),
                authority=authority, check_name=name, check_version=check_version)
        except BaseException:
            self.seal_actions()
            raise
        self.__assisted_stage = None
        return result['observations'][-1]['check_passed']

    def __commit_assisted_check(self, lease, *, check_name, check_version, passed):
        """Trusted helper hook, reached only after its actual boolean callback.

        This is not a model tool or a serialized capability. The helper invokes
        it under its current private stage token and scope/budget checks.
        """
        state = self._state
        try:
            self._assistance_host(self.__assistance_authority)
            stage = self.__assisted_stage
            anchor = {'revision': state['action_revision'], 'chain': state['evidence_chain']}
            if (stage is None or stage.kind != 'check' or type(passed) is not bool
                    or lease['anchor'] != anchor or state['recovery'] != lease['origin']
                    or lease['policy']['check_name'] != check_name or lease['policy']['check_version'] != check_version):
                raise ValueError('Actual assisted check checkpoint changed its exact stage or scope')
            phase_check = self.journal.state.get('phase_check')
            changes = {'help_continuation': lease}
            if phase_check is not None:
                if (not isinstance(phase_check, dict) or phase_check.get('status') != 'started'
                        or phase_check.get('check_name') != check_name or phase_check.get('check_version') != check_version
                        or phase_check.get('action_revision') != anchor['revision']
                        or phase_check.get('action_chain') != anchor['chain']):
                    raise ValueError('Actual assisted check checkpoint changed its phase intent')
                changes['phase_check'] = {**phase_check, 'status': 'passed' if passed else 'failed',
                                          'observation_sequence': self.journal.seq + 1}
            self.journal.record('recovery_check_executed', incremental=True, managed=state, **changes,
                data=dict(check_sha256=_hash(check_name), passed=passed, exception_type=None,
                          recovery=deepcopy(state['recovery']['pending']),
                          boundary='actual assisted host check; atomic lease/phase checkpoint; original recovery retained'))
        except BaseException:
            self._storage_failed = True
            self._accepting = False
            raise

    def _assisted_worker(self):
        helper = self._assistance()
        try:
            helper._host(self.__assistance_authority)
        except PermissionError:
            return False
        lease = self.assistance_state()
        stage = self.__assisted_stage
        if not lease or lease['status'] != 'assisted' or stage is None or stage.kind != 'worker':
            return False
        helper._token(stage, lease)
        return bool(self._state['recovery'] == lease['origin'] and not self.journal.state.get('uncertain'))

    @property
    def pending_recovery(self):
        """Detached durable decision; HTTP dispatch checks this after each handler."""
        pending = deepcopy(self._runtime().pending)
        if pending and pending['kind'] == 'needs_help' and not self._stopped and not self._storage_failed:
            lease = self.assistance_state()
            if lease and (lease['status'] == 'ready_for_verification' or self._assisted_worker()):
                return None
        return pending

    @property
    def _base_actions_admitted(self) -> bool:
        """One host decision for wrappers and HTTP builtins, even after I/O loss.

        Already admitted handlers may still settle their receipts. This property
        only controls new entries; it does not give a verifier or model a grant.
        """
        return bool(self._accepting and not self._stopped and not self._storage_failed
                    and self.journal.write_error is None and not self.pending_recovery
                    and (self.journal.state.get('help_continuation') is None or self._assisted_worker())
                    and not self._state['completed_receipt']
                    and not any(a.get('disposition') != 'intent'
                                for a in self.unresolved_actions.values()))

    @property
    def actions_admitted(self) -> bool:
        return bool(self._base_actions_admitted and self._child_execution_admitted())

    def _record(self, event, state, *, _parent_context=None, **data):
        try:
            validate_child_delegations({**self.journal.state, 'managed': state})
            # A checked reset and retirement of its old task-round context share
            # one ledger commit. No crash may leave a new generation with an old
            # conversation offered as its restart point.
            retirement = {'parent_context': None, 'parent_context_superseded': False} if event == 'boundary_acknowledged' else {}
            if _parent_context is not None:
                from .parent_continuation import validate_reference
                validate_reference(_parent_context, run_id=self.journal.run_id)
                if event not in {'parent_context_captured', 'parent_context_settled'}:
                    raise ValueError('Conversation publication requires its host checkpoint event')
                retirement = {'parent_context': deepcopy(_parent_context), 'parent_context_superseded': False}
            self.journal.record(event, data=data, incremental=True, managed=state, **retirement)
        except BaseException:
            self._storage_failed = True
            self._accepting = False
            raise

    def checkpoint_parent_context(self, reference, *, settled=False):
        """Publish a trusted adapter snapshot and retire earlier verification atomically.

        Capturing is new work and requires the live parent grant. Settlement is
        bookkeeping: it may finish after Stop, but cannot clear effect uncertainty,
        spend/reset budgets, reopen admission or manufacture checked completion.
        """
        from .parent_continuation import validate_reference
        validate_reference(reference, run_id=self.journal.run_id)
        state = self._state
        if (type(settled) is not bool or reference['generation'] != state['generation']
                or current() is not self or _CHILD.get() is not None
                or self._storage_failed or self.journal.write_error is not None):
            raise PermissionError('Parent conversation checkpoint admission is closed')
        previous = self.journal.state.get('parent_context')
        if settled:
            if (reference['status'] != 'settled' or previous is None
                    or previous['status'] != 'captured'
                    or any(reference[k] != previous[k] for k in reference if k not in {'status', 'receipt'})
                    or self.journal.state.get('parent_context_superseded')):
                raise ValueError('Settlement must match the reserved parent conversation')
        elif (reference['status'] != 'captured' or not tools_admitted()
                or not self.actions_admitted or self.unresolved_actions
                or self.journal.state.get('status') != 'running'
                or self._unreconciled_parent_turn()
                or self.journal.state.get('help_continuation') is not None
                or (previous is not None and previous['status'] != 'settled')):
            raise PermissionError('New conversation requires an admitted live parent')
        self._validate_task()
        # Conversation publication is a dispatch boundary: match the protected
        # acceptance contract itself, not only its SQLite projection.
        from .evaluator import ScopedReader
        try:
            _, contract_sha = ScopedReader(Path(self.journal.state['worker_workspace']), self.journal.path).read(
                str(self.journal.path / 'contract.md'))
        except (OSError, ValueError) as exc:
            raise ValueError('Parent acceptance contract is unavailable') from exc
        if contract_sha != state['contract_sha256']:
            raise ValueError('Parent acceptance contract changed')
        event = 'parent_context_settled' if settled else 'parent_context_captured'
        state['action_revision'] += 1
        state['receipt'] = None
        state['evidence_chain'] = _hash([state['evidence_chain'], event, reference])
        self._record(event, state, _parent_context=reference, capture_id=reference['capture_id'])

    def attach(self, *, standing_rules: str = ''):
        state = self.journal.state
        if state.get('managed'):
            self._validate_task()
            runtime = self._runtime()
            if 'recovery' not in state['managed']:
                managed = self._state
                managed['recovery'] = runtime.snapshot()
                self._record('recovery_initialized', managed)
            return self.task
        if not state.get('contract') or not state.get('contract_sha256'):
            raise ValueError('A managed task requires an established immutable contract')
        task = self.tasks._attach_managed(self.journal.run_id, state['goal'], state['contract'])
        managed = dict(version=1, task_id=task['id'], project=task['project'], revision=1,
                       contract_sha256=state['contract_sha256'], unresolved={}, recent=[],
                       action_revision=0, evidence_chain=_hash([]), receipt=None,
                       completed_receipt=None, boundary=None, generation=0, children={})
        managed['recovery'] = self._runtime(managed).snapshot()
        self.journal.record('managed_task_attached', incremental=True, managed=managed,
                            standing_rules=standing_rules)
        return task

    def _validate_task(self):
        state, row = self._state, self.task
        if row is None or any(row[k] != v for k, v in {
            'managed_run': self.journal.run_id, 'project': state['project'],
            'managed_revision': state['revision'], 'managed_contract': self.journal.state['contract'],
        }.items()):
            raise ValueError('Managed task contract, revision or scope mismatch')
        return row

    def open_actions(self):
        if self._stopped or self._storage_failed or self.pending_recovery or self._state['completed_receipt']:
            raise ValueError('Managed task is stopped or already checked complete')
        self._accepting = True

    def seal_actions(self):
        self._accepting = False

    def stop(self):
        try:
            if self._storage_failed or self.journal.write_error is not None:
                return
            state = self._state
            runtime = self._runtime(state)
            if runtime.pending and runtime.pending['kind'] == 'stopped':
                return
            lease = self.assistance_state()
            preserve_checked = False
            if lease and lease['status'] == 'ready_for_verification' and state['completed_receipt']:
                try:
                    self._checked_receipt(state['completed_receipt'])
                    preserve_checked = True
                except (ValueError, PermissionError, OSError):
                    pass
            self._stopped = True
            self.seal_actions()
            if lease and lease['status'] != 'closed' and not preserve_checked:
                self._assistance().close('stopped', authority=self.__assistance_authority)
            state = self._state
            runtime = self._runtime(state)
            state['recovery_before_stop'] = runtime.snapshot()
            state['recovery'] = runtime.observe(stopped=True)
            self._record('recovery_stopped', state)
        finally:
            # Stop seals admission even when validating or recording its history
            # fails. Preserving a checked lease never reopens a stage token.
            self._stopped = True
            self.seal_actions()

    def resume_stopped(self, *, note: str):
        """Trusted explicit owner resume, after uncertainty/action reconciliation."""
        if not isinstance(note, str) or not note.strip():
            raise ValueError('Explicit owner resume requires a meaningful reconciliation note')
        state = self._state
        if self._storage_failed or state['unresolved'] or self.journal.state.get('uncertain'):
            raise RecoveryPending('Reconcile every uncertain action before explicit Stop resume')
        runtime = self._runtime(state)
        if not runtime.pending or runtime.pending['kind'] != 'stopped':
            raise ValueError('Only an explicit stopped run can resume')
        prior = state.get('recovery_before_stop')
        if prior is None:
            # Compatibility for earlier stopped journals: no budget/history reset.
            prior = runtime.snapshot()
            prior['pending'] = None
            prior['controller'].update(status='active', reason='')
        candidate = {**state, 'recovery': prior}
        restored = self._runtime(candidate)
        old, new = runtime.snapshot(), restored.snapshot()
        for key in ('steps', 'no_progress', 'attempts', 'help_used', 'last_sequence',
                    'last_observation', 'calls', 'errors', 'checks', 'evidence', 'policy'):
            if old['controller'][key] != new['controller'][key]:
                raise ValueError('Stop resume cannot change recovery history or budgets')
        if old['total_interventions'] != new['total_interventions']:
            raise ValueError('Stop resume cannot replenish interventions')
        state['recovery'] = restored.snapshot()
        state.pop('recovery_before_stop', None)
        self._record('recovery_owner_resumed', state, note_sha256=_hash(note))
        self._stopped = False

    def begin_action(self, tool: str, arguments: dict) -> str:
        """Commit intent before actual handler entry. Never replays an intent."""
        if not self._accepting or self._stopped or self._storage_failed or self.journal.write_error is not None:
            raise PermissionError('Managed task action admission is closed')
        if self.pending_recovery:
            raise RecoveryPending('Persisted recovery decision must settle before another handler')
        if not self._child_execution_admitted():
            raise PermissionError('Child ownership, revoked context or closed parent denies action entry')
        state = self._state
        if any(action['disposition'] != 'intent' for action in state['unresolved'].values()):
            raise PermissionError('Settled uncertain effects require action-ID reconciliation before another handler')
        if state['completed_receipt']:
            raise ValueError('Managed task is already checked complete')
        if self.journal.state.get('help_continuation') is not None and not self._assisted_worker():
            raise PermissionError('Assisted actions require the current host worker token')
        if not isinstance(tool, str) or not tool or len(tool) > 300 or not isinstance(arguments, dict):
            raise ValueError('Invalid managed action')
        if len(state['unresolved']) >= 128:
            raise ValueError('Too many unresolved managed actions; reconcile before continuing')
        self._validate_task()
        assisted_slot = None
        if self._assisted_worker():
            self._assistance_host(self.__assistance_authority)
            assisted_slot = self._assistance().reserve_action(self.__assisted_stage,
                _hash([tool, _hash(arguments)]), self._assistance_budget(), authority=self.__assistance_authority)
        action_id = uuid4().hex
        intent = dict(id=action_id, tool=tool, arguments_sha256=_hash(arguments), disposition='intent')
        if self._child_admitted():
            intent['delegation_id'] = _CHILD.get()[1]
        state['unresolved'][action_id] = intent
        state['action_revision'] += 1
        state['receipt'] = None
        self._record('action_intent', state, action=intent)
        if assisted_slot is not None:
            self.__assisted_actions[action_id] = assisted_slot
        return action_id

    def finish_action(self, action_id: str, disposition: str, result=None, *, _research_digest='',
                      _read_failure=None):
        """A successful return establishes no task-completion or effect guarantee."""
        if disposition not in _DISPOSITIONS:
            raise ValueError('Invalid action disposition')
        from .read_effects import proven_read_failure
        read_failure = proven_read_failure(_read_failure, action_id)
        if read_failure and disposition != 'error':
            raise ValueError('Read-failure observation requires an error receipt')
        state = self._state
        result_hash = _hash(result)
        intent = state['unresolved'].get(action_id)
        if intent is None:
            previous = next((r for r in state['recent'] if r['id'] == action_id), None)
            if previous and previous['disposition'] == disposition and previous['result_sha256'] == result_hash:
                return deepcopy(previous)
            raise ValueError('Unknown or conflicting action receipt; no effect replay authorized')
        if intent['disposition'] != 'intent':
            if intent['disposition'] == disposition and intent['result_sha256'] == result_hash:
                return deepcopy(intent)
            raise ValueError('Conflicting action disposition')
        receipt = {**intent, 'disposition': disposition, 'result_sha256': result_hash,
                   'reported_artifacts': reported_handles(result)}
        if read_failure:
            receipt.update(effect_status='host_observed_no_effect', effect_observation=read_failure)
        if disposition in {'returned-success', 'refused'} or read_failure:
            del state['unresolved'][action_id]
        else:
            # A handler error/cancellation may follow a real effect. Explicit
            # reconciliation is required, even when the worker says DONE.
            state['unresolved'][action_id] = receipt
        state['recent'] = (state['recent'] + [receipt])[-16:]
        state['action_revision'] += 1
        state['evidence_chain'] = _hash([state['evidence_chain'], receipt])
        state['receipt'] = None
        runtime = self._runtime(state)
        # In-flight handlers may settle after another handler closed admission.
        # Their receipts remain authoritative; no late result clears that decision.
        if not runtime.pending:
            if self._stopped or disposition == 'interrupted':
                state['recovery_before_stop'] = runtime.snapshot()
            state['recovery'] = runtime.observe(
                call_digest=_hash([intent['tool'], intent['arguments_sha256']]),
                error_digest=_hash([intent['tool'], result]) if disposition == 'error' else '',
                evidence_digest=_research_digest if disposition == 'returned-success' else '',
                stopped=self._stopped or disposition == 'interrupted')
        self._record('action_returned', state, action=receipt,
                     recovery=deepcopy(state['recovery']['pending']))
        assisted_slot = self.__assisted_actions.get(action_id)
        if assisted_slot is not None:
            try:
                # Stop may close/revoke the stage while an entered handler is
                # returning. Its real receipt still settles; a closed lease
                # must not require that revoked token or pretend I/O failed.
                if self.assistance_state()['status'] != 'closed':
                    self._assistance().finish_action(self.__assisted_stage, assisted_slot, receipt,
                        authority=self.__assistance_authority, evidence_digest=_research_digest)
            except BaseException:
                # The real handler receipt already exists. Never replay it if
                # the separate assisted observation cannot be saved/validated.
                self._storage_failed = True
                self.seal_actions()
                raise
            del self.__assisted_actions[action_id]
        return deepcopy(receipt)

    def prepare_recovery_intervention(self) -> dict:
        """Consume one reserved changed-approach turn; never reset or replay work."""
        if self._stopped or self._storage_failed:
            raise RecoveryPending('Recovery admission is closed')
        state = self._state
        if state['unresolved']:
            raise RecoveryPending('Unresolved effects require explicit reconciliation before recovery')
        runtime = self._runtime(state)
        decision = deepcopy(runtime.pending)
        state['recovery'] = runtime.consume_intervention()
        state['recovery_intervention'] = decision
        capsule = self._recovery_capsule(state, decision)
        self._record('recovery_intervention_prepared', state, decision=decision,
                     capsule_sha256=_hash(capsule))
        return capsule

    def observe_guard_refusal(self, tool: str, arguments: dict) -> bool:
        """Count refused repetition of the latest actual call, not an execution.

        The HTTP guard invokes this host method. Unknown calls, changed args,
        uncertain effects and closed admission cannot acquire recovery authority.
        """
        if not self.actions_admitted or not isinstance(tool, str) or not isinstance(arguments, dict):
            return False
        state = self._state
        if not state['recent']:
            return False
        latest = state['recent'][-1]
        if latest['tool'] != tool or latest['arguments_sha256'] != _hash(arguments):
            return False
        if self._assisted_worker():
            self.seal_actions()
            try:
                self._assistance().close('interrupted', authority=self.__assistance_authority)
            except BaseException:
                self._storage_failed = True
                raise
            self._record('assistance_guard_refused', state, action_reference=latest['id'],
                         boundary='host refusal; no extra handler or recovery budget')
            return True
        runtime = self._runtime(state)
        state['recovery'] = runtime.observe(call_digest=_hash([tool, latest['arguments_sha256']]))
        state['receipt'] = None
        self._record('recovery_guard_refused', state, action_reference=latest['id'],
                     call_sha256=_hash([tool, latest['arguments_sha256']]),
                     boundary='host repetition refusal; no handler entered',
                     recovery=deepcopy(state['recovery']['pending']))
        return bool(self.pending_recovery)

    def observe_generation_cut(self, cut) -> bool:
        """Observe a closed lead stream, not a handler or server completion.

        Called only by the trusted HTTP adapter after its private cut identity,
        cleanup, deadline and Stop fences. Model error/event metadata is never
        interpreted as this signal. Same-user Python remains trusted host code.
        """
        from .stream_text_repetition import TextRepetitionCut
        if (type(cut) is not TextRepetitionCut or cut.channel not in ('content', 'reasoning_content')
                or type(cut.occurrences) is not int or cut.occurrences != 6
                or type(cut.line_bytes) is not int or not 48 <= cut.line_bytes <= 16384
                or not isinstance(cut.line_sha256, str)
                or re.fullmatch('[0-9a-f]{64}', cut.line_sha256) is None):
            raise ValueError('Expected a bounded actual text-cut witness')
        if (not tools_admitted() or self.journal.state.get('phase') != 'worker_running'
                or self.journal.state.get('uncertain') is not False
                or not self.actions_admitted or self.unresolved_actions):
            return False
        state = self._state
        if self._assisted_worker():
            # The one assisted stage cannot manufacture another intervention or
            # clear its original needs-help reservation after a generation cut.
            self.seal_actions()
            try:
                self._assistance().close('interrupted', authority=self.__assistance_authority)
            except BaseException:
                self._storage_failed = True
                raise
            self._record('assistance_generation_cut', state, channel=cut.channel,
                         occurrences=cut.occurrences, line_sha256=cut.line_sha256,
                         line_bytes=cut.line_bytes, boundary='closed client stream; no calls from this reply entered')
            return True
        runtime = self._runtime(state)
        state['recovery'] = runtime.observe(generation_cut=True)
        state['receipt'] = None
        self._record('generation_stream_cut_observed', state, channel=cut.channel,
                     occurrences=cut.occurrences, line_sha256=cut.line_sha256, line_bytes=cut.line_bytes,
                     recovery=deepcopy(state['recovery']['pending']),
                     boundary='closed client stream; server completion unconfirmed; no reply calls entered')
        return bool(self.pending_recovery)

    def recovery_guidance(self):
        """Reconstruct the last prepared intervention after a pre-dispatch crash."""
        state = self._state
        decision = state.get('recovery_intervention')
        if decision is None:
            return None
        if (not isinstance(decision, dict) or decision.get('kind') != 'recover'
                or type(decision.get('attempts')) is not int
                or not 1 <= decision['attempts'] <= state['recovery']['total_interventions']):
            raise ValueError('Invalid prepared recovery intervention')
        return self._recovery_capsule(state, decision)

    def _recovery_capsule(self, state, decision):
        return dict(goal=self.journal.state['goal'], contract=self.journal.state['contract'],
                       standing_rules=self.journal.state.get('standing_rules', ''),
                       task_id=state['task_id'], project=state['project'], revision=state['revision'],
                       run_directory=str(self.journal.path), ledger='ledger.jsonl', progress='progress.md',
                       unresolved_actions=deepcopy(state['unresolved']), recent_actions=deepcopy(state['recent']),
                       context_archive=deepcopy(self.journal.state.get('context_archive')),
                       lead_artifacts=self._lead_artifact_hints(),
                       decision=decision,
                       instruction='Change approach using recorded evidence. Inspect existing results first. '
                                   'Do not replay completed or uncertain actions. No reset or completion is authorized.')

    def _lead_artifact_hints(self):
        """Bounded journal provenance hints; grants and byte checks stay in HTTP.

        Validate even omitted receipts. Bodies/incidental fields never enter a
        changed-approach prompt, and truncation never hides malformed metadata.
        """
        receipts = self.journal.state.get('lead_artifacts', [])
        if not isinstance(receipts, list) or len(receipts) > 256:
            raise ValueError('Invalid bounded lead artifact journal')
        metadata = ('version', 'handle', 'sha256', 'full_bytes', 'workspace_key',
                    'session_key', 'run_key', 'call_key', 'session_id', 'run_id', 'workspace')
        hints = []
        for receipt in receipts:
            if (not isinstance(receipt, dict)
                    or any(not isinstance(receipt.get(k), str) or not receipt[k] or len(receipt[k]) > 4096
                           for k in ('handle', 'session_id', 'run_id', 'workspace'))
                    or receipt['run_id'] != self.journal.run_id
                    or Path(receipt['workspace']).resolve() != Path(self.journal.state['worker_workspace']).resolve()):
                raise ValueError('Lead artifact does not belong to this run/workspace')
            match = re.fullmatch(r'dream-artifact:v1:([0-9a-f]{64}):([0-9a-f]{64}):'
                                 r'([0-9a-f]{64}):([0-9a-f]{64}):([0-9a-f]{64}):(0|[1-9][0-9]{0,9})', receipt['handle'])
            if (match is None or type(receipt.get('version')) is not int or receipt['version'] != 1
                    or type(receipt.get('full_bytes')) is not int
                    or receipt['full_bytes'] != int(match[6])
                    or any(receipt.get(k) != value for k, value in zip(
                        ('workspace_key', 'session_key', 'run_key', 'call_key', 'sha256'), match.groups()[:5]))
                    or receipt['session_key'] != digest(receipt['session_id'])
                    or receipt['run_key'] != digest(receipt['run_id'])):
                raise ValueError('Malformed lead artifact receipt provenance')
            hints.append({k: receipt[k] for k in metadata})
        result = {'total': len(hints), 'omitted': max(0, len(hints) - 8), 'hints': hints[-8:]}
        while len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 16384:
            result['hints'].pop(0)
            result['omitted'] += 1
        return result

    def execute_check(self, name: str, check, *, authority):
        """Run a host-supplied named boolean check; never accept a model verdict.

        The caller owns execution containment/timeouts for its callable. The key
        is supplied by trusted host setup, never through model tool arguments.
        """
        if authority is not self.__recovery_check_authority:
            raise PermissionError('Only the trusted host may execute recovery checks')
        if self.journal.state.get('help_continuation') is not None:
            raise RecoveryPending('Assisted checks require the dedicated pinned host stage')
        if (not isinstance(name, str) or not name.strip() or len(name) > 256
                or not callable(check) or asyncio.iscoroutinefunction(check)):
            raise ValueError('A bounded named synchronous host check is required')
        if self._stopped or self._storage_failed or self.pending_recovery:
            raise RecoveryPending('Recovery check admission is closed')
        error = None
        try:
            passed = check()
        except Exception as exc:
            passed, error = False, exc
        if type(passed) is not bool:
            raise ValueError('Host check must return a boolean execution result')
        state = self._state
        runtime = self._runtime(state)
        state['recovery'] = runtime.observe(check_digest=_hash(name), check_passed=passed,
            evidence_digest=_hash(['host-check', name, True]) if passed else '')
        state['receipt'] = None
        self._record('recovery_check_executed', state, check_sha256=_hash(name), passed=passed,
                     exception_type=type(error).__name__ if error else None,
                     recovery=deepcopy(state['recovery']['pending']))
        if error is not None:
            raise error
        return passed

    def record_observation_failure(self, reason: str = 'artifact_persistence'):
        """Host-only admission stop after a real result could not be preserved.

        This does not revise the handler receipt, manufacture evidence/errors,
        or spend interventions/help. Never expose this as a model tool.
        """
        if not isinstance(reason, str) or not reason or len(reason) > 128:
            raise ValueError('A bounded observation failure reason is required')
        self.seal_actions()
        if self._storage_failed or self.journal.write_error is not None:
            raise RecoveryPending('Observation failure cannot be persisted; admission is closed')
        lease = self.assistance_state()
        if lease and lease['status'] != 'closed':
            self._assistance().close('storage', authority=self.__assistance_authority)
        state = self._state
        runtime = self._runtime(state)
        state['recovery'] = runtime.block_tracking()
        state['observation_failure'] = reason
        state['receipt'] = None
        self._record('recovery_observation_failed', state, reason=reason,
                     recovery=deepcopy(state['recovery']['pending']))
        return deepcopy(state['recovery']['pending'])

    def reconcile_action(self, action_id: str, *, note: str):
        """Explicit trusted caller report, never a model tool or effect receipt."""
        if not isinstance(note, str) or not note.strip():
            raise ValueError('Action reconciliation requires an explicit caller report')
        state = self._state
        if action_id not in state['unresolved']:
            raise ValueError('Unknown unresolved action')
        del state['unresolved'][action_id]
        state['action_revision'] += 1
        state['receipt'] = None
        state['evidence_chain'] = _hash([state['evidence_chain'], action_id, note])
        self._record('action_reconciled', state, action_id=action_id, note=note,
                     provenance='explicit caller report; not independently inspected evidence')

    def _check_evidence(self, inspected: dict):
        from .evaluator import ScopedReader
        if not isinstance(inspected, dict) or not inspected:
            raise ValueError('Verification requires inspected evidence')
        contract_path = str(self.journal.path / 'contract.md')
        progress_path = str(self.journal.path / 'progress.md')
        if inspected.get(contract_path) != self._state['contract_sha256'] or progress_path not in inspected:
            raise ValueError('Verification contract/progress evidence is missing or changed')
        reader = ScopedReader(Path(self.journal.state['worker_workspace']), self.journal.path)
        for path, expected in inspected.items():
            if not isinstance(path, str) or not isinstance(expected, str):
                raise ValueError('Invalid verification evidence')
            try:
                _, actual = reader.read(path)
            except (OSError, ValueError) as exc:
                raise ValueError('Verification evidence is unavailable or outside scope') from exc
            if actual != expected:
                raise ValueError('Verification evidence changed after review')

    def record_verification(self, *, authority, verdict: str, inspected: dict, assessment: dict) -> dict:
        if authority is not self.__verification_authority:
            raise PermissionError('Only the independent loop evaluator may record verification')
        if verdict != 'PASS':
            raise ValueError('A checked receipt requires independent PASS')
        state = self._state
        parent_context = self.journal.state.get('parent_context')
        if (self._stopped or state['unresolved'] or self.pending_children or self.journal.state.get('uncertain')
                or (parent_context and parent_context['status'] != 'settled')):
            raise ValueError('Cannot verify stopped or unresolved actions')
        if self.pending_recovery or self._storage_failed:
            raise ValueError('Cannot verify while recovery or storage failure is pending')
        if self.journal.state.get('help_continuation') is not None:
            lease = self.assistance_state()
            if lease['status'] != 'ready_for_verification':
                raise ValueError('Assisted verification requires actual finished review and host check')
        self._validate_task()
        self._check_evidence(inspected)
        receipt = dict(id=uuid4().hex, run_id=self.journal.run_id, task_id=state['task_id'],
                       revision=state['revision'], project=state['project'],
                       contract_sha256=state['contract_sha256'], action_revision=state['action_revision'],
                       evidence_sha256=_hash(inspected), inspected=deepcopy(inspected),
                       action_chain=state['evidence_chain'],
                       assessment_kind='independent_model_review', assessment_sha256=_hash(assessment))
        if self.journal.state.get('help_continuation') is not None:
            receipt['assistance_sha256'] = _hash(lease)
        state['receipt'] = receipt
        self._record('verification_checked', state, receipt=receipt)
        return deepcopy(receipt)

    def _checked_receipt(self, receipt_id):
        state = self._state
        receipt = state['receipt']
        if not receipt or receipt['id'] != receipt_id or any(receipt[k] != v for k, v in {
            'run_id': self.journal.run_id, 'task_id': state['task_id'], 'revision': state['revision'],
            'project': state['project'], 'contract_sha256': state['contract_sha256'],
            'action_revision': state['action_revision'], 'action_chain': state['evidence_chain'],
        }.items()) or receipt['evidence_sha256'] != _hash(receipt['inspected']):
            raise ValueError('Stale or mismatched managed verification receipt')
        if self.pending_recovery or (self._storage_failed and not state['completed_receipt']):
            raise ValueError('Cannot complete/reset while recovery or storage failure is pending')
        parent_context = self.journal.state.get('parent_context')
        if (self._stopped or state['unresolved'] or self.pending_children or self.journal.state.get('uncertain')
                or (parent_context and parent_context['status'] != 'settled')):
            raise ValueError('Cannot complete/reset with stopped or unresolved actions')
        if self.journal.state.get('help_continuation') is not None:
            if receipt.get('assistance_sha256') != _hash(self.assistance_state()):
                raise ValueError('Assisted completion no longer binds its exact checked lease')
        self._validate_task()
        self._check_evidence(receipt['inspected'])
        return receipt

    def complete_task(self, receipt_id: str) -> dict:
        receipt = self._checked_receipt(receipt_id)
        state = self._state
        if state['completed_receipt'] not in (None, receipt_id):
            raise ValueError('Conflicting completion receipt')
        if not state['completed_receipt']:
            state['completed_receipt'] = receipt_id
            self._record('task_checked_complete', state, receipt_id=receipt_id)
        # Journal first, SQLite projection second. An interrupted projection is
        # repaired idempotently from the existing receipt, never from model text.
        return self.tasks._complete_managed(state['task_id'], run_id=self.journal.run_id,
                    contract=self.journal.state['contract'], revision=state['revision'],
                    project=state['project'], receipt_id=receipt['id'])

    def boundary_eligibility(self) -> dict:
        state = self._state
        receipt_id = state['completed_receipt']
        try:
            self._checked_receipt(receipt_id)
            if not receipt_id or self.task['managed_receipt'] != receipt_id:
                raise ValueError('Task completion is not durably projected')
        except (ValueError, OSError) as exc:
            return dict(eligible=False, reason=str(exc), receipt_id=receipt_id, generation=state['generation'])
        return dict(eligible=True, reason='independent model review receipt checked',
                    receipt_id=receipt_id, generation=state['generation'])

    def prepare_boundary(self, receipt_id: str, *, standing_rules: str | None = None) -> dict:
        self._checked_receipt(receipt_id)
        state = self._state
        if state['completed_receipt'] != receipt_id or self.task['managed_receipt'] != receipt_id:
            raise ValueError('Task must be checked complete before a boundary')
        if state['boundary']:
            if state['boundary']['receipt_id'] != receipt_id:
                raise ValueError('Conflicting boundary receipt')
            return deepcopy(state['boundary'])
        if standing_rules is not None and not isinstance(standing_rules, str):
            raise ValueError('Standing rules must be text')
        # Immutable original goal/contract plus full original standing head. An
        # adapter can also retain its current head; no model-authored summary is
        # substituted for either original text.
        capsule = dict(goal=self.journal.state['goal'], contract=self.journal.state['contract'],
                       standing_rules=self.journal.state.get('standing_rules', ''),
                       current_standing_rules=standing_rules,
                       backlog=dict(task_id=state['task_id'], run_id=self.journal.run_id,
                                    project=state['project'], revision=state['revision']),
                       evidence=deepcopy(state['receipt']), unresolved_actions=state['unresolved'],
                       run_directory=str(self.journal.path), ledger='ledger.jsonl', progress='progress.md',
                       coverage='Dream registered handler returns only; native/host effects not exhaustively observed')
        token = dict(id=uuid4().hex, generation=state['generation'] + 1,
                     receipt_id=receipt_id, status='prepared', capsule=capsule)
        state['boundary'] = token
        self._record('boundary_prepared', state, boundary_id=token['id'], generation=token['generation'])
        return deepcopy(token)

    def acknowledge_boundary(self, boundary_id: str, *, generation: int, settled: bool) -> dict:
        state = self._state
        token = state['boundary']
        if settled is not True or not token or token['id'] != boundary_id or type(generation) is not int or token['generation'] != generation:
            raise ValueError('Boundary acknowledgement requires the matching settled reset')
        self._checked_receipt(token['receipt_id'])
        if token['status'] == 'acknowledged':
            return deepcopy(token)
        token['status'] = 'acknowledged'
        state['generation'] = generation
        self._record('boundary_acknowledged', state, boundary_id=boundary_id, generation=generation)
        return deepcopy(token)


def wrap_tool(tool):
    """Inside Engine permission/hook middleware, immediately around its handler."""
    from claude_agent_sdk import SdkMcpTool
    @wraps(tool.handler)
    async def handler(arguments):
        if not _ALLOW_TOOLS.get():
            return {'is_error': True, 'content': [{'type': 'text', 'text':
                'Tool execution is refused while negotiating the acceptance contract. Return the checklist first.'}]}
        coordinator = current()
        if coordinator is None:
            return await tool.handler(arguments)
        action_id = coordinator.begin_action(tool.name, arguments)
        from .read_effects import capture_read_failure
        try:
            before = file_observation(tool, arguments, coordinator.journal.state, coordinator.journal.path)
            with capture_read_failure(tool, action_id) as read_failure:
                result = await tool.handler(arguments)
        except asyncio.CancelledError as exc:
            try:
                coordinator.finish_action(action_id, 'interrupted')
            except Exception as storage_exc:
                exc.add_note(f'Interrupted action receipt failed: {type(storage_exc).__name__}; reconcile durable intent.')
                raise exc from storage_exc
            raise
        except BaseException as exc:
            coordinator.finish_action(action_id, 'error', {'exception_type': type(exc).__name__})
            raise
        failed = isinstance(result, dict) and bool(result.get('is_error') or result.get('isError'))
        observed = research_digest(tool, result, coordinator.journal.state['goal'],
                                   coordinator.journal.state['contract']) if not failed else ''
        if not failed and before is not None:
            after = file_observation(tool, arguments, coordinator.journal.state, coordinator.journal.path)
            observed = file_evidence(before, after, arguments, result) or observed
        coordinator.finish_action(action_id, 'error' if failed else 'returned-success', result,
                                  _research_digest=observed,
                                  _read_failure=read_failure if failed and read_failure is not None
                                                and read_failure.reason is not None else None)
        return result
    wrapped = SdkMcpTool(name=tool.name, description=tool.description,
                         input_schema=tool.input_schema, handler=handler,
                         annotations=getattr(tool, 'annotations', None))
    for key, value in vars(tool).items():
        if key.startswith('_dream_'):
            setattr(wrapped, key, value)
    return wrapped
