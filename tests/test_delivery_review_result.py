"""A completed HTTP response cannot erase its scheduled review's unmet checks."""
from types import SimpleNamespace

import pytest

from dream.core.loop import AutonomousLoop
from test_schema_deferral import _FakeClient, _backend, _text_round, _tool_round
from test_verifier_fork import _done_tool, _with_verifier


@pytest.mark.parametrize('report,failed,state,subtype', [
    ('PASS', False, 'pass', 'success'),
    ('Required export button is missing.', False, 'needs_attention', 'verification_findings'),
    ('PASS but I could not inspect playback.', False, 'needs_attention', 'verification_findings'),
    ('Transport unavailable.', True, 'unverified', 'verification_unverified'),
    ('PASS', True, 'unverified', 'verification_unverified'),
    ('  ', False, 'unverified', 'verification_unverified'),
])
async def test_review_controls_terminal_result(monkeypatch, report, failed, state, subtype):
    b, runs = _with_verifier(monkeypatch, report, failed=failed)
    b._client = _FakeClient([_tool_round('done', '{"path":"page.html"}'), _text_round('Complete.')])
    events = [e async for e in b.ask('Build a page with an export button.')]
    result = events[-1].data
    assert result['subtype'] == subtype
    assert result['is_error'] is (subtype != 'success')
    assert result['delivery_review']['status'] == state
    assert result['delivery_review']['path'] == 'page.html'
    assert len(runs) == 1 and len(b._client.payloads) == 2  # no automatic repair loop
    assert any(e.kind == 'assistant_done' and e.data == 'Complete.' for e in events)
    if state == 'needs_attention':
        assert any(e.kind == 'system' and report in str(e.data) for e in events)


async def test_scheduled_review_without_reviewer_is_unverified():
    b = _backend([_done_tool()])
    b._client = _FakeClient([_tool_round('done', '{"path":"page.html"}'), _text_round('Ready.')])
    events = [e async for e in b.ask('Build a page.')]
    result = events[-1].data
    assert result['is_error'] and result['subtype'] == 'verification_unverified'
    assert result['delivery_review']['status'] == 'unverified'
    assert b._pending_findings and 'no verifier' in b._pending_findings


async def test_plain_chat_after_failed_review_has_no_stale_completion_failure(monkeypatch):
    b, _ = _with_verifier(monkeypatch, 'Export button missing.')
    b._client = _FakeClient([_tool_round('done', '{"path":"page.html"}'), _text_round('Ready.')])
    first = [e async for e in b.ask('Build a page.')]
    assert first[-1].data['is_error']
    b._client = _FakeClient([_text_round('A hash maps data to a fixed-size representation.')])
    second = [e async for e in b.ask('Stop the page task. Explain hashes.')]
    assert second[-1].data['subtype'] == 'success'
    assert second[-1].data['delivery_review']['status'] == 'not_requested'
    assert not second[-1].data['is_error']


async def test_known_unmet_requirement_cannot_be_accepted_by_loop(monkeypatch, tmp_path):
    b, _ = _with_verifier(monkeypatch, 'Export button missing.')
    b._client = _FakeClient([_tool_round('done', '{"path":"page.html"}'),
                            _text_round('STATUS: DONE\nNEXT: nothing')])
    loop = AutonomousLoop(SimpleNamespace(ask=b.ask, workspace=tmp_path), state_dir=tmp_path / 'runs')
    with pytest.raises(RuntimeError, match='verification_findings'):
        await loop._drive('Build a page with an export button.')


async def test_existing_round_limit_keeps_precedence(monkeypatch):
    from dream.core.backends import openai_compat
    monkeypatch.setattr(openai_compat, '_MAX_TOOL_ROUNDS', 1)
    b, _ = _with_verifier(monkeypatch, 'Missing export button.')
    b._client = _FakeClient([_tool_round('done', '{"path":"page.html"}')])
    result = [e async for e in b.ask('Build page.')][-1].data
    assert result['is_error'] and result['subtype'] == 'tool_round_limit'
    assert result['delivery_review']['status'] == 'needs_attention'


def test_steering_retains_original_requirements_in_review_and_filing():
    b = _backend()
    b.messages.extend([
        {'role': 'user', 'name': 'dream_prior_user', 'content': 'OLD TASK'},
        {'role': 'user', 'name': 'dream_active_user', 'content': 'Build a page with export and accessible controls.'},
        {'role': 'assistant', 'content': 'Export is built.'},
        {'role': 'user', 'name': 'dream_steering_user', 'content': 'Make the heading blue.'},
        {'role': 'assistant', 'content': 'Heading changed.'},
    ])
    for text in [b._verification_prompt('page.html'), b._turn_text('Build a page with export and accessible controls.')]:
        assert 'Build a page with export and accessible controls.' in text
        assert 'Make the heading blue.' in text
        assert 'OLD TASK' not in text
    assert 'Export is built.' in b._turn_text('original')


async def test_abandoned_turn_cannot_schedule_old_artifact_review_on_new_task(monkeypatch):
    b, runs = _with_verifier(monkeypatch, 'Old page lacks export.')
    b._client = _FakeClient([_tool_round('done', '{"path":"old.html"}'), _text_round('Ready.')])
    iterator = b.ask('Build old page.')
    async for event in iterator:
        if event.kind == 'tool_result':
            break
    await iterator.aclose()
    assert b._verify_at_turn_end is None and not runs
    b._client = _FakeClient([_text_round('A hash maps input data.')])
    events = [e async for e in b.ask('Cancel the page task. Explain hashing.')]
    assert not runs
    assert events[-1].data['subtype'] == 'success'
    assert events[-1].data['delivery_review']['status'] == 'not_requested'
