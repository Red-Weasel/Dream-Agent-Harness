"""Owned child process exits preserve managed HTTP worker journal boundaries."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from dream.core.worker_checkpoints import WorkerJournal, WorkerCheckpointError
from dream.core.worker_continuation import WorkerSnapshots
from dream.core.run_state import RunState
from dream.core.task_checkpoints import ManagedTaskCoordinator
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore


CHILD = r'''
import asyncio,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from dream.core.worker_checkpoints import WorkerJournal
from dream.core.task_checkpoints import ManagedTaskCoordinator
from test_worker_delegations import parent
from test_local_subagents import _backend,_lead_final,_sub_final,_sub_toolcall,_tool
from test_worker_contracts import _Engine,_spec
from test_parent_continuation_http import collect,task_round
root,stage=Path(sys.argv[1]),sys.argv[2]
fixture=parent.__wrapped__(root);p=next(fixture);opened=[]
original_create=WorkerJournal.create
def create(self,*args,**kwargs):
    result=original_create(self,*args,**kwargs);opened.append(self);return result
WorkerJournal.create=create
def crash():
    journal=opened[0];saved=journal.inspect()
    (root/'crash-witness.json').write_text(json.dumps({
        'session_id':saved['binding']['session_id'],'run_id':journal.run_id,
        'binding':saved['binding'],'state':saved,'stage':stage,
        'parent_run_id':p.c.journal.run_id},ensure_ascii=False))
    os._exit(42)
async def write_file(arguments):
    (root/'effect-marker.txt').write_text('effect executed once')
    if stage=='handler':crash()
    return {'content':[{'type':'text','text':'write completed'}]}
original_finished=WorkerJournal.tool_finished
def finished(self,*args,**kwargs):
    result=original_finished(self,*args,**kwargs)
    if stage=='tool_returned':crash()
    return result
WorkerJournal.tool_finished=finished
original_settle=WorkerJournal.settle
def settle(self,*args,**kwargs):
    result=original_settle(self,*args,**kwargs)
    if stage=='settled' and self.inspect()['requests']==1:crash()
    return result
WorkerJournal.settle=settle
original_return=ManagedTaskCoordinator.record_child_return
def returned(self,*args,**kwargs):
    if stage=='terminal':crash()
    return original_return(self,*args,**kwargs)
ManagedTaskCoordinator.record_child_return=returned
original_ack=WorkerJournal.acknowledge
def ack(self,*args,**kwargs):
    if stage=='parent_returned':crash()
    return original_ack(self,*args,**kwargs)
WorkerJournal.acknowledge=ack
class Engine(_Engine):
    async def post(self,url,json=None):
        if stage=='before_post':crash()
        return await super().post(url,json=json)
b=_backend([_tool('write_file',write_file)],_spec('write_file',name='worker'))
b._client=Engine(_sub_toolcall('write_file',{'path':'effect-marker.txt','content':'effect executed once'}),
                _sub_final('exact terminal result'),lead=[task_round(),_lead_final()])
asyncio.run(collect(b,p))
raise AssertionError('Requested crash boundary was never reached')
'''


@pytest.mark.parametrize('stage', ['before_post', 'handler', 'tool_returned',
                                 'settled', 'terminal', 'parent_returned'])
def test_process_exit_preserves_exact_worker_boundary(tmp_path, stage):
    completed = subprocess.run([sys.executable, '-c', CHILD, str(tmp_path), stage],
                               cwd=Path(__file__).resolve().parents[1],
                               capture_output=True, text=True, timeout=20)
    assert completed.returncode == 42, completed.stderr
    witness = json.loads((tmp_path / 'crash-witness.json').read_text())
    marker = tmp_path / 'effect-marker.txt'
    assert marker.exists() is (stage != 'before_post')
    if marker.exists():
        assert marker.read_text() == 'effect executed once'
    with WorkerJournal(tmp_path, witness['session_id'], witness['run_id']) as journal:
        state = journal.inspect()
        assert state == witness['state']
        assert state['binding'] == witness['binding']
        assert state['requests'] == (2 if stage in ('terminal', 'parent_returned') else 1)
        assert not state['acknowledged']
        if stage in ('before_post', 'handler', 'tool_returned'):
            assert state['pending'] is not None
            if stage != 'before_post':
                tools = list(state['pending']['tools'].values())
                assert len(tools) == 1
                assert tools[0]['state'] == ('intent' if stage == 'handler' else 'returned')
            with pytest.raises(WorkerCheckpointError) as refused:
                journal.resume(witness['binding'], now_ns=state['last_clock_ns'] + 1)
            assert refused.value.code == 'unsettled'
        elif stage == 'settled':
            assert state['pending'] is None and state['outcome'] is None
            snapshot = WorkerSnapshots(journal).unpack()
            assert snapshot['next_round'] == 2
            assert snapshot['state']['completed_effects']
            assert any(item.get('role') == 'tool' for item in snapshot['messages'])
            restored = journal.resume(witness['binding'], now_ns=state['last_clock_ns'] + 1)
            assert restored['requests'] == 1
            assert restored['deadline_ns'] == state['deadline_ns']
        else:
            assert state['outcome'] == 'completed' and state['pending'] is None
            assert state['snapshot']['parent_result'] == {'text': 'exact terminal result', 'is_error': False}
            with pytest.raises(WorkerCheckpointError) as refused:
                journal.resume(witness['binding'], now_ns=state['last_clock_ns'] + 1)
            assert refused.value.code == 'terminal'
            parent_journal = RunState(tmp_path / 'runs', witness['parent_run_id'])
            memory = MemoryStore(tmp_path / 'tasks.db')
            try:
                coordinator = ManagedTaskCoordinator(parent_journal, TaskStore(memory))
                coordinator.attach()
                delegation = witness['binding']['delegation_id']
                children = parent_journal.state['managed']['children']
                assert children[delegation]['status'] == (
                    'reserved' if stage == 'terminal' else 'returned')
                context_before = parent_journal.state['parent_context']
                receipt = coordinator.record_child_return(witness['binding'], journal)
                assert coordinator.record_child_return(witness['binding'], journal) == receipt
                journal.acknowledge(state['completion_seq'])
                assert journal.inspect()['acknowledged'] is True
                assert coordinator.pending_children == {}
                assert parent_journal.state['parent_context'] == context_before
                assert marker.read_text() == 'effect executed once'
            finally:
                parent_journal.close()
                memory.close()
