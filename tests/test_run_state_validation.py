"""Reject disposable malformed recovery journals before dispatch or budget hooks."""
import copy
import fcntl
import json
import socket
import subprocess

import pytest

from dream.core.loop import AutonomousLoop
from dream.core.run_state import RunState
from test_durable_autonomy import Worker, Reviewer


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Recovery validation must not use external execution')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(subprocess.Popen, '__init__', blocked)


class BudgetWorker(Worker):
    def __init__(self, workspace, replies=()):
        super().__init__(workspace, replies)
        self.hooks = []

    def begin_run_budget(self):
        self.hooks.append('begin')

    def end_run_budget(self):
        self.hooks.append('end')


def checkpoint(tmp_path, **changes):
    journal = RunState(tmp_path / 'runs')
    state = dict(goal='goal', worker_workspace=str(tmp_path), phase='ready',
                 iterations=0, uncertain=False, contract='- criterion', status='running', result=None)
    state.update(changes)
    journal.record('fixture_checkpoint', **state)
    for name, content in [('contract.md', '# Contract\n\n- criterion\n'),
                          ('progress.md', 'existing evidence'), ('artifact.txt', 'existing effect'),
                          ('log.md', 'existing log')]:
        (journal.path / name).write_text(content)
    journal.close()
    return journal.path


def rows(path):
    return [json.loads(line) for line in (path / 'ledger.jsonl').read_text().splitlines()]


def rewrite(path, records):
    (path / 'ledger.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in records))


def contents(path):
    return {p.name: p.read_bytes() for p in path.iterdir() if p.is_file() and p.name != '.lock'}


def assert_unlocked(path):
    with (path / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def outcome(path, status='budget', iterations=0):
    return dict(status=status, iterations=iterations, message='recorded outcome',
                workspace=str(path), run_id=path.name)


BAD_FIELDS = [
    ('phase', 'worker_runninG'), ('phase', None), ('phase', 7), ('phase', {}),
    ('phase', []), ('phase', True), ('phase', ''), ('status', None), ('status', 'DONE'),
    ('iterations', True), ('iterations', -1), ('iterations', 1.0), ('iterations', '1'),
    ('uncertain', 0), ('uncertain', 'false'), ('goal', None), ('worker_workspace', []),
    ('contract', 7), ('worker_status', 'done'), ('worker_status', {}), ('next', []),
    ('response', None), ('gaps', 7), ('verdict', 'pass'), ('verdict', {}),
    ('contract_sha256', 7), ('contract_sha256', 'not a digest'), ('prompt_sha256', False),
    ('result', []), ('result', {'status': 'done', 'iterations': True}),
    ('result', {'status': 'running', 'iterations': 1}),
    ('result', {'status': 'done', 'iterations': 1, 'message': {}}),
    ('result', {'status': 'done', 'iterations': 1, 'workspace': 4}),
    ('result', {'status': 'done', 'iterations': 1, 'run_id': []}),
    ('result', {'status': 'done', 'iterations': 1, 'unexpected': 'constructor rejects this'}),
]


@pytest.mark.parametrize('field,value', BAD_FIELDS)
@pytest.mark.parametrize('valid_tail', [False, True])
def test_shared_loader_rejects_invalid_present_fields_even_before_valid_tail(tmp_path, field, value, valid_tail):
    path = checkpoint(tmp_path)
    records = rows(path)
    original = copy.deepcopy(records[0])
    records[0]['state'][field] = value
    if valid_tail:
        original['seq'] = 2
        records.append(original)
    rewrite(path, records)
    before = contents(path)
    with pytest.raises(ValueError):
        RunState(path.parent, path.name)
    assert contents(path) == before
    assert_unlocked(path)


@pytest.mark.parametrize('mutation', ['record_list', 'record_null', 'state_list', 'state_null',
    'seq_bool', 'seq_float', 'seq_string', 'seq_missing', 'event_missing', 'event_list',
    'at_missing', 'at_null', 'data_missing', 'data_list', 'identity_missing', 'identity_mismatch'])
def test_shared_loader_validates_envelope_before_indexing_or_acceptance(tmp_path, mutation):
    path = checkpoint(tmp_path)
    record = rows(path)[0]
    if mutation.startswith('record_'):
        record = [] if mutation == 'record_list' else None
    elif mutation.startswith('state_'):
        record['state'] = [] if mutation == 'state_list' else None
    elif mutation.startswith('identity_'):
        if mutation == 'identity_missing':
            del record['state']['run_id']
        else:
            record['state']['run_id'] = 'other'
    else:
        field, action = mutation.split('_')
        if action == 'missing':
            del record[field]
        else:
            record[field] = {'bool': True, 'float': 1.0, 'string': '1', 'list': [], 'null': None}[action]
    rewrite(path, [record])
    before = contents(path)
    with pytest.raises(ValueError):
        RunState(path.parent, path.name)
    assert contents(path) == before
    assert_unlocked(path)


@pytest.mark.parametrize('phase', ['contract_needed', 'contract_running', 'ready', 'worker_running', 'review_pending'])
def test_partial_generic_journals_still_load_and_ignore_snapshot(tmp_path, phase):
    journal = RunState(tmp_path)
    journal.record('generic', phase=phase, unknown={'allowed': True})
    journal.close()
    (journal.path / 'state.json').write_text('damaged snapshot')
    recovered = RunState(tmp_path, journal.run_id)
    try:
        assert recovered.state == {'phase': phase, 'unknown': {'allowed': True}, 'run_id': journal.run_id}
    finally:
        recovered.close()


async def rejected_resume(path, tmp_path):
    worker = BudgetWorker(tmp_path, ['STATUS: DONE'])
    effects = []
    loop = AutonomousLoop(worker, state_dir=path.parent, evaluate=False,
                          on_event=lambda event: effects.append(event))
    before = contents(path)
    result = await loop.run('goal', resume_run_id=path.name)
    assert result.status == 'error' and result.run_id == path.name
    assert 0 < len(result.message) <= 1000
    assert worker.prompts == [] and worker.hooks == [] and effects == []
    assert contents(path) == before
    assert_unlocked(path)
    assert not loop._active


@pytest.mark.parametrize('field', ['goal', 'worker_workspace', 'phase', 'iterations', 'uncertain',
                                    'contract', 'status', 'result'])
async def test_loop_requires_complete_state_without_runnable_defaults(tmp_path, field):
    path = checkpoint(tmp_path)
    records = rows(path)
    del records[0]['state'][field]
    rewrite(path, records)
    await rejected_resume(path, tmp_path)


@pytest.mark.parametrize('phase', ['worker_runninG', None, 7, {}, 'x' * 5000])
async def test_known_malformed_phases_never_claim_or_dispatch(tmp_path, phase):
    path = checkpoint(tmp_path, phase=phase, iterations=1)
    await rejected_resume(path, tmp_path)


@pytest.mark.parametrize('changes', [
    {'phase': 'contract_needed', 'contract': '- unexpected'},
    {'phase': 'contract_running', 'contract': '', 'iterations': 1},
    {'phase': 'worker_running', 'iterations': 0}, {'contract': ''},
    {'phase': 'review_pending', 'iterations': 0, 'worker_status': 'DONE'},
    {'phase': 'review_pending', 'iterations': 1, 'worker_status': 'CONTINUE'},
    {'phase': 'ready', 'status': 'done'}, {'phase': 'ready', 'status': 'unverified'},
    {'phase': 'contract_needed', 'contract': '', 'status': 'need_input'},
    {'status': 'budget', 'result': None}, {'status': 'need_input', 'result': None},
])
async def test_inconsistent_writer_state_rejected_before_hooks(tmp_path, changes):
    path = checkpoint(tmp_path, **changes)
    await rejected_resume(path, tmp_path)


@pytest.mark.parametrize('field,value', [('status', 'budget'), ('iterations', 0), ('run_id', 'other'),
                                        ('workspace', '/other/run'), ('message', 7), ('extra', 'unexpected')])
@pytest.mark.parametrize('status', ['done', 'need_input'])
async def test_terminal_result_cannot_bypass_validation(tmp_path, field, value, status):
    path = checkpoint(tmp_path, phase='review_pending', iterations=1, worker_status='DONE', status=status)
    record = rows(path)[0]
    record['state']['result'] = outcome(path, status, 1)
    record['state']['result'][field] = value
    rewrite(path, [record])
    await rejected_resume(path, tmp_path)


@pytest.mark.parametrize('phase', ['contract_running', 'worker_running'])
@pytest.mark.parametrize('uncertain', [False, True])
async def test_running_crash_prefix_requires_reconciliation_without_replay(tmp_path, phase, uncertain):
    path = checkpoint(tmp_path, phase=phase, uncertain=uncertain,
                      contract='' if phase == 'contract_running' else '- criterion',
                      iterations=0 if phase == 'contract_running' else 1)
    worker = BudgetWorker(tmp_path)
    loop = AutonomousLoop(worker, state_dir=path.parent, evaluate=False)
    result = await loop.run('goal', resume_run_id=path.name)
    assert result.status == 'need_input' and worker.prompts == []
    assert worker.hooks == ['begin', 'end']
    assert_unlocked(path)


async def test_conservative_uncertainty_in_ready_still_gates_replay(tmp_path):
    path = checkpoint(tmp_path, uncertain=True)
    worker = BudgetWorker(tmp_path)
    result = await AutonomousLoop(worker, state_dir=path.parent).run('goal', resume_run_id=path.name)
    assert result.status == 'need_input' and worker.prompts == []
    assert worker.hooks == ['begin', 'end']


@pytest.mark.parametrize('phase', ['contract_needed', 'contract_running', 'ready', 'worker_running', 'review_pending'])
async def test_writer_compatible_phases_resume_with_matched_budget_lifecycle(tmp_path, phase):
    early = phase.startswith('contract_')
    attempted = phase in ('worker_running', 'review_pending')
    path = checkpoint(tmp_path, phase=phase, contract='' if early else '- criterion',
                      iterations=int(attempted), worker_status='DONE' if phase == 'review_pending' else None)
    worker = BudgetWorker(tmp_path, ['- criterion', 'STATUS: DONE'] if early else ['STATUS: DONE'])
    reviewed = []
    def reviewer(*args):
        reviewed.append('review')
        return Reviewer()
    loop = AutonomousLoop(worker, state_dir=path.parent, evaluator_backend_factory=reviewer)
    result = await loop.run('goal', resume_run_id=path.name, resume_note='Inspected effects; proceed without repetition.')
    assert result.status == 'done' and reviewed == ['review']
    assert len(worker.prompts) == (0 if phase == 'review_pending' else 2 if early else 1)
    assert worker.hooks == ['begin', 'end']
    assert_unlocked(path)


async def test_running_checkpoint_preserves_stale_result_status_and_verdict(tmp_path):
    path = checkpoint(tmp_path, phase='worker_running', iterations=2, worker_status='DONE', verdict='PASS')
    records = rows(path)
    records[0]['state']['result'] = outcome(path, 'budget', 1)
    rewrite(path, records)
    worker = BudgetWorker(tmp_path, ['STATUS: DONE'])
    loop = AutonomousLoop(worker, state_dir=path.parent, evaluate=False)
    result = await loop.run('goal', resume_run_id=path.name, resume_note='Inspected previous effects; verify only.')
    assert result.status == 'unverified' and result.iterations == 3
    assert len(worker.prompts) == 1 and 'verify only' in worker.prompts[0]
    assert worker.hooks == ['begin', 'end']


async def test_valid_new_budget_resume_and_done_return_ignore_snapshot(tmp_path):
    worker = BudgetWorker(tmp_path, ['STATUS: CONTINUE'])
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs', max_iterations=1)
    first = await loop.run('goal', acceptance_criteria='- criterion')
    assert first.status == 'budget' and worker.hooks == ['begin', 'end']
    path = loop.workspace
    (path / 'state.json').write_text('invalid convenience snapshot')
    worker2 = BudgetWorker(tmp_path, ['STATUS: DONE'])
    loop2 = AutonomousLoop(worker2, state_dir=path.parent, evaluator_backend_factory=lambda *args: Reviewer())
    done = await loop2.run('goal', resume_run_id=path.name)
    assert done.status == 'done' and done.iterations == 2
    before = contents(path)
    again = await loop2.run('goal', resume_run_id=path.name)
    assert again == done and contents(path) == before and len(worker2.prompts) == 1
    assert worker2.hooks == ['begin', 'end', 'begin', 'end']


@pytest.mark.parametrize('note', [None, '', '   ', 'Inspected effects; review existing artifacts only.'])
async def test_explicit_uncertainty_precedes_done_return_and_reconciles_by_review(tmp_path, note):
    path = checkpoint(tmp_path, phase='review_pending', iterations=1, worker_status='DONE',
                      status='done', uncertain=True)
    record = rows(path)[0]
    record['state']['result'] = outcome(path, 'done', 1)
    rewrite(path, [record])
    worker = BudgetWorker(tmp_path)
    reviewed = []
    def reviewer(*args):
        reviewed.append('review')
        return Reviewer()
    loop = AutonomousLoop(worker, state_dir=path.parent, evaluator_backend_factory=reviewer)
    result = await loop.run('goal', resume_run_id=path.name, resume_note=note)
    meaningful = bool(note and note.strip())
    assert result.status == ('done' if meaningful else 'need_input')
    assert reviewed == (['review'] if meaningful else [])
    assert worker.prompts == [] and worker.hooks == ['begin', 'end']


@pytest.mark.parametrize('kind', ['cancel', 'keyboard'])
async def test_preclaim_budget_interruption_never_appends_to_loaded_run(tmp_path, kind):
    import asyncio
    path = checkpoint(tmp_path)
    before = contents(path)
    class InterruptedBudget(BudgetWorker):
        def begin_run_budget(self):
            self.hooks.append('begin')
            if kind == 'cancel':
                raise asyncio.CancelledError
            raise KeyboardInterrupt
    worker = InterruptedBudget(tmp_path)
    loop = AutonomousLoop(worker, state_dir=path.parent)
    if kind == 'cancel':
        with pytest.raises(asyncio.CancelledError):
            await loop.run('goal', resume_run_id=path.name)
    else:
        result = await loop.run('goal', resume_run_id=path.name)
        assert result.status == 'stopped' and result.run_id == path.name
    assert contents(path) == before and worker.prompts == []
    assert worker.hooks == ['begin']
    assert_unlocked(path)


async def test_rejected_resume_diagnostic_never_reuses_previous_run_workspace(tmp_path):
    worker = BudgetWorker(tmp_path, ['STATUS: DONE'])
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs', evaluate=False)
    original = await loop.run('goal', acceptance_criteria='- criterion')
    path = checkpoint(tmp_path, phase='worker_runninG', iterations=1)
    before = contents(path)
    result = await loop.run('goal', resume_run_id=path.name)
    assert result.status == 'error' and result.run_id == path.name
    assert result.workspace != original.workspace
    assert contents(path) == before and worker.hooks == ['begin', 'end']
    assert_unlocked(path)


async def test_claimed_worker_failure_preserves_original_unbounded_error_text(tmp_path):
    message = 'worker detail ' * 200 + 'final recovery instruction'
    async def failure():
        raise RuntimeError(message)
        yield
    worker = BudgetWorker(tmp_path, [failure])
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs')
    result = await loop.run('goal', acceptance_criteria='- criterion')
    assert result.status == 'error' and result.message == 'RuntimeError: ' + message
    assert worker.hooks == ['begin', 'end']


async def test_new_run_blank_criteria_preserves_budget_begin_end_order(tmp_path):
    worker = BudgetWorker(tmp_path)
    loop = AutonomousLoop(worker, state_dir=tmp_path / 'runs')
    result = await loop.run('goal', acceptance_criteria='   ')
    assert result.status == 'error' and worker.hooks == ['begin', 'end']
    assert worker.prompts == [] and not (tmp_path / 'runs').exists()


def numeric_record(path, token, *, location='state', valid_tail=False):
    record = rows(path)[0]
    original = copy.deepcopy(record)
    target = record if location == 'envelope' else record[location]
    target['opaque'] = {'nested': [None, True, '__numeric_token__']}
    encoded = json.dumps(record).replace('"__numeric_token__"', token) + '\n'
    if valid_tail:
        original['seq'] = 2
        encoded += json.dumps(original) + '\n'
    (path / 'ledger.jsonl').write_text(encoded)


@pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity', '1e999', '-1e999', '1E+999'])
@pytest.mark.parametrize('location', ['state', 'data', 'envelope'])
@pytest.mark.parametrize('valid_tail', [False, True])
def test_nonfinite_numbers_rejected_by_shared_parser_at_any_depth(tmp_path, token, location, valid_tail):
    path = checkpoint(tmp_path)
    numeric_record(path, token, location=location, valid_tail=valid_tail)
    before = contents(path)
    with pytest.raises(ValueError):
        RunState(path.parent, path.name)
    assert contents(path) == before
    assert_unlocked(path)


@pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity', '1e999', '-1e999', '1E+999'])
async def test_nonfinite_number_never_reaches_claim_budget_or_dispatch(tmp_path, token):
    path = checkpoint(tmp_path)
    numeric_record(path, token)
    await rejected_resume(path, tmp_path)


@pytest.mark.parametrize('token,expected', [
    ('0.125', 0.125), ('1.25e2', 125.0), ('-6.25E-2', -0.0625),
    ('1e308', 1e308), ('-1e308', -1e308), ('1e-999', 0.0), ('0e999', 0.0),
    ('123456789012345678901234567890', 123456789012345678901234567890),
])
async def test_finite_numeric_json_preserves_generic_and_loop_roundtrip(tmp_path, token, expected):
    path = checkpoint(tmp_path)
    numeric_record(path, token)
    recovered = RunState(path.parent, path.name)
    try:
        assert recovered.state['opaque'] == {'nested': [None, True, expected]}
    finally:
        recovered.close()
    worker = BudgetWorker(tmp_path, ['STATUS: DONE'])
    result = await AutonomousLoop(worker, state_dir=path.parent, evaluate=False).run('goal', resume_run_id=path.name)
    assert result.status == 'unverified' and len(worker.prompts) == 1
    assert worker.hooks == ['begin', 'end']
    assert rows(path)[-1]['state']['opaque'] == {'nested': [None, True, expected]}
    generic = RunState(tmp_path / 'generic')
    opaque = {'numeric': expected, 'text': 'NaN Infinity -Infinity 1e999',
              'normal': [None, True, False, {}, [], '界']}
    generic.record('generic', phase='ready', opaque=opaque)
    generic.close()
    loaded = RunState(tmp_path / 'generic', generic.run_id)
    try:
        assert loaded.state['opaque'] == opaque
        loaded.record('roundtrip', other_unknown={'allowed': []})
    finally:
        loaded.close()


def unicode_record(path, escaped, *, location='state', key=False, valid_tail=False):
    record = rows(path)[0]
    original = copy.deepcopy(record)
    target = record if location == 'envelope' else record[location]
    target['opaque'] = ([{'__unicode_token__': 'ordinary value'}] if key
                        else {'nested': ['__unicode_token__']})
    encoded = json.dumps(record).replace('__unicode_token__', escaped) + '\n'
    if valid_tail:
        original['seq'] = 2
        encoded += json.dumps(original) + '\n'
    (path / 'ledger.jsonl').write_text(encoded)


@pytest.mark.parametrize('escaped', [r'\ud800', r'\udbff', r'\udc00', r'\udfff',
                                      r'\udc00\ud800', r'prefix\ud800\ud800suffix'])
@pytest.mark.parametrize('location', ['state', 'data', 'envelope'])
@pytest.mark.parametrize('key', [False, True])
@pytest.mark.parametrize('valid_tail', [False, True])
async def test_unencodable_unicode_keys_and_values_reject_entire_recovery_record(
        tmp_path, escaped, location, key, valid_tail):
    path = checkpoint(tmp_path)
    unicode_record(path, escaped, location=location, key=key, valid_tail=valid_tail)
    before = contents(path)
    with pytest.raises(ValueError):
        RunState(path.parent, path.name)
    assert contents(path) == before
    assert_unlocked(path)
    await rejected_resume(path, tmp_path)


@pytest.mark.parametrize('escaped', [r'\ud800', r'\udbff', r'\udc00', r'\udfff',
                                      r'\udc00\ud800', r'prefix\ud800\ud800suffix'])
async def test_unencodable_unicode_never_reaches_budget_claim_or_dispatch(tmp_path, escaped):
    path = checkpoint(tmp_path)
    unicode_record(path, escaped)
    await rejected_resume(path, tmp_path)


@pytest.mark.parametrize('escaped,expected', [
    (r'\ud83d\ude00', '😀'), (r'\udbff\udfff', '\U0010ffff'),
    (r'\u754c\u00e9', '界é'), (r'e\u0301', 'e\u0301'),
    (r'\u0000\b\f\n\r\t', '\0\b\f\n\r\t'), (r'\uffff\ufdd0', '\uffff\ufdd0'),
])
@pytest.mark.parametrize('key', [False, True])
async def test_encodable_unicode_pairs_and_controls_preserve_read_resume_write(tmp_path, escaped, expected, key):
    path = checkpoint(tmp_path)
    unicode_record(path, escaped, key=key)
    opaque = [{expected: 'ordinary value'}] if key else {'nested': [expected]}
    recovered = RunState(path.parent, path.name)
    try:
        assert recovered.state['opaque'] == opaque
    finally:
        recovered.close()
    worker = BudgetWorker(tmp_path, ['STATUS: DONE'])
    result = await AutonomousLoop(worker, state_dir=path.parent, evaluate=False).run('goal', resume_run_id=path.name)
    assert result.status == 'unverified' and len(worker.prompts) == 1
    assert worker.hooks == ['begin', 'end']
    assert rows(path)[-1]['state']['opaque'] == opaque
    generic = RunState(tmp_path / 'generic')
    generic.record('generic', phase='ready', opaque=opaque)
    generic.close()
    loaded = RunState(tmp_path / 'generic', generic.run_id)
    try:
        assert loaded.state['opaque'] == opaque
        loaded.record('roundtrip', other_unknown={'allowed': [None, False, 0.125]})
    finally:
        loaded.close()
