"""Observed tool outcomes prepare calibration without recording tool content."""
import json
from dream import config
from dream.core.backends.base import Event
from dream.telemetry.turn import TurnTiming
from test_engine_profile_task import isolated_runtime, task_engine  # noqa: F401


def test_explicit_outcomes_and_unknown_are_separate_and_content_free():
    timing = TurnTiming()
    for flag in (True, False, None, 'false'):
        timing.observe_tool('tool_use', {'name': 'read_file', 'id': 'PRIVATE_ID', 'input': 'PRIVATE_PATH'})
        timing.observe_tool('tool_result', {'name': 'read_file', 'is_error': flag, 'content': 'PRIVATE_RESULT'})
    data = timing.summary()['tool_outcomes']
    assert data['use_events'] == 4 and data['result_events'] == 4
    assert data['reported_errors'] == data['reported_successes'] == 1
    assert data['unreported_outcomes'] == 2
    assert data['categories']['read']['reported_errors'] == 1
    assert data['task_success'] is None
    assert 'PRIVATE' not in json.dumps(data)


def test_tool_metadata_is_bounded_and_frozen_without_inventing_correlations():
    timing = TurnTiming()
    for i in range(1000):
        timing.observe_tool('tool_result', {'name': f'private-tool-{i}', 'is_error': True})
    timing.observe_tool('tool_use', {'name': 'run_bash'})
    first = timing.finish('interrupted')
    assert len(first['tool_outcomes']['categories']) <= 7
    assert first['tool_outcomes']['categories']['other']['reported_errors'] == 1000
    first['tool_outcomes']['categories']['other']['reported_errors'] = 0
    timing.observe_tool('tool_result', {'name': 'run_bash', 'is_error': False})
    final = timing.summary()['tool_outcomes']
    assert final['reported_errors'] == 1000 and final['reported_successes'] == 0
    assert final['use_events'] == 1  # Result events do not imply matching dispatches.
    assert 'private-tool-' not in json.dumps(final)


def test_delivery_review_status_is_reported_separately_from_task_success():
    timing = TurnTiming()
    assert timing.summary()['delivery_review']['status'] == 'unreported'
    timing.observe_delivery({'delivery_review': {'status': 'needs_attention', 'path': 'PRIVATE_PATH', 'findings': 'PRIVATE_FINDINGS'}})
    report = timing.finish('error')
    assert report['delivery_review'] == {'status': 'needs_attention', 'source': 'protocol_result'}
    assert 'PRIVATE' not in json.dumps(report)


async def test_real_engine_persists_tool_evidence_with_frozen_model_settings(task_engine):
    engine, _, _ = await task_engine(control='false_done', history=False)
    class Transport:
        model = 'fixture'
        async def ask(self, prompt):
            yield Event('tool_use', {'id': 'one', 'name': 'run_bash', 'input': {'command': 'PRIVATE_COMMAND'}})
            yield Event('tool_result', {'id': 'one', 'name': 'run_bash', 'is_error': True, 'content': 'PRIVATE_ERROR'})
            yield Event('tool_use', {'id': 'two', 'name': 'see', 'input': {'path': 'PRIVATE_IMAGE'}})
            yield Event('tool_result', {'id': 'two', 'name': 'see', 'content': 'PRIVATE_IMAGE_RESULT'})
            yield Event('result', {'subtype': 'success', 'delivery_review': {'status': 'not_requested'}})
    engine.backend = Transport()
    events = [event async for event in engine.ask('Fixture request')]
    path = config.LOG_DIR / 'runtime' / f'{engine.session_id}.jsonl'
    saved = [json.loads(line) for line in path.read_text().splitlines()]
    row = next(row for row in saved if row['event'] == 'turn_timing')
    assert row['configuration']['source'] == 'native_configuration_only'
    assert row['tool_outcomes']['reported_errors'] == 1
    assert row['tool_outcomes']['unreported_outcomes'] == 1
    assert row['tool_outcomes']['categories']['shell']['reported_errors'] == 1
    assert row['delivery_review']['status'] == 'not_requested'
    assert row['outcome'] == 'completed'  # Protocol completion does not erase tool errors.
    assert 'PRIVATE_' not in json.dumps(row)
    terminal = next(event for event in events if event.kind == 'result')
    assert terminal.data['stats']['timing']['tool_outcomes']['reported_errors'] == 1
