"""Actual HTTP adapter, scripted SSE only: no socket, model or provider starts."""
import json

import anyio
import httpx
import pytest

from dream import config
from dream.core.backends import openai_compat
from test_stream_repetition import (
    DONE, LEAD, TRIP, WORKER, _Rig, _call, _chunk, _make_local,
    _outcome, _text_reply, _worker_payload,
)


REFRAIN = 'I will now produce the complete verified result and save the requested artifact.'


@pytest.fixture(autouse=True)
def guard_configuration(monkeypatch):
    # Missing activation is a behavioral failure, not a collection/setup error.
    monkeypatch.setattr(config, 'TEXT_REPETITION_GUARD', True, raising=False)
    monkeypatch.setattr(openai_compat, '_RETRY_BACKOFF_S', (0.0, 0.0, 0.0))


def local_guard(rig, monkeypatch):
    # Exercise the local policy without any endpoint or health request.
    monkeypatch.setattr(rig.backend, '_cache_sensitive', lambda: True)


def repeating(channel, *, prior=(), ending=(TRIP,)):
    return [_chunk(role='assistant'), *prior,
            *[_chunk(**{channel: REFRAIN + '\n'}) for _ in range(6)], *ending]


def assert_closed_once_without_dispatch(rig):
    assert not rig.client.overread, 'The guard must close before reading the TRIP line.'
    assert rig.client.closes == 1 and rig.client.open_streams == 0
    assert len(rig.client.payloads) == 1 and rig.client.posted == []
    assert rig.ran == [], 'Neither collected structured calls nor recovered XML calls may run.'
    assert not [m for m in rig.backend.messages if m.get('tool_calls') or m.get('role') == 'tool']


@pytest.mark.parametrize('run,names', [('ask', LEAD), ('work', WORKER)])
@pytest.mark.parametrize('channel', ['content', 'reasoning_content'])
@pytest.mark.parametrize('prior_kind', ['structured', 'xml'])
async def test_text_cut_closes_before_overread_and_rejects_all_prior_tools(run,names,channel,prior_kind,monkeypatch):
    if prior_kind == 'structured':
        prior = [_chunk(_call(0,names[0],'{"query":"generic fixture"}','saved-call'))]
    else:
        xml = f'<invoke name="{names[0]}"><parameter name="query">generic fixture</parameter></invoke>\n'
        prior = [_chunk(content=xml)]
    first = repeating(channel,prior=prior)
    rig = _Rig(first,_text_reply('A retry would falsely hide the cut.'))
    local_guard(rig,monkeypatch)
    how,value = await _outcome(getattr(rig,run)())
    assert_closed_once_without_dispatch(rig)
    assert rig.client.lines_read == len(first)-1
    assert how == 'returned'
    if run == 'ask':
        assert not [e for e in rig.events if e.kind == 'assistant_done']
        results = [e.data for e in rig.events if e.kind == 'result']
        errors = [str(e.data) for e in rig.events if e.kind == 'error']
        assert len(results) == len(errors) == 1 and results[0]['is_error'] is True
        assert results[0]['subtype'] == 'request_failed'
        assert results[0]['failure']['code'] == 'stream_text_repetition'
        assert REFRAIN not in errors[0], 'Failure diagnostics must not duplicate private reply text.'
    else:
        assert isinstance(value,tuple) and value[1] is True
        assert REFRAIN not in value[0]


@pytest.mark.parametrize('run', ['ask', 'work'])
@pytest.mark.parametrize('disabled_by', ['config', 'remote'])
async def test_explicit_off_or_remote_endpoint_preserves_finite_repetition(run,disabled_by,monkeypatch):
    first = repeating('content',ending=(_chunk(finish='stop'),DONE))
    rig = _Rig(first)
    if disabled_by == 'config':
        local_guard(rig,monkeypatch)
        monkeypatch.setattr(config,'TEXT_REPETITION_GUARD',False,raising=False)
    else:
        rig.backend.provider.key = 'generic_remote'
        rig.backend.provider.base_url = 'https://example.invalid/v1'
        assert rig.backend._cache_sensitive() is False
    how,value = await _outcome(getattr(rig,run)())
    assert how == 'returned' and not rig.client.overread
    assert rig.client.closes == 1 and len(rig.client.payloads) == 1
    if run == 'ask':
        assert [e for e in rig.events if e.kind == 'assistant_done']
        assert not [e for e in rig.events if e.kind == 'error']
    else:
        assert value[1] is False and REFRAIN in value[0]


@pytest.mark.parametrize('run', ['ask', 'work'])
@pytest.mark.parametrize('channel', ['content', 'reasoning_content'])
async def test_sixth_line_in_server_finished_chunk_is_not_a_cut(run,channel,monkeypatch):
    first = [_chunk(**{channel:REFRAIN+'\n'}) for _ in range(5)]
    first += [_chunk(finish='stop',**{channel:REFRAIN+'\n','content':
        REFRAIN+'\n' if channel=='content' else 'Completed finite reply.'}),DONE]
    rig = _Rig(first)
    local_guard(rig,monkeypatch)
    how,value = await _outcome(getattr(rig,run)())
    assert how == 'returned' and rig.client.lines_read == len(first)
    assert len(rig.client.payloads) == rig.client.closes == 1
    if run == 'ask':
        assert [e for e in rig.events if e.kind == 'assistant_done']
        assert not [e for e in rig.events if e.kind == 'error']
    else:
        assert value[1] is False


@pytest.mark.parametrize('run,names', [('ask', LEAD), ('work', WORKER)])
async def test_repetition_inside_one_tool_argument_is_not_prose(run,names,monkeypatch):
    argument = json.dumps({'query':(REFRAIN+'\n')*20})
    first = [_chunk(_call(0,names[0],argument,'only-call')),
             _chunk(finish='tool_calls'),DONE]
    rig = _Rig(first,_text_reply('Completed after the actual call.'))
    local_guard(rig,monkeypatch)
    how,value = await _outcome(getattr(rig,run)())
    assert how == 'returned' and len(rig.ran) == 1
    assert rig.ran[0] == (names[0],{'query':(REFRAIN+'\n')*20},0)
    assert len(rig.client.payloads) == rig.client.closes == 2 and not rig.client.posted
    if run == 'ask':
        assert not [e for e in rig.events if e.kind == 'error']
    else:
        assert value[1] is False


@pytest.mark.parametrize('run,names', [('ask', LEAD), ('work', WORKER)])
async def test_raw_xml_multiline_tool_argument_repetition_executes_once_without_text_cut(run,names,monkeypatch):
    argument = '\n'+(REFRAIN+'\n')*8
    xml = (f'<tool_calls>\n<invoke name="{names[0]}">\n'
           f'<parameter name="query" string="true">{argument}</parameter>\n</invoke>\n</tool_calls>\n')
    first = [_chunk(content=line) for line in xml.splitlines(keepends=True)]
    first += [_chunk(finish='stop'),DONE]
    rig = _Rig(first,_text_reply('Completed after the actual XML call.'))
    local_guard(rig,monkeypatch)
    how,value = await _outcome(getattr(rig,run)())
    assert how == 'returned' and len(rig.ran) == 1
    assert rig.ran[0] == (names[0],{'query':argument},0)
    assert len(rig.client.payloads) == rig.client.closes == 2 and not rig.client.posted
    if run == 'ask':
        assert not [e for e in rig.events if e.kind == 'error']
    else:
        assert value[1] is False


@pytest.mark.parametrize('run,names', [('ask', LEAD), ('work', WORKER)])
@pytest.mark.parametrize('fragment_size', [13,None])
@pytest.mark.parametrize('framing', ['oversized_open','oversized_close','newline_name',
                                    'late_open','bisected_open','split_close'])
async def test_valid_xml_argument_framing_survives_large_lines_and_split_opener(run,names,fragment_size,framing,monkeypatch):
    argument = '\n'+(REFRAIN+'\n')*8
    opening = f'<invoke name="{names[0]}"><parameter name="query" string="true">'
    if framing=='oversized_open':
        opening = '<invoke'+' '*5000+f'name="{names[0]}"><parameter name="query" string="true">'
        argument = 'x'*5000+argument
    elif framing=='oversized_close':
        argument += 'x'*5000
    elif framing=='newline_name':
        opening = f'<invoke\nname="{names[0]}"><parameter name="query" string="true">'
    elif framing in {'late_open','bisected_open'}:
        opening = 'x'*(4100 if framing=='late_open' else 4093)+opening
    closing = '</parameter></invoke\n>\n' if framing=='split_close' else '</parameter></invoke>\n'
    xml = opening+argument+closing
    width = fragment_size or len(xml)
    first = [_chunk(content=xml[start:start+width]) for start in range(0,len(xml),width)]
    first += [_chunk(finish='stop'),DONE]
    rig = _Rig(first,_text_reply('Completed after exactly one actual XML tool call.'))
    local_guard(rig,monkeypatch)
    how,value = await _outcome(getattr(rig,run)())
    assert how == 'returned' and len(rig.ran)==1
    assert rig.ran[0] == (names[0],{'query':argument},0)
    assert len(rig.client.payloads)==rig.client.closes==2 and not rig.client.posted
    if run=='ask':
        assert not [e for e in rig.events if e.kind=='error']
    else:
        assert value[1] is False


@pytest.mark.parametrize('run,names', [('ask', LEAD), ('work', WORKER)])
async def test_prose_after_oversized_xml_close_still_cuts_without_running_collected_xml(run,names,monkeypatch):
    xml = (f'<invoke name="{names[0]}"><parameter name="query" string="true">\n'
           +'x'*5000+'</parameter></invoke>\n')
    prior = [_chunk(content=xml[start:start+13]) for start in range(0,len(xml),13)]
    rig = _Rig(repeating('content',prior=prior),_text_reply('No retry is authorized.'))
    local_guard(rig,monkeypatch)
    how,value = await _outcome(getattr(rig,run)())
    assert_closed_once_without_dispatch(rig)
    assert how=='returned'
    if run=='ask':
        results=[e.data for e in rig.events if e.kind=='result']
        assert len(results)==1 and results[0]['failure']['code']=='stream_text_repetition'
    else:
        assert value[1] is True


@pytest.mark.parametrize('run,names',[('ask',LEAD),('work',WORKER)])
@pytest.mark.parametrize('fragment_size',[13,None])
@pytest.mark.parametrize('literal',['</invoke\nnot a closing bracket\n',
                                    '</invoke =notclose\n',
                                    '</invoke'+' '*5000+'\nnotclose\n'],
                         ids=['newline-literal','invalid-attribute','oversized-incomplete-close'])
async def test_xml_argument_literal_closing_tag_does_not_end_exclusion(run,names,fragment_size,literal,monkeypatch):
    argument='\nliteral '+literal+(REFRAIN+'\n')*8
    xml=(f'<invoke name="{names[0]}"><parameter name="query" string="true">'
         +argument+'</parameter>\n</invoke\n'+' '*5000+'\n>\n')
    width=fragment_size or len(xml)
    first=[_chunk(content=xml[start:start+width]) for start in range(0,len(xml),width)]
    first += [_chunk(finish='stop'),DONE]
    rig=_Rig(first,_text_reply('Completed after the valid XML call.'))
    local_guard(rig,monkeypatch)
    how,value=await _outcome(getattr(rig,run)())
    assert how=='returned' and len(rig.ran)==1
    assert rig.ran[0]==(names[0],{'query':argument},0)
    assert len(rig.client.payloads)==rig.client.closes==2 and not rig.client.posted
    if run=='ask':
        assert not [e for e in rig.events if e.kind=='error']
    else:
        assert value[1] is False


@pytest.mark.parametrize('run',['ask','work'])
@pytest.mark.parametrize('marker',['```','~~~'])
@pytest.mark.parametrize('fragment_size',[13,None])
async def test_finite_code_with_xml_in_fence_info_does_not_cut(run,marker,fragment_size,monkeypatch):
    text=marker+'xml title="<invoke name=example>"\n</invoke>\n'+(REFRAIN+'\n')*8+marker+'\n'
    width=fragment_size or len(text)
    first=[_chunk(content=text[start:start+width]) for start in range(0,len(text),width)]
    first += [_chunk(finish='stop'),DONE]
    rig=_Rig(first)
    local_guard(rig,monkeypatch)
    how,value=await _outcome(getattr(rig,run)())
    assert how=='returned' and rig.ran==[]
    assert rig.client.lines_read==len(first) and len(rig.client.payloads)==rig.client.closes==1
    if run=='ask':
        assert [e for e in rig.events if e.kind=='assistant_done']
        assert not [e for e in rig.events if e.kind=='error']
    else:
        assert value==(text.strip(),False)


@pytest.mark.parametrize('run,names',[('ask',LEAD),('work',WORKER)])
@pytest.mark.parametrize('marker',['```','~~~'])
@pytest.mark.parametrize('outer',['fence','xml_argument'])
async def test_prose_after_actual_outer_frame_closes_is_cut_at_the_prose_boundary(run,names,marker,outer,monkeypatch):
    if outer=='fence':
        text=marker+'xml title="<invoke name=example>"\n</invoke>\n'+(REFRAIN+'\n')*8+marker+'\n'
    else:
        text=(f'<invoke name="{names[0]}"><parameter name="query" string="true">\n'
              +marker+'text\n'+(REFRAIN+'\n')*8+'</parameter>\n</invoke>\n')
    prior=[_chunk(content=text[start:start+13]) for start in range(0,len(text),13)]
    first=repeating('content',prior=prior)
    rig=_Rig(first,_text_reply('No retry is authorized.'))
    local_guard(rig,monkeypatch)
    how,value=await _outcome(getattr(rig,run)())
    assert_closed_once_without_dispatch(rig)
    assert rig.client.lines_read==len(first)-1, 'The cut must follow the actual frame, never occur inside it.'
    assert how=='returned'
    if run=='ask':
        result=[e.data for e in rig.events if e.kind=='result']
        assert len(result)==1 and result[0]['failure']['code']=='stream_text_repetition'
    else:
        assert value[1] is True


@pytest.mark.parametrize('run', ['ask','work'])
async def test_content_and_reasoning_do_not_share_a_threshold(run,monkeypatch):
    first = [_chunk(content=REFRAIN+'\n',reasoning_content=REFRAIN+'\n') for _ in range(3)]
    first += [_chunk(content='A finite conclusion.',finish='stop'),DONE]
    rig = _Rig(first)
    local_guard(rig,monkeypatch)
    how,value = await _outcome(getattr(rig,run)())
    assert how == 'returned' and len(rig.client.payloads) == 1
    if run == 'ask':
        assert not [e for e in rig.events if e.kind == 'error']
    else:
        assert value[1] is False


async def test_local_text_cut_does_not_fabricate_completion_or_release_lease(tmp_path,monkeypatch):
    rig = _Rig(repeating('reasoning_content'))
    fences = _make_local(rig,tmp_path,monkeypatch)
    how,value = await _outcome(rig.backend._subagent_stream(_worker_payload(),1))
    assert_closed_once_without_dispatch(rig)
    assert how == 'raised' and isinstance(value,openai_compat._RequestFailed)
    assert value.code == 'stream_text_repetition'
    assert len(fences) == 1 and fences[0].complete is False and fences[0].finish_reason is None
    assert rig.backend.coordination_status()['state'] == 'uncertain'


@pytest.mark.parametrize('run', ['ask','work','direct_worker'])
async def test_stop_during_close_takes_precedence_over_text_cut(run,monkeypatch):
    rig = _Rig(repeating('content'),_text_reply('Stop must not continue.'))
    local_guard(rig,monkeypatch)
    rig.client.during_close = rig.backend.interrupt
    operation = rig.backend._subagent_stream(_worker_payload(),1) if run=='direct_worker' else getattr(rig,run)()
    how,value = await _outcome(operation)
    assert_closed_once_without_dispatch(rig)
    if run == 'direct_worker':
        assert how == 'cancelled'
    elif run == 'ask':
        assert not [e for e in rig.events if e.kind == 'assistant_done']
    else:
        assert how == 'cancelled' or (how == 'returned' and value[1] is True)


@pytest.mark.parametrize('error_type', [RuntimeError,httpx.ReadError])
@pytest.mark.parametrize('run', ['ask','direct_worker'])
async def test_cleanup_failure_is_not_replaced_by_text_cut(run,error_type,monkeypatch):
    rig = _Rig(repeating('content'),_text_reply('Cleanup failure must not retry.'))
    local_guard(rig,monkeypatch)
    def failed_close():raise error_type('synthetic close failed')
    rig.client.during_close = failed_close
    operation = rig.backend._subagent_stream(_worker_payload(),1) if run=='direct_worker' else rig.ask()
    how,value = await _outcome(operation)
    assert_closed_once_without_dispatch(rig)
    if run == 'direct_worker':
        assert how == 'raised' and isinstance(value,error_type) and str(value)=='synthetic close failed'
    elif how == 'raised':
        assert isinstance(value,error_type) and str(value)=='synthetic close failed'
    else:
        errors = [str(e.data) for e in rig.events if e.kind=='error']
        assert len(errors)==1 and 'synthetic close failed' in errors[0]
        assert not [e for e in rig.events if e.kind=='assistant_done']


@pytest.mark.parametrize('pending', ['cancel','deadline'])
async def test_cancelled_or_expired_cleanup_cannot_return_a_text_cut(pending,monkeypatch):
    rig = _Rig(repeating('content'))
    local_guard(rig,monkeypatch)
    outcome = None
    with anyio.CancelScope() as scope:
        def end_scope():
            if pending=='cancel':scope.cancel()
            else:scope.deadline=anyio.current_time()-1
        rig.client.during_close=end_scope
        outcome=await _outcome(rig.backend._subagent_stream(_worker_payload(),1))
    assert_closed_once_without_dispatch(rig)
    if outcome is not None:
        assert outcome[0]=='cancelled' or (outcome[0]=='raised' and isinstance(outcome[1],TimeoutError))
