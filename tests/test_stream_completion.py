"""Incomplete model responses cannot authorize actions or completion claims."""
import httpx
import pytest

from test_backend_resilience import _backend, _tool, _sse, _ScriptedClient, _text_round


def tool_chunk(reason=None):
    return _sse({'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'effect',
        'function': {'name': 'write_file', 'arguments': '{}'}}]}, 'finish_reason': reason}]})


def result(events):
    return next(e.data for e in events if e.kind == 'result')


@pytest.mark.parametrize('failure', ['eof', 'exception'])
async def test_partial_text_is_incomplete_and_never_replayed(failure):
    b = _backend()
    script = [_sse({'choices': [{'delta': {'content': 'Partial evidence'}, 'finish_reason': None}]})]
    if failure == 'exception':
        script.append(httpx.ReadError('fixture transport loss'))
    b._client = _ScriptedClient([script])
    events = [e async for e in b.ask('Current request')]
    assert result(events)['is_error'] is True
    assert any(e.kind == 'text_delta' and e.data == 'Partial evidence' for e in events)
    assert any(e.kind == 'error' for e in events)
    assert b._client.attempts == 1
    assert not any(e.kind == 'tool_use' for e in events)


@pytest.mark.parametrize('reason', [None, 'length', 'content_filter', 'unknown_vendor_reason'])
async def test_incomplete_tool_generation_executes_nothing(reason):
    effects, optional = [], []
    async def write(args):
        effects.append('written')
        return {'content': [{'type': 'text', 'text': 'written'}]}
    b = _backend([_tool('write_file', write)])
    async def verify():
        optional.append('verify')
        return []
    async def file(*args):
        optional.append('file')
        return []
    b._run_verifier_sweep, b._finish_filing = verify, file
    script = [tool_chunk(reason)] + (['data: [DONE]'] if reason else [])
    # DREAM-117 (fix #85): a `length` cut with no completed call is continued twice within the turn before it
    # ends; every attempt here is cut, so the turn still ends incomplete and nothing runs, is filed or is verified.
    b._client = _ScriptedClient([script] if reason == 'length' else [script, _text_round()])
    events = [e async for e in b.ask('Write the requested artifact')]
    assert result(events)['is_error'] is True
    assert effects == [] and optional == []
    assert b._client.attempts == (3 if reason == 'length' else 1)
    assert not any(e.kind == 'tool_use' for e in events)
    assert not any(m.get('tool_calls') for m in b.messages)


@pytest.mark.parametrize('terminal', ['done_only', 'finish_only', 'both'])
async def test_recognized_completion_retains_tool_compatibility(terminal):
    effects = []
    async def write(args):
        effects.append('written')
        return {'content': [{'type': 'text', 'text': 'written'}]}
    b = _backend([_tool('write_file', write)])
    script = [tool_chunk('tool_calls' if terminal != 'done_only' else None)]
    if terminal != 'finish_only':
        script.append('data: [DONE]')
    b._client = _ScriptedClient([script, _text_round()])
    events = [e async for e in b.ask('Write the requested artifact')]
    assert result(events)['is_error'] is False
    assert effects == ['written'] and b._client.attempts == 2


async def test_new_explicit_input_can_continue_after_remote_eof_without_replaying():
    b = _backend()
    b._client = _ScriptedClient([['data: {}'], _text_round('New answer')])
    failed = [e async for e in b.ask('First request')]
    assert result(failed)['is_error']
    assert b._client.attempts == 1
    succeeded = [e async for e in b.ask('New explicit request')]
    assert not result(succeeded)['is_error']
    assert b._client.attempts == 2


async def test_local_eof_keeps_uncertainty_fence_and_reports_failed_result(tmp_path):
    from dream.core.inference_coordination import EndpointCoordinator
    b = _backend()
    b.provider.base_url = 'http://127.0.0.1:19435/v1'
    b._coordination_override = EndpointCoordinator('loopback:19435', root=tmp_path / 'coord')
    b._client = _ScriptedClient([['data: {}']])
    events = [e async for e in b.ask('Request')]
    assert result(events)['is_error'] is True
    assert b.coordination_status()['state'] == 'uncertain'
    [e async for e in b.ask('Try again')]
    assert b._client.attempts == 1


@pytest.mark.parametrize('script', [
    [tool_chunk(), _sse({'choices': [{'index': 1, 'delta': {}, 'finish_reason': 'stop'}]})],
    *[[tool_chunk(reason), _sse({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}),
       'data: [DONE]'] for reason in ('length', 'content_filter', 'unknown_vendor_reason')],
    [_sse({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}), tool_chunk()],
])
async def test_other_choice_or_post_terminal_chunks_cannot_authorize_actions(script):
    effects = []
    async def write(args):
        effects.append('written')
        return {'content': [{'type': 'text', 'text': 'written'}]}
    b = _backend([_tool('write_file', write)])
    b._client = _ScriptedClient([script, _text_round()])
    events = [e async for e in b.ask('Request')]
    assert result(events)['is_error'] is True
    assert effects == [] and b._client.attempts == 1


async def test_primary_choice_is_selected_by_index_not_array_position():
    b = _backend()
    script = [_sse({'choices': [
        {'index': 1, 'delta': {'content': 'Other choice'}, 'finish_reason': 'stop'},
        {'index': 0, 'delta': {'content': 'Selected choice'}, 'finish_reason': 'stop'}]})]
    b._client = _ScriptedClient([script])
    events = [e async for e in b.ask('Request')]
    assert next(e.data for e in events if e.kind == 'assistant_done') == 'Selected choice'
    assert result(events)['is_error'] is False
