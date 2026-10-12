"""Actual HTTP reset hook: durable originals, checked receipts and interruption."""
import copy
import json
from types import SimpleNamespace
import pytest
from dream.core.backends import openai_compat
from dream.core.tool_artifacts import ArtifactError, ArtifactStore
from dream.core.task_checkpoints import bind
from dream.tools.context import bind_context
from test_local_subagents import _backend
from test_task_checkpoints import managed, verified

@pytest.fixture
def boundary(managed,tmp_path):
    coordinator,key=managed
    b=_backend([],None)
    b.messages=[{'role':'system','content':'full standing head'},
                {'role':'user','content':'original request'},
                {'role':'assistant','tool_calls':[{'id':'call','function':{'name':'fixture','arguments':'{}'}}]},
                {'role':'tool','tool_call_id':'call','content':'original result 界\x00'}]
    context=SimpleNamespace(workspace=tmp_path,session_id='checked-session')
    return b,coordinator,key,context

async def test_checked_boundary_archives_exact_history_and_is_idempotent(boundary):
    b,c,key,context=boundary
    receipt=verified(c,key);c.complete_task(receipt['id'])
    original=copy.deepcopy(b.messages)
    with bind(c),bind_context(context):
        assert await b.checked_task_boundary(c)
        archive=c.journal.state['context_archive']
        saved=ArtifactStore(context.workspace,context.session_id,c.journal.run_id).read(archive['handle'],limit=32768)
        assert json.loads(saved['text'])==original
        assert b.messages[0]==original[0] and len(b.messages)==2
        assert 'full goal' in b.messages[1]['content'] and '- evidence is correct' in b.messages[1]['content']
        assert c._state['boundary']['status']=='acknowledged'
        b.messages.append({'role':'user','content':'new owner input after reset'})
        again=copy.deepcopy(b.messages)
        assert await b.checked_task_boundary(c)
        assert b.messages==again

@pytest.mark.parametrize('busy',['_turn_active','workers','request'])
async def test_busy_or_unchecked_context_never_reset(boundary,busy):
    b,c,key,context=boundary;original=copy.deepcopy(b.messages)
    with bind(c),bind_context(context):
        with pytest.raises(ValueError):await b.checked_task_boundary(c)
        receipt=verified(c,key);c.complete_task(receipt['id'])
        if busy=='_turn_active':b._turn_active=True
        elif busy=='workers':b._workers['running']=openai_compat._Worker({'run_id':'running','agent':'fixture'},openai_compat.CancelScope())
        else:b._interrupts.active.add(object())
        with pytest.raises(ValueError):await b.checked_task_boundary(c)
    assert b.messages==original

async def test_storage_failure_after_check_leaves_original_and_prepared_boundary(boundary,monkeypatch):
    b,c,key,context=boundary;receipt=verified(c,key);c.complete_task(receipt['id'])
    original=copy.deepcopy(b.messages)
    def fail(*args,**kwargs):raise ArtifactError('disk_limit')
    monkeypatch.setattr(ArtifactStore,'save',fail)
    with bind(c),bind_context(context):
        with pytest.raises(ArtifactError):await b.checked_task_boundary(c)
    assert b.messages==original and c._state['boundary']['status']=='prepared'
    assert c.task['status']=='done' and c.journal.state.get('context_archive') is None

async def test_stop_during_archive_cannot_acknowledge_or_drop_history(boundary,monkeypatch):
    b,c,key,context=boundary;receipt=verified(c,key);c.complete_task(receipt['id'])
    original=copy.deepcopy(b.messages)
    from dream.tools import context as tool_context
    real=tool_context.in_thread
    async def interrupted(fn,*args,**kwargs):
        result=await real(fn,*args,**kwargs)
        b._interrupts.generation+=1
        return result
    monkeypatch.setattr(tool_context,'in_thread',interrupted)
    with bind(c),bind_context(context):
        with pytest.raises(BaseException):await b.checked_task_boundary(c)
    assert b.messages==original and c._state['boundary']['status']=='prepared'

async def test_ack_failure_restores_originals_and_retries_without_replay(boundary,monkeypatch):
    b,c,key,context=boundary;receipt=verified(c,key);c.complete_task(receipt['id'])
    original=copy.deepcopy(b.messages);real=c.acknowledge_boundary
    def fail(*args,**kwargs):raise OSError('ack unavailable')
    with bind(c),bind_context(context):
        with monkeypatch.context() as patch:
            patch.setattr(c,'acknowledge_boundary',fail)
            with pytest.raises(OSError):await b.checked_task_boundary(c)
        assert b.messages==original
        assert await b.checked_task_boundary(c)
    assert c._state['boundary']['status']=='acknowledged'


def test_managed_model_markers_and_pressure_cannot_edit_middle(boundary,monkeypatch):
    b,c,key,context=boundary
    original=copy.deepcopy(b.messages)
    b._phase_reset_pending=True
    b._snips=[('first','last','model claimed complete')]
    monkeypatch.setattr(b,'_fill_bound',lambda:10**9)
    with bind(c):
        assert b._execute_snips()==0
        assert b._phase_reset()
        assert b._maybe_compact()==[]
    assert b.messages==original

async def test_contract_admission_also_blocks_builtin_delegation(boundary,monkeypatch):
    b,c,key,context=boundary
    b._subagents={'fixture':object()}
    async def forbidden(*args,**kwargs):pytest.fail('contract must not launch a worker')
    monkeypatch.setattr(b,'_run_subagent',forbidden)
    with bind(None,allow_tools=False),bind_context(context):
        text,failed=await b._exec_tool('task',{'subagent_type':'fixture','prompt':'do work'})
    assert failed and 'contract' in text.lower()

async def test_actual_lead_tool_recovers_archive_after_coordinator_binding_ends(boundary):
    from dream.tools.artifact_tools import artifact_read
    b,c,key,context=boundary;receipt=verified(c,key);c.complete_task(receipt['id'])
    b.tools_by_name['artifact_read']=artifact_read
    with bind_context(context):
        with bind(c):await b.checked_task_boundary(c)
        text,failed=await b._exec_tool('artifact_read',{'handle':c.journal.state['context_archive']['handle'],'query':'original result','limit':100})
    assert not failed and 'original result' in text

async def test_restart_after_archive_before_ack_preserves_original_and_real_retrieval(boundary,monkeypatch):
    from dream.tools.artifact_tools import artifact_read
    b,c,key,context=boundary;receipt=verified(c,key);c.complete_task(receipt['id'])
    original=copy.deepcopy(b.messages)
    def fail(*args,**kwargs):raise OSError('crash before acknowledgement')
    with bind(c),bind_context(context),monkeypatch.context() as patch:
        patch.setattr(c,'acknowledge_boundary',fail)
        with pytest.raises(OSError):await b.checked_task_boundary(c)
    archive=copy.deepcopy(c.journal.state['context_archive'])
    restarted=_backend([artifact_read],None)
    restarted.messages=[{'role':'system','content':'fresh session standing head'}]
    new_context=SimpleNamespace(workspace=context.workspace,session_id='fresh-session')
    with bind_context(new_context):
        with bind(c):assert await restarted.checked_task_boundary(c)
        assert c.journal.state['context_archive']==archive
        text,failed=await restarted._exec_tool('artifact_read',{'handle':archive['handle'],'query':'original result','limit':100})
    assert not failed and 'original result' in text
    assert restarted.messages[0]['content']=='fresh session standing head'
    stored=ArtifactStore(context.workspace,context.session_id,c.journal.run_id).read(archive['handle'],limit=32768)
    assert json.loads(stored['text'])==original

async def test_prepared_old_reset_refuses_new_unchecked_user_history(boundary,monkeypatch):
    b,c,key,context=boundary;receipt=verified(c,key);c.complete_task(receipt['id'])
    def fail(*args,**kwargs):raise OSError('ack failed')
    with bind(c),bind_context(context):
        with monkeypatch.context() as patch:
            patch.setattr(c,'acknowledge_boundary',fail)
            with pytest.raises(OSError):await b.checked_task_boundary(c)
        b.messages.append({'role':'user','content':'new unchecked owner work'})
        original=copy.deepcopy(b.messages)
        with pytest.raises(ValueError,match='unchecked'):await b.checked_task_boundary(c)
    assert b.messages==original
