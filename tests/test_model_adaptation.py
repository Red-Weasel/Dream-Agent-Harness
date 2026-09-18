"""Model-bound settings and actual request limits, without provider inference."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.profiles import PROFILES
from dream.local.settings import session_options
from test_compaction import _FakeClient, _text_round


def backend(*, metadata=None, profile=None, temperature=.7):
    return OpenAICompatBackend(provider=SimpleNamespace(key='machx', label='fixture',
        base_url='http://fixture.invalid/v1', multimodal=False), model='first',
        system_prompt='Fixture instructions', tools=[], permission_cb=None,
        provider_metadata=metadata, profile=profile, temperature=temperature)


@pytest.mark.parametrize('profile', [None, PROFILES['frontier']])
@pytest.mark.parametrize('n_ctx', [None, 8192])
async def test_reported_context_bounds_real_requests(profile, n_ctx):
    b = backend(metadata={'context_tokens': 4096}, profile=profile)
    b.n_ctx = n_ctx
    b._client = _FakeClient([_text_round()])
    assert b._window() == 4096
    events = [e async for e in b.ask('Keep this exact current request')]
    assert not next(e.data for e in events if e.kind == 'result')['is_error']
    payload = b._client.payloads[0]
    assert payload['max_tokens'] < 4096
    assert 'Keep this exact current request' in str(payload['messages'])
    assert b.context_report['window'] == 4096
    assert len(b._client.payloads) == 1


def test_tighter_user_context_limit_is_preserved():
    b = backend(metadata={'context_tokens': 8192},
                profile=replace(PROFILES['frontier'], context_limit=2048))
    assert b._window() == 2048


@pytest.mark.parametrize('value', [False, -1, '4096', 0])
def test_malformed_context_does_not_become_a_bound(value):
    b = backend(metadata={'context_tokens': value}, profile=PROFILES['balanced'])
    assert b._window() == 32768
    assert not b.capability_status()['context_tokens']['known']


def test_conflicting_reports_remain_unknown_and_existing_bound_is_kept():
    b = backend(metadata={'context_tokens': 4096}, profile=PROFILES['frontier'])
    b._server_props = {'default_generation_settings': {'n_ctx': 8192}}
    b.n_ctx = 8192
    assert not b.capability_status()['context_tokens']['known']
    assert b._window() == 8192


async def test_model_switch_discards_old_launch_tuning_in_outgoing_request(monkeypatch):
    from dream import config
    monkeypatch.setattr(config, 'FREQUENCY_PENALTY', 0)
    monkeypatch.setattr(config, 'PRESENCE_PENALTY', 0)
    monkeypatch.setattr(config, 'REPETITION_PENALTY', 1)
    with session_options({'max_tokens': 65000, 'temperature': .12, 'top_k': 40,
                          'thinking': True, 'reasoning_effort': 'max',
                          'stop': ['first-only'], 'context_overflow': 'error'}, 'first'):
        b = backend(profile=PROFILES['balanced'], temperature=.4)
    b.n_ctx = 131072
    b._last_prompt_tokens = 90000
    b.last_usage = {'prompt_tokens': 90000}
    b.context_report = {'window': 131072}
    b._client = _FakeClient([_text_round(), _text_round()])
    for model in ('second', 'first'):
        await b.set_model(model)
        assert b._last_prompt_tokens == 0
        assert b.last_usage is None and b.context_report is None
        assert b._context_overflow == 'compact'
        [e async for e in b.ask('Use the newly selected model')]
        payload = b._client.payloads[-1]
        assert payload['model'] == model
        assert payload['temperature'] == .4
        assert payload['max_tokens'] <= PROFILES['balanced'].output_tokens
        for field in ('top_k', 'enable_thinking', 'reasoning_effort', 'stop'):
            assert field not in payload
    assert len(b._client.payloads) == 2


async def test_same_model_keeps_exact_launch_settings_and_explicit_effort():
    with session_options({'max_tokens': 12000, 'temperature': .12, 'top_k': 40,
                          'thinking': False, 'reasoning_effort': 'max'}, 'first'):
        b = backend(metadata={'context_tokens': 131072})
    b.set_effort('high')
    await b.set_model('first')
    assert b._local_options['max_tokens'] == 12000
    assert b.temperature == .12 and b._sampling['enable_thinking'] is False
    assert b._base_effort_params() == {'reasoning_effort': 'high'}
    await b.set_model('second')
    assert b._base_effort_params() == {'reasoning_effort': 'high'}
    assert not b.capability_status()['context_tokens']['known']


async def test_same_model_keeps_selected_performance_in_next_payload():
    b = backend(profile=PROFILES['frontier'])
    b.set_performance_mode('quick')
    await b.set_model('first')
    b._client = _FakeClient([_text_round()])
    [e async for e in b.ask('Keep the selected mode')]
    assert b._client.payloads[0]['max_tokens'] == 2048
    assert b.performance_status()['current'] == 'quick'


async def test_salvage_cannot_elide_oversized_current_user_to_admit():
    from test_salvage import _FakeClient as RecoveryClient, SALVAGE
    b = backend(metadata={'context_tokens': 4096})
    current = 'CURRENT USER REQUEST ' + 'x' * 30000
    b.messages.append({'role': 'user', 'content': current})
    b._client = RecoveryClient(post_scripts=[SALVAGE])
    assert await b._salvage_reply('fixture stopped') == ''
    assert b._client.posted == []
    assert b.messages[-1]['content'] == current


async def test_salvage_compacts_scratch_history_but_keeps_current_request():
    from copy import deepcopy
    from test_salvage import _FakeClient as RecoveryClient, SALVAGE
    b = backend(metadata={'context_tokens': 4096})
    b.messages.extend([{'role': 'user', 'content': 'old ' * 12000},
                       {'role': 'assistant', 'content': 'old answer ' * 1000},
                       {'role': 'user', 'content': 'Current request must remain exact'}])
    original = deepcopy(b.messages)
    b._client = RecoveryClient(post_scripts=[SALVAGE])
    assert await b._salvage_reply('fixture stopped')
    assert b.messages == original
    assert any(m.get('content') == 'Current request must remain exact'
               for m in b._client.posted[0]['messages'])
