"""Subagent visibility from actual CPU fixture requests and tool results."""
import asyncio
import json
from types import SimpleNamespace
import pytest
from dream.core.backends.base import Event
from dream.gui.conversation import Conversation
from test_local_subagents import _backend, _tool, _researcher, _FakeClient, _sub_toolcall, _sub_final


def worker(events, replies, handler=None):
    async def default(args):return {'content':[{'type':'text','text':'source observed'}]}
    backend=_backend([_tool('web_search',handler or default)],_researcher())
    backend._background_emit=events.append
    backend._client=_FakeClient(post_scripts=replies)
    return backend


def activities(events):return [e.data for e in events if e.kind=='agent_activity']


async def test_actual_request_reasoning_and_tool_pair_are_attributed_without_duplicate_answer():
    events=[]
    response=_sub_toolcall('web_search',{'query':'observations'})
    response['choices'][0]['message']['reasoning_content']='I will inspect the reported source.'
    response['usage']={'prompt_tokens':11,'completion_tokens':4}
    backend=worker(events,[response,_sub_final('FINAL ANSWER MUST STAY WITH LEAD')])
    answer,failed=await backend._run_subagent('researcher','inspect')
    rows=activities(events)
    assert rows, 'Actual subagent activity is invisible'
    assert [r['kind'] for r in rows]==['status','request','response','thinking_report','tool_use','tool_result','request','response','status']
    assert len({r['run_id'] for r in rows})==1 and all(r['agent']=='researcher' for r in rows)
    thinking=next(r for r in rows if r['kind']=='thinking_report')
    assert thinking['text']=='I will inspect the reported source.' and thinking['reported_after_response'] is True
    use=next(r for r in rows if r['kind']=='tool_use');result=next(r for r in rows if r['kind']=='tool_result')
    assert use['data']['id']==result['data']['id']
    assert use['data']['input']=={'query':'observations'}
    assert result['data']['content']=='source observed' and not result['data']['is_error']
    assert rows[-1]['status']=='completed' and not failed
    assert answer=='FINAL ANSWER MUST STAY WITH LEAD' and answer not in json.dumps(rows)
    assert backend._delegated_usage=={'prompt_tokens':11,'completion_tokens':4}
    assert all(e.kind=='agent_activity' for e in events)


async def test_request_waiting_is_visible_before_nonstream_response_arrives():
    events=[];entered=asyncio.Event();release=asyncio.Event()
    backend=worker(events,[])
    async def post(payload):
        entered.set();await release.wait()
        return SimpleNamespace(json=lambda:_sub_final('done'))
    backend._post_with_retry=post
    pending=asyncio.create_task(backend._run_subagent('researcher','inspect'))
    await entered.wait()
    before=activities(events)
    release.set();await pending
    assert [r['kind'] for r in before]==['status','request']
    assert before[-1]['status']=='awaiting_response'


async def test_concurrent_same_role_workers_have_distinct_runs_and_tool_ids():
    events=[]
    async def handler(args):
        await asyncio.sleep(0);return {'content':[{'type':'text','text':args['query']}]}
    backend=worker(events,[],handler)
    counts={}
    async def post(payload):
        name=payload['messages'][1]['content'];count=counts.get(name,0);counts[name]=count+1
        await asyncio.sleep(0)
        return SimpleNamespace(json=lambda:_sub_toolcall('web_search',{'query':name}) if count==0 else _sub_final(name))
    backend._post_with_retry=post
    await asyncio.gather(backend._run_subagent('researcher','one'),backend._run_subagent('researcher','two'))
    rows=activities(events)
    assert len({r['run_id'] for r in rows})==2
    uses=[r for r in rows if r['kind']=='tool_use']
    assert len({r['data']['id'] for r in uses})==2
    for use in uses:
        result=next(r for r in rows if r['kind']=='tool_result' and r['data']['id']==use['data']['id'])
        assert result['run_id']==use['run_id'] and result['data']['content']==use['data']['input']['query']


@pytest.mark.parametrize('reply,status',[
    ({'choices':[{'message':{'content':'partial'},'finish_reason':'length'}]},'failed'),
    (_sub_final(''),'unknown'),
    (_sub_final('done'),'completed'),
])
async def test_terminal_status_distinguishes_actual_outcomes(reply,status):
    events=[];backend=worker(events,[reply]);await backend._run_subagent('researcher','inspect')
    assert activities(events)[-1]['status']==status


async def test_interruption_and_callback_failure_preserve_runtime_behavior():
    events=[];backend=worker(events,[])
    async def cancel(payload):raise asyncio.CancelledError()
    backend._post_with_retry=cancel
    with pytest.raises(asyncio.CancelledError):await backend._run_subagent('researcher','inspect')
    assert activities(events)[-1]['status']=='interrupted'
    backend=worker([],[_sub_final('done')])
    def broken(event):raise RuntimeError('view disconnected')
    backend._background_emit=broken
    assert await backend._run_subagent('researcher','inspect')==('done',False)


def test_activity_history_is_bounded_and_drops_undisplayed_payloads():
    conversation=Conversation()
    conversation.append(Event('agent_activity',{'run_id':'run','agent':'researcher','phase':'subagent','kind':'tool_result',
        'data':{'id':'tool','name':'web_search','content':'words ' * 20000,'is_error':False,'hidden':'secret'},
        'hidden':'private extra','images':['data:image/png;base64,'+'A'*20000]}))
    rows=conversation.snapshot()['events']
    assert len(rows)==1, 'Worker activity is missing from reconnect history'
    data=rows[0]['data'];assert len(json.dumps(data))<25000
    assert 'hidden' not in data and 'images' not in data and 'hidden' not in data['data']
    conversation.append(Event('agent_activity',{'run_id':'run','agent':'researcher','kind':'tool_use','data':{
        'id':'tool','name':'inspect','input':{'image':'data:image/png;base64,'+'A'*20000,'query':'visible'}}}))
    retained=json.dumps(conversation.snapshot())
    assert 'base64' not in retained and 'A'*100 not in retained and 'visible' in retained


async def test_tool_failure_and_interruption_leave_honest_paired_observations():
    for failure in (RuntimeError('tool fixture failed'), asyncio.CancelledError()):
        events=[]
        async def handler(args):raise failure
        backend=worker(events,[_sub_toolcall('web_search',{}),_sub_final('done')],handler)
        try:await backend._run_subagent('researcher','inspect')
        except asyncio.CancelledError:pass
        rows=activities(events);use=next(r for r in rows if r['kind']=='tool_use')
        result=next(r for r in rows if r['kind']=='tool_result')
        assert result['data']['id']==use['data']['id'] and result['data']['is_error']
        if isinstance(failure,asyncio.CancelledError):assert rows[-1]['status']=='interrupted'


async def test_backend_error_does_not_claim_completed_and_no_reasoning_is_invented():
    events=[];backend=worker(events,[])
    async def fail(payload):raise RuntimeError('CPU fixture unavailable')
    backend._post_with_retry=fail
    _,failed=await backend._run_subagent('researcher','inspect')
    rows=activities(events)
    assert failed and rows[-1]['status']=='failed'
    assert not any(r['kind']=='thinking_report' for r in rows)


def test_activity_forwarding_does_not_change_lead_usage_or_make_tools():
    from dream.core.engine import Engine
    from dream.telemetry.meter import InferenceMeter
    emitted=[];meter=InferenceMeter();meter.turn_start()
    engine=SimpleNamespace(session_tokens=17,session_id='fixture',emit=emitted.append)
    event=Event('agent_activity',{'run_id':'one','agent':'verifier','kind':'request','status':'awaiting_response'})
    before=meter.snapshot()
    Engine._background_event(engine,event);meter.feed(event.kind,event.data)
    assert engine.session_tokens==17 and emitted==[event]
    after=meter.snapshot()
    before.pop('elapsed_s');after.pop('elapsed_s')
    assert after==before


def test_activity_numbers_always_serialize_as_strict_json():
    from dream.agent_activity import bounded_agent_activity
    data=bounded_agent_activity({'run_id':'run','agent':'worker','kind':'tool_use','data':{
        'id':'call','name':'tool','input':{'nan':float('nan'),'inf':float('inf'),'huge':10**5000,'finite':2.5}}})
    serialized=json.dumps(data,allow_nan=False)
    assert 'Non-finite' in serialized and 'Oversized' in serialized
    assert data['data']['input']['finite']==2.5
