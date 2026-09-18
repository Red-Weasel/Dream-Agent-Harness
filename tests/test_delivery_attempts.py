"""A failed Studio handoff cannot be erased by a terminal success claim."""
import json

import pytest

from dream.core.backends import openai_compat
from test_schema_deferral import _FakeClient, _text_round, _tool_round
from test_verifier_fork import _with_verifier


@pytest.mark.parametrize('paths', [ ['broken.html'], ['page.html', 'broken.html'] ])
async def test_failed_latest_delivery_invalidates_prior_review(monkeypatch, paths):
    b, reviews = _with_verifier(monkeypatch)
    b._client = _FakeClient([*[_tool_round('done', json.dumps({'path': p})) for p in paths], _text_round('STATUS: DONE')])
    result = [e async for e in b.ask('Deliver the page')][-1].data
    assert result['subtype'] == 'delivery_failed' and result['is_error']
    assert result['delivery_attempt']['status'] == 'failed'
    assert reviews == [] and b._verify_at_turn_end is None and b._last_done_path is None


@pytest.mark.parametrize('failure', ['bad_arguments', 'disabled', 'exception', 'declined'])
async def test_all_failed_delivery_paths_prevent_success(monkeypatch, failure):
    b, reviews = _with_verifier(monkeypatch)
    arguments = '{invalid' if failure == 'bad_arguments' else '{"path":"page.html"}'
    if failure == 'disabled':
        from dream import extensions
        monkeypatch.setattr(extensions, 'tool_enabled', lambda tool: tool.name != 'done')
    elif failure == 'exception':
        async def crash(args):
            raise ValueError('fixture handler failure')
        monkeypatch.setattr(b.tools_by_name['done'], 'handler', crash)
    elif failure == 'declined':
        async def refuse(name, args):
            return False
        b.permission_cb = refuse
        from dream.core import policy
        monkeypatch.setattr(policy, 'capability', lambda name: 'fixture_write')
    b._client = _FakeClient([_tool_round('done', arguments), _text_round('Done')])
    result = [e async for e in b.ask('Deliver the page')][-1].data
    assert result['subtype'] == 'delivery_failed' and result['is_error']
    assert not reviews


async def test_clean_retry_and_new_turn_resolve_delivery_failure(monkeypatch):
    b, reviews = _with_verifier(monkeypatch)
    b._client = _FakeClient([_tool_round('done', '{"path":"broken.html"}'),
                            _tool_round('done', '{"path":"page.html"}'), _text_round('Done')])
    result = [e async for e in b.ask('Deliver the page')][-1].data
    assert result['subtype'] == 'success'
    assert result['delivery_attempt']['status'] == 'prepared' and len(reviews) == 1
    b._client = _FakeClient([_text_round('A new unrelated answer')])
    result = [e async for e in b.ask('Explain a hash')][-1].data
    assert result['subtype'] == 'success' and result['delivery_attempt']['status'] == 'not_requested'


async def test_disabled_auto_review_does_not_ignore_failed_delivery(monkeypatch):
    monkeypatch.setattr(openai_compat, '_AUTO_VERIFY', False)
    b, reviews = _with_verifier(monkeypatch)
    b._client = _FakeClient([_tool_round('done', '{"path":"broken.html"}'), _text_round('Done')])
    result = [e async for e in b.ask('Deliver the page')][-1].data
    assert result['subtype'] == 'delivery_failed' and not reviews
