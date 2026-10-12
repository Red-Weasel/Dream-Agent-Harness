"""Host-owned assisted lease; no transport, execution, reset or completion grant.

Policy is declared with the initial RecoveryRuntime. The original terminal
recovery remains intact. Existing coordinator APIs deliberately stay closed;
activation requires explicit stage-aware host integration. The caller must hold
both RunState locks and reserve parent dispatch BEFORE requesting a stage token.
Only the returned in-process token authorizes entry. An intent found on restart
is uncertain and cannot be replayed. Advice is never parsed as an instruction.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
import inspect
import json
from threading import RLock
from uuid import uuid4
from weakref import WeakKeyDictionary

from .recovery import assisted_projection, payload_digest, _digest, _integer
from .run_state import RunState


def _time(value):
    if type(value) is not str or len(value) > 40:
        raise ValueError('Invalid assistance clock')
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('Invalid assistance clock') from exc
    if result.utcoffset() is None:
        raise ValueError('Assistance requires an explicit timezone')
    return result


@dataclass(frozen=True)
class AssistancePolicy:
    """None means disabled; enabled policies are immutable and predeclared."""
    budget_identity: str
    deadline: str
    max_dispatches: int
    check_name: str
    check_version: str
    max_actions: int = 4
    max_observations: int = 5

    def __post_init__(self):
        _digest(self.budget_identity); _digest(self.check_version); _time(self.deadline)
        _integer(self.max_dispatches, 1, 512)
        _integer(self.max_actions, 1, 8); _integer(self.max_observations, 2, 16)
        if self.max_observations < self.max_actions + 1:
            raise ValueError('Assistance must leave one observation for the host check')
        if type(self.check_name) is not str or not self.check_name.strip() or len(self.check_name) > 128:
            raise ValueError('Invalid pinned assistance check')

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {f.name for f in fields(cls)}:
            raise ValueError('Invalid assistance policy fields')
        return cls(**value)


@dataclass(frozen=True)
class BudgetView:
    identity: str
    deadline: str
    maximum: int
    used: int
    last_kind: str | None
    check_name: str
    check_version: str
    parent_seq: int
    parent_clock: str
    _parent: RunState = field(repr=False, compare=False)
    _child: RunState = field(repr=False, compare=False)


def budget_from_run(parent: RunState, child: RunState) -> BudgetView:
    """Read actual locked parent/child projections; never write or reserve work.

    This validates the budget/phase linkage required here, not historical phase
    completion. PhaseRunner remains responsible for its full schedule admission.
    """
    if type(parent) is not RunState or type(child) is not RunState:
        raise ValueError('Actual parent and child RunState required')
    if (parent.write_error is not None or child.write_error is not None
            or parent._lock is None or child._lock is None):
        raise PermissionError('Assistance journal admission is closed')
    try:
        schedule = parent.state['phase_schedule']; manifest = schedule['manifest']
        if len(json.dumps(manifest,ensure_ascii=False,allow_nan=False).encode('utf-8'))>131072:
            raise ValueError('Schedule manifest exceeds its bounded authority')
        phases = manifest['phases']; index = schedule['cursor']
        if type(phases) is not list or not 1 <= len(phases) <= 32:
            raise ValueError('Invalid bounded phase schedule')
        _integer(index, 0, len(phases) - 1)
        phase = phases[index]; row = schedule['phases'][index]
        _digest(schedule['manifest_sha256'])
        if payload_digest(manifest) != schedule['manifest_sha256']:
            raise ValueError('Schedule manifest changed')
        binding = dict(schedule_id=parent.run_id, schedule_sha256=schedule['manifest_sha256'],
                       phase_id=phase['id'], phase_index=index, phase_sha256=payload_digest(phase))
        if (child.state['phase_binding'] != binding or row['run_id'] != child.run_id
                or row['id'] != phase['id'] or row['status'] not in {'running','reserved','blocked'}
                or child.state['goal'] != phase['goal'] or child.state['contract'] != phase['contract']
                or child.state['worker_workspace'] != manifest['workspace']
                or (child.state.get('managed') is not None
                    and child.state['managed']['project'] != manifest['project'])):
            raise ValueError('Assistance budget belongs to another task or phase')
        maximum = manifest['limits']['max_dispatches']; used = schedule['dispatches']
        _integer(maximum, 1, 512); _integer(used, 0, maximum)
        _integer(manifest['limits']['max_wall_seconds'],1,86400)
        deadline = schedule['deadline']; created = _time(schedule['created_at'])
        if (_time(deadline) - created).total_seconds() != manifest['limits']['max_wall_seconds']:
            raise ValueError('Original schedule deadline changed')
        if not created <= _time(schedule['last_clock']) <= _time(deadline):
            raise ValueError('Invalid schedule clock')
        last = schedule.get('last_dispatch')
        if last is not None and (type(last) is not dict or set(last) != {'phase','kind'}
                or type(last['phase']) is not int or not 0<=last['phase']<=index
                or last['kind'] not in {'worker','independent_review','frontier_help'}):
            raise ValueError('Invalid parent dispatch receipt')
        _digest(phase['check_version'])
        return BudgetView(payload_digest(binding), deadline, maximum, used,
                          last['kind'] if last and last['phase']==index else None, phase['check_name'], phase['check_version'],
                          parent.seq, schedule['last_clock'], parent, child)
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError('Malformed assistance budget projection') from exc


def _scope(run, managed):
    from .recovery_runtime import RecoveryRuntime
    runtime = RecoveryRuntime(run, managed)
    pending = runtime.pending
    if (not pending or pending['kind'] != 'needs_help' or not pending['help_request']
            or managed['completed_receipt'] or managed['unresolved']
            or run.get('uncertain') or run.get('status') in {'stopped','done','error'}):
        raise PermissionError('Assistance requires settled needs_help with no uncertain effects')
    return dict(run_id=run['run_id'], task_id=managed['task_id'], project=managed['project'],
                revision=managed['revision'], contract_sha256=managed['contract_sha256'],
                action_revision=managed['action_revision'], action_chain=managed['evidence_chain'],
                workspace=run['worker_workspace'], pending_sequence=pending['sequence'],
                help_request_sha256=payload_digest(pending['help_request']))


def _returned_help(journal):
    return _returned_help_snapshot(journal.state)


def _returned_help_snapshot(state):
    from .frontier_help import HelpRoute, HelpRequest, TransportQualification, FrontierHelp
    saved = state.get('frontier_help')
    if (type(saved) is not dict or set(saved) != {'version','binding','qualification','status','outcome'}
            or type(saved['version']) is not int or saved['version'] != 1 or saved['status'] != 'returned'):
        raise PermissionError('Only durable returned advice can authorize assistance')
    binding = saved['binding']; outcome = saved['outcome']
    if type(binding) is not dict or set(binding) != {'scope','route','route_sha256','packet_sha256','input_bytes'}:
        raise ValueError('Invalid durable help binding')
    try:
        route = HelpRoute(**binding['route'])
        qualification = TransportQualification(**saved['qualification'])
    except (TypeError, KeyError) as exc:
        raise ValueError('Malformed returned help qualification') from exc
    if binding['route_sha256'] != payload_digest(asdict(route)):
        raise ValueError('Returned help route changed')
    _digest(binding['packet_sha256']); _integer(binding['input_bytes'],1,route.input_byte_limit)
    request = HelpRequest(route,b'',binding['route_sha256'],binding['packet_sha256'],
                          route.output_token_limit,route.output_byte_limit)
    FrontierHelp._validate_qualification(qualification,request)
    if (type(outcome) is not dict or set(outcome) != {'advice','usage','output_bytes','advisory_only'}
            or type(outcome['advice']) is not str or len(outcome['advice']) > route.output_byte_limit
            or outcome['advisory_only'] is not True or type(outcome['usage']) is not dict
            or set(outcome['usage']) != {'input_tokens','output_tokens'}):
        raise ValueError('Malformed bounded returned advice')
    _integer(outcome['output_bytes'],0,route.output_byte_limit)
    if len(outcome['advice'].encode('utf-8')) != outcome['output_bytes']:
        raise ValueError('Returned advice byte count changed')
    for name,limit in (('input_tokens',route.input_token_limit),('output_tokens',route.output_token_limit)):
        if outcome['usage'][name] is not None:_integer(outcome['usage'][name],0,limit)
    if outcome['usage']['input_tokens'] not in (None,qualification.input_tokens):
        raise ValueError('Returned actual usage changed')
    return deepcopy(saved)


def validate_help_binding(journal: RunState) -> dict:
    """Read bounded returned advice and its exact settled managed scope.

    No coordinator, transport, payload collection or execution is invoked. The
    qualified adapter's durable record is evidence of returned advice only.
    """
    if type(journal) is not RunState or journal.write_error is not None:
        raise PermissionError('Actual writable RunState required')
    saved = _returned_help(journal)
    expected = _scope(journal.state, journal.state['managed'])
    if saved['binding']['scope'] != expected:
        raise ValueError('Returned advice is stale or belongs to another scope')
    return saved


_LOCKS = WeakKeyDictionary()
_LOCKS_GUARD = RLock()


@dataclass(frozen=True)
class StageToken:
    id: str
    kind: str
    _owner: object = field(repr=False, compare=False)


class HelpContinuation:
    """A durable one-worker/review/check lease under a private host capability.

    A successful check yields ready_for_verification, never task completion.
    Host wiring must still create its normal independent checked receipt/reset.
    """
    def __init__(self, journal: RunState, *, authority, clock=None, check_checkpoint=None):
        if type(journal) is not RunState or authority is None:
            raise ValueError('A real journal and private host authority are required')
        self.journal, self.__authority = journal, authority
        self._owner = object(); self._tokens = {}; self._failed = False; self._checking=False
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if check_checkpoint is not None and (not callable(check_checkpoint)
                or inspect.iscoroutinefunction(check_checkpoint)):
            raise ValueError('A synchronous trusted host check checkpoint is required')
        self.__check_checkpoint = check_checkpoint
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(journal, RLock())

    def _host(self, authority):
        if authority is not self.__authority:
            raise PermissionError('Only the trusted host may admit assisted execution')
        if self._failed or self.journal.write_error is not None:
            raise PermissionError('Assisted journal admission is closed')

    def _record(self, event, state):
        try:
            self.journal.record(event, incremental=True, help_continuation=state)
        except BaseException:
            self._failed = True
            raise

    def _budget(self, view, policy, previous=None):
        if type(view) is not BudgetView or view._child is not self.journal or view != budget_from_run(view._parent,view._child):
            raise ValueError('Fresh actual parent budget evidence required')
        if (view.identity,view.deadline,view.maximum,view.check_name,view.check_version) != (
                policy.budget_identity,policy.deadline,policy.max_dispatches,policy.check_name,policy.check_version):
            raise ValueError('Assisted budget or immutable check changed')
        now = self.clock()
        if not isinstance(now,datetime) or now.utcoffset() is None:
            raise ValueError('Invalid host assistance clock')
        if (now >= _time(policy.deadline) or now < _time(view.parent_clock)
                or (previous and now < _time(previous['last_clock']))):
            if previous and previous['status']=='assisted':self._close(previous,'budget')
            raise PermissionError('Assisted deadline exhausted or clock reversed')
        if previous and view.used < previous['last_dispatch']:
            raise ValueError('Parent dispatch budget decreased')
        return now.isoformat()

    def inspect(self):
        """Read and validate bounded state; an unsettled intent stays uncertain."""
        return self.inspect_state(self.journal.state)

    @classmethod
    def inspect_state(cls, state):
        """Validate an already-read snapshot without journal or execution authority."""
        raw = state.get('help_continuation')
        if raw is None:
            return None
        keys = {'version','policy','binding','origin','help_sha256','status','reason','stage_index',
                'stage','action','observations','stages','last_dispatch','last_clock','anchor'}
        if type(raw) is not dict or set(raw) != keys or type(raw['version']) is not int or raw['version'] != 1:
            raise ValueError('Invalid assisted snapshot fields')
        policy = AssistancePolicy.from_dict(raw['policy'])
        from .recovery_runtime import RecoveryRuntime
        managed = deepcopy(state['managed'])
        managed['recovery'] = raw['origin']
        runtime = RecoveryRuntime(state,managed)
        if runtime.assistance_policy != policy or not runtime.pending or runtime.pending['kind'] != 'needs_help':
            raise ValueError('Assistance changed its original recovery policy')
        if type(raw['binding']) is not dict or raw['binding'] != _scope({**state,'uncertain':False,'status':'running'},
                {**managed,'unresolved':{},'completed_receipt':None,
                 'action_revision':raw['binding'].get('action_revision'),
                 'evidence_chain':raw['binding'].get('action_chain')}):
            raise ValueError('Assisted task binding changed')
        _integer(raw['binding']['action_revision'],0,1_000_000_000)
        _digest(raw['binding']['action_chain'])
        if raw['binding'] != _returned_help_snapshot(state)['binding']['scope']:
            raise ValueError('Assisted original help scope changed')
        _digest(raw['help_sha256'])
        if raw['help_sha256'] != payload_digest(state.get('frontier_help')):
            raise ValueError('Durable help outcome changed')
        _integer(raw['stage_index'],0,3); _integer(raw['last_dispatch'],0,policy.max_dispatches)
        _time(raw['last_clock'])
        if (type(raw['status']) is not str or type(raw['reason']) is not str
                or raw['status'] not in {'assisted','closed','ready_for_verification'} or raw['reason'] not in {
                '', 'stopped','uncertain','storage','failed_acceptance','failed_action','recovery_signal','budget','interrupted'}):
            raise ValueError('Invalid assisted state')
        if (raw['status']=='closed') != bool(raw['reason']):
            raise ValueError('Invalid assisted closure')
        if type(raw['observations']) is not list or len(raw['observations']) > policy.max_observations:
            raise ValueError('Assisted observation capacity exceeded')
        projection = assisted_projection(raw['origin']['controller'],raw['observations'])
        if projection['reason'] and raw['status'] != 'closed':
            raise ValueError('Recovery signal did not close assistance')
        stages = raw['stages']
        if type(stages) is not list or len(stages) != raw['stage_index']:
            raise ValueError('Invalid assisted stage count')
        for index,stage in enumerate(stages):
            cls._stage_shape(stage, settled=True)
            if stage['kind'] != ('worker','review','check')[index] or stage['passed'] is not True:
                raise ValueError('Assisted stages did not pass in order')
        if raw['stage'] is not None:
            cls._stage_shape(raw['stage'])
            if raw['stage_index'] == 3 or raw['stage']['kind'] != ('worker','review','check')[raw['stage_index']]:
                raise ValueError('Invalid unsettled assisted stage')
        cls._anchor(raw['anchor'])
        if raw['action'] is not None:
            if (type(raw['action']) is not dict or set(raw['action']) != {'id','call_digest','anchor'}
                    or raw['stage'] is None or raw['stage']['kind'] != 'worker'):
                raise ValueError('Invalid assisted action intent')
            _digest(raw['action']['call_digest']); cls._anchor(raw['action']['anchor'])
            cls._identity(raw['action']['id'])
        actions = [o for o in raw['observations'] if o['call_digest']]
        checks = [o for o in raw['observations'] if o['check_digest']]
        if (len(actions) > policy.max_actions or len(checks) > 1
                or len(actions)+len(checks)!=len(raw['observations'])
                or any(o['check_digest'] for o in actions)
                or any(o['error_digest'] or o['check_digest']!=payload_digest(policy.check_name) for o in checks)
                or raw['anchor']['revision']!=raw['binding']['action_revision']+2*len(actions)):
            raise ValueError('Assisted action/check allowance or receipt linkage changed')
        if checks and raw['stage_index']<2:
            raise ValueError('Host check preceded independent review')
        if raw['action'] and raw['action']['anchor']!=raw['anchor']:
            raise ValueError('Assisted action intent anchor changed')
        if any(stage['anchor']!=raw['anchor'] for stage in stages):
            raise ValueError('Review and check must bind the final action chain')
        identities=[stage['id'] for stage in stages]+([raw['stage']['id']] if raw['stage'] else [])
        if len(identities)!=len(set(identities)):
            raise ValueError('Assisted stage identity reused')
        if raw['stage_index']==3 and raw['status']=='assisted':
            raise ValueError('Finished assistance cannot still dispatch')
        if raw['status']=='ready_for_verification' and (raw['stage_index']!=3 or raw['stage'] or raw['action']
                or len(checks)!=1 or checks[0]['check_passed'] is not True):
            raise ValueError('Assistance has no final host check')
        if raw['status']=='ready_for_verification':
            current=state['managed']
            if (state.get('uncertain') or state.get('status') in {'stopped','error'}
                    or current['unresolved'] or current['recovery']!=raw['origin']
                    or {'revision':current['action_revision'],'chain':current['evidence_chain']}!=raw['anchor']):
                raise PermissionError('Assisted review/check no longer bind the settled final action chain')
        return deepcopy(raw)

    @staticmethod
    def _identity(value):
        if type(value) is not str or len(value)!=32 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('Invalid assisted intent identity')

    @staticmethod
    def _anchor(value):
        if type(value) is not dict or set(value)!={'revision','chain'}:
            raise ValueError('Invalid assisted action anchor')
        _integer(value['revision'],0,1_000_000_000); _digest(value['chain'])

    @classmethod
    def _stage_shape(cls,value,settled=False):
        if type(value) is not dict or set(value)!=({'id','kind','anchor'} | ({'passed'} if settled else set())):
            raise ValueError('Invalid assisted stage receipt')
        cls._identity(value['id']); cls._anchor(value['anchor'])
        if value['kind'] not in {'worker','review','check'}:
            raise ValueError('Invalid assisted stage kind')

    def _live(self,state):
        if state is None or state['status']!='assisted':
            raise PermissionError('Assisted execution is not active')
        run=self.journal.state; managed=run['managed']
        if (run.get('uncertain') or run.get('status') in {'stopped','done','error'}
                or managed['unresolved'] or managed['completed_receipt']
                or managed['recovery'] != state['origin']):
            self._close(state,'stopped' if run.get('status')=='stopped' else 'uncertain')
            raise PermissionError('Assisted scope stopped, changed or has unresolved effects')
        if {'revision':managed['action_revision'],'chain':managed['evidence_chain']} != state['anchor']:
            raise ValueError('Unexpected action outside assisted admission')

    def authorize(self, budget: BudgetView, *, authority):
        with self._lock:
            self._host(authority)
            if self.inspect() is not None:
                raise PermissionError('Assistance is one-use and cannot be renewed')
            from .recovery_runtime import RecoveryRuntime
            managed=self.journal.state['managed']; runtime=RecoveryRuntime(self.journal.state,managed)
            policy=runtime.assistance_policy
            if policy is None:
                raise PermissionError('Assistance was not predeclared')
            now=self._budget(budget,policy)
            saved=validate_help_binding(self.journal)
            if (runtime.controller.to_snapshot()['steps']+2 > runtime.controller.to_snapshot()['policy']['phase_step_budget']
                    or budget.used+2 > budget.maximum):
                raise PermissionError('Original phase/dispatch budget cannot fit assistance and review')
            state=dict(version=1,policy=asdict(policy),binding=saved['binding']['scope'],origin=runtime.snapshot(),
                help_sha256=payload_digest(saved),status='assisted',reason='',stage_index=0,stage=None,action=None,
                observations=[],stages=[],last_dispatch=budget.used,last_clock=now,
                anchor={'revision':managed['action_revision'],'chain':managed['evidence_chain']})
            self._record('assistance_authorized',state)
            return deepcopy(state)

    def begin_stage(self,kind,budget: BudgetView,*,authority):
        with self._lock:
            self._host(authority); state=self.inspect(); self._live(state)
            if state['stage'] is not None or state['action'] is not None:
                raise PermissionError('Unsettled assisted intent cannot be replayed')
            if state['stage_index']>=3 or kind!=('worker','review','check')[state['stage_index']]:
                raise PermissionError('Assisted stages must run once in order')
            policy=AssistancePolicy.from_dict(state['policy'])
            now=self._budget(budget,policy,state)
            if kind in {'worker','review'}:
                expected='worker' if kind=='worker' else 'independent_review'
                if budget.used<=state['last_dispatch'] or budget.last_kind!=expected:
                    raise PermissionError('Parent must durably reserve this stage dispatch first')
                if kind=='worker' and budget.used>=budget.maximum:
                    raise PermissionError('Original dispatch budget must leave independent review')
            state.update(stage={'id':uuid4().hex,'kind':kind,'anchor':deepcopy(state['anchor'])},
                         last_dispatch=budget.used,last_clock=now)
            self._record('assistance_stage_intent',state)
            token=StageToken(state['stage']['id'],kind,self._owner); self._tokens[token.id]=token
            return token

    def _token(self,token,state):
        if (type(token) is not StageToken or token._owner is not self._owner
                or self._tokens.get(token.id) is not token or state['stage'] is None
                or token.id!=state['stage']['id']):
            raise PermissionError('Current host stage token required; restart cannot replay an intent')

    def reserve_action(self,stage: StageToken,call_digest:str,budget:BudgetView,*,authority):
        with self._lock:
            self._host(authority); state=self.inspect(); self._live(state); self._token(stage,state)
            if stage.kind!='worker' or state['action'] is not None:
                raise PermissionError('Only a settled worker stage can admit an action')
            _digest(call_digest); policy=AssistancePolicy.from_dict(state['policy'])
            now=self._budget(budget,policy,state)
            projection=assisted_projection(state['origin']['controller'],state['observations'])
            if (len(state['observations'])>=min(policy.max_actions,policy.max_observations-1)
                    or projection['steps']+2 > projection['policy']['phase_step_budget']):
                self._close(state,'budget'); raise PermissionError('Assisted actions must leave a host check observation')
            action={'id':uuid4().hex,'call_digest':call_digest,'anchor':deepcopy(state['anchor'])}
            state.update(action=action,last_clock=now)
            self._record('assistance_action_intent',state)
            return action['id']

    def finish_action(self,stage,action_id,receipt,*,authority,evidence_digest=''):
        with self._lock:
            self._host(authority); state=self.inspect(); self._token(stage,state)
            action=state['action']; managed=self.journal.state['managed']
            if (action is None or action['id']!=action_id or type(receipt) is not dict
                    or not managed['recent'] or receipt!=managed['recent'][-1]
                    or managed['action_revision']!=action['anchor']['revision']+2
                    or managed['evidence_chain']!=payload_digest([action['anchor']['chain'],receipt])
                    or payload_digest([receipt.get('tool'),receipt.get('arguments_sha256')])!=action['call_digest']):
                raise ValueError('Actual managed action receipt does not match assisted intent')
            _digest(evidence_digest,optional=True)
            ok=receipt.get('disposition')=='returned-success' and not managed['unresolved']
            observation=dict(call_digest=action['call_digest'],error_digest='' if ok else payload_digest(receipt),
                             evidence_digest=evidence_digest if ok else '',check_digest='',check_passed=None)
            state['observations'].append(observation)
            state['anchor']={'revision':managed['action_revision'],'chain':managed['evidence_chain']}
            state['action']=None
            projection=assisted_projection(state['origin']['controller'],state['observations'])
            run=self.journal.state
            if run.get('status')=='stopped' or managed['recovery'].get('pending',{}).get('kind')=='stopped':
                state.update(status='closed',reason='stopped')
            elif run.get('uncertain') or managed['recovery']!=state['origin']:
                state.update(status='closed',reason='uncertain')
            elif not ok:state.update(status='closed',reason='failed_action')
            elif projection['reason']:state.update(status='closed',reason='recovery_signal')
            self._record('assistance_action_observed',state)
            return deepcopy(state)

    def finish_stage(self,stage,budget:BudgetView,*,authority,passed:bool):
        if type(stage) is StageToken and stage.kind=='check':
            raise PermissionError('The final check must actually execute through execute_check')
        return self._finish_stage(stage,budget,authority=authority,passed=passed)

    def execute_check(self,stage,check,budget:BudgetView,*,authority,check_name,check_version):
        """Execute the one predeclared synchronous check after its durable intent.

        The trusted caller owns callable containment/timeouts and definition
        identity. No command, callback, key or version is supplied by model text.
        A crash/interrupt leaves the check intent uncertain; it cannot rerun.
        """
        with self._lock:
            self._host(authority); state=self.inspect(); self._live(state); self._token(stage,state)
            policy=AssistancePolicy.from_dict(state['policy'])
            if (stage.kind!='check' or self._checking or not callable(check) or inspect.iscoroutinefunction(check)
                    or (check_name,check_version)!=(policy.check_name,policy.check_version)):
                raise ValueError('The exact bounded synchronous host check is required')
            self._budget(budget,policy,state)
            self._checking=True
            try:
                passed=check()
                if type(passed) is not bool:
                    if inspect.iscoroutine(passed):passed.close()
                    raise ValueError('Actual host check must return a boolean')
            except BaseException as exc:
                try:self._close(state,'interrupted' if not isinstance(exc,Exception) else 'failed_acceptance')
                except Exception as storage_exc:exc.add_note('Assisted check closure persistence failed: '+type(storage_exc).__name__)
                raise
            finally:
                self._checking=False
            return self._finish_stage(stage,budget,authority=authority,passed=passed,
                                      check_name=check_name,check_version=check_version)

    def _finish_stage(self,stage,budget:BudgetView,*,authority,passed:bool,check_name=None,check_version=None):
        with self._lock:
            self._host(authority); state=self.inspect(); self._live(state); self._token(stage,state)
            if type(passed) is not bool or state['action'] is not None:
                raise ValueError('Actual settled host stage outcome required')
            policy=AssistancePolicy.from_dict(state['policy'])
            if stage.kind=='check':
                if (check_name,check_version)!=(policy.check_name,policy.check_version):
                    raise ValueError('Actual check must match the immutable acceptance definition')
                if len(state['observations'])>=policy.max_observations:
                    self._close(state,'budget'); raise PermissionError('Assisted observations exhausted')
                state['observations'].append(dict(call_digest='',error_digest='',check_digest=payload_digest(check_name),
                    check_passed=passed,evidence_digest=payload_digest(['host-check',check_name,True]) if passed else ''))
            elif check_name is not None or check_version is not None:
                raise ValueError('Only the host check stage accepts a check identity')
            # Preserve an actual returned check even if its deadline expired
            # while the caller-owned synchronous callback was executing.
            now=self._budget(budget,policy,state)
            if not passed:
                state.update(status='closed',reason='failed_acceptance')
            elif assisted_projection(state['origin']['controller'],state['observations'])['reason']:
                state.update(status='closed',reason='recovery_signal')
            else:
                state['stages'].append({**state['stage'],'anchor':deepcopy(state['anchor']),'passed':True})
                state['stage_index']+=1; state['stage']=None
                if stage.kind=='check':state['status']='ready_for_verification'
            state['last_clock']=now
            if stage.kind == 'check' and self.__check_checkpoint is not None:
                # Only the actual check path reaches this hook. The coordinator
                # commits its receipt/projection AND this final snapshot in one
                # append, without a durable ready state preceding that receipt.
                try:
                    before = self.journal.seq
                    committed = self.__check_checkpoint(deepcopy(state), check_name=check_name,
                        check_version=check_version, passed=passed)
                    if inspect.isawaitable(committed):
                        if inspect.iscoroutine(committed):committed.close()
                        raise ValueError('Host check checkpoint must commit synchronously')
                    if committed is not None:
                        raise ValueError('Host check checkpoint must acknowledge with None')
                    if self.journal.seq <= before or self.journal.state.get('help_continuation') != state:
                        raise ValueError('Host check checkpoint did not commit its exact final snapshot')
                except BaseException:
                    self._failed = True
                    raise
            else:
                self._record('assistance_stage_observed',state)
            self._tokens.pop(stage.id,None)
            return deepcopy(state)

    def _close(self,state,reason):
        state.update(status='closed',reason=reason)
        self._record('assistance_closed',state)
        self._tokens.clear()

    def close(self,reason,*,authority):
        with self._lock:
            self._host(authority)
            if reason not in {'stopped','uncertain','storage','interrupted'}:
                raise ValueError('Explicit host closure reason required')
            state=self.inspect()
            if state is None:raise PermissionError('No assistance to close')
            self._close(state,reason)
            return deepcopy(state)
