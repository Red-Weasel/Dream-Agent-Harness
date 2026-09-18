"""Failure hints never retain arbitrary messages or claim a proven root cause."""
import json

import pytest

from dream.telemetry.turn import TurnTiming


@pytest.mark.parametrize('kind,data,expected', [
    ('tool_result', {'is_error': True, 'content': 'Invalid arguments: PRIVATE'}, 'arguments'),
    ('tool_result', {'is_error': True, 'content': 'Sandbox is unavailable PRIVATE'}, 'sandbox'),
    ('tool_result', {'is_error': True, 'content': 'error while loading shared libraries: PRIVATE'}, 'dependency'),
    ('tool_result', {'is_error': True, 'content': 'No such file or directory: PRIVATE'}, 'workspace'),
    ('tool_result', {'is_error': True, 'content': 'Preview failed PRIVATE'}, 'preview'),
    ('error', 'HTTP 429 rate limit PRIVATE', 'provider_rate_limit'),
    ('error', 'Connection reset PRIVATE', 'provider_connection'),
    ('result', {'is_error': True, 'error': 'PRIVATE'}, 'unknown'),
    ('result', {'subtype': 'interrupted'}, 'interrupted'),
])
def test_failure_signal_is_actionable_and_content_free(kind, data, expected):
    timing = TurnTiming()
    timing.observe_tool(kind, data)
    report = timing.finish('error')['failure_diagnostics']
    assert report['counts'] == {expected: 1}
    assert report['root_cause_verified'] is False
    assert report['raw_content_recorded'] is False
    assert 'PRIVATE' not in json.dumps(report)


def test_success_unknown_flags_and_arbitrary_content_do_not_invent_failures():
    timing = TurnTiming()
    for flag in (False, None, 'true', 1):
        timing.observe_tool('tool_result', {'is_error': flag, 'content': 'sandbox unavailable'})
    timing.observe_tool('result', {'subtype': 'success'})
    assert timing.summary()['failure_diagnostics']['counts'] == {}


def test_bounded_untrusted_payloads_and_frozen_snapshot():
    timing = TurnTiming()
    timing.observe_tool('tool_result', {'is_error': True, 'content': [
        {'type': 'text', 'text': 'PRIVATE ' * 100000},
        {'type': 'image', 'data': 'PRIVATE'},
    ]})
    first = timing.finish('error')
    assert first['failure_diagnostics']['counts'] == {'unknown': 1}
    timing.observe_tool('error', 'rate limit')
    first['failure_diagnostics']['counts']['unknown'] = 999
    assert timing.summary()['failure_diagnostics']['counts'] == {'unknown': 1}
    assert len(json.dumps(timing.summary()['failure_diagnostics'])) < 500


async def test_actual_engine_records_diagnostics_without_raw_error(task_engine):
    from dream.core.backends.base import Event
    engine, _, _ = await task_engine(control='false_done', history=False)
    class Transport:
        model = 'fixture'
        async def ask(self, prompt):
            yield Event('error', 'Connection reset PRIVATE_ENDPOINT')
            yield Event('result', {'subtype': 'error', 'is_error': True})
    engine.backend = Transport()
    events = [e async for e in engine.ask('Synthetic request')]
    timing = next(e.data['stats']['timing'] for e in events if e.kind == 'result')
    assert timing['failure_diagnostics']['counts']['provider_connection'] == 1
    assert 'PRIVATE_ENDPOINT' not in json.dumps(timing)


from test_engine_profile_task import isolated_runtime, task_engine  # noqa: E402,F401
