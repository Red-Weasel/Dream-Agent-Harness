"""Review regressions at actual managed HTTP dispatch and acknowledgement seams."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys

import pytest

from dream.core.worker_checkpoints import WorkerJournal
from dream.core.worker_continuation import WorkerSnapshots
from test_local_subagents import _backend, _lead_final, _sub_final, _sub_toolcall, _tool
from test_parent_continuation_http import collect, task_round
from test_worker_contracts import _Engine, _spec
from test_worker_delegations import parent
from test_worker_journal_http import durable, journals


UNSENT_CHILD = r'''
import asyncio,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from dream.core.worker_checkpoints import WorkerJournal
from test_worker_delegations import parent
from test_local_subagents import _backend,_lead_final,_sub_final,_sub_toolcall,_tool
from test_worker_contracts import _Engine,_spec
from test_parent_continuation_http import collect,task_round
root,stage=Path(sys.argv[1]),sys.argv[2]
fixture=parent.__wrapped__(root);p=next(fixture);opened=[];posted=[]
original_create=WorkerJournal.create
def create(self,*args,**kwargs):
    result=original_create(self,*args,**kwargs);opened.append(self);return result
WorkerJournal.create=create
def crash(journal):
    saved=journal.inspect()
    (root/'unsent-witness.json').write_text(json.dumps({
        'session_id':saved['binding']['session_id'],'run_id':journal.run_id,
        'state':saved,'posted':len(posted)},ensure_ascii=False))
    os._exit(42)
original_started=WorkerJournal.request_started
def started(self,*args,**kwargs):
    if stage=='pre_request':crash(self)
    return original_started(self,*args,**kwargs)
WorkerJournal.request_started=started
original_settle=WorkerJournal.settle
def settle(self,*args,**kwargs):
    result=original_settle(self,*args,**kwargs)
    if stage=='inbox' and kwargs.get('consumed_messages'):crash(self)
    return result
WorkerJournal.settle=settle
async def read_file(arguments):return {'content':[]}
class Engine(_Engine):
    async def post(self,url,json=None):
        posted.append(json)
        if stage=='inbox' and len(posted)==1:
            b.worker_control('agent_message',opened[0].run_id,'unsent owner instruction')
        return await super().post(url,json=json)
b=_backend([_tool('read_file',read_file)],_spec('read_file',name='worker'))
b._client=Engine(_sub_toolcall('read_file',{'path':'fixture'}),_sub_final('done'),
                 lead=[task_round(),_lead_final()])
asyncio.run(collect(b,p))
raise AssertionError('Unsent request crash boundary was never reached')
'''


@pytest.mark.parametrize('stage,used', [('pre_request', 0), ('inbox', 1)])
def test_abrupt_exit_keeps_unsent_round_runnable(tmp_path, stage, used):
    completed = subprocess.run(
        [sys.executable, '-c', UNSENT_CHILD, str(tmp_path), stage],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert completed.returncode == 42, completed.stderr
    witness = json.loads((tmp_path / 'unsent-witness.json').read_text())
    assert witness['posted'] == used
    with WorkerJournal(tmp_path, witness['session_id'], witness['run_id']) as journal:
        state = journal.inspect()
        assert state == witness['state']
        assert state['requests'] == used and state['pending'] is None
        assert state['outcome'] is None
        snapshot = WorkerSnapshots(journal).unpack()
        assert snapshot['next_round'] == used + 1
        if stage == 'inbox':
            assert state['messages'] == []
            assert any('unsent owner instruction' in str(item.get('content', ''))
                       for item in snapshot['messages'])
        restored = journal.resume(state['binding'], now_ns=state['last_clock_ns'] + 1)
        assert restored['requests'] == used
        assert restored['deadline_ns'] == state['deadline_ns']


@pytest.mark.parametrize('action', ['agent_message', 'agent_pause', 'agent_resume', 'agent_stop'])
async def test_routed_properties_barrier_refuses_undurable_controls(parent, monkeypatch, action):
    backend = _backend([], _spec(name='worker'))
    monkeypatch.setattr(backend, '_role_model', lambda role: 'different-worker-model')
    observed = {}
    async def properties(model):
        # This awaited callback holds the real child at its routed-properties barrier.
        worker = next(iter(backend._workers.values()))
        observed['entered'] = True
        before = (worker.state, list(worker.inbox), worker.resume.is_set())
        try:
            observed['ack'] = backend.worker_control(action, worker.activity['run_id'], 'startup instruction')
        except ValueError as exc:
            observed['refusal'] = str(exc)
        observed['after'] = (worker.state, list(worker.inbox), worker.resume.is_set())
        observed['before'] = before
        observed['cancelled'] = worker.scope.cancel_called
        if action == 'agent_stop':
            # Stop must cancel the awaited initialization even when its ACK is refused.
            await asyncio.Future()
        # Let the broken implementation finish so RED reports the false ACK
        # rather than hanging forever on the pause it incorrectly accepted.
        worker.resume.set()
        return {}
    monkeypatch.setattr(backend, '_routed_props', properties)
    fake = backend._client = _Engine(_sub_final('done'), lead=[task_round(), _lead_final()])
    await collect(backend, parent)
    assert observed.get('entered')
    assert 'ack' not in observed
    assert 'refusal' in observed
    if action == 'agent_stop':
        assert observed['cancelled'] is True
        assert observed['after'][0] == 'stopping'
        assert 'reconciliation' in observed['refusal'].lower()
        assert not fake.posted
    else:
        assert 'initializ' in observed['refusal'].lower()
        assert observed['after'] == observed['before']
        assert observed['cancelled'] is False


async def test_owner_message_during_final_response_requires_another_worker_round(parent, journals):
    message = 'owner correction arriving during final response 🌙'
    backend = _backend([], _spec(name='worker'))
    observations = []
    class Engine(_Engine):
        async def post(self, url, json=None):
            if not self.posted:
                ack = backend.worker_control('agent_message', journals[0].run_id, message)
                assert ack['pending'] == 1
                assert [item['text'] for item in durable(journals[0])['messages']] == [message]
                observations.append('acknowledged')
            else:
                assert any(item.get('role') == 'assistant' and item.get('content') == 'superseded answer'
                           for item in json['messages'])
                assert any(message in str(item.get('content', '')) for item in json['messages'])
                assert durable(journals[0])['messages'] == []
                observations.append('consumed')
            return await super().post(url, json=json)
    fake = backend._client = Engine(_sub_final('superseded answer'), _sub_final('corrected answer'),
                                    lead=[task_round(), _lead_final()])
    await collect(backend, parent)
    assert observations == ['acknowledged', 'consumed']
    assert len(fake.posted) == 2
    state = durable(journals[0])
    assert state['requests'] == 2 and state['messages'] == []
    assert state['outcome'] == 'completed' and state['acknowledged'] is True
    with WorkerJournal(parent.root, state['binding']['session_id'], journals[0].run_id) as restored:
        assert restored.inspect()['snapshot']['parent_result'] == {'text': 'corrected answer', 'is_error': False}


async def test_final_budget_message_returns_incomplete_instead_of_done(parent, journals, monkeypatch):
    from dream.core.backends import openai_compat
    monkeypatch.setattr(openai_compat, '_SUB_MAX_ROUNDS', 1)
    backend = _backend([], _spec(name='worker'))
    message = 'instruction must not disappear at budget exhaustion'
    class Engine(_Engine):
        async def post(self, url, json=None):
            ack = backend.worker_control('agent_message', journals[0].run_id, message)
            assert ack['pending'] == 1
            return await super().post(url, json=json)
    fake = backend._client = Engine(_sub_final('premature done'), lead=[task_round(), _lead_final()])
    events = await collect(backend, parent)
    assert len(fake.posted) == 1
    state = durable(journals[0])
    assert state['requests'] == 1 and state['outcome'] == 'incomplete'
    assert [item['text'] for item in state['messages']] == [message]
    with WorkerJournal(parent.root, state['binding']['session_id'], journals[0].run_id) as restored:
        assert restored.inspect()['snapshot']['parent_result']['is_error'] is True
    assert any(event.kind == 'tool_result' and event.data['is_error'] for event in events)
