"""Process exits and terminal delivery preserve acknowledged owner intent."""
import json
from contextlib import closing
from pathlib import Path
import subprocess
import sys

import pytest

from dream.core.run_state import RunState
from dream.core.task_checkpoints import ManagedTaskCoordinator
from dream.core.worker_checkpoints import WorkerCheckpointError, WorkerJournal
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
from test_local_subagents import _backend, _lead_final, _sub_final
from test_parent_continuation_http import collect, task_round
from test_worker_contracts import _Engine, _spec
from test_worker_delegations import parent
from test_worker_journal_http import durable, journals


STARTUP_CHILD = r'''
import asyncio,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_worker_delegations import parent
from test_local_subagents import _backend,_lead_final,_sub_final,_tool
from test_worker_contracts import _Engine,_spec
from test_parent_continuation_http import collect,task_round
root,action=Path(sys.argv[1]),sys.argv[2]
fixture=parent.__wrapped__(root);p=next(fixture);posts=[];handlers=[]
async def read_file(arguments):
    handlers.append(arguments)
    return {'content':[]}
b=_backend([_tool('read_file',read_file)],_spec('read_file',name='worker'))
b._role_model=lambda role:'different-worker-model'
async def properties(model):
    worker=next(iter(b._workers.values()))
    observation={'action':action,'parent_run_id':p.c.journal.run_id,
        'binding':worker.binding,'before':worker.state,
        'posts':len(posts),'handlers':len(handlers)}
    try:observation['ack']=b.worker_control(action,worker.activity['run_id'],'startup instruction')
    except ValueError as exc:observation['refusal']=str(exc)
    observation.update(state=worker.state,cancelled=worker.scope.cancel_called,
        inbox=list(worker.inbox),resume=worker.resume.is_set())
    (root/'startup-witness.json').write_text(json.dumps(observation,ensure_ascii=False))
    os._exit(42)
b._routed_props=properties
class Engine(_Engine):
    async def post(self,url,json=None):
        posts.append(json)
        return await super().post(url,json=json)
b._client=Engine(_sub_final('must not execute'),lead=[task_round(),_lead_final()])
asyncio.run(collect(b,p))
raise AssertionError('Routed-properties startup boundary was never reached')
'''


@pytest.mark.parametrize('action', ['agent_message', 'agent_pause', 'agent_resume', 'agent_stop'])
def test_startup_control_refusal_survives_process_exit(tmp_path, action):
    completed = subprocess.run(
        [sys.executable, '-c', STARTUP_CHILD, str(tmp_path), action],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert completed.returncode == 42, completed.stderr
    witness = json.loads((tmp_path / 'startup-witness.json').read_text())
    assert witness['action'] == action
    assert 'ack' not in witness and 'refusal' in witness
    assert witness['posts'] == witness['handlers'] == 0
    assert witness['inbox'] == []
    if action == 'agent_stop':
        assert witness['state'] == 'stopping' and witness['cancelled'] is True
        assert 'reconciliation' in witness['refusal'].lower()
    else:
        assert witness['state'] == witness['before'] == 'running'
        assert witness['cancelled'] is False and witness['resume'] is True
        assert 'initializ' in witness['refusal'].lower()
    memory = MemoryStore(tmp_path / 'tasks.db')
    try:
        with closing(RunState(tmp_path / 'runs', witness['parent_run_id'])) as parent_journal:
            coordinator = ManagedTaskCoordinator(parent_journal, TaskStore(memory))
            coordinator.attach()
            binding = witness['binding']
            assert binding['delegation_id'] in coordinator.pending_children
            assert parent_journal.state['managed']['children'][binding['delegation_id']]['status'] == 'reserved'
            # Opening storage must not turn the uninitialized child into runnable work.
            with pytest.raises(WorkerCheckpointError) as refused:
                with WorkerJournal(tmp_path, binding['session_id'], binding['worker_id']) as child:
                    child.inspect()
            assert refused.value.code == 'missing'
    finally:
        memory.close()


async def test_message_after_loop_return_blocks_terminal_commit(parent, journals, monkeypatch):
    from dream.core.backends import openai_compat
    backend = _backend([], _spec(name='worker'))
    fake = backend._client = _Engine(_sub_final('premature final answer'),
                                    lead=[task_round(), _lead_final()])
    message = 'owner instruction after loop return must remain recoverable'
    returned = False
    observed = []
    original_loop = backend._subagent_loop
    original_checkpoint = openai_compat._work_checkpoint
    async def loop(*args, **kwargs):
        nonlocal returned
        result = await original_loop(*args, **kwargs)
        returned = True
        return result
    async def checkpoint():
        nonlocal returned
        if returned:
            returned = False
            ack = backend.worker_control('agent_message', journals[0].run_id, message)
            observed.append(ack)
        await original_checkpoint()
    monkeypatch.setattr(backend, '_subagent_loop', loop)
    monkeypatch.setattr(openai_compat, '_work_checkpoint', checkpoint)
    events = await collect(backend, parent)
    assert len(observed) == 1 and observed[0]['pending'] == 1
    assert len(fake.posted) == 1
    state = durable(journals[0])
    assert [item['text'] for item in state['messages']] == [message]
    assert state['outcome'] != 'completed' and state['acknowledged'] is False
    assert state['binding']['delegation_id'] in parent.c.pending_children
    assert parent.c.actions_admitted is False
    assert any(event.kind == 'tool_result' and event.data['is_error'] for event in events)
