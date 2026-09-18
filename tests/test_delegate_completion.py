"""Nonstream delegate completion must authorize both tools and successful results."""
from types import SimpleNamespace

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.inference_coordination import EndpointCoordinator
from dream.core.subagents import LocalSubagentSpec
from test_local_subagents import _FakeClient


OMITTED = object()


def choice(mode='text', reason='stop', index=OMITTED, text='selected partial text'):
    message = {'content': text}
    if mode == 'structured':
        message['tool_calls'] = [{'id': 'effect', 'type': 'function',
                                 'function': {'name': 'record_probe', 'arguments': '{}'}}]
    elif mode == 'xml':
        message['content'] += '\n<tool_calls><invoke name="record_probe"></invoke></tool_calls>'
    selected = {'message': message}
    if reason is not OMITTED:
        selected['finish_reason'] = reason
    if index is not OMITTED:
        selected['index'] = index
    return selected


def prepared(tmp_path, monkeypatch, first, local):
    import socket
    def deny(*args, **kwargs):
        raise AssertionError('No network permitted in delegate fixtures')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    effects, events = [], []
    async def record(args):
        effects.append(args)
        return {'content': [{'type': 'text', 'text': 'recorded'}]}
    tool = SimpleNamespace(name='record_probe', description='in-memory effect',
                           input_schema={'type': 'object', 'properties': {}}, handler=record)
    spec = LocalSubagentSpec('worker', 'Fixture worker', 'Follow fixture', ('record_probe',))
    b = OpenAICompatBackend(provider=SimpleNamespace(key='fixture', label='Fixture',
        base_url='http://127.0.0.1:19876/v1' if local else 'https://fixture.invalid/v1',
        multimodal=False), model='fixture', system_prompt='Fixture', tools=[tool],
        permission_cb=None, subagents={'worker': spec})
    b._client = _FakeClient(post_scripts=[first, {'choices': [choice(text='complete summary')]}])
    b._background_emit = events.append
    if local:
        b._coordination_override = EndpointCoordinator('loopback:19876', root=tmp_path / 'leases')
    return b, effects, events


@pytest.mark.parametrize('local', [False, True], ids=['remote', 'local'])
@pytest.mark.parametrize('mode', ['text', 'structured', 'xml'])
@pytest.mark.parametrize('reason', ['length', 'content_filter', 'unknown_vendor_reason', '', False, 0, [], {}],
                         ids=['length', 'filtered', 'unknown', 'empty', 'bool', 'number', 'array', 'object'])
async def test_explicit_unsuccessful_completion_cannot_dispatch_or_report_success(tmp_path, monkeypatch, local, mode, reason):
    b, effects, events = prepared(tmp_path, monkeypatch, {'choices': [choice(mode, reason)]}, local)
    text, failed = await b._run_subagent('worker', 'Do the fixture')
    assert failed and effects == []
    assert len(b._client.posted) == 1
    assert 'selected partial text' in text
    assert 'incomplete' in text
    if reason == 'length':
        assert 'truncated' in text
    assert events[-1].data['status'] == 'failed'
    assert not any(e.data['kind'] == 'tool_use' for e in events)
    if local:
        assert b.coordination_status()['state'] == 'idle'


@pytest.mark.parametrize('local', [False, True], ids=['remote', 'local'])
@pytest.mark.parametrize('mode', ['text', 'structured', 'xml'])
@pytest.mark.parametrize('reason', ['stop', 'tool_calls', 'function_call', None, OMITTED],
                         ids=['stop', 'tool_calls', 'function_call', 'null', 'omitted'])
async def test_recognized_and_legacy_completion_remains_compatible(tmp_path, monkeypatch, local, mode, reason):
    b, effects, events = prepared(tmp_path, monkeypatch, {'choices': [choice(mode, reason)]}, local)
    text, failed = await b._run_subagent('worker', 'Do the fixture')
    assert not failed
    assert len(effects) == (0 if mode == 'text' else 1)
    assert len(b._client.posted) == (1 if mode == 'text' else 2)
    assert text == ('selected partial text' if mode == 'text' else 'complete summary')
    assert events[-1].data['status'] == 'completed'
    if local:
        assert b.coordination_status()['state'] == 'idle'


@pytest.mark.parametrize('local', [False, True], ids=['remote', 'local'])
@pytest.mark.parametrize('first', [
    {'choices': [choice('structured', index=1)]},
    {'choices': [choice('structured', index=0), choice(index=0)]},
    {'choices': [choice('structured', index=True)]},
    {'choices': [choice('structured', index='0')]},
    {'choices': []}, {'choices': 'malformed'},
], ids=['secondary_only', 'duplicate_primary', 'bool_index', 'string_index', 'no_primary', 'bad_choices'])
async def test_missing_or_ambiguous_primary_cannot_authorize_actions(tmp_path, monkeypatch, local, first):
    b, effects, events = prepared(tmp_path, monkeypatch, first, local)
    text, failed = await b._run_subagent('worker', 'Do the fixture')
    assert failed and effects == [] and len(b._client.posted) == 1
    assert events[-1].data['status'] == 'failed'
    if local:
        assert b.coordination_status()['state'] == 'idle'


@pytest.mark.parametrize('local', [False, True], ids=['remote', 'local'])
@pytest.mark.parametrize('secondary_mode', ['structured', 'xml', 'text'])
async def test_explicit_primary_overrides_array_order(tmp_path, monkeypatch, local, secondary_mode):
    first = {'choices': [choice(secondary_mode, index=1, text='wrong choice'),
                         choice(index=0, text='primary answer')]}
    b, effects, events = prepared(tmp_path, monkeypatch, first, local)
    text, failed = await b._run_subagent('worker', 'Do the fixture')
    assert not failed and text == 'primary answer'
    assert effects == [] and len(b._client.posted) == 1


@pytest.mark.parametrize('message', [OMITTED, None, '', [], False],
                         ids=['missing', 'null', 'string', 'array', 'bool'])
async def test_invalid_selected_message_is_failed_not_empty_success(tmp_path, monkeypatch, message):
    selected = {'index': 0, 'finish_reason': 'stop'}
    if message is not OMITTED:
        selected['message'] = message
    b, effects, events = prepared(tmp_path, monkeypatch, {'choices': [selected]}, False)
    _, failed = await b._run_subagent('worker', 'Do the fixture')
    assert failed and effects == [] and len(b._client.posted) == 1
    assert events[-1].data['status'] == 'failed'


async def test_later_bad_completion_retains_prior_effect_and_usage(tmp_path, monkeypatch):
    b, effects, events = prepared(tmp_path, monkeypatch, {'choices': [choice('structured')]}, False)
    b._client._p.append({'choices': [choice('structured', 'length')],
                         'usage': {'prompt_tokens': 20, 'completion_tokens': 7}})
    del b._client._p[1]
    text, failed = await b._run_subagent('worker', 'Do the fixture')
    assert failed and len(effects) == 1 and len(b._client.posted) == 2
    assert 'no tools from this response ran' in text
    assert b._delegated_usage == {'prompt_tokens': 20, 'completion_tokens': 7}
    assert events[-1].data['status'] == 'failed'
