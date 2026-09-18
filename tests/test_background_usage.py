"""Late filing usage updates session totals without replaying a lead turn."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from dream.core.backends.base import Event
from dream.core.engine import Engine
from dream.core.profiles import PROFILES
from dream.telemetry.meter import InferenceMeter
from dream.telemetry.runtime import RunMeter
from dream.tools.context import ToolContext, bind_context
from test_schema_deferral import _backend, _tool


def usage_event(session='fixture'):
    return Event('background_usage', {'session_id': session, 'turn': 4,
                                     'usage': {'prompt_tokens': 17, 'completion_tokens': 3},
                                     'duration_s': 1.5})


def test_inference_meter_adds_background_usage_without_changing_foreground():
    meter = InferenceMeter()
    meter.turn_start()
    meter.feed('result', {'usage': {'input_tokens': 10, 'output_tokens': 5}, 'duration_ms': 2000})
    last = copy.deepcopy(meter.last_turn)
    meter.turn_start()
    event = usage_event()
    meter.feed(event.kind, event.data)
    summary = meter.snapshot()
    assert summary['session']['in_tokens'] == 27
    assert summary['session']['out_tokens'] == 8
    assert summary['session']['turns'] == 1
    assert summary['session']['gen_s'] == 3.5
    assert summary['session']['avg_tps'] == pytest.approx(8 / 3.5)
    assert meter.state == 'waiting' and meter.ttft_s is None
    assert meter.last_turn == last and meter.prompt_tokens is None


def test_background_usage_rejects_invalid_counts_and_does_not_make_a_turn():
    meter = InferenceMeter()
    meter.feed('background_usage', {'usage': {'prompt_tokens': -2, 'completion_tokens': True},
                                    'duration_s': float('nan')})
    assert meter.snapshot()['session'] == {'turns': 0, 'in_tokens': 0, 'out_tokens': 0, 'gen_s': 0.0, 'avg_tps': None}
    assert meter.last_turn is None


def test_engine_adds_only_matching_session_tokens_and_forwards_metadata():
    emitted = []
    engine = SimpleNamespace(session_id='fixture', session_tokens=10, last_context_tokens=42,
                             total_cost_usd=7, emit=emitted.append)
    event = usage_event()
    Engine._background_event(engine, event)
    assert engine.session_tokens == 30
    assert engine.last_context_tokens == 42 and engine.total_cost_usd == 7
    Engine._background_event(engine, usage_event('another-session'))
    assert engine.session_tokens == 30 and emitted == [event]


@pytest.mark.parametrize('finish_reason,expected_state', [('stop', 'completed'), ('length', 'failed')])
async def test_completed_or_failed_filing_counts_received_usage_once(tmp_path, monkeypatch, finish_reason, expected_state):
    from dream.core.backends import openai_compat
    monkeypatch.setattr(openai_compat, '_AUTO_FILE', True)
    backend = _backend(subagents={'filer': SimpleNamespace(description='filer', prompt='file', tool_names=[])})
    meter = InferenceMeter()
    emitted, finished = [], asyncio.Event()
    engine = SimpleNamespace(session_id='fixture', session_tokens=10, last_context_tokens=42, total_cost_usd=7)

    def emit(event):
        emitted.append(event)
        meter.feed(event.kind, event.data)
        if event.kind == 'background_work' and event.data['kind'] == expected_state:
            finished.set()

    engine.emit = emit
    backend.enable_background_filing(lambda event: Engine._background_event(engine, event))
    backend._idle_work.idle_grace = 0
    backend._msg_seq = 4
    backend.runtime_meter = RunMeter('fixture', 1, PROFILES['lean'])

    async def post(*args, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {
            'usage': {'prompt_tokens': 17, 'completion_tokens': 3},
            'choices': [{'message': {'content': 'NOTHING'}, 'finish_reason': finish_reason}],
        })

    backend._client = SimpleNamespace(post=post)
    try:
        context = ToolContext(None, None, None, 'fixture', workspace=tmp_path, runtime_meter=backend.runtime_meter)
        with bind_context(context):
            await backend._finish_filing('source turn')
        backend._msg_seq = 5
        await asyncio.wait_for(finished.wait(), 2)
    finally:
        await backend.close_background()
    usage = [event.data for event in emitted if event.kind == 'background_usage']
    assert len(usage) == 1 and usage[0]['session_id'] == 'fixture' and usage[0]['turn'] == 4
    assert usage[0]['usage'] == {'prompt_tokens': 17, 'completion_tokens': 3}
    assert engine.session_tokens == 30 and engine.last_context_tokens == 42
    assert meter.session_in_tokens == 17 and meter.session_out_tokens == 3 and meter.session_turns == 0
    assert backend._delegated_usage == {'prompt_tokens': 0, 'completion_tokens': 0}


async def test_cancelled_filing_reports_prior_response_usage_once(tmp_path, monkeypatch):
    from dream.core.backends import openai_compat
    monkeypatch.setattr(openai_compat, '_AUTO_FILE', True)
    started = asyncio.Event()
    tool = _tool('fixture_wait')

    async def wait(args):
        started.set()
        await asyncio.Event().wait()

    tool.handler = wait
    backend = _backend([tool], subagents={'filer': SimpleNamespace(description='filer', prompt='file', tool_names=['fixture_wait'])})
    emitted = []
    backend.enable_background_filing(emitted.append)
    backend._idle_work.idle_grace = 0
    backend._msg_seq = 9
    backend.runtime_meter = RunMeter('fixture', 1, PROFILES['lean'])

    async def post(*args, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {
            'usage': {'prompt_tokens': 17, 'completion_tokens': 3},
            'choices': [{'message': {'content': '', 'tool_calls': [
                {'id': 'c1', 'function': {'name': 'fixture_wait', 'arguments': '{}'}},
            ]}, 'finish_reason': 'tool_calls'}],
        })

    backend._client = SimpleNamespace(post=post)
    try:
        with bind_context(ToolContext(None, None, None, 'fixture', workspace=tmp_path, runtime_meter=backend.runtime_meter)):
            await backend._finish_filing('cancel this source')
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.wait_for(backend.prepare_user_turn(), 2)
        await backend.prepare_user_turn()
    finally:
        await backend.close_background()
    usage = [event.data for event in emitted if event.kind == 'background_usage']
    assert len(usage) == 1 and usage[0]['usage'] == {'prompt_tokens': 17, 'completion_tokens': 3}
    assert backend.background_status()['interrupted'] == 1
