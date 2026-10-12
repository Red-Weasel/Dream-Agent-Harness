"""Read-only evidence accounting for explicitly selected canonical run ledgers.

This is a qualification *report*, not an endurance runner or a benchmark claim.
Canonical host records are trusted as records, not cryptographic attestations.
Model review, executed host checks, and task-store linkage remain distinct. No
artifact/model/trace/store is opened implicitly; task_lookup is a trusted host
callback. Missing telemetry/build/fault evidence cannot be filled with zeroes.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .run_state import _read_ledger, _finite_float, MAX_RECORD_BYTES


@dataclass(frozen=True)
class ReportLimits:
    max_runs: int = 16
    max_run_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_records: int = 100_000
    max_observation_gap_s: int = 300

    def __post_init__(self):
        ceilings = {'max_runs':64, 'max_run_bytes':512*1024*1024,
                    'max_total_bytes':512*1024*1024, 'max_records':200_000,
                    'max_observation_gap_s':300}
        for key, ceiling in ceilings.items():
            value = getattr(self,key)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError('Invalid bounded report limit: '+key)


def _time(value):
    if not isinstance(value, str) or len(value) > 64 or 'T' not in value:
        raise ValueError('Invalid ledger timestamp')
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid ledger timestamp') from exc
    if parsed.utcoffset() is None:
        raise ValueError('Ledger timestamps require an explicit timezone')
    return parsed.astimezone(timezone.utc)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON field in ledger')
        result[key] = value
    return result


def _hash(value):
    # Match the canonical ManagedTaskCoordinator receipt contract exactly.
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),
                                     ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _sha(value):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def _merge(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1]=max(end,merged[-1][1])
        else:
            merged.append([start,end])
    return merged


def _duration(intervals):
    return sum((end-start).total_seconds() for start,end in _merge(intervals))


def _overlap(left,right):
    left,right=_merge(left),_merge(right)
    i=j=0; seconds=0.0
    while i<len(left) and j<len(right):
        start,end=max(left[i][0],right[j][0]),min(left[i][1],right[j][1])
        if start<end: seconds+=(end-start).total_seconds()
        if left[i][1]<=right[j][1]: i+=1
        else: j+=1
    return seconds


class _Projection:
    def __init__(self, now, limits):
        self.now,self.limits=now,limits
        self.state={}; self.first=self.last=None
        self.active=[];self.idle=[];self.fault_intervals=[]
        self.events=Counter();self.actions={};self.returned={}
        self.checks=[];self.verifications={};self.completions={}
        self.model_done=0;self.model_reviews=0;self.faults=Counter()
        self.invocation=0;self.usage={}
        self.advisor=None

    def observe(self,record):
        at=_time(record.get('at'))
        if at>self.now or (self.last is not None and at<self.last):
            raise ValueError('Future or reversed ledger timestamp')
        if self.last is not None:
            managed=self.state.get('managed') or {}
            pending=(managed.get('recovery') or {}).get('pending')
            settled_uncertain=any(action.get('disposition')!='intent'
                                  for action in managed.get('unresolved',{}).values())
            uncertain=bool(self.state.get('uncertain') or settled_uncertain or pending)
            interval=(self.last,at)
            if uncertain: self.fault_intervals.append(interval)
            # A stopped/waiting state is recorded idle even over a long gap.
            # Otherwise, long gaps provide no activity evidence at all.
            if self.state.get('status') in {'stopped','need_input','budget','error','done'}:
                self.idle.append(interval)
            elif (not uncertain and self.state.get('status')=='running'
                  and self.state.get('phase') in {'worker_running','contract_running'}
                  and (at-self.last).total_seconds()<=self.limits.max_observation_gap_s):
                self.active.append(interval)
        self.first=self.first or at;self.last=at
        if record.get('version')==2:
            self.state={**self.state,**record.get('changes',{})}
        else:
            self.state=record.get('state',{})
        if not isinstance(self.state,dict):raise ValueError('Invalid projected run state')
        event,data=record.get('event'),record.get('data')
        if not isinstance(event,str) or len(event)>256 or not isinstance(data,dict):
            raise ValueError('Invalid report event envelope')
        self.events[event]+=1
        managed=self.state.get('managed') or {}
        if event=='resume_requested': self.invocation+=1
        if event=='worker_finished' and self.state.get('worker_status')=='DONE': self.model_done+=1
        if event in {'error','interrupted','runtime_budget_exhausted','recovery_observation_failed'}:
            self.faults[event]+=1
        if event in {'action_intent','action_returned'}:
            action=data.get('action')
            if (not isinstance(action,dict) or not isinstance(action.get('id'),str)
                    or not action['id'] or len(action['id'])>256):
                raise ValueError('Invalid action observation')
            target=self.actions if event=='action_intent' else self.returned
            previous=target.get(action['id'])
            if previous is not None and previous!=action:
                raise ValueError('Conflicting duplicate action observation')
            target[action['id']]=action
            if event=='action_returned' and action.get('disposition') in {'error','interrupted','unknown'}:
                if previous is None: self.faults['handler_'+action['disposition']]+=1
        if event=='recovery_check_executed':
            if not _sha(data.get('check_sha256')) or type(data.get('passed')) is not bool:
                raise ValueError('Invalid executed host check record')
            if data.get('exception_type') is not None and (not isinstance(data['exception_type'],str) or data['passed']):
                raise ValueError('Invalid host check exception disposition')
            self.checks.append(dict(sequence=record['seq'],check=data['check_sha256'],
                passed=data['passed'],exception=data.get('exception_type'),
                task_id=managed.get('task_id'),revision=managed.get('revision'),
                action_revision=managed.get('action_revision'),chain=managed.get('evidence_chain')))
        if event=='verification_checked':
            receipt=data.get('receipt')
            if isinstance(receipt,dict) and isinstance(receipt.get('id'),str):
                self.verifications[receipt['id']]=receipt
                self.model_reviews+=int(receipt.get('assessment_kind')=='independent_model_review')
        if event=='task_checked_complete' and isinstance(data.get('receipt_id'),str):
            self.completions[data['receipt_id']]=record['seq']
        if event=='runtime_budget':
            usage=data.get('usage')
            if not isinstance(usage,dict): raise ValueError('Invalid runtime usage record')
            selected={}
            for key in ('prompt_tokens','output_tokens','cached_tokens','tools','failures'):
                value=usage.get(key)
                if value is not None and (type(value) is not int or value<0):
                    raise ValueError('Invalid reported runtime counter')
                selected[key]=value
            self.usage[self.invocation]=selected
        if event=='frontier_help_returned':
            observed=_returned_advisor(self.state)
            if self.advisor is not None and self.advisor!=observed:
                raise ValueError('Conflicting returned advisor observations')
            self.advisor=observed


class _ObservedSource:
    """Bounded projection while the existing canonical reader validates replay.

    No partial report escapes when canonical validation subsequently rejects a
    line. Only compact facts are retained; source bodies are not copied to output.
    """
    def __init__(self,source,projection,limit,limits):
        self.source,self.projection,self.limit,self.limits=source,projection,limit,limits
        self.bytes=self.records=0;self.digest=hashlib.sha256()

    def readline(self,size):
        line=self.source.readline(min(size,MAX_RECORD_BYTES+1))
        if not line:return line
        self.bytes+=len(line);self.records+=1
        if self.bytes>self.limit or self.records>self.limits.max_records or len(line)>MAX_RECORD_BYTES:
            raise ValueError('Run exceeds report bounded reader capacity')
        self.digest.update(line)
        try:
            record=json.loads(line,object_pairs_hook=_pairs,parse_constant=_finite_float,parse_float=_finite_float)
            if not isinstance(record,dict):raise ValueError('Invalid record shape')
            self.projection.observe(record)
        except (TypeError,KeyError,AttributeError,RecursionError,UnicodeError) as exc:
            raise ValueError('Malformed report ledger') from exc
        return line


def _read(root,run_id,now,limits,remaining):
    fds=[]
    try:
        flags=os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory=os.open('/',flags);fds.append(directory)
        for part in Path(os.path.abspath(Path(root).expanduser())).parts[1:]:
            directory=os.open(part,flags,dir_fd=directory);fds.append(directory)
        directory=os.open(run_id,flags,dir_fd=directory);fds.append(directory)
        lock=os.open('.lock',os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=directory);fds.append(lock)
        lock_info=os.fstat(lock)
        if not stat.S_ISREG(lock_info.st_mode):raise ValueError('Run lock is not regular')
        try:fcntl.flock(lock,fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:raise ValueError('Active run is busy; select a stable ledger snapshot') from exc
        fd=os.open('ledger.jsonl',os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as source:
            before=os.fstat(source.fileno());limit=min(limits.max_run_bytes,remaining)
            if not stat.S_ISREG(before.st_mode) or before.st_size>limit:
                raise ValueError('Run ledger exceeds report byte limit or is not regular')
            projection=_Projection(now,limits)
            observed=_ObservedSource(source,projection,limit,limits)
            state,seq,_=_read_ledger(observed,run_id)
            after=os.fstat(source.fileno())
            if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                raise ValueError('Ledger changed during read-only inspection')
        current=os.stat('.lock',dir_fd=directory,follow_symlinks=False)
        canonical=os.stat('ledger.jsonl',dir_fd=directory,follow_symlinks=False)
        if (current.st_dev,current.st_ino)!=(lock_info.st_dev,lock_info.st_ino) or (canonical.st_dev,canonical.st_ino)!=(after.st_dev,after.st_ino):
            raise ValueError('Canonical run files changed during inspection')
        return projection,state,seq,observed.bytes,observed.digest.hexdigest()
    finally:
        for fd in reversed(fds):os.close(fd)


def _checked_assistance(state):
    """Bind retained terminal history to its protected completion receipt.

    The coordinator mints this hash only after its strict lease validator,
    independent review and executed check. This is receipt evidence, never a
    permission to execute or trust model recovery/completion declarations.
    """
    managed=state.get('managed') or {};lease=state.get('help_continuation')
    receipt=managed.get('receipt') or {};recovery=managed.get('recovery') or {}
    pending=recovery.get('pending') or {};check=state.get('phase_check') or {}
    if not isinstance(lease,dict) or not isinstance(receipt,dict):return False
    policy=lease.get('policy') or {};observations=lease.get('observations')
    if (not isinstance(policy,dict) or not isinstance(observations,list) or not observations
            or not isinstance(observations[-1],dict)):return False
    return bool(state.get('status')=='done' and pending.get('kind')=='needs_help'
        and managed.get('completed_receipt')==receipt.get('id') and receipt.get('id')
        and type(lease.get('version')) is int and lease['version']==1
        and lease.get('status')=='ready_for_verification'
        and type(lease.get('stage_index')) is int and lease['stage_index']==3
        and lease.get('stage') is None and lease.get('action') is None
        and lease.get('origin')==recovery and receipt.get('assistance_sha256')==_hash(lease)
        and lease.get('anchor')=={'revision':managed.get('action_revision'),'chain':managed.get('evidence_chain')}
        and observations[-1].get('check_passed') is True
        and observations[-1].get('check_digest')==_hash(policy.get('check_name'))
        and check.get('status')=='passed' and check.get('check_name')==policy.get('check_name')
        and check.get('check_version')==policy.get('check_version')
        and check.get('action_revision')==managed.get('action_revision')
        and check.get('action_chain')==managed.get('evidence_chain'))


def _completion(projection,state,task_lookup,selectors):
    managed=state.get('managed') or {};receipt=managed.get('receipt')
    reasons=[];linkage='unverified';checked=False
    completed=managed.get('completed_receipt')
    if not isinstance(receipt,dict) or not completed:
        return dict(receipt_linked=False,task_projection='unverified',accepted=False,reasons=['missing_managed_completion'])
    expected={'id':completed,'run_id':state['run_id'],'task_id':managed.get('task_id'),
        'revision':managed.get('revision'),'project':managed.get('project'),
        'contract_sha256':state.get('contract_sha256'),'action_revision':managed.get('action_revision'),
        'action_chain':managed.get('evidence_chain')}
    inspected=receipt.get('inspected')
    checked=(all(receipt.get(k)==v for k,v in expected.items())
        and managed.get('contract_sha256')==state.get('contract_sha256')
        and receipt.get('assessment_kind')=='independent_model_review'
        and isinstance(inspected,dict) and bool(inspected)
        and all(isinstance(k,str) and _sha(v) for k,v in inspected.items())
        and receipt.get('evidence_sha256')==_hash(inspected)
        and projection.verifications.get(completed)==receipt and completed in projection.completions)
    if not checked:reasons.append('unlinked_or_stale_managed_receipt')
    if (state.get('status')!='done' or state.get('uncertain') or managed.get('unresolved')
            or ((managed.get('recovery') or {}).get('pending') and not _checked_assistance(state))):
        reasons.append('completion_has_unsettled_state')
    if task_lookup is not None:
        try:
            row=task_lookup(managed.get('task_id'))
        except (OSError,LookupError,ValueError):
            row=None
        identity={'id':managed.get('task_id'),'managed_run':state['run_id'],'project':managed.get('project'),
            'managed_revision':managed.get('revision'),'managed_contract':state.get('contract'),
            'managed_receipt':completed,'status':'done','managed_state':'checked_complete'}
        linkage='linked' if isinstance(row,dict) and all(type(row.get(k)) is type(v) and row.get(k)==v for k,v in identity.items()) else 'mismatch_or_missing'
    if linkage!='linked':reasons.append('task_projection_not_verified')
    latest={check['check']:check for check in projection.checks
            if check['sequence']<projection.completions.get(completed,0)}
    passed={check['check'] for check in latest.values() if check['passed'] and check['exception'] is None
        and check['task_id']==managed.get('task_id') and check['revision']==managed.get('revision')
        and check['action_revision']==managed.get('action_revision') and check['chain']==managed.get('evidence_chain')}
    if not selectors or not set(selectors)<=passed:reasons.append('missing_selected_passing_host_checks_at_final_revision')
    return dict(receipt_linked=checked,task_projection=linkage,accepted=not reasons,reasons=reasons)


def _recovery(state):
    saved=(state.get('managed') or {}).get('recovery')
    if saved is None:return dict(interventions=None,help_used=None,help_limit=None,phase_steps=None,
                                pending=None,effective_pending=None,coverage='not_recorded')
    from .recovery import EvidenceAuthority,RecoveryController,payload_digest
    if not isinstance(saved,dict) or type(saved.get('version')) is not int or saved['version'] not in (1,2):
        raise ValueError('Invalid recovery projection')
    if saved['version']==2:
        from .recovery_runtime import RecoveryRuntime
        RecoveryRuntime(state,state['managed'])
    snapshot=RecoveryController.from_snapshot(saved.get('controller'),evidence_authority=EvidenceAuthority()).to_snapshot()
    managed=state['managed']
    expected=payload_digest({k:managed[k] for k in ('task_id','project','revision','contract_sha256')} | {'run_id':state['run_id']})
    total=saved.get('total_interventions')
    if (snapshot['task_digest']!=expected or type(total) is not int
            or not snapshot['attempts']<=total<=snapshot['policy']['max_recovery_attempts']):
        raise ValueError('Invalid task-scoped recovery counters')
    pending=saved.get('pending')
    if pending is not None and (not isinstance(pending,dict) or pending.get('kind') not in ('recover','needs_help','blocked','stopped')):
        raise ValueError('Invalid pending recovery projection')
    if pending:
        expected_status='active' if pending['kind']=='recover' else pending['kind']
        if (snapshot['status']!=expected_status or pending.get('reason')!=snapshot['reason']
                or type(pending.get('attempts')) is not int or pending['attempts']!=total
                or type(pending.get('sequence')) is not int or pending['sequence']!=snapshot['last_sequence']):
            raise ValueError('Recovery pending state is inconsistent')
    elif snapshot['status']!='active':
        raise ValueError('Terminal recovery decision is missing')
    return dict(interventions=total,help_used=snapshot['help_used'],help_limit=snapshot['policy']['help_request_limit'],
                phase_steps=snapshot['steps'],pending=pending.get('kind') if pending else None,
                effective_pending=None if _checked_assistance(state) else pending.get('kind') if pending else None,
                coverage='canonical_host_records')


def build_endurance_report(root,run_ids,*,now=None,task_lookup=None,acceptance_checks=None,limits=None):
    """Return bounded qualification JSON without claiming/resuming/repairing runs.

    acceptance_checks maps selected run IDs to host-check SHA256 names selected
    by the operator as acceptance criteria. Selectors do not create check passes.
    Only settled locked snapshots are accepted; retry an active run after its
    owner releases the canonical lock. All timestamps are checked against now.
    """
    return _build_report(root,run_ids,now=now,task_lookup=task_lookup,
                         acceptance_checks=acceptance_checks,limits=limits)


def _build_report(root,run_ids,*,now=None,task_lookup=None,acceptance_checks=None,limits=None,
                  snapshots=None):
    limits=limits or ReportLimits()
    if type(limits) is not ReportLimits:raise ValueError('Expected ReportLimits')
    if (not isinstance(run_ids,(list,tuple)) or not 1<=len(run_ids)<=limits.max_runs
        or any(not isinstance(r,str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,99}',r) is None for r in run_ids)
        or len(set(run_ids))!=len(run_ids)):
        raise ValueError('Select bounded distinct canonical run IDs')
    now=now or datetime.now(timezone.utc)
    if not isinstance(now,datetime) or now.utcoffset() is None:raise ValueError('now must have an explicit timezone')
    now=now.astimezone(timezone.utc)
    if task_lookup is not None and not callable(task_lookup):raise ValueError('task_lookup must be a trusted host callable')
    selections=acceptance_checks or {}
    if not isinstance(selections,dict) or not set(selections)<=set(run_ids):raise ValueError('Acceptance selectors must reference selected runs')
    for values in selections.values():
        if not isinstance(values,(list,tuple)) or len(values)>64 or any(not _sha(x) for x in values) or len(set(values))!=len(values):
            raise ValueError('Invalid bounded acceptance check selectors')
    runs=[];active=[];idle=[];faults=[];firsts=[];lasts=[];used=0;invocation_usage=[]
    for run_id in run_ids:
        p,state,seq,size,sha=(_read(root,run_id,now,limits,limits.max_total_bytes-used)
                              if snapshots is None else snapshots[run_id])
        used+=size;active.extend(p.active);idle.extend(p.idle);faults.extend(p.fault_intervals)
        firsts.append(p.first);lasts.append(p.last)
        completion=_completion(p,state,task_lookup,selections.get(run_id,[]))
        managed=state.get('managed') or {}
        invocation_usage.extend(p.usage.get(i) for i in range(p.invocation+1))
        runs.append(dict(run_id=run_id,ledger_sha256=sha,ledger_bytes=size,sequence=seq,
            first_at=p.first.isoformat(),last_at=p.last.isoformat(),recorded_status=state.get('status','unknown'),
            model_done_claims=p.model_done,model_review_receipts=p.model_reviews,
            explicit_resume_requests=p.events['resume_requested'],process_restarts=None,
            action_intents=len(p.actions),action_returns=len(p.returned),
            action_dispositions=dict(Counter(a.get('disposition','unknown') for a in p.returned.values())),
            unresolved_actions=len(managed.get('unresolved',{})),uncertain=state.get('uncertain'),
            reconciliation_reports=p.events['action_reconciled'],faults=dict(p.faults),
            host_checks={'passed':sum(c['passed'] for c in p.checks),'failed':sum(not c['passed'] for c in p.checks)},
            recovery=_recovery(state),completion=completion))
    wall=(max(lasts)-min(firsts)).total_seconds();active_s=_duration(active)
    idle_s=_duration(idle)-_overlap(active,idle)
    metrics={'cost':None,'provider_total_tokens':None,'coverage':'reported invocation counters only; hidden upstream usage unknown'}
    for key in ('prompt_tokens','output_tokens','cached_tokens','tools','failures'):
        metrics['reported_'+key]=sum(u[key] for u in invocation_usage) if all(u is not None and u[key] is not None for u in invocation_usage) else None
    accepted=sum(run['completion']['accepted'] for run in runs)
    gates=[]
    for hours in (1,4,12):
        reasons=['build_checkpoint_not_bound','fault_injection_qualification_not_supplied',
                 'independent_runtime_trace_not_bound','total_cost_and_upstream_usage_unknown']
        if active_s<hours*3600:reasons.append('insufficient_observed_active_duration')
        if not accepted:reasons.append('no_accepted_work_with_selected_host_checks_and_task_linkage')
        if any(r['uncertain'] or r['unresolved_actions'] or r['recovery']['effective_pending'] for r in runs):reasons.append('unsettled_run_state')
        gates.append(dict(hours=hours,qualified=False,duration_evidence_met=active_s>=hours*3600,reasons=reasons))
    return dict(version=1,report_kind='bounded_canonical_ledger_evidence',generated_at=now.isoformat(),
        timing={'observed_wall_window_s':wall,'observed_active_s':active_s,'recorded_idle_s':idle_s,
                'unobserved_s':max(0.0,wall-active_s-idle_s),'fault_affected_s':_duration(faults),
                'max_observation_gap_s':limits.max_observation_gap_s,
                'basis':'union of bounded adjacent worker/contract observations, not CPU utilization or accepted work'},
        runs=runs,accepted_work_count=accepted,metrics=metrics,gates=gates,
        limitations=['Canonical host records are not independently attested execution traces.',
                     'Current artifact bytes are not re-read; receipts identify recorded inspected evidence.',
                     'Model DONE and independent model review are not executed acceptance checks.',
                     'A runner must bind build/checkpoint, fault qualifications and complete usage before endurance qualification.'])


def _schedule_manifest(state, schedule_id, limits, now):
    """Validate the immutable parent without opening task stores or children."""
    schedule = state.get('phase_schedule')
    if type(schedule) is not dict or type(schedule.get('version')) is not int or schedule['version'] != 1:
        raise ValueError('Invalid phase schedule projection')
    manifest = schedule.get('manifest')
    if (type(manifest) is not dict or type(manifest.get('version')) is not int or manifest['version'] != 1
            or len(json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode()) > 131072
            or not _sha(schedule.get('manifest_sha256')) or _hash(manifest) != schedule['manifest_sha256']):
        raise ValueError('Immutable schedule manifest changed')
    phases, rows = manifest.get('phases'), schedule.get('phases')
    if (type(phases) is not list or not 1 <= len(phases) <= 32 or type(rows) is not list
            or len(rows) != len(phases) or type(schedule.get('cursor')) is not int
            or not 0 <= schedule['cursor'] <= len(phases)):
        raise ValueError('Invalid bounded phase backlog')
    budget = manifest.get('limits')
    if (type(budget) is not dict or set(budget) != {'max_dispatches', 'max_wall_seconds'}
            or type(budget['max_dispatches']) is not int or not 1 <= budget['max_dispatches'] <= 512
            or type(budget['max_wall_seconds']) is not int or not 1 <= budget['max_wall_seconds'] <= 86400
            or type(schedule.get('dispatches')) is not int
            or not 0 <= schedule['dispatches'] <= budget['max_dispatches']):
        raise ValueError('Invalid original schedule budget')
    created, clock, deadline = (_time(schedule.get(k)) for k in ('created_at', 'last_clock', 'deadline'))
    if (not created <= clock <= now or clock > deadline
            or (deadline-created).total_seconds() != budget['max_wall_seconds']):
        raise ValueError('Invalid original schedule clock')
    sources = manifest.get('runtime_sources')
    if (type(sources) is not dict or not 1 <= len(sources) <= 64
            or any(not isinstance(k, str) or len(k) > 256 or k.startswith('/')
                   or '..' in k.split('/') or not _sha(v) for k, v in sources.items())):
        raise ValueError('Invalid recorded runtime source identities')
    for key, maximum in (('goal', 8192), ('workspace', 4096), ('project', 4096), ('standing_rules', 131072)):
        if not isinstance(manifest.get(key), str) or len(manifest[key]) > maximum:
            raise ValueError('Invalid bounded schedule scope')
    selected = []; identities = set()
    for index, (phase, row) in enumerate(zip(phases, rows)):
        if (type(phase) is not dict or set(phase) != {'id', 'goal', 'contract', 'check_name', 'check_version'}
                or not isinstance(phase['id'], str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', phase['id']) is None
                or phase['id'] in identities or not _sha(phase['check_version'])):
            raise ValueError('Invalid immutable phase identity/check')
        identities.add(phase['id'])
        for key, maximum in (('goal', 8192), ('contract', 8192), ('check_name', 128)):
            if not isinstance(phase[key], str) or not phase[key].strip() or len(phase[key]) > maximum:
                raise ValueError('Invalid immutable phase text')
        if (type(row) is not dict or set(row) != {'id', 'run_id', 'status', 'completion'}
                or row['id'] != phase['id'] or row['status'] not in {'pending', 'reserved', 'running', 'accepted', 'blocked'}
                or ((row['run_id'] is None) != (row['status'] == 'pending'))
                or (index < schedule['cursor'] and (row['status'] != 'accepted' or type(row['completion']) is not dict))
                or (index >= schedule['cursor'] and (row['status'] == 'accepted' or row['completion'] is not None))
                or (index > schedule['cursor'] and row['status'] != 'pending')):
            raise ValueError('Invalid parent-to-child backlog linkage')
        run_id = row['run_id']
        if run_id is not None:
            if (not isinstance(run_id, str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,99}', run_id) is None
                    or run_id == schedule_id or run_id in selected):
                raise ValueError('Invalid distinct child run identity')
            selected.append(run_id)
    if not 1 <= len(selected) <= limits.max_runs:
        raise ValueError('Select a schedule with bounded existing child runs')
    return schedule, manifest, selected


def _phase_receipt(projection, state, phase, row, children_root):
    """Join recorded parent completion with the settled child check/boundary."""
    managed = state.get('managed') or {}
    check = state.get('phase_check') or {}
    boundary = managed.get('boundary') or {}
    archive = state.get('context_archive')
    observed = next((entry for entry in projection.checks
                     if entry['sequence'] == check.get('observation_sequence')), None)
    if (check.get('status') != 'passed' or check.get('phase_id') != phase['id']
            or check.get('check_name') != phase['check_name'] or check.get('check_version') != phase['check_version']
            or check.get('action_revision') != managed.get('action_revision')
            or check.get('action_chain') != managed.get('evidence_chain')
            or type(check.get('observation_sequence')) is not int
            or observed is None or observed['check'] != _hash(phase['check_name'])
            or observed['passed'] is not True or observed['exception'] is not None
            or observed['task_id'] != managed.get('task_id') or observed['revision'] != managed.get('revision')
            or observed['action_revision'] != managed.get('action_revision') or observed['chain'] != managed.get('evidence_chain')
            or boundary.get('status') != 'acknowledged'
            or not managed.get('completed_receipt') or boundary.get('receipt_id') != managed['completed_receipt']
            or not isinstance(archive, dict) or archive.get('boundary_id') != boundary.get('id')):
        raise ValueError('Accepted phase lacks its exact checked boundary')
    expected = dict(run_id=state['run_id'], task_id=managed.get('task_id'), receipt_id=managed['completed_receipt'],
                    receipt_sha256=_hash(managed.get('receipt')), boundary_id=boundary.get('id'),
                    boundary_status='acknowledged', generation=boundary.get('generation'),
                    host_check_passed=True, host_check_name=phase['check_name'], host_check_version=phase['check_version'],
                    host_check_sequence=check['observation_sequence'], context_archive=archive,
                    progress_path=str(children_root/state['run_id']/'progress.md'))
    if row['completion'] != expected:
        raise ValueError('Parent completion changed its child receipt/check/archive identity')
    if state.get('status') == 'done' and state.get('help_continuation') is not None:
        from .help_continuation import HelpContinuation
        HelpContinuation.inspect_state(state)


def _returned_advisor(state):
    """Observe returned usage at its original needs-help event, before any lease."""
    from .help_continuation import _returned_help_snapshot, _scope
    saved = _returned_help_snapshot(state)
    if saved['binding']['scope'] != _scope(state, state['managed']):
        raise ValueError('Returned advisor usage belongs to another original help scope')
    count = state.get('responses_help_preflight')
    if count is not None:
        binding, qualification = saved['binding'], saved['qualification']
        if (type(count) is not dict or set(count) != {'version', 'binding', 'status', 'input_tokens', 'reason'}
                or type(count['version']) is not int or count['version'] != 1
                or count['status'] != 'counted' or count['reason'] is not None
                or type(count['input_tokens']) is not int or count['input_tokens'] <= 0
                or count['input_tokens'] != qualification['input_tokens']):
            raise ValueError('Advisor preflight count changed')
        expected = dict(scope=binding['scope'], route_sha256=binding['route_sha256'], packet_sha256=binding['packet_sha256'],
                        transport_version=qualification['transport_version'], model_id=binding['route']['model_id'])
        recorded = count['binding']
        if (type(recorded) is not dict or set(recorded) != set(expected) | {'input_sha256'}
                or not _sha(recorded['input_sha256']) or any(recorded[k] != v for k, v in expected.items())):
            raise ValueError('Advisor preflight input/route/scope linkage changed')
    usage = saved['outcome']['usage']
    return dict(help_sha256=_hash(saved), scope_sha256=_hash(saved['binding']['scope']),
                preflight_sha256=_hash(count), input_tokens=usage['input_tokens'], output_tokens=usage['output_tokens'])


def _advisor_subset(projection, state):
    """Historical usage linkage grants no current execution or acceptance rights."""
    saved = state.get('frontier_help')
    if saved is None:
        if projection.advisor is not None:
            raise ValueError('Observed returned advisor outcome was removed')
        return (None, None, None) if state.get('responses_help_preflight') is not None else (0, None, None)
    if type(saved) is not dict or saved.get('status') != 'returned':
        return None, None, None
    observed = projection.advisor
    if (observed is None or observed['help_sha256'] != _hash(saved)
            or observed['preflight_sha256'] != _hash(state.get('responses_help_preflight'))):
        raise ValueError('Returned advisor outcome/count lacks unchanged original event linkage')
    lease = state.get('help_continuation')
    if lease is not None and (type(lease) is not dict or lease.get('help_sha256') != observed['help_sha256']
                             or _hash(lease.get('binding')) != observed['scope_sha256']):
        raise ValueError('Retained assistance changed original advisor linkage')
    return 1, observed['input_tokens'], observed['output_tokens']


def build_phase_endurance_report(schedule_root, schedule_id, *, task_lookup=None, now=None, limits=None):
    """Read only one selected parent and its linked children; never resume or repair.

    Recorded source hashes are provenance, not an independent build attestation.
    CLI callers have no task-store authority. Provider usage is a partial separate
    subtotal, never a cost estimate or qualification of a 1/4/12-hour run.
    """
    limits = limits or ReportLimits()
    if type(limits) is not ReportLimits:
        raise ValueError('Expected ReportLimits')
    if not isinstance(schedule_id, str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,99}', schedule_id) is None:
        raise ValueError('Select one canonical schedule ID')
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('now must have an explicit timezone')
    now = now.astimezone(timezone.utc)
    root = Path(os.path.abspath(Path(schedule_root).expanduser()))
    parent = _read(root, schedule_id, now, limits, limits.max_total_bytes)
    try:
        schedule, manifest, selected = _schedule_manifest(parent[1], schedule_id, limits, now)
        # Reserve the final parent read too: consistency checks consume the same
        # bounded I/O allowance as the first projection and child observations.
        used = 2 * parent[3]
        if used >= limits.max_total_bytes:
            raise ValueError('Parent consistency reads exceed report byte limit')
        snapshots = {}; checks = {}; usages = []
        children_root = root/'children'
        for index, (phase, row) in enumerate(zip(manifest['phases'], schedule['phases'])):
            run_id = row['run_id']
            if run_id is None:
                continue
            child = _read(children_root, run_id, now, limits, limits.max_total_bytes-used)
            used += child[3]; state = child[1]
            binding = dict(schedule_id=schedule_id, schedule_sha256=schedule['manifest_sha256'],
                           phase_id=phase['id'], phase_index=index, phase_sha256=_hash(phase))
            managed = state.get('managed')
            if (state.get('phase_binding') != binding or state.get('goal') != phase['goal']
                    or state.get('worker_workspace') != manifest['workspace']
                    or state.get('contract') not in ('', phase['contract'])
                    or (managed and managed.get('project') != manifest['project'])):
                raise ValueError('Child belongs to another phase, workspace or contract')
            if row['status'] == 'accepted':
                _phase_receipt(child[0], state, phase, row, children_root)
                checks[run_id] = [_hash(phase['check_name'])]
            snapshots[run_id] = child
            usages.append(_advisor_subset(child[0], state))
        result = _build_report(children_root, selected, now=now, task_lookup=task_lookup,
                               acceptance_checks=checks, limits=limits, snapshots=snapshots)
        final_parent = _read(root, schedule_id, now, limits, limits.max_total_bytes-used+parent[3])
        if final_parent[1:] != parent[1:]:
            raise ValueError('Parent changed during linked schedule inspection')
    except (KeyError, TypeError, AttributeError, IndexError, RecursionError) as exc:
        raise ValueError('Malformed bounded schedule evidence') from exc
    result['schedule'] = dict(schedule_id=schedule_id, manifest_sha256=schedule['manifest_sha256'],
                              ledger_sha256=parent[4], sequence=parent[2],
                              recorded_status=schedule.get('last_status'), completed_phases=schedule['cursor'],
                              phase_count=len(manifest['phases']))
    result['recorded_runtime_sources'] = dict(manifest['runtime_sources'])
    result['independently_verified_build'] = None
    result['advisor_usage'] = dict(zip(('requests', 'input_tokens', 'output_tokens'),
        (sum(u[i] for u in usages) if all(u[i] is not None for u in usages) else None for i in range(3))))
    result['advisor_usage']['coverage'] = 'Bound linked returned-advisor subset only; unknown usage is not zero and preflight counts are excluded.'
    return result
