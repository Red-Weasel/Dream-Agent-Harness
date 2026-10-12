"""Journal projection and trusted observations for managed-task recovery.

No provider calls, reset authorization or completion verdicts live here. Counters
belong to the full managed task; a new model turn or process never refreshes them.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import inspect
import os
from pathlib import Path
import re

from .recovery import (EvidenceAuthority, HelpRequest, RecoveryController,
                       RecoveryObservation, RecoveryPolicy, payload_digest)


class RecoveryPending(PermissionError):
    """A persisted recovery decision must settle before further dispatch."""


class RecoveryRuntime:
    def __init__(self, run: dict, managed: dict, *, policy: RecoveryPolicy | None = None, assistance_policy=None):
        self.authority = EvidenceAuthority()
        self.task_digest = payload_digest({k: managed[k] for k in
            ('task_id', 'project', 'revision', 'contract_sha256')} | {'run_id': run['run_id']})
        self.phase_digest = payload_digest({'task': self.task_digest, 'phase': 'execution'})
        from .help_continuation import AssistancePolicy
        if assistance_policy is not None and type(assistance_policy) is not AssistancePolicy:
            raise ValueError('Invalid predeclared assistance policy')
        self.assistance_policy = None
        saved = managed.get('recovery')
        if saved is None:
            if assistance_policy is not None:
                if managed.get('action_revision', 0) or managed.get('recent') or managed.get('unresolved'):
                    raise ValueError('Assistance must be declared before any task action')
                self.assistance_policy = assistance_policy
            self.controller = RecoveryController(self.task_digest, self.phase_digest,
                policy=policy, evidence_authority=self.authority)
            self.total_interventions = 0
            self.pending = None
        else:
            if (type(saved) is not dict or type(saved.get('version')) is not int
                    or saved['version'] not in (1, 2)
                    or set(saved) != ({'version', 'controller', 'total_interventions', 'pending'}
                                      | ({'assistance_policy'} if saved['version'] == 2 else set()))
                    or type(saved['total_interventions']) is not int
                    or not 0 <= saved['total_interventions'] <= 2):
                raise ValueError('Invalid durable recovery runtime')
            if saved['version'] == 2:
                try:
                    self.assistance_policy = AssistancePolicy.from_dict(saved['assistance_policy'])
                except (TypeError, KeyError) as exc:
                    raise ValueError('Invalid saved assistance policy') from exc
            if assistance_policy is not None and assistance_policy != self.assistance_policy:
                raise ValueError('Resume cannot enable or change assistance policy')
            self.controller = RecoveryController.from_snapshot(saved['controller'], evidence_authority=self.authority)
            snapshot = self.controller.to_snapshot()
            if snapshot['task_digest'] != self.task_digest or snapshot['phase_digest'] != self.phase_digest:
                raise ValueError('Recovery scope does not match the managed task')
            if policy is not None and asdict(policy) != snapshot['policy']:
                raise ValueError('Resume cannot replenish or change recovery policy')
            self.total_interventions = saved['total_interventions']
            self.pending = deepcopy(saved['pending'])
            if not snapshot['attempts'] <= self.total_interventions <= snapshot['policy']['max_recovery_attempts']:
                raise ValueError('Recovery interventions cannot be replenished')
            if self.pending is not None:
                if (type(self.pending) is not dict
                        or set(self.pending) != {'kind', 'reason', 'attempts', 'sequence', 'help_request'}
                        or type(self.pending['kind']) is not str
                        or self.pending['kind'] not in {'recover', 'needs_help', 'blocked', 'stopped'}
                        or self.pending['reason'] != snapshot['reason']
                        or type(self.pending['attempts']) is not int
                        or self.pending['attempts'] != self.total_interventions
                        or type(self.pending['sequence']) is not int
                        or self.pending['sequence'] != snapshot['last_sequence']):
                    raise ValueError('Invalid pending recovery decision')
                expected = 'active' if self.pending['kind'] == 'recover' else self.pending['kind']
                if snapshot['status'] != expected:
                    raise ValueError('Pending recovery disagrees with controller')
                request = self.pending['help_request']
                if (self.pending['kind'] == 'needs_help') != (request is not None):
                    raise ValueError('Help reservation requires its persisted envelope')
                if request is not None:
                    if (self.pending['kind'] != 'needs_help' or type(request) is not dict
                            or set(request) != {'version', 'requires_review', 'task_digest', 'phase_digest',
                                                'signal_digest', 'reason', 'attempts'}
                            or type(request['version']) is not int or request['version'] != 1
                            or request['requires_review'] is not True
                            or request['task_digest'] != self.task_digest
                            or request['phase_digest'] != self.phase_digest
                            or request['reason'] != snapshot['reason']
                            or request['attempts'] != snapshot['attempts']
                            or request['signal_digest'] != snapshot['last_observation']):
                        raise ValueError('Invalid reserved help envelope')
                    HelpRequest(**{k: request[k] for k in ('task_digest', 'phase_digest', 'signal_digest', 'reason', 'attempts')})
            elif snapshot['status'] != 'active':
                raise ValueError('Terminal recovery decision is missing')

    def snapshot(self):
        result = dict(version=1, controller=self.controller.to_snapshot(),
                      total_interventions=self.total_interventions, pending=deepcopy(self.pending))
        if self.assistance_policy is not None:
            result.update(version=2, assistance_policy=asdict(self.assistance_policy))
        return result

    def observe(self, *, call_digest='', error_digest='', evidence_digest='',
                check_digest='', check_passed=None, stopped=False, generation_cut=False):
        if self.pending and not stopped:
            raise RecoveryPending('A recovery decision is already pending')
        if type(generation_cut) is not bool:
            raise ValueError('Invalid generation-cut observation')
        if generation_cut:
            if call_digest or check_digest or evidence_digest:
                raise ValueError('A generation cut cannot invent action or evidence receipts')
            error_digest = payload_digest({'kind': 'generation_repetition'})
        old = self.controller.to_snapshot()
        evidence = self.authority.issue(self.task_digest, self.phase_digest, evidence_digest) if evidence_digest else None
        action = self.controller.observe(RecoveryObservation(
            sequence=old['last_sequence'] + 1, call_digest=call_digest, error_digest=error_digest,
            evidence=evidence, check_digest=check_digest, check_passed=check_passed,
            stopped=stopped, generation_cut=generation_cut))
        snapshot = self.controller.to_snapshot()
        if action.kind == 'recover':
            # Core attempts can reset after novel evidence; the task-wide budget
            # cannot. Reserve each intervention before any changed-approach turn.
            if self.total_interventions < snapshot['policy']['max_recovery_attempts']:
                self.total_interventions += 1
            else:
                snapshot['attempts'] = snapshot['policy']['max_recovery_attempts']
                help_left = snapshot['help_used'] < snapshot['policy']['help_request_limit']
                snapshot['status'] = 'needs_help' if help_left else 'blocked'
                if help_left:
                    snapshot['help_used'] += 1
                self.controller = RecoveryController.from_snapshot(snapshot, evidence_authority=self.authority)
                from .recovery import RecoveryAction
                request = HelpRequest(self.task_digest, self.phase_digest, snapshot['last_observation'],
                                      action.reason, snapshot['attempts']) if help_left else None
                action = RecoveryAction(snapshot['status'], action.reason, snapshot['attempts'], request)
        if action.kind != 'continue':
            self.pending = dict(kind=action.kind, reason=action.reason,
                attempts=self.total_interventions, sequence=snapshot['last_sequence'],
                help_request=action.help_request.to_dict() if action.help_request else None)
        return self.snapshot()

    def consume_intervention(self):
        if not self.pending or self.pending['kind'] != 'recover':
            raise RecoveryPending('Only a pending bounded intervention can be consumed')
        self.pending = None
        return self.snapshot()

    def block_tracking(self):
        """Lost observation persistence is a capacity block, not handler failure.

        Preserve existing terminal reservations and all observation/intervention
        counters. No invented repeated error or help request is issued.
        """
        if self.pending and self.pending['kind'] != 'recover':
            return self.snapshot()
        snapshot = self.controller.to_snapshot()
        snapshot.update(status='blocked', reason='tracking_capacity')
        self.controller = RecoveryController.from_snapshot(snapshot, evidence_authority=self.authority)
        self.pending = dict(kind='blocked', reason='tracking_capacity',
                            attempts=self.total_interventions,
                            sequence=snapshot['last_sequence'], help_request=None)
        return self.snapshot()


_TOPIC_STOP = {'goal', 'task', 'work', 'complete', 'research', 'search', 'find', 'sources',
               'source', 'evidence', 'criteria', 'criterion', 'about', 'from', 'with', 'that', 'this'}


_SOURCE_SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.go', '.rs', '.c', '.cpp', '.h',
                    '.java', '.sh', '.html', '.css', '.sql', '.toml', '.yaml', '.yml', '.json',
                    '.md', '.txt', '.rst'}


def file_observation(tool, arguments: dict, run: dict, run_path: Path):
    """Bounded byte observation for exact built-in source tools, never metadata.

    Workspace source data is task-scoped; run/archive/cache self-observation is
    excluded. Before/after reads use the existing no-symlink scoped reader. A
    missing write target is distinct from an unreadable/unsupported source.
    """
    from ..tools import native, files
    from .evaluator import ScopedReader
    actual = inspect.unwrap(tool.handler)
    kind = ('read' if actual is inspect.unwrap(native.read_file.handler) else
            'write' if actual in (inspect.unwrap(native.write_file.handler),
                                  inspect.unwrap(files.str_replace_edit.handler)) else None)
    raw = arguments.get('path')
    if kind is None or not isinstance(raw, str) or not raw or len(raw) > 4096:
        return None
    workspace = Path(run['worker_workspace']).resolve()
    path = Path(os.path.abspath(workspace / Path(raw).expanduser()))
    if (not path.is_relative_to(workspace) or path.is_relative_to(run_path.parent.resolve())
            or path.suffix.lower() not in _SOURCE_SUFFIXES
            or any(part.startswith('.') or part in {'__pycache__', 'node_modules'}
                   for part in path.relative_to(workspace).parts)):
        return None
    try:
        body, sha = ScopedReader(workspace).read(str(path))
    except FileNotFoundError:
        return (kind, None, None) if kind == 'write' else None
    except (OSError, ValueError):
        return None
    return kind, body, sha


def file_evidence(before, after, arguments: dict, result) -> str:
    """Only successful changed bytes or actually delivered stable source text."""
    if before is None or after is None or not isinstance(result, dict):
        return ''
    if result.get('is_error') or result.get('isError') or after[1] is None:
        return ''
    kind, body, sha = after
    if kind == 'write':
        content = body if before[2] != sha else ''
    else:
        if before[2] != sha or arguments.get('query'):
            return ''
        try:
            if 'start_line' in arguments or 'line_count' in arguments:
                start = max(1, int(arguments.get('start_line') or 1))
                count = max(1, int(arguments.get('line_count') or 200))
                content = ''.join(body.splitlines(keepends=True)[start-1:start-1+count])
            else:
                start, count = int(arguments.get('offset', 0)), int(arguments.get('limit', 12000))
                if start < 0 or not 1 <= count <= 50000:
                    return ''
                content = body[start:start+count]
        except (TypeError, ValueError, OverflowError):
            return ''
        blocks = result.get('content')
        if not isinstance(blocks, list) or len(blocks) > 128:
            return ''
        if not any(isinstance(b, dict) and isinstance(b.get('text'), str)
                   and len(b['text']) <= 1_048_576 and content.strip() in b['text'] for b in blocks):
            return ''
    # Identity omits path/query/mtime: copied content and repeated same-byte
    # writes cannot refresh progress; a later read of identical content cannot.
    return payload_digest(['workspace-source', content.strip()]) if content.strip() else ''


def research_digest(tool, result, goal: str, contract: str) -> str:
    """Conservative task-keyword relevance over actual built-in web data.

    Exact built-in handler identity is required: model text, custom names,
    read/archive loops, queries, mutable timestamps and failed tools never issue
    receipts. Query headings, suggestion text and browse delivery nonces are
    excluded. This establishes observed source data, not goal acceptance.
    """
    if not isinstance(result, dict) or result.get('is_error') or result.get('isError'):
        return ''
    from ..tools import web
    actual = inspect.unwrap(tool.handler)
    kind = next((name for name in ('web_search', 'browse')
                 if actual is inspect.unwrap(getattr(web, name).handler)), None)
    if kind is None:
        return ''
    blocks = result.get('content')
    if not isinstance(blocks, list) or len(blocks) > 128:
        return ''
    texts = [b.get('text', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'text']
    if any(not isinstance(text, str) for text in texts) or sum(map(len, texts)) > 131072:
        return ''
    text = '\n'.join(texts)
    if kind == 'web_search':
        records = re.findall(r'^\d+\. (.+)[ \t]+\[[^\n]+\]\n[ \t]+(https?://\S+)\n[ \t]+([^\n]+)', text, re.M)
        data = sorted(set((url, title.strip(), snippet.strip()) for title, url, snippet in records))
        if not data:
            return ''
        content = ' '.join(title + ' ' + snippet for _, title, snippet in data)
    else:
        # Match the nonce-bound page only, excluding screenshot paths and hints.
        match = re.search(r'<untrusted_web_content id="([a-f0-9]+)">\n[^\n]*\n# ([^\n]*)\n'
                          r'URL: (https?://\S+)  \(status 2\d\d\)\n\n(.*?)\n'
                          r'</untrusted_web_content id="\1">', text, re.S)
        if not match or not match[4].strip() or match[4] == '(no readable text extracted)':
            return ''
        data = (match[3], match[2], match[4])
        content = match[2] + ' ' + match[4]
    topics = set(re.findall(r'[a-z0-9]{4,}', (goal + ' ' + contract).lower())) - _TOPIC_STOP
    if not topics.intersection(re.findall(r'[a-z0-9]{4,}', content.lower())):
        return ''
    return payload_digest(data)


_ARTIFACT_HANDLE = re.compile(r'dream-artifact:v1:(?:[0-9a-f]{64}:){5}(?:0|[1-9][0-9]{0,9})(?![0-9])')


def reported_handles(result) -> dict:
    """Retain bounded handle hints from returned data, never proof or permission."""
    blocks = result.get('content', []) if isinstance(result, dict) else []
    if not isinstance(blocks, list):
        return {'handles': [], 'scan_complete': False}
    remaining, complete, handles = 131072, True, set()
    for block in blocks[:128]:
        text = block.get('text') if isinstance(block, dict) else None
        if not isinstance(text, str):
            continue
        sample = text[:remaining]
        complete = complete and len(sample) == len(text)
        handles.update(_ARTIFACT_HANDLE.findall(sample))
        remaining -= len(sample)
        if not remaining:
            complete = False
            break
    complete = complete and len(blocks) <= 128 and len(handles) <= 32
    return {'handles': sorted(handles)[:32], 'scan_complete': complete}
