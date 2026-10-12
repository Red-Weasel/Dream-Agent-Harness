"""One durable, advisory-only help request through a host-qualified transport.

No provider, Council client, history extraction, tool or completion authority is
created here. The host approves packet disclosure and qualifies its dedicated
transport/tokenizer. Those assertions are not live frontier qualification.
Transport cancellation/close must cooperate; arbitrary trusted Python extensions
cannot be forcibly contained. A persisted send intent is never replayed.
"""
from __future__ import annotations

import asyncio
from contextlib import aclosing
from copy import deepcopy
from dataclasses import asdict, dataclass
import inspect
import json
import re
import threading
from weakref import WeakKeyDictionary

from .run_state import digest


def _hash(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False))


def _integer(value, maximum, minimum=1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Invalid bounded help counter')


def _digest(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('A pinned SHA256 version is required')


def _safe_text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value)>maximum or len(value.encode('utf-8')) > maximum:
        raise ValueError('Invalid bounded sanitized help text')
    # Conservative disclosure guard, not a claim to discover every secret/PII.
    # Explicit host review is mandatory even when these checks pass.
    if re.search(r'(?i)(?:file://|https?://|(?:^|\s)/(?:[\w.-]+/)*[\w.-]+|[a-z]:\\|'
                 r'-----BEGIN .*PRIVATE KEY|\b(?:password|api[_-]?key|access[_-]?token)\s*[:=])', value):
        raise ValueError('Redact paths, URLs and credentials before approving a help packet')
    return value


@dataclass(frozen=True)
class HelpRoute:
    route_id: str
    model_id: str
    version: str
    input_token_limit: int = 4096
    output_token_limit: int = 1024
    input_byte_limit: int = 16384
    output_byte_limit: int = 32768
    timeout_seconds: float = 30.0

    def __post_init__(self):
        for value in (self.route_id, self.model_id):
            if not isinstance(value, str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value) is None:
                raise ValueError('A bounded explicit public route/model identity is required')
        _digest(self.version)
        for field, maximum in (('input_token_limit',8192),('output_token_limit',2048),
                               ('input_byte_limit',32768),('output_byte_limit',65536)):
            _integer(getattr(self,field),maximum)
        if type(self.timeout_seconds) not in (int,float) or not 0.01 <= self.timeout_seconds <= 120:
            raise ValueError('Invalid bounded monotonic help timeout')


@dataclass(frozen=True)
class SanitizedPacket:
    question: str
    context: str
    attempted: tuple[str,...] = ()
    approved_for_external_use: bool = False

    def __post_init__(self):
        if self.approved_for_external_use is not True:
            raise ValueError('The trusted host must explicitly approve this sanitized packet for disclosure')
        _safe_text(self.question,4096); _safe_text(self.context,8192)
        if type(self.attempted) is not tuple or len(self.attempted)>4:
            raise ValueError('At most four sanitized approach summaries are allowed')
        for value in self.attempted:_safe_text(value,1024)

    def payload(self):
        return json.dumps({'instruction':'Return advisory text only. Do not call tools, claim completion, '
                           'authorize execution, reset budgets, or request automatic continuation.',
                           'question':self.question,'context':self.context,'attempted':self.attempted},
                          ensure_ascii=False,separators=(',', ':')).encode('utf-8')


@dataclass(frozen=True)
class HelpRequest:
    route: HelpRoute
    payload: bytes
    route_sha256: str
    packet_sha256: str
    output_token_limit: int
    output_byte_limit: int


@dataclass(frozen=True)
class TransportQualification:
    route_sha256: str
    packet_sha256: str
    transport_version: str
    input_tokens: int
    output_token_ceiling: int
    output_byte_ceiling: int
    single_request: bool
    retries: bool
    continuations: bool
    tools: bool
    fallback: bool
    actual_token_count: bool
    cancel_safe: bool


@dataclass(frozen=True)
class AdviceChunk:
    text: str


@dataclass(frozen=True)
class ActualUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


_LOCKS = WeakKeyDictionary()
_ACTIVE = WeakKeyDictionary()
_LOCKS_GUARD = threading.Lock()


def _lock(journal):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(journal, threading.RLock())


class FrontierHelp:
    """Trusted host API over a live, exclusively owned managed RunState.

    ``transport.qualify(request)`` is synchronous/local and returns exact token
    counts for the complete serialized payload, plus the enforced one-request
    policy. ``transport.stream_once(request)`` is an async generator of bounded
    AdviceChunk frames and optional final ActualUsage. No other frame is valid.
    The host owns safe cancellation/close and truthful transport qualification.
    There is deliberately no retry/resume-send API after a send intent.
    """
    def __init__(self,coordinator):
        self.coordinator=coordinator
        self.journal=coordinator.journal
        self.lock=_lock(self.journal)

    def _scope(self):
        if self.journal.write_error is not None:
            raise RuntimeError('Help cannot dispatch after a journal write failure')
        self.coordinator.attach()
        managed=self.journal.state['managed']
        pending=self.coordinator.pending_recovery
        if not pending or pending['kind']!='needs_help' or not pending['help_request']:
            raise PermissionError('A durable reserved needs_help envelope is required')
        recovery=managed['recovery']
        if recovery['controller']['help_used']!=1 or managed['completed_receipt']:
            raise PermissionError('Only the single already-reserved help envelope is eligible')
        return {'run_id':self.journal.run_id,'task_id':managed['task_id'],'project':managed['project'],
                'revision':managed['revision'],'contract_sha256':managed['contract_sha256'],
                'action_revision':managed['action_revision'],'action_chain':managed['evidence_chain'],
                'workspace':self.journal.state['worker_workspace'],
                'pending_sequence':pending['sequence'],'help_request_sha256':_hash(pending['help_request'])}

    def _record(self,event,state):
        self.coordinator.seal_actions()
        self.journal.record(event,incremental=True,frontier_help=state)

    def _saved(self):
        saved=self.journal.state.get('frontier_help')
        if saved is None:return None
        if (type(saved) is not dict or set(saved)!={'version','binding','qualification','status','outcome'}
                or type(saved['version']) is not int or saved['version']!=1
                or saved['status'] not in {'reserved','intent','returned','unknown','reconciled'}
                or type(saved['binding']) is not dict or type(saved['qualification']) is not dict):
            raise ValueError('Malformed durable help state')
        binding=saved['binding']
        if set(binding)!={'scope','route','route_sha256','packet_sha256','input_bytes'}:
            raise ValueError('Malformed help binding')
        try:route=HelpRoute(**binding['route'])
        except (TypeError,ValueError) as exc:raise ValueError('Malformed saved help route') from exc
        if binding['route_sha256']!=_hash(asdict(route)):
            raise ValueError('Saved route digest changed')
        _digest(binding['packet_sha256']);_integer(binding['input_bytes'],route.input_byte_limit)
        scope=binding['scope']
        if (type(scope) is not dict or set(scope)!={'run_id','task_id','project','revision',
                'contract_sha256','action_revision','action_chain','workspace','pending_sequence','help_request_sha256'}
                or any(not isinstance(scope[name],str) or len(scope[name])>8192
                       for name in ('run_id','project','workspace'))):
            raise ValueError('Malformed saved help scope')
        _integer(scope['task_id'],2**63-1)
        for name in ('contract_sha256','action_chain','help_request_sha256'):_digest(scope[name])
        for name in ('revision','action_revision','pending_sequence'):_integer(scope[name],1_000_000_000,0)
        try:q=TransportQualification(**saved['qualification'])
        except TypeError as exc:raise ValueError('Malformed saved transport qualification') from exc
        request=HelpRequest(route,b'',binding['route_sha256'],binding['packet_sha256'],
                            route.output_token_limit,route.output_byte_limit)
        self._validate_qualification(q,request)
        outcome=saved['outcome'];status=saved['status']
        if status in {'reserved','intent'}:
            if outcome is not None:raise ValueError('Unsettled intent cannot contain a claimed outcome')
        elif status=='returned':
            if (type(outcome) is not dict or set(outcome)!={'advice','usage','output_bytes','advisory_only'}
                    or not isinstance(outcome['advice'],str) or len(outcome['advice'])>route.output_byte_limit
                    or len(outcome['advice'].encode('utf-8'))!=outcome['output_bytes']
                    or outcome['advisory_only'] is not True or type(outcome['usage']) is not dict
                    or set(outcome['usage'])!={'input_tokens','output_tokens'}):
                raise ValueError('Malformed saved advisory outcome')
            _integer(outcome['output_bytes'],route.output_byte_limit,0)
            for name,limit in (('input_tokens',route.input_token_limit),('output_tokens',route.output_token_limit)):
                if outcome['usage'][name] is not None:_integer(outcome['usage'][name],limit,0)
            if outcome['usage']['input_tokens'] not in (None,q.input_tokens):
                raise ValueError('Saved actual token usage changed')
        else:
            field='reason' if status=='unknown' else 'host_note_sha256'
            if (type(outcome) is not dict or set(outcome)!={field,'usage','advisory_only'}
                    or outcome['usage'] is not None or outcome['advisory_only'] is not True):
                raise ValueError('Malformed saved unresolved/reconciled outcome')
            if status=='unknown':_safe_text(outcome[field],128)
            else:_digest(outcome[field])
        return deepcopy(saved)

    def _request(self,route,packet):
        if type(route) is not HelpRoute or type(packet) is not SanitizedPacket:
            raise TypeError('Explicit immutable host route and sanitized packet required')
        payload=packet.payload()
        if len(payload)>route.input_byte_limit:raise ValueError('Help input exceeds its byte ceiling')
        return HelpRequest(route,payload,_hash(asdict(route)),digest(payload.decode()),
                           route.output_token_limit,route.output_byte_limit)

    def _qualify(self,transport,request):
        qualify=getattr(transport,'qualify',None)
        stream=getattr(transport,'stream_once',None)
        if not callable(qualify) or inspect.iscoroutinefunction(qualify) or not inspect.isasyncgenfunction(stream):
            raise ValueError('A dedicated local qualifier and one-request async generator are required')
        q=qualify(request)
        return self._validate_qualification(q,request)

    @staticmethod
    def _validate_qualification(q,request):
        if type(q) is not TransportQualification:
            raise ValueError('Missing qualified actual token count or one-request policy')
        _digest(q.transport_version)
        _integer(q.input_tokens,request.route.input_token_limit)
        if (q.route_sha256!=request.route_sha256 or q.packet_sha256!=request.packet_sha256
                or type(q.output_token_ceiling) is not int or q.output_token_ceiling!=request.output_token_limit
                or type(q.output_byte_ceiling) is not int or q.output_byte_ceiling!=request.output_byte_limit
                or q.single_request is not True or q.actual_token_count is not True or q.cancel_safe is not True
                or any(value is not False for value in (q.retries,q.continuations,q.tools,q.fallback))):
            raise ValueError('Transport does not qualify for this bounded single request')
        return asdict(q)

    async def dispatch(self,route,packet,transport):
        request=self._request(route,packet)
        with self.lock:
            binding={'scope':self._scope(),'route':asdict(route),'route_sha256':request.route_sha256,
                     'packet_sha256':request.packet_sha256,'input_bytes':len(request.payload)}
            saved=self._saved()
            if saved and saved['binding']!=binding:
                raise ValueError('Reserved help scope, route, version or approved packet changed')
            if saved and saved['status']!='reserved':
                return deepcopy(saved)
            qualification=self._qualify(transport,request)
            if saved and saved['qualification']!=qualification:
                raise ValueError('Reserved help transport qualification changed')
            if not saved:
                saved={'version':1,'binding':binding,'qualification':qualification,'status':'reserved','outcome':None}
                self._record('frontier_help_reserved',saved)
            intent={**saved,'status':'intent'}
            # No await or callback occurs between the durable intent and entry.
            self._record('frontier_help_send_intent',intent)
            _ACTIVE[self.journal]=True
        chunks=[];size=0;frames=0;usage={'input_tokens':None,'output_tokens':None};saw_usage=False
        outcome=None
        try:
            async with asyncio.timeout(route.timeout_seconds):
                async with aclosing(transport.stream_once(request)) as stream:
                    async for frame in stream:
                        frames+=1
                        if frames>129:raise ValueError('Help response exceeds bounded frame count')
                        if type(frame) is AdviceChunk:
                            if saw_usage or not isinstance(frame.text,str) or len(frame.text)>4096:raise ValueError('Invalid or oversized advisory response frame')
                            encoded=frame.text.encode('utf-8')
                            if len(encoded)>4096 or size+len(encoded)>route.output_byte_limit:
                                raise ValueError('Help response exceeds byte ceiling')
                            chunks.append(frame.text);size+=len(encoded)
                        elif type(frame) is ActualUsage:
                            if saw_usage:raise ValueError('Duplicate actual usage frame')
                            saw_usage=True
                            for name,limit in (('input_tokens',route.input_token_limit),('output_tokens',route.output_token_limit)):
                                value=getattr(frame,name)
                                if value is not None:_integer(value,limit,0)
                                usage[name]=value
                            if usage['input_tokens'] is not None and usage['input_tokens']!=qualification['input_tokens']:
                                raise ValueError('Actual input usage disagrees with qualified tokenizer')
                        else:raise ValueError('Tools, continuations, fallback and unrecognized frames are forbidden')
            outcome={'advice':''.join(chunks),'usage':usage,'output_bytes':size,'advisory_only':True}
        except asyncio.CancelledError as exc:
            try:self._unknown('cancelled')
            except Exception as storage_exc:
                exc.add_note('Help cancellation outcome persistence failed: '+type(storage_exc).__name__)
            raise
        except Exception as exc:
            return self._unknown('timeout' if isinstance(exc,TimeoutError) else
                                 'protocol_error' if isinstance(exc,ValueError) else 'transport_error')
        finally:
            with self.lock:_ACTIVE.pop(self.journal,None)
        with self.lock:
            saved=self._saved()
            if saved['status']!='intent':raise ValueError('Concurrent host reconciliation changed the send intent')
            saved.update(status='returned',outcome=outcome)
            self._record('frontier_help_returned',saved)
            return deepcopy(saved)

    def _unknown(self,reason):
        with self.lock:
            saved=self._saved()
            if saved and saved['status']=='intent':
                saved.update(status='unknown',outcome={'reason':reason,'usage':None,'advisory_only':True})
                self._record('frontier_help_unknown',saved)
            return deepcopy(saved)

    def reconcile_unknown(self,*,packet_sha256,note):
        """Record host inspection only; never retry, unseal, refund or complete."""
        _digest(packet_sha256);_safe_text(note,4096)
        with self.lock:
            scope=self._scope();saved=self._saved()
            if _ACTIVE.get(self.journal):raise ValueError('An active transport must settle before host reconciliation')
            if saved and saved['binding']['scope']!=scope:raise ValueError('Help reconciliation scope changed')
            if not saved or saved['status'] not in {'intent','unknown'} or saved['binding']['packet_sha256']!=packet_sha256:
                raise ValueError('Reconciliation requires the exact unresolved help intent')
            saved.update(status='reconciled',outcome={'host_note_sha256':digest(note),'advisory_only':True,'usage':None})
            self._record('frontier_help_reconciled',saved)
            return deepcopy(saved)
