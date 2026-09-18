"""Recover session identity from listings using real stores, handlers and Engine."""
import asyncio
import json
from json import loads as decode_json

import pytest

from dream.memory import store as store_module
from dream.tools.memory_tools import recall_sessions, read_session
from test_compaction import _text_round
from test_engine_profile_task import isolated_runtime, task_engine, tool_round
from test_sdk_review_completion import deny_external_operations
from test_session_turn_pages import session


def advertised_calls(text, prefix='Open: read_session('):
    """Consume the actual JSON argument records, including escaped identifiers."""
    calls = []
    for suffix in text.split(prefix)[1:]:
        args, end = json.JSONDecoder().raw_decode(suffix)
        assert suffix[end:].startswith(')')
        calls.append(args)
    return calls


async def test_recent_listing_opens_duplicate_untitled_and_empty_sessions(session, monkeypatch):
    times = iter(['2100-01-01T00:00:01', '2100-01-01T00:00:02',
                  '2100-01-01T00:00:03', '2100-01-01T00:00:04'])
    with monkeypatch.context() as clock:
        clock.setattr(store_module, '_now', lambda: next(times))
        session.start_session('duplicate-a', 'Same title')
        session.start_session('duplicate-b', 'Same title')
        session.start_session('quoted"\\\n雪')
        session.start_session('empty')
    session.add_turn('duplicate-a', 'user', 'First distinct request.')
    session.add_turn('duplicate-b', 'user', 'Second distinct request.')
    session.add_turn('quoted"\\\n雪', 'user', 'Third distinct request.')
    before = session.recent_sessions(20)
    result = await recall_sessions.handler({'limit': 4})
    assert not result.get('is_error')
    text = result['content'][0]['text']
    calls = advertised_calls(text)
    assert calls == [{'id': 'empty'}, {'id': 'quoted"\\\n雪'},
                     {'id': 'duplicate-b'}, {'id': 'duplicate-a'}]
    for args, expected in zip(calls, ['has no turns', 'Third distinct request.',
                                     'Second distinct request.', 'First distinct request.']):
        opened = await read_session.handler(args)
        assert not opened.get('is_error') and expected in opened['content'][0]['text']
    assert session.recent_sessions(20) == before
    assert session.session_turns('reader') == []


@pytest.mark.parametrize('identifier', [' retained ', '\tretained\n', '   '])
async def test_advertised_ids_are_opaque_and_never_fall_back_to_trimmed_session(session, identifier):
    session.start_session(identifier, 'Exact session')
    turn = session.add_turn(identifier, 'user', 'EXACT CONTENT')
    session.start_session(identifier.strip(), 'Trimmed decoy')
    session.add_turn(identifier.strip(), 'user', 'WRONG CONTENT')
    listing = (await recall_sessions.handler({'limit': 100}))['content'][0]['text']
    args = next(args for args in advertised_calls(listing) if args['id'] == identifier)
    opened = (await read_session.handler(args))['content'][0]['text']
    assert 'EXACT CONTENT' in opened and 'WRONG CONTENT' not in opened
    page = json.loads((await read_session.handler({**args, 'at': turn, 'offset': 0}))['content'][0]['text'])
    assert page['session_id'] == identifier and page['content'] == 'EXACT CONTENT'
    # An absent exact ID must not silently become a different stored ID.
    missing = await read_session.handler({'id': identifier + ' '})
    assert missing.get('is_error') and 'no session' in missing['content'][0]['text']


async def test_recent_listing_limits_empty_store_and_query_compatibility(session, monkeypatch, tmp_path):
    # The fixture's existing sessions are older than these explicit timestamps.
    times = iter(['2100-01-01T00:00:01', '2100-01-01T00:00:02'])
    with monkeypatch.context() as clock:
        clock.setattr(store_module, '_now', lambda: next(times))
        session.start_session('older', 'Older')
        session.start_session('newer', 'Newer')
    session.end_session('older', 'Saved older summary.')
    turn = session.add_turn('older', 'user', 'UNIQUERECOVERY request.')
    limited = await recall_sessions.handler({'limit': 1})
    assert advertised_calls(limited['content'][0]['text']) == [{'id': 'newer'}]
    assert 'Saved older summary.' not in limited['content'][0]['text']
    all_text = (await recall_sessions.handler({'limit': 2}))['content'][0]['text']
    assert 'Saved older summary.' in all_text
    query = (await recall_sessions.handler({'query': 'UNIQUERECOVERY'}))['content'][0]['text']
    assert f'session older — Older — turn {turn}' in query
    opened = await read_session.handler({'id': 'older', 'at': turn})
    assert 'UNIQUERECOVERY request.' in opened['content'][0]['text']
    missing = await recall_sessions.handler({'query': 'ABSENTWORD'})
    assert "No past session mentions 'ABSENTWORD'." in missing['content'][0]['text']
    # An actual empty store, not a substituted recent_sessions result.
    empty = store_module.MemoryStore(tmp_path / 'empty-list.db')
    from dream.memory.working import WorkingMemory
    from dream.tools.context import ToolContext, bind_context
    try:
        with bind_context(ToolContext(empty, WorkingMemory(empty, 'none'), None, 'none')):
            result = await recall_sessions.handler({})
        assert result['content'][0]['text'] == 'No past sessions recorded yet.'
    finally:
        empty.close()


async def test_advertised_listing_to_unicode_tail_pages_is_read_only(session):
    raw = ' \t\n' + 'Prior tool detail 🙂e\u0301中\r\n' * 90
    raw += 'FINALCONSTRAINT: inspect existing artifact; do not repeat the edit.\t '
    turn = session.add_turn('past', 'user', raw)
    before = session.session_turns('past')
    listing = (await recall_sessions.handler({}))['content'][0]['text']
    args = next((args for args in advertised_calls(listing) if args['id'] == 'past'), None)
    assert args is not None, 'Recent session has no usable read_session locator'
    opened = (await read_session.handler(args))['content'][0]['text']
    pages = advertised_calls(opened, 'Read raw turn pages: read_session(')
    assert len(pages) == 1 and pages[0]['at'] == turn
    args, parts = pages[0], []
    while True:
        page = json.loads((await read_session.handler(args))['content'][0]['text'])
        assert page['session_id'] == 'past' and page['turn_id'] == turn
        parts.append(page['content'])
        if page['next_offset'] is None:
            break
        args = {**args, 'offset': page['next_offset']}
    assert ''.join(parts) == raw
    assert session.session_turns('past') == before
    assert session.session_turns('reader') == []


async def test_engine_consumes_listing_locator_and_raw_page_continuations(task_engine):
    e, transport, permissions = await task_engine(history=False)
    e.store.start_session('archived"\\雪', 'Previous task')
    raw = ' \n' + 'Old tool result 🙂e\u0301中\r\n' * 90
    raw += 'FINALCONSTRAINT: preserve shipping.py and perform no edit.\t '
    archived_turn = e.store.add_turn('archived"\\雪', 'user', raw)
    before = e.store.session_turns('archived"\\雪')
    files = {path.name: path.read_bytes() for path in e.workspace.iterdir()}
    request = 'Inspect the previous task only. Preserve all files; do not replay any action.'
    recovered, locator = [], None
    original_stream = transport.stream

    def stream(method, url, json=None):
        nonlocal locator
        index = len(transport.payloads)
        results = [message for message in json['messages'] if message['role'] == 'tool']
        if index == 0:
            reply = tool_round('tool_schema', {'name': 'recall_sessions'}, index)
        elif index == 1:
            reply = tool_round('recall_sessions', {'limit': 8}, index)
        elif index == 2:
            calls = advertised_calls(results[-1]['content'])
            locator = next((args for args in calls if args['id'] == 'archived"\\雪'), None)
            assert locator is not None, 'Engine received no advertised recovery locator'
            reply = tool_round('tool_schema', {'name': 'read_session'}, index)
        elif index == 3:
            reply = tool_round('read_session', locator, index)
        elif index == 4:
            pages = advertised_calls(results[-1]['content'], 'Read raw turn pages: read_session(')
            assert len(pages) == 1
            locator = pages[0]
            reply = tool_round('read_session', locator, index)
        else:
            page = decode_json(results[-1]['content'])
            assert page['turn_id'] == archived_turn
            recovered.append(page['content'])
            if page['next_offset'] is None:
                reply = _text_round('Recovered the existing task. No edit performed.')
            else:
                locator = {**locator, 'offset': page['next_offset']}
                reply = tool_round('read_session', locator, index)
        assert index < 10, 'Recovery exceeded its finite scripted request bound'
        transport.rounds.append(reply)
        return original_stream(method, url, json=json)

    transport.rounds = []
    transport.stream = stream
    async with asyncio.timeout(15):
        events = [event async for event in e.ask(request)]
    assert ''.join(recovered) == raw
    results = [event.data for event in events if event.kind == 'tool_result']
    assert results and all(not result['is_error'] for result in results)
    assert e.store.session_turns('archived"\\雪') == before
    assert {path.name: path.read_bytes() for path in e.workspace.iterdir()} == files
    assert permissions == []
    (e.workspace.parent / 'session-recovery-engine-evidence.json').write_text(json.dumps({
        'request': request, 'payloads': transport.payloads, 'recovered': ''.join(recovered),
        'archived_unchanged': e.store.session_turns('archived"\\雪') == before,
        'permissions': permissions,
    }, ensure_ascii=False, indent=2))
    # Admission appends the existing source marker after the complete request.
    assert all(any(message['role'] == 'user'
                   and message['content'].split('\n\n[id:', 1)[0] == request
                   for message in payload['messages']) for payload in transport.payloads)
    assert any(turn['role'] == 'user' and turn['content'] == request
               for turn in e.store.session_turns(e.session_id))
    assert any(event.kind == 'result' and not event.data['is_error'] for event in events)
