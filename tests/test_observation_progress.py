"""Synthetic read evidence; no files, models, browsers or shell handlers run."""
from types import SimpleNamespace

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend


@pytest.fixture
def reader():
    backend = OpenAICompatBackend(
        provider=SimpleNamespace(key='machx', label='fixture', base_url='http://unused/v1',
                                 multimodal=False, api_key=lambda: 'unused'),
        model='fixture', system_prompt='fixture', tools=[], permission_cb=None)
    calls = []
    async def execute(name, args, allowed=None):
        calls.append((name, args))
        return args.get('fixture_result', 'unchanged document'), False
    backend._exec_tool = execute
    return backend, calls


async def test_argument_churn_gets_evidence_specific_guidance(reader):
    backend, calls = reader
    history = {}
    results = [await backend._guarded_exec('read_file', {'path': 'scene.py', 'offset': i}, history)
               for i in range(4)]
    assert '[progress check]' not in results[0][0]
    assert '[progress check]' in results[2][0]
    assert 'same target' in results[2][0]
    assert 'unchanged document' in results[2][0]
    assert len(calls) == 4  # Advisory; repeated text cannot prove the task is stalled.
    assert all(not error and not stop for _, error, stop in results)


async def test_new_pages_and_distinct_targets_are_not_stalls(reader):
    backend, _ = reader
    history = {}
    for i in range(8):
        text, _, _ = await backend._guarded_exec('read_file', {
            'path': 'scene.py', 'offset': i, 'fixture_result': f'new page {i}'}, history)
        assert '[progress check]' not in text
    history = {}
    for i in range(8):
        text, _, _ = await backend._guarded_exec('read_file', {'path': f'file{i}.py'}, history)
        assert '[progress check]' not in text


@pytest.mark.parametrize('mutation', ['write_file', 'run_bash'])
async def test_mutation_resets_observation_evidence(reader, mutation):
    backend, _ = reader
    history = {}
    for i in range(3):
        await backend._guarded_exec('read_file', {'path': 'scene.py', 'offset': i}, history)
    await backend._guarded_exec(mutation, {'path': 'scene.py'}, history)
    text, _, _ = await backend._guarded_exec('read_file', {'path': 'scene.py', 'offset': 4}, history)
    assert '[progress check]' not in text


async def test_failure_evidence_is_not_mixed_with_success(reader):
    backend, _ = reader
    history = {}
    failed = True
    async def execute(*args, **kwargs):
        return 'same text', failed
    backend._exec_tool = execute
    for i in range(2):
        await backend._guarded_exec('read_file', {'path': 'scene.py', 'offset': i}, history)
    failed = False
    text, _, _ = await backend._guarded_exec('read_file', {'path': 'scene.py', 'offset': 3}, history)
    assert '[progress check]' not in text


async def test_workers_do_not_share_progress_evidence(reader):
    backend, _ = reader
    for i in range(4):
        text, _, _ = await backend._guarded_exec('read_file', {'path': 'scene.py', 'offset': i}, {})
        assert '[progress check]' not in text


async def test_targetless_and_unknown_tools_are_not_classified(reader):
    backend, _ = reader
    for name in ['read_file', 'custom_read', 'run_bash']:
        history = {}
        for i in range(4):
            args = {'offset': i} if name == 'read_file' else {'path': 'scene.py', 'offset': i}
            text, _, _ = await backend._guarded_exec(name, args, history)
            assert '[progress check]' not in text


async def test_denied_mutation_does_not_erase_repeated_evidence(reader):
    backend, _ = reader
    history = {}
    execute = backend._exec_tool
    async def deny_write(name, args, allowed=None):
        if name == 'write_file':
            return 'Declined by the user.', True
        return await execute(name, args, allowed=allowed)
    backend._exec_tool = deny_write
    for i in range(2):
        await backend._guarded_exec('read_file', {'path': 'scene.py', 'offset': i}, history)
    await backend._guarded_exec('write_file', {'path': 'scene.py'}, history)
    text, _, _ = await backend._guarded_exec('read_file', {'path': 'scene.py', 'offset': 3}, history)
    assert '[progress check]' in text


async def test_actual_rounds_carry_guidance_and_preserve_tool_pairing(reader):
    import json
    from test_loop_guard import _SequenceClient, _sse, _text_round, _run
    backend, executions = reader
    scripts = []
    for i in range(4):
        scripts.append([
            _sse({'choices': [{'delta': {'tool_calls': [{
                'index': 0, 'id': f'read{i}', 'function': {
                    'name': 'read_file', 'arguments': json.dumps({'path': 'scene.py', 'offset': i})}
            }]}, 'finish_reason': None}]}),
            _sse({'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}]}),
            'data: [DONE]',
        ])
    backend._client = _SequenceClient([*scripts, _text_round('Use existing evidence to make the change.')])
    events = await _run(backend)
    assert len(executions) == 4
    assert next(event.data for event in events if event.kind == 'result')['subtype'] == 'success'
    results = [message for message in backend.messages if message.get('role') == 'tool']
    assert '[progress check]' in results[2]['content']
    calls = [call for message in backend.messages for call in message.get('tool_calls', [])]
    assert [call['id'] for call in calls] == [message['tool_call_id'] for message in results]
