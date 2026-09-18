"""Dispatch completion and subsequent observation failure are distinct evidence."""
import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

import dream.computer as module
from dream.computer import Computer, ComputerError, _fingerprint
from dream.tools import computer_tools
from dream.tools.context import bind_context


@pytest.fixture
def native(tmp_path, monkeypatch):
    c = Computer(tmp_path, tmp_path / 'captures')
    state = {'window_id': '123', 'focus': '123', 'title': 'Save As', 'rect': [0, 0, 400, 300]}
    target = {'kind': 'desktop', 'window_id': '123', 'observation': 'before',
              'observed': time.monotonic(), 'fingerprint': _fingerprint(state),
              'pixels_sha256': hashlib.sha256(b'pixels').hexdigest()}
    c.targets['owned'] = target
    calls = []
    async def command(binary, *args, **kwargs):
        calls.append(args)
        if args == ('getwindowfocus',):
            return '123'
        return ''
    async def read(_):
        return dict(state)
    async def capture(*_):
        return b'pixels'
    def no_capture(*_, **__):
        raise AssertionError('Live desktop capture forbidden')
    monkeypatch.setattr(module, '_command', command)
    monkeypatch.setattr(module.ImageGrab, 'grab', no_capture)
    monkeypatch.setattr(c, '_desktop_state', read)
    monkeypatch.setattr(c, '_capture', capture)
    return c, target, state, calls


@pytest.mark.parametrize('failure', [ComputerError('Bad Drawable: No such window'), OSError('capture unavailable'), TimeoutError('capture timeout')])
async def test_completed_native_input_survives_observation_failure(native, monkeypatch, failure):
    c, target, _, calls = native
    async def failed_capture(_):
        assert calls[-1] == ('click', '1')
        raise failure
    monkeypatch.setattr(c, '_settled_capture', failed_capture)
    with bind_context(SimpleNamespace(computer=c, multimodal=True)):
        output = await computer_tools.computer_action.handler(
            {'target_id': 'owned', 'observation_id': 'before', 'action': 'click', 'x': 10, 'y': 10})
    assert not output.get('is_error')
    assert len(output['content']) == 1
    result = json.loads(output['content'][0]['text'])
    assert result['action_result']['action_dispatched'] is True
    assert result['action_result']['task_success'] == 'unverified'
    assert result['action_result']['after_observation_id'] is None
    assert result['observation_error'] == {'type': type(failure).__name__, 'message': str(failure)}
    assert result['observation_id'] is None and result['screenshot'] is None
    assert 'Do not replay' in result['recovery']
    assert 'explicit' in result['recovery']
    assert 'observation' not in target
    assert target['window_id'] == '123'
    assert set(c.targets) == {'owned'}
    with pytest.raises(ComputerError, match='already used'):
        await c.act('owned', 'before', 'click', x=10, y=10)
    assert calls == [('mousemove', '--window', '123', '10', '10'), ('getwindowfocus',), ('click', '1')]


@pytest.mark.parametrize('kind', ['desktop', 'browser'])
@pytest.mark.parametrize('failure', [ComputerError('partial dispatch'), TimeoutError('dispatch timeout'), asyncio.CancelledError()])
async def test_incomplete_dispatch_never_returns_completed_result(native, monkeypatch, kind, failure):
    c, target, _, _ = native
    target['kind'] = kind
    async def dispatch(*_):
        raise failure
    async def must_not_observe(*_):
        pytest.fail('An incomplete dispatch must not be reported as completed')
    monkeypatch.setattr(c, '_state', c._desktop_state)
    monkeypatch.setattr(c, '_' + kind + '_action', dispatch)
    monkeypatch.setattr(c, '_observe', must_not_observe)
    expected = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else ComputerError
    with pytest.raises(expected):
        await c.act('owned', 'before', 'key', key='Tab')
    assert 'observation' not in target


async def test_cancel_during_observation_propagates(native, monkeypatch):
    c, target, _, calls = native
    async def cancelled(_):
        raise asyncio.CancelledError()
    monkeypatch.setattr(c, '_settled_capture', cancelled)
    with pytest.raises(asyncio.CancelledError):
        await c.act('owned', 'before', 'key', key='Tab')
    assert calls[-1] == ('key', '--clearmodifiers', 'Tab')
    assert 'observation' not in target


async def test_stale_state_refuses_input_before_dispatch(native):
    c, target, state, calls = native
    state['title'] = 'Changed'
    with pytest.raises(ComputerError, match='state changed'):
        await c.act('owned', 'before', 'key', key='Tab')
    assert calls == []
    assert 'observation' not in target


async def test_failed_refresh_clears_any_new_token_and_bounds_error(native, monkeypatch):
    c, target, _, _ = native
    async def failed_refresh(*_):
        target.update(observation='incomplete', visible_ids={'new'})
        raise OSError('x' * 5000)
    monkeypatch.setattr(c, '_observe', failed_refresh)
    result = await c.act('owned', 'before', 'key', key='Tab')
    assert result['observation_error']['message'] == 'x' * 1000
    assert 'observation' not in target and 'visible_ids' not in target
    with pytest.raises(ComputerError, match='already used'):
        await c.act('owned', 'incomplete', 'key', key='Tab')


async def test_same_target_can_be_observed_after_transient_capture_failure(native, monkeypatch):
    c, target, state, calls = native
    async def unavailable(*_):
        raise OSError('temporarily unavailable')
    monkeypatch.setattr(c, '_settled_capture', unavailable)
    result = await c.act('owned', 'before', 'key', key='Tab')
    assert result['screenshot'] is None
    async def recovered(_):
        return dict(state), b'pixels', True
    monkeypatch.setattr(c, '_settled_capture', recovered)
    fresh = await c.observe('owned')
    assert fresh['observation_id'] == target['observation']
    assert fresh['observation_id'] != 'before'
    assert fresh['state']['window_id'] == '123'
    assert calls == [('getwindowfocus',), ('key', '--clearmodifiers', 'Tab')]


@pytest.mark.parametrize('token', [None, ''])
async def test_missing_token_cannot_replay_after_partial_result(native, monkeypatch, token):
    c, target, _, calls = native
    async def unavailable(*_):
        raise OSError('temporarily unavailable')
    monkeypatch.setattr(c, '_settled_capture', unavailable)
    await c.act('owned', 'before', 'key', key='Tab')
    with pytest.raises(ComputerError, match='already used'):
        await c.act('owned', token, 'key', key='Tab')
    assert calls == [('getwindowfocus',), ('key', '--clearmodifiers', 'Tab')]
