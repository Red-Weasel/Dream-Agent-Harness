"""Run actual worker loops through durable context recovery without providers."""
import asyncio
import copy
import json
from types import SimpleNamespace
import pytest
from dream.core.backends import openai_compat
from dream.core.tool_artifacts import ArtifactStore, ArtifactError
from dream.tools import context
from dream.tools.artifact_tools import artifact_read
from test_local_subagents import _backend, _researcher, _tool, _PostResp, _sub_toolcall, _sub_final
from test_worker_delegations import parent

@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setattr('dream.core.instructions.worker_prompt', lambda *a, **k: 'standing rules: keep all evidence')
    return SimpleNamespace(workspace=tmp_path,session_id='survival-fixture')

def backend(monkeypatch, handler, tools=None):
    b=_backend(tools or [_tool('web_search',handler),artifact_read],_researcher()); b.n_ctx=32768
    # Simulate the server's context pressure after any completed call, using the
    # actual overflow/recovery/dispatch paths. Fresh capsules fit naturally.
    monkeypatch.setattr(b,'_sub_fill',lambda messages,*a,**k: 30000 if any(
        tc['function']['name'] != 'artifact_read' for m in messages for tc in m.get('tool_calls', [])) else 1000)
    return b

def archive_from(messages):
    text=next(m['content'] for m in messages if m.get('name')=='dream_worker_recovery')
    return json.loads(text.split('\n',1)[1].split('\n',1)[0])

async def test_recovers_full_original_and_rules_then_reads_same_run(scope,monkeypatch):
    calls=[]; seen=[]; archives=[]
    async def search(a): calls.append(a); return {'content':[{'type':'text','text':'observed fixture evidence'}]}
    b=backend(monkeypatch,search)
    class Transport:
        async def post(self,url,json=None):
            seen.append(copy.deepcopy(json))
            if len(seen)==1:return _PostResp(_sub_toolcall('web_search',{'q':'source'}))
            assert json['messages'][0]==seen[0]['messages'][0]
            assert json['messages'][1]==seen[0]['messages'][1]
            receipt=archive_from(json['messages']); archives.append(receipt)
            store=ArtifactStore(scope.workspace,scope.session_id,openai_compat._AGENT_ACTIVITY.get()['run_id'])
            saved=__import__('json').loads(store.read(receipt['handle'],limit=32768)['text'])
            assert saved['task']=='exact assigned task'
            assert saved['system']==seen[0]['messages'][0]
            assert any(m.get('content')=='observed fixture evidence' for m in saved['history']) or saved['previous_archives']
            if len(seen)==2:return _PostResp(_sub_toolcall('artifact_read',{'handle':receipt['handle'],'query':'observed fixture evidence','limit':120}))
            return _PostResp(_sub_final('finished from retrieved evidence'))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','exact assigned task')
    assert not failed and reply=='finished from retrieved evidence',reply
    assert len(calls)==1 and len(archives)==2

async def test_recovery_limit_returns_incomplete_without_another_request(scope,monkeypatch):
    calls=[]; posted=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':str(a)}]}
    b=backend(monkeypatch,search)
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            recovery=any(m.get('name')=='dream_worker_recovery' for m in json['messages'])
            if recovery and not any(m['role']=='tool' for m in json['messages']):
                return _PostResp(_sub_toolcall('artifact_read',{'handle':archive_from(json['messages'])['handle'],'limit':100}))
            return _PostResp(_sub_toolcall('web_search',{'q':str(len(posted))}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','exhaust budget')
    assert failed and 'recovery_limit' in reply and len(posted)==5 and len(calls)==3,reply
    assert 'artifact:' in reply

async def test_snapshot_storage_failure_never_replays_completed_tool(scope,monkeypatch):
    calls=[]; posted=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':'effect already observed'}]}
    b=backend(monkeypatch,search)
    def failure(*a,**k):raise ArtifactError('disk_limit')
    monkeypatch.setattr(ArtifactStore,'save',failure)
    class Transport:
        async def post(self,url,json=None):posted.append(json);return _PostResp(_sub_toolcall('web_search',{'q':'once'}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','storage failure')
    assert failed and len(calls)==len(posted)==1 and 'disk_limit' in reply,reply
    assert 'must not be replayed' in reply

async def test_stop_during_snapshot_prevents_reset_and_next_request(scope,monkeypatch):
    calls=[]; posted=[]; saved=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':'recorded once'}]}
    b=backend(monkeypatch,search)
    original=ArtifactStore.save
    def saving(store,*a,**kw):
        receipt=original(store,*a,**kw);saved.append(receipt)
        b._interrupts.generation+=1
        return receipt
    monkeypatch.setattr(ArtifactStore,'save',saving)
    class Transport:
        async def post(self,url,json=None):posted.append(copy.deepcopy(json));return _PostResp(_sub_toolcall('web_search',{'q':'once'}))
    b._client=Transport()
    with context.bind_context(scope):
        with pytest.raises(asyncio.CancelledError):await b._run_subagent('researcher','stop while saving')
    assert len(saved)==len(calls)==len(posted)==1

async def test_disabled_archive_reader_is_not_reenabled(scope,monkeypatch):
    calls=[];posted=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':'short result'}]}
    b=backend(monkeypatch,search)
    from dream import extensions
    actual=extensions.tool_enabled
    monkeypatch.setattr(extensions,'tool_enabled',lambda tool:False if tool.name=='artifact_read' else actual(tool))
    class Transport:
        async def post(self,url,json=None):posted.append(json);return _PostResp(_sub_toolcall('web_search',{'q':'once'}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','reader disabled')
    assert failed and 'recovery_unavailable' in reply and len(calls)==len(posted)==1,reply

@pytest.mark.parametrize('reply_kind',['final','tool'])
async def test_reset_cannot_claim_success_or_act_without_reading_archive(scope,monkeypatch,reply_kind):
    calls=[];posted=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':'observed once'}]}
    b=backend(monkeypatch,search)
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            if len(posted)==1:return _PostResp(_sub_toolcall('web_search',{'q':'once'}))
            return _PostResp(_sub_final('all done') if reply_kind=='final' else _sub_toolcall('web_search',{'q':'again'}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','retrieve first')
    assert failed and 'recovery_read_required' in reply and len(calls)==1 and len(posted)==2,reply
    assert 'tools_uncertain=0' in reply

async def test_deadline_expiring_during_save_does_not_start_fresh_request(scope,monkeypatch):
    posted=[];saved=[]
    async def search(a):return {'content':[{'type':'text','text':'observed once'}]}
    b=backend(monkeypatch,search);original=ArtifactStore.save
    def saving(store,*a,**kw):
        receipt=original(store,*a,**kw);saved.append(receipt)
        # Mutate in caller task below: AnyIO CancelScope requires an async task.
        return receipt
    from dream.tools import context as context_module
    original_thread=context_module.in_thread
    async def save_then_expire(fn,*a,**kw):
        result=await original_thread(fn,*a,**kw)
        worker=next(iter(b._workers.values()));worker.deadline.deadline=openai_compat.current_time()-1
        return result
    monkeypatch.setattr(context_module,'in_thread',save_then_expire)
    monkeypatch.setattr(ArtifactStore,'save',saving)
    class Transport:
        async def post(self,url,json=None):posted.append(json);return _PostResp(_sub_toolcall('web_search',{'q':'once'}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','expire while saving')
    assert failed and 'timed out' in reply and len(posted)==len(saved)==1,reply

@pytest.mark.parametrize('error',[False,True])
async def test_mutation_is_never_replayed_after_recovery_or_uncertain_failure(scope,monkeypatch,error):
    from dream.core.subagents import LocalSubagentSpec
    effects=[];posted=[]
    async def mutate(a):
        effects.append(a)
        return {'content':[{'type':'text','text':'effect attempted'}], 'is_error':error}
    b=backend(monkeypatch,mutate,tools=[_tool('write_file',mutate),artifact_read])
    b._subagents={'researcher':LocalSubagentSpec(name='researcher',description='fixture',prompt='fixture',tool_names=('write_file',))}
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            if len(posted)==2:
                return _PostResp(_sub_toolcall('artifact_read',{'handle':archive_from(json['messages'])['handle'],'limit':100}))
            return _PostResp(_sub_toolcall('Write_File' if len(posted)>2 else 'write_file',{'path':'output.txt','content':'same effect'}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','do effect once')
    assert failed and len(effects)==1,reply
    assert ('uncertain_effect' if error else 'completed_effect_replay_refused') in reply
    assert len(posted)==(1 if error else 3)
    if not error:assert 'tools_uncertain=0' in reply

async def test_round_budget_is_not_replenished_by_recovery(scope,monkeypatch):
    calls=[];posted=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':'observed'}]}
    b=backend(monkeypatch,search);monkeypatch.setattr(openai_compat,'_SUB_MAX_ROUNDS',2)
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            if len(posted)==1:return _PostResp(_sub_toolcall('web_search',{'q':'source'}))
            return _PostResp(_sub_toolcall('artifact_read',{'handle':archive_from(json['messages'])['handle'],'limit':100}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','two total rounds')
    assert failed and '2-round limit' in reply and len(posted)==2 and len(calls)==1,reply

async def test_repetition_history_survives_reset(scope,monkeypatch):
    calls=[];posted=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':'identical evidence'}]}
    b=backend(monkeypatch,search)
    # A single context pressure event, then let the actual repetition guard end it.
    monkeypatch.setattr(b,'_sub_fill',lambda messages,*a,**kw:30000 if any(m['role']=='tool' for m in messages)
        and not any(m.get('name')=='dream_worker_recovery' for m in messages) else 1000)
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            if len(posted)==2:return _PostResp(_sub_toolcall('artifact_read',{'handle':archive_from(json['messages'])['handle'],'limit':100}))
            return _PostResp(_sub_toolcall('web_search',{'q':'same'}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','do not replenish call budget')
    assert failed and 'loop guard' in reply and len(calls)==2 and len(posted)==5,reply

async def test_exact_original_survives_tail_fit(scope,monkeypatch):
    posted=[];archives=[]
    original='full original observation before tail fitting'
    async def search(a):return {'content':[{'type':'text','text':original}]}
    b=backend(monkeypatch,search)
    def fit(messages,*a,**kw):
        for message in messages:
            if message['role']=='tool':message['content']='[trimmed]'
        return True
    monkeypatch.setattr(b,'_fit_worker_observations',fit)
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            if len(posted)==1:return _PostResp(_sub_toolcall('web_search',{'q':'source'}))
            receipt=archive_from(json['messages']);archives.append(receipt)
            store=ArtifactStore(scope.workspace,scope.session_id,openai_compat._AGENT_ACTIVITY.get()['run_id'])
            saved=__import__('json').loads(store.read(receipt['handle'],limit=32768)['text'])
            assert any(m.get('content')==original for m in saved['history'])
            if len(posted)==2:return _PostResp(_sub_toolcall('artifact_read',{'handle':receipt['handle'],'limit':100}))
            return _PostResp(_sub_final('finished'))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','keep originals')
    assert not failed and reply=='finished',reply

async def test_real_context_pressure_recovers_without_mocking_admission(scope):
    posted=[];calls=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':'source result'}]}
    b=_backend([_tool('web_search',search),artifact_read],_researcher());b.n_ctx=12000
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            if len(posted)==1:return _PostResp(_sub_toolcall('web_search',{'q':'large tool argument '*2000}))
            if len(posted)==2:
                receipt=archive_from(json['messages'])
                return _PostResp(_sub_toolcall('artifact_read',{'handle':receipt['handle'],'query':'source result','limit':100}))
            assert 'source result' in next(m['content'] for m in reversed(json['messages']) if m['role']=='tool')
            return _PostResp(_sub_final('recovered actual overflowing request'))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','inspect actual request')
    assert not failed and reply=='recovered actual overflowing request' and len(calls)==1,reply

async def test_error_limit_is_carried_through_recovery_and_archive_reads(scope,monkeypatch):
    posted=[];calls=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':'same failure'}],'is_error':True}
    b=backend(monkeypatch,search)
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            if any(m.get('name')=='dream_worker_recovery' for m in json['messages']) and not any(m['role']=='tool' for m in json['messages']):
                return _PostResp(_sub_toolcall('artifact_read',{'handle':archive_from(json['messages'])['handle'],'limit':100}))
            return _PostResp(_sub_toolcall('web_search',{'q':str(len(posted))}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','bounded failures')
    assert failed and 'error_limit' in reply and len(calls)==3 and len(posted)==5,reply

async def test_managed_unresolved_action_refuses_recovery(scope,parent,monkeypatch):
    from dream.core.task_checkpoints import bind
    action = parent.c.begin_action('write_file', {'path': 'uncertain'})
    parent.c.finish_action(action, 'unknown')
    posted=[]
    async def search(a):return {'content':[{'type':'text','text':'observed'}]}
    b=backend(monkeypatch,search)
    class Transport:
        async def post(self,url,json=None):posted.append(json);return _PostResp(_sub_toolcall('web_search',{'q':'once'}))
    b._client=Transport()
    with bind(parent.c), context.bind_context(scope):
        token = openai_compat._PARENT_TASK_CALL.set({'id': 'original-call', 'arguments': {
            'subagent_type': 'researcher', 'prompt': 'unresolved task'}})
        try:
            with pytest.raises(PermissionError, match='admission is closed'):
                await b._run_subagent('researcher','unresolved task')
        finally:
            openai_compat._PARENT_TASK_CALL.reset(token)
    assert not posted and not parent.c.actions_admitted

async def test_no_progress_budget_is_not_replenished_by_archive_read(scope,monkeypatch):
    posted=[];calls=[]
    async def search(a):calls.append(a);return {'content':[{'type':'text','text':'unchanged observed evidence'}]}
    b=backend(monkeypatch,search)
    monkeypatch.setattr(b,'_sub_fill',lambda messages,*a,**kw:30000 if any(m['role']=='tool' for m in messages)
        and not any(m.get('name')=='dream_worker_recovery' for m in messages) else 1000)
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            if len(posted)==2:return _PostResp(_sub_toolcall('artifact_read',{'handle':archive_from(json['messages'])['handle'],'limit':100}))
            return _PostResp(_sub_toolcall('web_search',{'q':str(len(posted))}))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','bounded repetitive research')
    assert failed and 'no_progress_limit' in reply and len(posted)==13 and len(calls)==12,reply

async def test_managed_action_starting_during_save_blocks_reset(scope,parent,monkeypatch):
    from dream.core.task_checkpoints import bind
    coordinator=parent.c
    posted=[];saved=[];original=ArtifactStore.save
    async def search(a):return {'content':[{'type':'text','text':'observed'}]}
    b=backend(monkeypatch,search)
    def saving(store,*a,**kw):
        receipt=original(store,*a,**kw)
        if len(a) > 1 and a[1].startswith('worker-recovery-'):
            saved.append(receipt)
            action = coordinator.begin_action('run_bash', {'command': 'uncertain-effect'})
            coordinator.finish_action(action, 'unknown')
        return receipt
    monkeypatch.setattr(ArtifactStore,'save',saving)
    class Transport:
        async def post(self,url,json=None):posted.append(json);return _PostResp(_sub_toolcall('web_search',{'q':'once'}))
    b._client=Transport()
    with bind(parent.c), context.bind_context(scope):
        token = openai_compat._PARENT_TASK_CALL.set({'id': 'original-call', 'arguments': {
            'subagent_type': 'researcher', 'prompt': 'concurrent task'}})
        try:
            reply,failed=await b._run_subagent('researcher','concurrent task')
        finally:
            openai_compat._PARENT_TASK_CALL.reset(token)
    assert failed and 'unresolved_managed_action' in reply and len(posted)==len(saved)==1,reply
    assert saved[0].handle in reply

async def test_empty_archive_search_does_not_authorize_completion(scope,monkeypatch):
    posted=[]
    async def search(a):return {'content':[{'type':'text','text':'observed'}]}
    b=backend(monkeypatch,search)
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json))
            if len(posted)==1:return _PostResp(_sub_toolcall('web_search',{'q':'source'}))
            if len(posted)==2:return _PostResp(_sub_toolcall('artifact_read',{'handle':archive_from(json['messages'])['handle'],'query':'NEVER_APPEARS_IN_THE_ARCHIVE','limit':100}))
            return _PostResp(_sub_final('all done without reading'))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','read actual data')
    assert failed and 'recovery_read_required' in reply and len(posted)==3,reply

@pytest.mark.parametrize('command,new_source,expected',[
    ('pytest -q','new source',True),
    ('pytest -q','old source',False),
    ('echo create-external-effect','new source',False),
])
async def test_recovered_checks_require_actual_source_change_and_fixed_test_runner(scope,monkeypatch,command,new_source,expected):
    from dream.core.subagents import LocalSubagentSpec
    source=scope.workspace/'app.py';source.write_text('old source')
    effects=[];posted=[]
    async def check(args):
        effects.append(('shell',source.read_text()))
        return {'content':[{'type':'text','text':'checked '+source.read_text()}]}
    async def write(args):
        source.write_text(args['content'])
        effects.append(('write',args['content']))
        return {'content':[{'type':'text','text':'saved source'}]}
    b=backend(monkeypatch,check,tools=[_tool('run_bash',check),_tool('write_file',write),artifact_read])
    b._subagents={'researcher':LocalSubagentSpec(name='researcher',description='fixture',prompt='fixture',tool_names=('run_bash','write_file'))}
    monkeypatch.setattr(b,'_sub_fill',lambda messages,*a,**k:30000 if not any(
        m.get('name')=='dream_worker_recovery' for m in messages) and any(
        tc['function']['name']=='run_bash' for m in messages for tc in m.get('tool_calls',[])) else 1000)
    class Transport:
        async def post(self,url,json=None):
            posted.append(copy.deepcopy(json));number=len(posted)
            if number==1:return _PostResp(_sub_toolcall('run_bash',{'command':command}))
            if number==2:return _PostResp(_sub_toolcall('artifact_read',{'handle':archive_from(json['messages'])['handle'],'limit':100}))
            if number==3:return _PostResp(_sub_toolcall('write_file',{'path':'app.py','content':new_source}))
            if number==4:return _PostResp(_sub_toolcall('run_bash',{'command':command}))
            return _PostResp(_sub_final('checked changed source'))
    b._client=Transport()
    with context.bind_context(scope):reply,failed=await b._run_subagent('researcher','check edit check')
    assert failed is not expected,reply
    assert effects==[('shell','old source'),('write',new_source)]+([('shell',new_source)] if expected else [])
    if not expected:assert 'completed_effect_replay_refused' in reply
