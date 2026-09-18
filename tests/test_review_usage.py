"""Observed-only Council/reviewer accounting, isolated from global tool context."""
import json
from types import SimpleNamespace

import pytest

from dream.core.cli_review import _record_usage
from dream.core import moe
from dream.core.evaluator import ReviewSettings, SDKReviewBackend, collect_review, review_backend
from dream.core.profiles import resolve_profile
from dream.core.providers import get_provider
from dream.core.review_usage import attributed_meter
from dream.telemetry.runtime import RunMeter
from dream.tools import context


def meter():
    return RunMeter('fixture-owner', 1, resolve_profile(get_provider('machx')))


def test_advisor_meter_never_uses_another_sessions_global_fallback(monkeypatch):
    wrong, right = meter(), meter()
    monkeypatch.setattr(context, '_CTX', SimpleNamespace(runtime_meter=wrong))
    provider = get_provider('machx')
    assert attributed_meter(provider, scope='council') is None
    with context.bind_context(SimpleNamespace(runtime_meter=right)):
        attributed_meter(provider, scope='council').usage({'prompt_tokens': 4, 'completion_tokens': 2})
    assert right.prompt_tokens == 4 and wrong.prompt_tokens == 0


@pytest.mark.parametrize('provider,events,expected,cached', [
    ('codex', [{'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':2,'cached_input_tokens':4}}], 12, 4),
    ('gemini', [{'type':'result','stats':{'input_tokens':10,'output_tokens':2,'cached':4}}], 12, 4),
    ('grok', [{'type':'usage','messageId':'one','usage':{'input_tokens':6,'output_tokens':2,'cache_read_input_tokens':4}},
              {'type':'end','usage':{'input_tokens':6,'output_tokens':2,'cache_read_input_tokens':4}}], 12, 4),
    ('grok', [{'type':'usage','messageId':'one','usage':{'input_tokens':6,'output_tokens':2}},
              {'type':'usage','messageId':'one','usage':{'input_tokens':6,'output_tokens':2}},
              {'type':'error'}], 8, 0),
    ('grok', [{'type':'end'}], 0, 0),
    ('codex', [{'type':'turn.completed','usage':{'input_tokens':-10,'output_tokens':2}}], 0, 0),
])
def test_cli_usage_is_observed_and_never_double_counts_terminal_totals(provider, events, expected, cached):
    parent = meter()
    proxy = attributed_meter(get_provider(provider), scope='council', meter=parent)
    _record_usage(provider, '\n'.join(json.dumps(event) for event in events).encode(), proxy)
    assert parent.prompt_tokens + parent.output_tokens == expected
    assert parent.cached_tokens == cached
    if not expected:
        assert parent.phases == {}
    else:
        assert parent.phases[f'council:{provider}:lead']['requests'] == 1


async def test_sdk_advisor_and_evaluator_count_result_usage_without_live_queries(monkeypatch, tmp_path):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    async def query(**kwargs):
        yield AssistantMessage(content=[TextBlock(text='VERDICT: PASS\nGAPS: none')], model='fixture-sdk')
        yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                            num_turns=1, session_id='fixture-sdk', usage={'input_tokens':6,'output_tokens':2,'cache_read_input_tokens':4})
    monkeypatch.setattr('claude_agent_sdk.query', query)
    parent = meter()
    with context.bind_context(SimpleNamespace(runtime_meter=parent)):
        await moe._consult_anthropic(get_provider('anthropic'), 'fixture')
    settings = ReviewSettings(get_provider('anthropic'), 'fixture-sdk', runtime_meter=parent)
    backend = SDKReviewBackend(settings, [], 'review', tmp_path, query)
    await collect_review(backend, 'fixture', 1)
    assert parent.prompt_tokens == 20 and parent.output_tokens == 4
    assert set(parent.phases) == {'council:anthropic:lead', 'evaluator:anthropic:lead'}


def test_evaluator_uses_explicit_engine_owner_meter(tmp_path):
    parent = meter()
    provider = get_provider('machx')
    engine = SimpleNamespace(provider=provider, model='fixture-local', profile=resolve_profile(provider), runtime_meter=parent)
    settings = ReviewSettings.resolve(engine)
    backend = review_backend(settings, [], 'review', tmp_path)
    assert backend.runtime_meter.meter is parent
    backend.runtime_meter.usage({'prompt_tokens': 3, 'completion_tokens': 1})
    assert parent.phases['evaluator:machx:lead']['requests'] == 1
