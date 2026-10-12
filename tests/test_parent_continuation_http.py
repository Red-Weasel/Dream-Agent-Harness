"""Actual compatible task dispatch and managed resume seams, with fake transports."""
import asyncio
from copy import deepcopy
import json

import pytest

from dream.core import parent_continuation as pc
from dream.core.backends.base import Event
from dream.core.run_state import RunState, digest
from dream.core.task_checkpoints import ManagedTaskCoordinator, bind
from dream.core.tool_artifacts import ArtifactError, ArtifactStore
from test_local_subagents import _backend, _lead_final, _sub_final, _sse
from test_worker_contracts import _Engine, _workspace, _spec
from test_worker_delegations import parent
from test_worker_delegations import verified
from test_managed_loop import worker, loop


def task_round(prompt='original task', *, call_id='same-call', wrapped=False, siblings=False):
    arguments = json.dumps({'subagent_type': 'worker', 'prompt': prompt})
    name = 'task'
    if wrapped:
        name = 'tool_invoke'
        arguments = json.dumps({'name': 'task', 'arguments': json.loads(arguments)})
    calls = [{'index': 0, 'function': {'name': name, 'arguments': arguments}}]
    if call_id is not None:
        calls[0]['id'] = call_id
    if siblings:
        calls.append({'index': 1, 'id': 'sibling', 'function': {'name': 'read_file', 'arguments': '{"path":"note"}'}})
    return [_sse({'id': 'reply-original', 'choices': [{'delta': {'tool_calls': calls}, 'finish_reason': None}]}),
            _sse({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}), 'data: [DONE]']


async def collect(backend, parent):
    with bind(parent.c), _workspace(parent.root):
        return [event async for event in backend.ask('original goal')]


async def stop_at_return(backend, parent):
    with bind(parent.c), _workspace(parent.root):
        events = backend.ask('original goal')
        try:
            async for event in events:
                if event.kind == 'tool_result':
                    return
        finally:
            await events.aclose()


async def test_real_dispatch_saves_unclipped_arguments_and_settles_before_final_result(parent):
    backend = _backend([], _spec(name='worker'))
    prompt = 'original 🌙 task ' * 2000
    fake = backend._client = _Engine(_sub_final('checked child result'), lead=[task_round(prompt), _lead_final()])
    seen = []
    with bind(parent.c), _workspace(parent.root):
        async for event in backend.ask('original goal'):
            if event.kind == 'tool_use':
                assert parent.c.journal.state['parent_context']['status'] == 'captured'
                assert not fake.posted
            if event.kind == 'tool_result':
                assert parent.c.journal.state['parent_context']['status'] == 'settled'
                seen.append(event.data)
    reference = parent.c.journal.state['parent_context']
    restored = pc.load(parent.root, reference, execution_binding=backend._parent_context_binding())
    call = restored[reference['assistant_index']]['tool_calls'][0]
    assert json.loads(call['function']['arguments'])['prompt'] == prompt
    assert len(call['function']['arguments']) > 2000
    assert restored[-1]['content'] == 'checked child result'
    assert len(seen) == 1 and len(fake.posted) == 1


@pytest.mark.parametrize('call_id,source', [('same-call', 'supplied'), (None, 'fallback')])
async def test_actual_response_and_call_id_provenance_survive(parent, call_id, source):
    backend = _backend([], _spec(name='worker'))
    backend._client = _Engine(_sub_final('child result'), lead=[task_round(call_id=call_id), _lead_final()])
    await collect(backend, parent)
    reference = parent.c.journal.state['parent_context']
    body = json.loads(ArtifactStore(parent.root, reference['session_id'], reference['run_id']).load_original(
        reference['receipt']['handle']))
    assert body['provider_response_id'] == 'reply-original'
    assert body['wire_calls'][0]['id_source'] == source
    assert body['wire_calls'][0]['id'] == (call_id or 'call_0')


async def test_capture_storage_failure_enters_no_worker_request(parent, monkeypatch):
    backend = _backend([], _spec(name='worker'))
    fake = backend._client = _Engine(_sub_final('must not execute'), lead=[task_round()])
    def failed(*args, **kwargs):
        raise ArtifactError('disk_limit')
    monkeypatch.setattr(pc, 'capture', failed)
    events = await collect(backend, parent)
    assert not fake.posted and fake.streamed == 1
    assert events[-1].data['subtype'] == 'parent_context_checkpoint_failed'
    assert not parent.c.actions_admitted


async def test_settlement_failure_does_not_request_another_lead_or_replay_child(parent, monkeypatch):
    backend = _backend([], _spec(name='worker'))
    fake = backend._client = _Engine(_sub_final('returned exactly once'), lead=[task_round(), _lead_final('must not run')])
    def failed(*args, **kwargs):
        raise ArtifactError('disk_limit')
    monkeypatch.setattr(pc, 'settle', failed)
    events = await collect(backend, parent)
    assert len(fake.posted) == 1 and fake.streamed == 1
    assert parent.c.journal.state['parent_context']['status'] == 'captured'
    assert any(event.kind == 'tool_result' and event.data['is_error'] for event in events)
    assert any(message.get('role') == 'tool' and message['content'] == 'returned exactly once'
               for message in backend.messages)


async def test_repeated_restore_installs_one_real_pair_before_dangling_repair(parent):
    backend = _backend([], _spec(name='worker'))
    backend._client = _Engine(_sub_final('original result'), lead=[task_round(), _lead_final()])
    await stop_at_return(backend, parent)
    fresh = _backend([], _spec(name='worker'))
    assert await fresh.restore_parent_context(parent.c)
    expected = deepcopy(fresh.messages)
    assert await fresh.restore_parent_context(parent.c)
    fresh._repair_dangling()
    assert fresh.messages == expected
    assert sum(message.get('role') == 'tool' and message.get('tool_call_id') == 'same-call'
               for message in fresh.messages) == 1
    assert fresh.messages[-1]['content'] == 'original result'


@pytest.mark.parametrize('drift', ['model', 'head', 'generation'])
async def test_actual_backend_restore_refuses_execution_binding_drift(parent, drift):
    backend = _backend([], _spec(name='worker'))
    backend._client = _Engine(_sub_final('original result'), lead=[task_round(), _lead_final()])
    await stop_at_return(backend, parent)
    fresh = _backend([], _spec(name='worker'))
    if drift == 'model':
        fresh.model = 'different'
    elif drift == 'head':
        fresh.messages[0]['content'] = 'different instructions'
    else:
        state = parent.c._state
        state['generation'] += 1
        parent.c._record('changed_generation', state)
    before = deepcopy(fresh.messages)
    with pytest.raises(ValueError):
        await fresh.restore_parent_context(parent.c)
    assert fresh.messages == before


def saved_loop(worker, tmp_path, *, settled):
    backend = worker.backend = _backend([], _spec(name='worker'))
    journal = RunState(tmp_path / 'runs')
    contract = '- original criterion'
    (journal.path / 'contract.md').write_text(contract)
    (journal.path / 'progress.md').write_text('original evidence')
    journal.record('created', goal='goal', contract=contract, contract_sha256=digest(contract),
                   worker_workspace=str(tmp_path), phase='worker_running', iterations=1,
                   status='running', uncertain=True, result=None)
    coordinator = ManagedTaskCoordinator(journal, worker.tasks)
    coordinator.attach()
    messages = [deepcopy(backend.messages[0]), {'role': 'user', 'content': 'original goal'},
                {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': 'original-call', 'type': 'function', 'function': {'name': 'task', 'arguments': '{}'}}]}]
    reference = pc.capture(tmp_path, 'original-session', journal.run_id, messages,
        wire_calls=[{'id': 'original-call', 'name': 'task', 'arguments': '{}', 'id_source': 'supplied'}],
        effective_calls=[{'id': 'original-call', 'name': 'task', 'arguments': '{}'}],
        provider_response_id=None, execution_binding=backend._parent_context_binding())
    if settled:
        reference = pc.settle(tmp_path, reference, [*messages,
            {'role': 'tool', 'tool_call_id': 'original-call', 'content': 'already returned result'}])
    journal.record('parent_context_saved', parent_context=reference)
    identity = journal.run_id
    journal.close()
    return identity, backend


async def test_resume_note_cannot_clear_an_unsettled_parent_round(worker, tmp_path):
    identity, _ = saved_loop(worker, tmp_path, settled=False)
    driver = loop(worker, tmp_path)
    result = await asyncio.wait_for(driver.run('goal', resume_run_id=identity, resume_note='continue'), 5)
    assert result.status == 'need_input' and worker.calls == 0
    assert driver._journal.state['parent_context']['status'] == 'captured'


async def test_loop_installs_settled_parent_before_its_first_new_ask(worker, tmp_path):
    identity, backend = saved_loop(worker, tmp_path, settled=True)
    observed = []
    async def ask(prompt):
        observed.append(deepcopy(backend.messages))
        yield Event('assistant_done', 'STATUS: NEED_INPUT\nNEXT: stop this synthetic fixture')
    worker.ask = ask
    result = await asyncio.wait_for(loop(worker, tmp_path).run('goal', resume_run_id=identity, resume_note='continue'), 5)
    assert result.status == 'need_input' and len(observed) == 1
    assert observed[0][-1] == {'role': 'tool', 'tool_call_id': 'original-call', 'content': 'already returned result'}


async def test_loop_binding_drift_blocks_first_new_ask(worker, tmp_path):
    identity, backend = saved_loop(worker, tmp_path, settled=True)
    backend.model = 'different model'
    result = await asyncio.wait_for(loop(worker, tmp_path).run('goal', resume_run_id=identity, resume_note='continue'), 5)
    assert result.status == 'need_input' and worker.calls == 0


async def test_new_lead_request_cannot_restore_an_older_parent_snapshot(parent):
    backend = _backend([], _spec(name='worker'))
    backend._client = _Engine(_sub_final('original result'), lead=[task_round(), _lead_final('new reasoning')])
    await collect(backend, parent)
    fresh = _backend([], _spec(name='worker'))
    with pytest.raises(ValueError):
        await fresh.restore_parent_context(parent.c)
    assert fresh.messages == [{'role': 'system', 'content': 's'}]


async def test_failed_artifact_grant_validation_keeps_original_context(parent):
    backend = _backend([], _spec(name='worker'))
    backend._client = _Engine(_sub_final('original result'), lead=[task_round(), _lead_final()])
    await stop_at_return(backend, parent)
    parent.c.journal.record('invalid_saved_grant', lead_artifacts=[{'handle': 'invalid'}])
    fresh = _backend([], _spec(name='worker'))
    before = deepcopy(fresh.messages)
    with pytest.raises(ValueError):
        await fresh.restore_parent_context(parent.c)
    assert fresh.messages == before


async def test_loop_tampered_original_is_need_input_without_a_new_ask(worker, tmp_path):
    identity, _ = saved_loop(worker, tmp_path, settled=True)
    for artifact in (tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'):
        artifact.write_bytes(b'changed original')
    result = await asyncio.wait_for(loop(worker, tmp_path).run('goal', resume_run_id=identity, resume_note='continue'), 5)
    assert result.status == 'need_input' and worker.calls == 0


async def test_incomplete_parent_round_cannot_receive_a_completion_receipt(parent):
    backend = _backend([], _spec(name='worker'))
    fake = backend._client = _Engine(_sub_final('returned result'), lead=[task_round(), _lead_final()])
    with bind(parent.c), _workspace(parent.root):
        events = backend.ask('original goal')
        try:
            async for event in events:
                if event.kind == 'tool_use':
                    assert not fake.posted
                    break
        finally:
            await events.aclose()
    assert parent.c.journal.state['parent_context']['status'] == 'captured'
    with pytest.raises(ValueError):
        verified(parent)


def test_unique_call_count_overflow_is_refused_without_duplicate_ids(tmp_path):
    calls = [{'id': f'call_{i}', 'name': 'task', 'arguments': '{}'} for i in range(257)]
    messages = [{'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': call['id'], 'type': 'function', 'function': {'name': call['name'], 'arguments': '{}'}}
        for call in calls]}]
    with pytest.raises(ValueError):
        pc.capture(tmp_path, 'session', 'a' * 32, messages,
            wire_calls=[{**call, 'id_source': 'supplied'} for call in calls], effective_calls=calls,
            provider_response_id=None, execution_binding={'version': 1})


async def test_new_task_round_invalidates_previous_verification_receipt(parent):
    receipt = verified(parent)
    backend = _backend([], _spec(name='worker'))
    backend._client = _Engine(_sub_final('new task result'), lead=[task_round(), _lead_final()])
    await stop_at_return(backend, parent)
    with pytest.raises(ValueError):
        parent.c.complete_task(receipt['id'])


async def test_checkpoint_and_receipt_invalidation_survive_reopen_together(parent):
    from test_worker_delegations import reopen
    receipt = verified(parent)
    backend = _backend([], _spec(name='worker'))
    fake = backend._client = _Engine(_sub_final('result'), lead=[task_round(), _lead_final()])
    with bind(parent.c), _workspace(parent.root):
        events = backend.ask('original goal')
        try:
            async for event in events:
                if event.kind == 'tool_use':
                    assert not fake.posted
                    assert parent.c._state['receipt'] is None
                    assert parent.c._state['action_revision'] > receipt['action_revision']
                    assert parent.c._state['evidence_chain'] != receipt['action_chain']
                    break
        finally:
            await events.aclose()
    reopen(parent)
    assert parent.c.journal.state['parent_context']['status'] == 'captured'
    assert parent.c._state['receipt'] is None
    with pytest.raises(ValueError):
        parent.c.complete_task(receipt['id'])


@pytest.mark.parametrize('closed', ['stopped', 'completed', 'unbound_uncertainty', 'task_projection', 'contract'])
async def test_closed_parent_never_dispatches_checkpointed_task(parent, closed):
    if closed == 'stopped':
        parent.c.stop()
    elif closed == 'completed':
        parent.c.complete_task(verified(parent)['id'])
    elif closed == 'unbound_uncertainty':
        parent.c.journal.record('interrupted', phase='worker_interrupted', uncertain=True)
    elif closed == 'contract':
        (parent.c.journal.path / 'contract.md').write_text('changed contract')
    else:
        # The live task projection must bind the original host contract too.
        parent.c.tasks._conn.execute('UPDATE tasks SET managed_revision = managed_revision + 1')
        parent.c.tasks._conn.commit()
    backend = _backend([], _spec(name='worker'))
    fake = backend._client = _Engine(_sub_final('must not run'), lead=[task_round()])
    events = await collect(backend, parent)
    assert not fake.posted and fake.streamed == 1
    assert events[-1].data['subtype'] == 'parent_context_checkpoint_failed'
    assert parent.c.journal.state.get('parent_context') is None
    assert not parent.c.actions_admitted


async def test_parent_settlement_after_stop_is_bookkeeping_only(parent, monkeypatch):
    real_settle = pc.settle
    def stop_then_settle(*args):
        parent.c.stop()
        return real_settle(*args)
    monkeypatch.setattr(pc, 'settle', stop_then_settle)
    backend = _backend([], _spec(name='worker'))
    fake = backend._client = _Engine(_sub_final('returned result'), lead=[task_round(), _lead_final()])
    await stop_at_return(backend, parent)
    assert len(fake.posted) == 1 and fake.streamed == 1
    assert parent.c.journal.state['parent_context']['status'] == 'settled'
    assert not parent.c.actions_admitted
    with pytest.raises(ValueError):
        verified(parent)


@pytest.mark.parametrize('stage', ['captured', 'settled'])
async def test_parent_checkpoint_record_failure_seals_without_partial_publication(parent, monkeypatch, stage):
    real_record = parent.c.journal.record
    def fail_record(event, **kwargs):
        if event == 'parent_context_' + stage:
            raise OSError('injected parent ledger failure')
        return real_record(event, **kwargs)
    monkeypatch.setattr(parent.c.journal, 'record', fail_record)
    backend = _backend([], _spec(name='worker'))
    fake = backend._client = _Engine(_sub_final('returned result'), lead=[task_round(), _lead_final()])
    events = await collect(backend, parent)
    assert fake.streamed == 1 and len(fake.posted) == (stage == 'settled')
    if stage == 'captured':
        assert parent.c.journal.state.get('parent_context') is None
        assert events[-1].data['subtype'] == 'parent_context_checkpoint_failed'
    else:
        assert parent.c.journal.state['parent_context']['status'] == 'captured'
        assert any(event.kind == 'tool_result' and event.data['is_error'] for event in events)
    assert not parent.c.actions_admitted
    with pytest.raises(ValueError):
        verified(parent)


async def test_checked_reset_retires_parent_snapshot_in_the_ack_commit(parent):
    backend = _backend([], _spec(name='worker'))
    backend._client = _Engine(_sub_final('returned result'), lead=[task_round(), _lead_final()])
    await stop_at_return(backend, parent)
    receipt = verified(parent)
    parent.c.complete_task(receipt['id'])
    token = parent.c.prepare_boundary(receipt['id'])
    with bind(parent.c), _workspace(parent.root):
        assert await backend.checked_task_boundary(parent.c)
    state = parent.c.journal.state
    assert state['parent_context'] is None and state['parent_context_superseded'] is False
    assert state['managed']['generation'] == token['generation']
    last = json.loads((parent.c.journal.path / 'ledger.jsonl').read_text().splitlines()[-1])
    assert last['event'] == 'boundary_acknowledged'


@pytest.mark.parametrize('window', ['capture', 'before_settle', 'after_settle'])
def test_owned_backend_process_exit_preserves_parent_round_without_replay(parent, window):
    import os
    from pathlib import Path
    import subprocess
    import sys
    journal = parent.c.journal
    run_id = journal.run_id
    journal.close()
    script = '''
import asyncio,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from dream.core import parent_continuation as pc
from dream.core.run_state import RunState
from dream.core.task_checkpoints import ManagedTaskCoordinator,bind
from dream.memory.store import MemoryStore
from dream.memory.tasks import TaskStore
from test_local_subagents import _backend,_sub_final,_lead_final
from test_worker_contracts import _Engine,_workspace,_spec
from test_parent_continuation_http import task_round
root,run,window=Path(sys.argv[1]),sys.argv[2],sys.argv[3]
store=MemoryStore(root/'tasks.db');journal=RunState(root/'runs',run)
c=ManagedTaskCoordinator(journal,TaskStore(store));c.attach()
journal.record('owned_worker_started',phase='worker_running',uncertain=True)
b=_backend([],_spec(name='worker'))
fake=b._client=_Engine(_sub_final('original child return'),lead=[task_round('exact original task'),_lead_final('must not run')])
def stop():
 (root/'owned-process-witness.json').write_text(json.dumps({'worker_posts':len(fake.posted),'lead_streamed':fake.streamed}))
 os._exit(23)
if window=='capture':
 original=pc.capture
 def capturing(*a,**kw):
  result=original(*a,**kw);stop()
 pc.capture=capturing
elif window=='before_settle':
 def settling(*a,**kw):stop()
 pc.settle=settling
else:
 original=b._settle_parent_round
 async def settling(*a,**kw):
  await original(*a,**kw);stop()
 b._settle_parent_round=settling
async def main():
 with bind(c),_workspace(root):
  async for e in b.ask('original goal'):pass
asyncio.run(main())
'''
    completed = subprocess.run([sys.executable, '-c', script, str(parent.root), run_id, window],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
    assert completed.returncode == 23, completed.stderr
    witness = json.loads((parent.root / 'owned-process-witness.json').read_text())
    assert witness == {'worker_posts': 0 if window == 'capture' else 1, 'lead_streamed': 1}
    reopened = RunState(journal.path.parent, run_id)
    parent.journals.append(reopened)
    reference = reopened.state.get('parent_context')
    assert reopened.state['uncertain']
    if window == 'capture':
        assert reference is None  # orphan publication preceded reservation; no handler ran
    elif window == 'before_settle':
        assert reference['status'] == 'captured'
        with pytest.raises(ValueError):
            pc.load(parent.root, reference, execution_binding=_backend([], _spec(name='worker'))._parent_context_binding())
    else:
        assert reference['status'] == 'settled'
        restored = pc.load(parent.root, reference, execution_binding=_backend([], _spec(name='worker'))._parent_context_binding())
        assert restored[-1]['content'] == 'original child return'
        assert json.loads(restored[-2]['tool_calls'][0]['function']['arguments'])['prompt'] == 'exact original task'
