"""Exact private snapshot reads and non-settling debt across reopen."""
from copy import deepcopy
import hashlib
import json
import time

import pytest

from dream.core.tool_artifacts import ArtifactError
from dream.core.worker_checkpoints import WorkerJournal, WorkerCheckpointError, _encode
from dream.core.worker_continuation import WorkerSnapshots


def snapshot(messages=None):
    return {'kind': 'dream.worker_http_snapshot', 'version': 1,
            'messages': messages or [{'role': 'system', 'content': 'standing rules 🌙'}],
            'original_history': [], 'state': {'requests_already_used': 1}}


def create(root, run='a' * 32):
    journal = WorkerJournal(root, 'private-session', run, create=True)
    journal.__enter__()
    packed = WorkerSnapshots(journal).pack(snapshot())
    journal.create({'task': 'original'}, packed,
                   deadline_ns=time.time_ns() + 60_000_000_000, max_rounds=20)
    return journal


def test_repeated_snapshots_store_large_unchanged_message_once(tmp_path):
    journal = create(tmp_path)
    try:
        codec = WorkerSnapshots(journal)
        messages = [{'role': 'system', 'content': 'rules'},
                    {'role': 'user', 'content': 'large exact source 🌙\n' * 20000}]
        for step in range(15):
            value = snapshot(messages)
            value['state']['step'] = step
            journal.settle(codec.pack(value))
        files = list((tmp_path / '.dream/tool-artifacts').rglob('*.utf8'))
        assert sum(path.stat().st_size for path in files) < 2 * len(_encode(messages))
        assert codec.unpack()['messages'] == messages
    finally:
        journal.close()


def test_exact_round_includes_reasoning_arguments_images_and_originals(tmp_path):
    journal = create(tmp_path)
    try:
        codec = WorkerSnapshots(journal)
        value = snapshot([{'role': 'assistant', 'content': None, 'reasoning_content': 'reason 🌙',
            'tool_calls': [{'id': 'call', 'type': 'function', 'function': {
                'name': 'read_file', 'arguments': '{"path":"exact 🌙"}'}}]},
            {'role': 'tool', 'tool_call_id': 'call', 'content': 'exact result'},
            {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}}]}])
        value['original_history'] = deepcopy(value['messages'])
        journal.settle(codec.pack(value))
        assert WorkerSnapshots(journal).unpack() == value
    finally:
        journal.close()


@pytest.mark.parametrize('returned', [False, True])
def test_observation_checkpoint_never_settles_pending_request(tmp_path, returned):
    journal = create(tmp_path)
    journal.request_started(now_ns=time.time_ns())
    journal.tool_started('original-action')
    if returned:
        journal.tool_finished('original-action', 'b' * 64)
    saved = journal.inspect()
    journal.checkpoint(WorkerSnapshots(journal).pack(snapshot()))
    assert journal.inspect()['pending'] == saved['pending']
    journal.close()
    with WorkerJournal(tmp_path, 'private-session', 'a' * 32) as restored:
        with pytest.raises(WorkerCheckpointError, match='unsettled'):
            restored.resume({'task': 'original'}, now_ns=time.time_ns())


def test_checkpoint_preserves_pause_anchor_across_reopen(tmp_path):
    journal = create(tmp_path)
    journal.control('paused')
    before = journal.inspect()
    journal.checkpoint(WorkerSnapshots(journal).pack(snapshot()))
    journal.close()
    with WorkerJournal(tmp_path, 'private-session', 'a' * 32) as restored:
        restored.control('running')
        after = restored.inspect()
        assert after['deadline_ns'] - before['deadline_ns'] == after['last_clock_ns'] - before['last_clock_ns']
        assert after['requests'] == before['requests']


def test_corrupt_original_and_foreign_receipt_are_refused(tmp_path):
    journal = create(tmp_path)
    other = create(tmp_path, 'b' * 32)
    try:
        packed = journal.inspect()['snapshot']
        with pytest.raises(ArtifactError, match='scope'):
            WorkerSnapshots(other).unpack(packed)
        receipt = packed['messages'][0]
        name = journal.store._name(journal.store._receipt(receipt['handle']))
        original = next(path for path in (tmp_path / '.dream/tool-artifacts').rglob('*.utf8')
                        if path.name == name)
        original.write_bytes(b'altered original')
        with pytest.raises(ArtifactError, match='integrity'):
            WorkerSnapshots(journal).unpack(packed)
    finally:
        journal.close()
        other.close()


@pytest.mark.parametrize('damage', ['pending', 'requests', 'control'])
def test_rehashed_observation_checkpoint_cannot_change_admission(tmp_path, damage):
    journal = create(tmp_path)
    journal.request_started(now_ns=time.time_ns())
    journal.checkpoint(WorkerSnapshots(journal).pack(snapshot()))
    path = journal.path / 'ledger.jsonl'
    journal.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    state = rows[-1]['state']
    if damage == 'pending':
        state['pending'] = None
    elif damage == 'requests':
        state['requests'] += 1
        state['pending']['request'] += 1
    else:
        state['control'] = 'paused'
    row = rows[-1]
    row.pop('sha256')
    row['sha256'] = hashlib.sha256(_encode(row)).hexdigest()
    path.write_text(''.join(json.dumps(item) + '\n' for item in rows))
    with pytest.raises(WorkerCheckpointError, match='corrupt'):
        with WorkerJournal(tmp_path, 'private-session', 'a' * 32):
            pass


def test_terminal_checkpoint_cannot_replace_published_return(tmp_path):
    journal = create(tmp_path)
    try:
        value = WorkerSnapshots(journal).pack(snapshot())
        value['parent_result'] = {'text': 'terminal original', 'is_error': False}
        journal.finish('completed', value)
        with pytest.raises(WorkerCheckpointError, match='terminal'):
            journal.checkpoint(WorkerSnapshots(journal).pack(snapshot()))
        assert journal.inspect()['snapshot']['parent_result']['text'] == 'terminal original'
    finally:
        journal.close()
