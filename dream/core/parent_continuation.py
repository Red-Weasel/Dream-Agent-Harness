"""Exact private parent call rounds; conversation provenance never grants execution.

Capture before handlers, including wire arguments before history clipping. Only a
complete ordered result round may settle. Resume callers still enforce real action
and child reconciliation, owner admission and original budgets. Hash receipts are
not external attestation against a process running as the same operating-system user.
"""
from __future__ import annotations

import hashlib
import json
import re
from uuid import uuid4

from .tool_artifacts import ArtifactStore, MAX_RESULT_BYTES, _HANDLE
from .worker_checkpoints import _encode, _decode

MAX_MESSAGES = 8192
MAX_CALLS = 256
_RUN = re.compile(r'[0-9a-f]{32}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_REF_FIELDS = {'kind', 'version', 'run_id', 'session_id', 'capture_id', 'generation', 'status',
               'binding_sha256', 'assistant_index', 'receipt'}
_BODY_FIELDS = {'kind', 'version', 'run_id', 'capture_id', 'generation', 'binding', 'provider_response_id',
                'messages', 'wire_calls', 'effective_calls', 'assistant_index', 'settled', 'history_assistant'}
_RECEIPT_FIELDS = {'version', 'handle', 'sha256', 'full_bytes', 'workspace_key', 'session_key', 'run_key', 'call_key'}


def _invalid():
    raise ValueError('Invalid or mismatched parent conversation checkpoint')


def _hash(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _plain(value):
    """Detach host strings, including tool text subclasses; never infer their attributes."""
    nodes = 0
    def copy(item, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 64 or nodes > 200000:
            _invalid()
        if isinstance(item, str):
            if len(item) > MAX_RESULT_BYTES:
                _invalid()
            return str(item)
        if type(item) is dict:
            if not all(type(key) is str for key in item):
                _invalid()
            return {key: copy(val, depth + 1) for key, val in item.items()}
        if type(item) is list:
            return [copy(val, depth + 1) for val in item]
        if type(item) in (int, float, bool, type(None)):
            return item
        _invalid()
    result = copy(value)
    try:
        raw = _encode(result)
    except ValueError:
        _invalid()
    if len(raw) > MAX_RESULT_BYTES:
        _invalid()
    return result


def _id(value, limit=256):
    return type(value) is str and bool(value.strip()) and len(value) <= limit


def _number(value):
    return type(value) is int and 0 <= value < (1 << 63)


def validate_reference(reference, run_id=None):
    if type(reference) is not dict or set(reference) != _REF_FIELDS:
        _invalid()
    if (reference['kind'] != 'dream.parent_continuation' or type(reference['version']) is not int
            or reference['version'] != 1 or type(reference['run_id']) is not str
            or not _RUN.fullmatch(reference['run_id']) or not _id(reference['session_id'])
            or type(reference['capture_id']) is not str or not _RUN.fullmatch(reference['capture_id'])
            or not _number(reference['generation']) or not _number(reference['assistant_index'])
            or reference['assistant_index'] >= MAX_MESSAGES or reference['status'] not in ('captured', 'settled')
            or type(reference['binding_sha256']) is not str or not _SHA.fullmatch(reference['binding_sha256'])
            or (run_id is not None and reference['run_id'] != run_id)):
        _invalid()
    receipt = reference['receipt']
    if (type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS or type(receipt['version']) is not int
            or receipt['version'] != 1 or not _number(receipt['full_bytes'])
            or receipt['full_bytes'] > MAX_RESULT_BYTES or type(receipt['handle']) is not str):
        _invalid()
    match = _HANDLE.fullmatch(receipt['handle'])
    if match is None or int(match[6]) != receipt['full_bytes']:
        _invalid()
    for key, value in zip(('workspace_key', 'session_key', 'run_key', 'call_key', 'sha256'), match.groups()[:5]):
        if type(receipt[key]) is not str or receipt[key] != value:
            _invalid()
    expected = {'session_key': hashlib.sha256(reference['session_id'].encode()).hexdigest(),
                'run_key': hashlib.sha256(reference['run_id'].encode()).hexdigest(),
                'call_key': hashlib.sha256((reference['capture_id'] + ':' + reference['status']).encode()).hexdigest()}
    if any(receipt[key] != value for key, value in expected.items()):
        _invalid()


def _messages(messages, *, open_last=False):
    if type(messages) is not list or not 1 <= len(messages) <= MAX_MESSAGES:
        _invalid()
    i = 0
    while i < len(messages):
        message = messages[i]
        if (type(message) is not dict or message.get('role') not in ('system', 'developer', 'user', 'assistant')
                or type(message.get('content')) not in (str, list, type(None))):
            _invalid()
        calls = message.get('tool_calls')
        if calls is None:
            i += 1
            continue
        if message['role'] != 'assistant' or type(calls) is not list or not 1 <= len(calls) <= MAX_CALLS:
            _invalid()
        ids = []
        for call in calls:
            if (type(call) is not dict or set(call) != {'id', 'type', 'function'} or not _id(call['id'])
                    or call['type'] != 'function' or type(call['function']) is not dict
                    or set(call['function']) != {'name', 'arguments'}
                    or not _id(call['function']['name'], 4096) or type(call['function']['arguments']) is not str):
                _invalid()
            ids.append(call['id'])
        if len(set(ids)) != len(ids):
            _invalid()
        if open_last and i == len(messages) - 1:
            return
        for offset, call_id in enumerate(ids, 1):
            if i + offset >= len(messages):
                _invalid()
            result = messages[i + offset]
            if (type(result) is not dict or result.get('role') != 'tool'
                    or result.get('tool_call_id') != call_id or type(result.get('content')) is not str):
                _invalid()
        i += len(ids) + 1


def _calls(calls, *, wire):
    fields = {'id', 'name', 'arguments', *(['id_source'] if wire else [])}
    if type(calls) is not list or not 1 <= len(calls) <= MAX_CALLS:
        _invalid()
    for call in calls:
        if (type(call) is not dict or set(call) != fields or not _id(call['id'])
                or not _id(call['name'], 4096) or type(call['arguments']) is not str
                or (wire and call['id_source'] not in ('supplied', 'fallback'))):
            _invalid()
    if len({call['id'] for call in calls}) != len(calls):
        _invalid()


def _body(data):
    if (type(data) is not dict or set(data) != _BODY_FIELDS or data['kind'] != 'dream.parent_continuation_snapshot'
            or type(data['version']) is not int or data['version'] != 1
            or type(data['run_id']) is not str or not _RUN.fullmatch(data['run_id'])
            or type(data['capture_id']) is not str or not _RUN.fullmatch(data['capture_id'])
            or not _number(data['generation']) or not _number(data['assistant_index'])
            or type(data['settled']) is not bool or type(data['binding']) is not dict or not data['binding']
            or len(_encode(data['binding'])) > 8192
            or (data['provider_response_id'] is not None and not _id(data['provider_response_id'], 1024))):
        _invalid()
    _messages(data['messages'], open_last=not data['settled'])
    index = data['assistant_index']
    if index >= len(data['messages']) or (not data['settled'] and index != len(data['messages']) - 1):
        _invalid()
    _calls(data['wire_calls'], wire=True)
    _calls(data['effective_calls'], wire=False)
    if ([call['id'] for call in data['wire_calls']] != [call['id'] for call in data['effective_calls']]
            or type(data['history_assistant']) is not dict):
        _invalid()
    assistant = data['messages'][index]
    expected = [{'id': call['id'], 'type': 'function',
                 'function': {'name': call['name'], 'arguments': call['arguments']}} for call in data['wire_calls']]
    if assistant.get('role') != 'assistant' or assistant.get('tool_calls') != expected:
        _invalid()
    history = data['history_assistant']
    _messages([history], open_last=True)
    if ([call['id'] for call in history['tool_calls']] != [call['id'] for call in expected]
            or _encode({key: val for key, val in assistant.items() if key != 'tool_calls'})
            != _encode({key: val for key, val in history.items() if key != 'tool_calls'})):
        _invalid()


def _save(store, data, session_id, status):
    _body(data)
    raw = _encode(data)
    if len(raw) > MAX_RESULT_BYTES:
        _invalid()
    receipt = store.save(raw.decode(), data['capture_id'] + ':' + status)
    reference = dict(kind='dream.parent_continuation', version=1, run_id=data['run_id'], session_id=session_id,
                     capture_id=data['capture_id'], generation=data['generation'], status=status,
                     binding_sha256=_hash(data['binding']), assistant_index=data['assistant_index'],
                     receipt=receipt.metadata())
    validate_reference(reference)
    return reference


def _read(workspace, reference):
    validate_reference(reference)
    store = ArtifactStore(workspace, reference['session_id'], reference['run_id'])
    if store._receipt(reference['receipt']['handle']).metadata() != reference['receipt']:
        _invalid()
    data = _decode(store.load_original(reference['receipt']['handle']))
    _body(data)
    if (data['run_id'] != reference['run_id'] or data['capture_id'] != reference['capture_id']
            or data['generation'] != reference['generation'] or data['assistant_index'] != reference['assistant_index']
            or data['settled'] != (reference['status'] == 'settled')
            or _hash(data['binding']) != reference['binding_sha256']):
        _invalid()
    return store, data


def capture(workspace, session_id, run_id, messages, *, wire_calls, effective_calls,
            provider_response_id, execution_binding, generation=0):
    if not _id(session_id) or type(run_id) is not str or not _RUN.fullmatch(run_id):
        _invalid()
    messages, wire_calls, effective_calls = _plain(messages), _plain(wire_calls), _plain(effective_calls)
    _messages(messages, open_last=True)
    if not messages[-1].get('tool_calls'):
        _invalid()
    history = _plain(messages[-1])
    _calls(wire_calls, wire=True)
    messages[-1]['tool_calls'] = [{'id': call['id'], 'type': 'function',
                                  'function': {'name': call['name'], 'arguments': call['arguments']}}
                                 for call in wire_calls]
    data = dict(kind='dream.parent_continuation_snapshot', version=1, run_id=run_id, capture_id=uuid4().hex,
                generation=generation, binding=_plain(execution_binding), provider_response_id=provider_response_id,
                messages=messages, wire_calls=wire_calls, effective_calls=effective_calls,
                assistant_index=len(messages) - 1, settled=False, history_assistant=history)
    return _save(ArtifactStore(workspace, session_id, run_id), data, session_id, 'captured')


def settle(workspace, reference, messages):
    store, saved = _read(workspace, reference)
    messages = _plain(messages)
    _messages(messages)
    index = saved['assistant_index']
    if (len(messages) <= index or _encode(messages[:index]) != _encode(saved['messages'][:index])
            or _encode(messages[index]) not in (_encode(saved['history_assistant']), _encode(saved['messages'][index]))):
        _invalid()
    messages[index] = saved['messages'][index]
    if saved['settled'] and _encode(messages) != _encode(saved['messages']):
        _invalid()
    data = {**saved, 'messages': messages, 'settled': True}
    return _save(store, data, reference['session_id'], 'settled')


def load(workspace, reference, *, execution_binding, generation=0):
    _, data = _read(workspace, reference)
    if (not data['settled'] or not _number(generation) or generation != data['generation']
            or _encode(_plain(execution_binding)) != _encode(data['binding'])):
        _invalid()
    return data['messages']
