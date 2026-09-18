"""Session recovery must not ingest the current request's growing tool log."""
import json

import pytest

from dream.tools.memory_tools import read_session, recall_sessions
from test_session_turn_pages import session


@pytest.mark.parametrize('arguments', [{}, {'offset': 0}, {'chars': 12000}])
async def test_current_request_log_is_not_retrievable(session, arguments):
    session.add_turn('reader', 'user', 'Earlier request')
    prior = session.add_turn('reader', 'assistant', 'Earlier work is recoverable')
    boundary = session.add_turn('reader', 'user', 'Continue the earlier work')
    for _ in range(3):
        live = session.add_turn('reader', 'tool_result', 'LIVE_RETRIEVAL_ECHO', 'read_session')
        result = await read_session.handler({'id': 'reader', 'at': live, **arguments})
        assert result.get('is_error') and 'current request' in str(result)
        assert 'LIVE_RETRIEVAL_ECHO' not in str(result)
        session.add_turn('reader', 'tool_result', str(result), 'read_session')
        visible = await read_session.handler({'id': 'reader', 'window': 20})
        assert 'Earlier work is recoverable' in str(visible)
        assert 'LIVE_RETRIEVAL_ECHO' not in str(visible)
        centered = await read_session.handler({'id': 'reader', 'at': prior, 'window': 20})
        assert 'LIVE_RETRIEVAL_ECHO' not in str(centered)
    page = json.loads((await read_session.handler(
        {'id': 'reader', 'at': boundary, 'offset': 0}))['content'][0]['text'])
    assert page['content'] == 'Continue the earlier work'
    session.add_turn('reader', 'user', 'Next request')
    recovered = await read_session.handler({'id': 'reader', 'at': live, 'offset': 0})
    assert json.loads(recovered['content'][0]['text'])['content'] == 'LIVE_RETRIEVAL_ECHO'


async def test_current_session_without_user_has_no_recoverable_log(session):
    live = session.add_turn('reader', 'tool_result', 'LIVE_RETRIEVAL_ECHO', 'read_session')
    assert 'LIVE_RETRIEVAL_ECHO' not in str(await read_session.handler({'id': 'reader'}))
    result = await read_session.handler({'id': 'reader', 'at': live, 'offset': 0})
    assert result.get('is_error')


async def test_recall_excludes_current_before_limit(session):
    session.add_turn('past', 'user', 'UNIQUEARCHIVE')
    session.add_turn('reader', 'user', 'UNIQUEARCHIVE')
    with session._lock:
        session._conn.execute("UPDATE sessions SET started_at='2200-01-01' WHERE id='reader'")
        session._conn.commit()
    assert session.recent_sessions(1)[0]['id'] == 'reader'
    assert session.search_turns('UNIQUEARCHIVE', 1)[0]['session_id'] == 'reader'
    listing = await recall_sessions.handler({'limit': 1})
    assert '"id": "past"' in str(listing) and '"id": "reader"' not in str(listing)
    hits = await recall_sessions.handler({'query': 'UNIQUEARCHIVE', 'limit': 1})
    assert 'session past' in str(hits) and 'session reader' not in str(hits)


async def test_large_default_and_explicit_pages_preserve_raw_unicode(session):
    raw = ' \n' + '🙂e\u0301中\r\n' * 2500
    tid = session.add_turn('past', 'tool_result', raw, 'run_bash')
    first = json.loads((await read_session.handler(
        {'id': 'past', 'at': tid, 'offset': 0}))['content'][0]['text'])
    assert first['content'] == raw[:12000] and first['next_offset'] == 12000
    second = json.loads((await read_session.handler(
        {'id': 'past', 'at': tid, 'offset': 12000, 'chars': 12000}))['content'][0]['text'])
    assert first['content'] + second['content'] == raw
    assert second['next_offset'] is None


@pytest.mark.parametrize('raw', ['\n' * 12000, '\x00\x01\t"\\🙂' * 3000],
                         ids=['newlines', 'escaped-controls'])
@pytest.mark.parametrize('cap', [24000, 700])
async def test_serialized_pages_fit_transport_cap(session, monkeypatch, raw, cap):
    from dream import config
    monkeypatch.setattr(config, 'TOOL_RESULT_CAP', cap, raising=False)
    sid = ' archive "\\\n🙂 ' * 8
    session.start_session(sid)
    tid = session.add_turn(sid, 'tool_result', raw, 'run_bash')
    parts, offset = [], 0
    while offset is not None:
        result = await read_session.handler({'id': sid, 'at': tid, 'offset': offset})
        assert not result.get('is_error')
        text = result['content'][0]['text']
        assert len(text) <= cap
        page = json.loads(text)
        assert page['session_id'] == sid and page['start'] == offset
        assert page['content'] == raw[offset:page['end']]
        assert page['end'] > offset
        parts.append(page['content'])
        offset = page['next_offset']
    assert ''.join(parts) == raw


async def test_unrepresentable_page_envelope_is_explicit_error(session, monkeypatch):
    from dream import config
    monkeypatch.setattr(config, 'TOOL_RESULT_CAP', 200, raising=False)
    sid = 'opaque-session-id' * 30
    session.start_session(sid)
    tid = session.add_turn(sid, 'user', 'detail')
    result = await read_session.handler({'id': sid, 'at': tid, 'offset': 0})
    assert result.get('is_error') and 'DREAM_TOOL_RESULT_CAP' in str(result)
