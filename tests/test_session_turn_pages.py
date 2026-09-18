"""Bounded raw-turn recovery using disposable SQLite and real tool dispatch."""
import json
import asyncio

import pytest

from dream.memory.store import MemoryStore
from dream.memory.working import WorkingMemory
from dream.tools.context import ToolContext, bind_context
from dream.tools.memory_tools import read_session
from test_sdk_review_completion import deny_external_operations
from test_engine_profile_task import isolated_runtime, task_engine, tool_round
from test_compaction import _text_round


@pytest.fixture
def session(tmp_path, monkeypatch):
    from dream import config
    monkeypatch.setattr(config, 'SESSIONS_DIR', tmp_path / 'sessions')
    store = MemoryStore(tmp_path / 'sessions.db')
    store.start_session('past')
    store.start_session('reader')
    context = ToolContext(store, WorkingMemory(store, 'reader'), None, 'reader',
                          workspace=tmp_path)
    try:
        with bind_context(context):
            yield store
    finally:
        store.close()


async def test_long_turn_has_actionable_continuation(session):
    raw = 'Aurora request: compare the drafts. ' + 'Background detail. ' * 160
    raw += 'TAILRESTRICTION: Do not publish, delete, or overwrite the original.'
    tid = session.add_turn('past', 'user', raw)
    default = await read_session.handler({'id': 'past', 'at': tid})
    text = default['content'][0]['text']
    assert raw[:1200] + '…' in text
    assert 'TAILRESTRICTION' not in text
    assert 'offset' in text and 'chars' in text, 'Clipped turn has no paging instructions'
    chunks, offset = [], 0
    while offset is not None:
        result = await read_session.handler({'id': 'past', 'at': tid, 'offset': offset})
        assert not result.get('is_error')
        page = json.loads(result['content'][0]['text'])
        assert page['session_id'] == 'past' and page['turn_id'] == tid
        assert page['start'] == offset and page['total'] == len(raw)
        assert page['end'] - page['start'] == len(page['content']) <= 12000
        chunks.append(page['content'])
        offset = page['next_offset']
    assert ''.join(chunks) == raw
    assert session.session_turns('reader') == []


async def test_raw_unicode_whitespace_reconstruction_and_single_record_fetch(session, monkeypatch):
    raw = ' \t\r\n' + 'A🙂e\u0301中\n  line\r\n' * 130 + '\n\t  '
    tid = session.add_turn('past', 'tool', raw, tool_name='synthetic')
    session.add_turn('past', 'user', 'NEIGHBOR MUST NOT APPEAR')
    before = session.session_turns('past')
    calls = []
    original = MemoryStore.turns_window
    def observed(self, *args):
        calls.append(args)
        return original(self, *args)
    monkeypatch.setattr(MemoryStore, 'turns_window', observed)
    offset, chunks = 0, []
    while offset is not None:
        args = {'id': 'past', 'at': tid, 'chars': 1199, 'window': 20}
        if chunks:
            args['offset'] = offset
        page = json.loads((await read_session.handler(args))['content'][0]['text'])
        assert page['content'] == raw[offset:offset + 1199]
        assert page['start'] == offset and page['total'] == len(raw)
        assert page['end'] == offset + len(page['content'])
        assert page['next_offset'] == (page['end'] if page['end'] < len(raw) else None)
        chunks.append(page['content'])
        offset = page['next_offset']
    assert ''.join(chunks) == raw
    assert calls == [('past', tid, 0)] * len(chunks)
    assert session.session_turns('past') == before
    assert session.session_turns('reader') == []


@pytest.mark.parametrize('arguments', [
    {'offset': 0}, {'at': None, 'offset': 0}, {'at': True, 'offset': 0},
    {'at': 1.0, 'offset': 0}, {'at': '1', 'offset': 0},
    {'at': 0, 'offset': 0}, {'at': -1, 'offset': 0},
    {'at': 2**63, 'offset': 0}, {'at': 1, 'offset': True},
    {'at': 1, 'offset': 0.5}, {'at': 1, 'offset': '0'},
    {'at': 1, 'offset': None}, {'at': 1, 'offset': -1},
    {'at': 1, 'offset': 2**63}, {'at': 1, 'chars': False},
    {'at': 1, 'chars': 1.0}, {'at': 1, 'chars': '1'},
    {'at': 1, 'chars': None}, {'at': 1, 'chars': 0},
    {'at': 1, 'chars': 12001}, {'at': 1, 'chars': []},
])
async def test_invalid_page_arguments_rejected_before_store(session, monkeypatch, arguments):
    calls = []
    def forbidden(*args):
        calls.append(args)
        raise AssertionError('invalid arguments reached store')
    monkeypatch.setattr(MemoryStore, 'get_session', forbidden)
    monkeypatch.setattr(MemoryStore, 'turns_window', forbidden)
    result = await read_session.handler({'id': 'past', **arguments})
    assert result.get('is_error') and 'integer' in result['content'][0]['text']
    assert calls == []


async def test_exact_turn_identity_cannot_fall_forward_to_session_neighbor(session):
    session.start_session('other')
    session.start_session('empty')
    first = session.add_turn('past', 'user', 'first')
    foreign = session.add_turn('other', 'user', 'FOREIGN_SECRET')
    neighbor = session.add_turn('past', 'user', 'NEIGHBOR_SECRET')
    for sid, tid in [('past', foreign), ('past', neighbor + 1),
                     ('past', 2**63 - 1), ('other', first), ('empty', first)]:
        result = await read_session.handler({'id': sid, 'at': tid, 'offset': 0})
        assert result.get('is_error')
        assert f'no turn {tid}' in result['content'][0]['text']
        assert 'SECRET' not in str(result)
    missing = await read_session.handler({'id': 'missing', 'at': first, 'chars': 1})
    assert missing.get('is_error') and 'no session' in str(missing)
    assert 'has no turns' in str(await read_session.handler({'id': 'empty'}))


@pytest.mark.parametrize('raw', ['', ' \t🙂\n '])
async def test_end_empty_page_and_offset_beyond_end(session, raw):
    tid = session.add_turn('past', 'user', raw)
    result = await read_session.handler({'id': 'past', 'at': tid, 'offset': len(raw)})
    assert json.loads(result['content'][0]['text']) == {
        'session_id': 'past', 'turn_id': tid, 'start': len(raw), 'end': len(raw),
        'total': len(raw), 'content': '', 'next_offset': None}
    for offset in [len(raw) + 1, 2**63 - 1]:
        result = await read_session.handler({'id': 'past', 'at': tid, 'offset': offset})
        assert result.get('is_error') and f'0 to {len(raw)}' in str(result)
    if raw:
        page = json.loads((await read_session.handler(
            {'id': 'past', 'at': tid, 'offset': 2, 'chars': 1}))['content'][0]['text'])
        assert page['content'] == '🙂' and page['end'] == page['next_offset'] == 3


async def test_default_short_output_legacy_window_clamps_and_coercion(session, monkeypatch):
    tid = session.add_turn('past', 'user', '  short\nline  ')
    row = session.turns_window('past')[0]
    sess = session.get_session('past')
    expected = (f"Session past — (untitled) — started {sess['started_at']}\n"
                f"Stored turn IDs {tid}–{tid}; session has 1 records:\n\n"
                f"[{tid}] user {row['ts']}\n    short\n    line")
    assert (await read_session.handler({'id': 'past'}))['content'][0]['text'] == expected
    calls = []
    original = MemoryStore.turns_window
    def observed(self, *args):
        calls.append(args)
        return original(self, *args)
    monkeypatch.setattr(MemoryStore, 'turns_window', observed)
    for supplied, expected_window in [(None, 6), (-3, 1), (0, 1), ('30', 20), (2.9, 2)]:
        result = await read_session.handler({'id': 'past', 'at': str(tid), 'window': supplied})
        assert not result.get('is_error') and ' ◀' in str(result)
        assert calls[-1] == ('past', tid, expected_window)
    invalid = await read_session.handler({'id': 'past', 'window': 'bad'})
    assert invalid.get('is_error') and 'at and window must be integers' in str(invalid)


async def test_display_indentation_clipping_hint_points_to_raw_start(session):
    raw = ' x\n' * 250
    assert len(raw) < 1200 < len(raw.strip().replace('\n', '\n    '))
    tid = session.add_turn('past', 'assistant', raw)
    text = (await read_session.handler({'id': 'past'}))['content'][0]['text']
    call = text.split('Read raw turn pages: read_session(', 1)[1].split('). Read more', 1)[0]
    arguments = json.loads(call)
    assert arguments == {'id': 'past', 'at': tid, 'offset': 0, 'chars': 12000}
    page = json.loads((await read_session.handler(arguments))['content'][0]['text'])
    assert page['content'] == raw and page['next_offset'] is None


async def test_engine_native_schema_and_session_bound_tool_path(task_engine):
    e, transport, permissions = await task_engine(history=False)
    e.store.start_session('archived')
    raw = ' \n' + '🙂 archive context\n' * 90 + 'TAILRESTRICTION: Preserve the original.\t '
    tid = e.store.add_turn('archived', 'user', raw)
    arguments = {'id': 'archived', 'at': tid, 'offset': 1200, 'chars': 1200}
    transport.rounds = [tool_round('tool_schema', {'name': 'read_session'}, 0),
                        tool_round('read_session', arguments, 1),
                        tool_round('read_session', {**arguments, 'offset': True}, 2),
                        tool_round('read_session', {'id': 'archived', 'chars': 20}, 3),
                        _text_round('Read the archived excerpt; no action performed.')]
    before = e.store.session_turns('archived')
    async with asyncio.timeout(15):
        events = [event async for event in e.ask('Read the requested archived turn page only.')]
    results = [event.data for event in events
               if event.kind == 'tool_result' and event.data['name'] == 'read_session']
    assert len(results) == 3
    page = json.loads(results[0]['content'])
    assert not results[0]['is_error'] and page['content'] == raw[1200:2400]
    assert page['session_id'] == 'archived' and page['turn_id'] == tid
    assert 'TAILRESTRICTION' in page['content'] and page['next_offset'] is None
    assert results[1]['is_error'] and 'Invalid arguments' in results[1]['content']
    assert results[2]['is_error'] and 'requires at' in results[2]['content']
    schema = next(tool['function']['parameters'] for tool in transport.payloads[1]['tools']
                  if tool['function']['name'] == 'read_session')
    assert schema['properties']['offset']['type'] == 'integer'
    assert schema['properties']['chars']['maximum'] == 12000
    assert e.store.session_turns('archived') == before
    assert e.session_id != 'archived' and permissions == []
    assert any(event.kind == 'result' and not event.data['is_error'] for event in events)
