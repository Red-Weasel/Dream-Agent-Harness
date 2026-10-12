"""Original call provenance and result delivery use real private artifacts."""
import copy
import json

import pytest

from dream.core import parent_continuation as pc
from dream.core.tool_artifacts import ArtifactError


RUN = 'a' * 32
BINDING = {'parent': RUN, 'workspace_revision': 'original'}
ERRORS = (ValueError, ArtifactError, OSError)


def inputs():
    wire = [{'id': 'child', 'name': 'task',
             'arguments': json.dumps({'prompt': 'original 🌙 ' * 600}),
             'id_source': 'supplied'},
            {'id': 'sibling', 'name': 'read_file',
             'arguments': '{"path":"notes.txt"}', 'id_source': 'fallback'}]
    effective = [{k: c[k] for k in ('id', 'name', 'arguments')} for c in wire]
    effective[0]['arguments'] = '{"prompt":"effective wrapped prompt"}'
    messages = [{'role': 'user', 'content': 'Do the original work'},
                {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': c['id'], 'type': 'function', 'function': {
                        'name': c['name'], 'arguments': c['arguments'][:2000]}}
                    for c in wire]}]
    return messages, wire, effective


def capture(tmp_path, *, response='response-original', generation=0):
    messages, wire, effective = inputs()
    ref = pc.capture(tmp_path, 'session', RUN, messages, wire_calls=wire,
                     effective_calls=effective, provider_response_id=response,
                     execution_binding=BINDING, generation=generation)
    return ref, messages, wire


def complete(messages):
    return copy.deepcopy(messages) + [
        {'role': 'tool', 'tool_call_id': 'child', 'content': 'exact child 🌙 result'},
        {'role': 'tool', 'tool_call_id': 'sibling', 'content': 'exact sibling result'}]


@pytest.mark.parametrize('response', ['response-original', None])
def test_exact_original_calls_results_roundtrip_and_detachment(tmp_path, response):
    ref, messages, wire = capture(tmp_path, response=response)
    pc.validate_reference(json.loads(json.dumps(ref)), run_id=RUN)
    settled = pc.settle(tmp_path, ref, complete(messages))
    restored = pc.load(tmp_path, settled, execution_binding=BINDING)
    for call, original in zip(restored[1]['tool_calls'], wire):
        assert call['id'] == original['id']
        assert call['function'] == {k: original[k] for k in ('name', 'arguments')}
    assert len(restored[1]['tool_calls'][0]['function']['arguments']) > 2000
    assert restored[-2:] == complete(messages)[-2:]
    restored[0]['content'] = 'mutated local copy'
    assert pc.load(tmp_path, settled, execution_binding=BINDING)[0] == messages[0]


def test_settle_idempotence_preserves_original_artifacts(tmp_path):
    ref, messages, _ = capture(tmp_path)
    before = {p: p.read_bytes() for p in (tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8')}
    result = complete(messages)
    settled = pc.settle(tmp_path, ref, result)
    assert pc.settle(tmp_path, ref, result) == settled
    assert pc.settle(tmp_path, settled, result) == settled
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize('case', ['missing', 'wrong', 'order', 'duplicate', 'prefix', 'history'])
def test_settle_refuses_incomplete_or_changed_conversation(tmp_path, case):
    ref, messages, _ = capture(tmp_path)
    result = complete(messages)
    if case == 'missing': result.pop()
    elif case == 'wrong': result[-1]['tool_call_id'] = 'foreign'
    elif case == 'order': result[-2:] = reversed(result[-2:])
    elif case == 'duplicate': result[-1]['tool_call_id'] = 'child'
    elif case == 'prefix': result[0]['content'] = 'changed goal'
    else: result[1]['tool_calls'][0]['function']['arguments'] = 'changed history'
    with pytest.raises(ERRORS): pc.settle(tmp_path, ref, result)


def test_reused_ids_in_prior_round_are_allowed(tmp_path):
    messages, wire, effective = inputs()
    messages[:0] = [{'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'child', 'type': 'function', 'function': {'name': 'task', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'child', 'content': 'prior result'}]
    ref = pc.capture(tmp_path, 'session', RUN, messages, wire_calls=wire,
                     effective_calls=effective, provider_response_id=None,
                     execution_binding=BINDING)
    restored = pc.load(tmp_path, pc.settle(tmp_path, ref, complete(messages)), execution_binding=BINDING)
    assert restored[:2] == messages[:2]
    assert restored[-2]['content'] == 'exact child 🌙 result'


@pytest.mark.parametrize('case', ['captured', 'binding', 'generation', 'workspace', 'run', 'session', 'receipt'])
def test_load_refuses_unfinished_or_foreign_authority(tmp_path, case):
    ref, messages, _ = capture(tmp_path)
    ref = ref if case == 'captured' else pc.settle(tmp_path, ref, complete(messages))
    kwargs = {'execution_binding': BINDING}
    workspace = tmp_path
    if case == 'binding': kwargs['execution_binding'] = {'parent': 'foreign'}
    elif case == 'generation': kwargs['generation'] = 1
    elif case == 'workspace': workspace = tmp_path / 'foreign'; workspace.mkdir(mode=0o700)
    elif case == 'run': ref['run_id'] = 'b' * 32
    elif case == 'session': ref['session_id'] = 'foreign'
    elif case == 'receipt': ref['receipt']['sha256'] = '0' * 64
    with pytest.raises(ERRORS): pc.load(workspace, ref, **kwargs)


def test_saved_original_tamper_refuses_load(tmp_path):
    ref, messages, _ = capture(tmp_path)
    settled = pc.settle(tmp_path, ref, complete(messages))
    for artifact in (tmp_path / '.dream/tool-artifacts/v1').glob('*.utf8'):
        artifact.write_bytes(b'tampered original')
    with pytest.raises(ERRORS): pc.load(tmp_path, settled, execution_binding=BINDING)


@pytest.mark.parametrize('field,value', [('version', True), ('version', 1.0),
    ('generation', True), ('generation', -1), ('assistant_index', False),
    ('assistant_index', -1), ('capture_id', 'INVALID'), ('run_id', 'A' * 32),
    ('status', 'running'), ('extra', 'unexpected')])
def test_reference_strict_types_and_keys(tmp_path, field, value):
    ref, _, _ = capture(tmp_path)
    ref[field] = value
    with pytest.raises(ValueError): pc.validate_reference(ref)


@pytest.mark.parametrize('case', ['generation_bool', 'generation_float', 'nan_binding',
    'empty_binding', 'large_binding', 'duplicate_calls', 'call_extra', 'bad_origin',
    'bad_args', 'too_many_calls', 'too_many_messages', 'oversize', 'bad_run', 'long_session'])
def test_capture_refuses_malformed_and_unbounded_inputs(tmp_path, case):
    messages, wire, effective = inputs()
    kwargs = dict(wire_calls=wire, effective_calls=effective, provider_response_id=None,
                  execution_binding=BINDING, generation=0)
    run, session = RUN, 'session'
    if case == 'generation_bool': kwargs['generation'] = True
    elif case == 'generation_float': kwargs['generation'] = 0.0
    elif case == 'nan_binding': kwargs['execution_binding'] = {'number': float('nan')}
    elif case == 'empty_binding': kwargs['execution_binding'] = {}
    elif case == 'large_binding': kwargs['execution_binding'] = {'text': 'x' * 8193}
    elif case == 'duplicate_calls': wire[1]['id'] = wire[0]['id']; effective[1]['id'] = effective[0]['id']
    elif case == 'call_extra': wire[0]['unexpected'] = 1
    elif case == 'bad_origin': wire[0]['id_source'] = 'provider'
    elif case == 'bad_args': wire[0]['arguments'] = {}
    elif case == 'too_many_calls': kwargs['wire_calls'] = wire * 129; kwargs['effective_calls'] = effective * 129
    elif case == 'too_many_messages': messages[:0] = [{'role': 'user', 'content': 'x'}] * 8192
    elif case == 'oversize': messages[0]['content'] = 'x' * (8 * 1024 * 1024 + 1)
    elif case == 'bad_run': run = 'wrong'
    elif case == 'long_session': session = 'x' * 257
    with pytest.raises(ERRORS): pc.capture(tmp_path, session, run, messages, **kwargs)
