"""Read-only generation metadata and GPU-disabled Controls fixtures."""
import copy
import json
from types import SimpleNamespace

import pytest
from playwright.async_api import expect

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.local.settings import session_options
from test_studio_controls import READ_ONLY_STARTUP_CALLS, controls, open_controls, no_overflow  # noqa: F401


def backend():
    with session_options({'max_tokens': 65000, 'reasoning_effort': 'low', 'thinking': True,
                          'temperature': .12, 'top_k': 40, 'parallel': 2,
                          'stop': ['private prompt', '/home/private/token']}, 'fixture'):
        b = OpenAICompatBackend(provider=SimpleNamespace(key='machx', label='fixture',
            base_url='http://fixture.invalid/v1', multimodal=False), model='fixture',
            system_prompt='private system text', tools=[], permission_cb=None)
    b.n_ctx = 250000
    b._server_props = {'default_generation_settings': {'n_ctx': 250000}}
    return b


def rows(report, section):
    return {row['name']: row for row in report[section]}


def test_summary_uses_actual_precedence_and_preserves_all_backend_state(monkeypatch):
    b = backend()
    b._sampling['temperature'] = .25
    b._sampling['api_key'] = 'private-secret'
    b.context_report = {'window': 250000, 'output': 62000, 'remaining': 2000, 'private': 'private-secret'}
    before = {key: copy.deepcopy(value) if isinstance(value, (dict, list, tuple, set)) else value
              for key, value in b.__dict__.items()}
    report = b.generation_settings_status()
    summary = rows(report, 'summary')
    assert summary['context_window']['value'] == 250000
    assert summary['output_ceiling']['value'] == 65000
    assert summary['reasoning_effort']['value'] == 'low'
    assert summary['thinking']['value'] is True
    assert summary['temperature']['value'] == .25
    assert not summary['gpu_count']['known']
    assert rows(report, 'sampling')['stop_count']['value'] == 2
    assert rows(report, 'launch')['parallel']['value'] == 2
    assert rows(report, 'last_admission')['output']['value'] == 62000
    assert 'private' not in json.dumps(report)
    assert b.__dict__ == before
    assert b._performance is None


def test_next_turn_choice_is_separate_from_last_prepared_snapshot():
    b = backend()
    b._active_performance = {'output_tokens': 65000, 'reasoning_effort': 'low'}
    b.set_performance_mode('quick')
    report = b.generation_settings_status()
    assert rows(report, 'summary')['output_ceiling']['value'] == 2048
    assert rows(report, 'last_prepared_turn')['output_tokens']['value'] == 65000
    assert report['snapshot_scope'] == 'last_prepared_turn'
    assert report['applies'] == 'next_user_turn'


def test_unknown_and_malformed_values_do_not_become_defaults_or_expose_text():
    b = backend()
    b.n_ctx = None
    b._server_props = None
    b._sampling['enable_thinking'] = 'private-secret'
    b._sampling['top_k'] = float('nan')
    b._local_options = {}
    b._effort = 'private-secret'
    report = b.generation_settings_status()
    assert not rows(report, 'summary')['reported_context']['known']
    assert not rows(report, 'summary')['thinking']['known']
    assert not rows(report, 'summary')['reasoning_effort']['known']
    assert not rows(report, 'sampling')['top_k']['known']
    assert 'private' not in json.dumps(report, allow_nan=False)


async def test_active_settings_are_read_only_precise_and_distinguish_admission(controls):
    _, page, api, url, errors = controls
    b = backend()
    b.context_report = {'window': 250000, 'output': 62000, 'remaining': 2000}
    api.runtime['generation_settings'] = b.generation_settings_status()
    await open_controls(page, url)
    box = page.locator('#dc-active-settings')
    await expect(box).to_contain_text('250,000')
    await expect(box).to_contain_text('65,000')
    await expect(box).to_contain_text('0.12')
    await expect(box).to_contain_text('low')
    await expect(box).to_contain_text('Unknown')
    await expect(box).to_contain_text('Last admission calculation')
    await expect(box).not_to_contain_text('private prompt')
    assert await box.locator('input,select,button').count() == 0
    assert api.calls == READ_ONLY_STARTUP_CALLS
    await page.set_viewport_size({'width': 480, 'height': 800})
    await no_overflow(page)
    assert not errors


async def test_native_adapter_summary_is_honestly_unavailable(controls):
    _, page, api, url, errors = controls
    api.runtime['generation_settings'] = {'available': False, 'reason': 'Provider CLI owns generation settings.'}
    await open_controls(page, url)
    await expect(page.locator('#dc-active-settings')).to_contain_text('Provider CLI owns generation settings.')
    assert api.calls == READ_ONLY_STARTUP_CALLS and not errors


@pytest.mark.parametrize('enabled', [False, True])
async def test_image_configuration_and_reported_support_are_distinct_in_controls(controls, enabled):
    from test_vision_status import backend as vision_backend
    _, page, api, url, errors = controls
    b = vision_backend(enabled, {'vision': not enabled})
    api.runtime['generation_settings'] = b.generation_settings_status()
    api.runtime['capabilities'] = b.capability_status()
    await open_controls(page, url)
    box = page.locator('#dc-active-settings')
    for label in ('Tool images enabled', 'See tool registered'):
        await expect(box.locator(f'dt:has-text("{label}") + dd')).to_contain_text('On' if enabled else 'Off')
    await expect(box).to_contain_text('Adapter configuration; not reported support')
    await expect(box).to_contain_text('not execution permission')
    await expect(page.locator('#dc-capability-facts dt:has-text("Image input") + dd')).to_contain_text(
        'Unsupported' if enabled else 'Supported')
    await expect(page.locator('#dc-capability-warnings')).to_have_text(b.capability_status()['warnings'][0])
    assert await box.locator('input,select,button').count() == 0
    await page.set_viewport_size({'width': 480, 'height': 800})
    await no_overflow(page)
    assert api.calls == READ_ONLY_STARTUP_CALLS and not errors


def reported_backend(metadata, *, stored=None, limit=None):
    from dataclasses import replace
    from dream.core.profiles import PROFILES
    b = OpenAICompatBackend(provider=SimpleNamespace(key='openai', label='fixture',
        base_url='https://fixture.invalid/v1', multimodal=False), model='fixture',
        system_prompt='Fixture instructions', tools=[], permission_cb=None,
        provider_metadata=metadata, profile=replace(PROFILES['frontier'], context_limit=limit))
    b.n_ctx = stored
    return b


async def captured_request(b):
    import httpx
    payloads = []
    def handle(request):
        assert request.url.host == 'fixture.invalid'
        assert request.method == 'POST'
        payloads.append(json.loads(request.content))
        chunk = {'choices': [{'index': 0, 'delta': {'content': 'done'}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')
    async with httpx.AsyncClient(base_url='https://fixture.invalid/v1',
                                transport=httpx.MockTransport(handle)) as client:
        b._client = client
        events = [event async for event in b.ask('Fixture request')]
    assert not next(event.data for event in events if event.kind == 'result')['is_error']
    assert len(payloads) == 1
    return payloads[0]


async def test_reported_custom_effort_matches_actual_request_and_prepared_snapshot():
    b = reported_backend({'context_tokens': 4096,
                          'reasoning_levels': ['economy', 'standard', 'extended']})
    b.set_performance_mode('thorough')
    payload = await captured_request(b)
    assert payload['reasoning_effort'] == 'extended'
    report = b.generation_settings_status()
    assert rows(report, 'summary')['reasoning_effort']['value'] == 'extended'
    assert rows(report, 'last_prepared_turn')['reasoning_effort']['value'] == 'extended'
    b.set_performance_mode('quick')
    report = b.generation_settings_status()
    assert rows(report, 'summary')['reasoning_effort']['value'] == 'economy'
    assert rows(report, 'last_prepared_turn')['reasoning_effort']['value'] == 'extended'
    assert rows(report, 'last_admission')['output']['value'] == payload['max_tokens']


async def test_reported_context_source_matches_actual_admission():
    b = reported_backend({'context_tokens': 4096})
    payload = await captured_request(b)
    report = b.generation_settings_status()
    context = rows(report, 'summary')['context_window']
    assert context['value'] == rows(report, 'last_admission')['window']['value'] == 4096
    assert payload['max_tokens'] == rows(report, 'last_admission')['output']['value'] == 1024
    assert 'reported' in context['source'].lower()
    assert 'assumption' not in context['source'].lower()


@pytest.mark.parametrize('reported,stored,limit,props,window,sources', [
    (1, None, 4096, None, 1, {'reported'}),
    (None, 1, 4096, None, 1, {'unattributed'}),
    (1, 1, 4096, None, 1, {'reported', 'unattributed'}),
    (4096, None, None, None, 4096, {'reported'}),
    (4096, 8192, None, None, 4096, {'reported'}),
    (8192, 4096, None, None, 4096, {'unattributed'}),
    (8192, 16384, 2048, None, 2048, {'configured'}),
    (4096, 4096, None, None, 4096, {'reported', 'unattributed'}),
    (4096, None, 4096, None, 4096, {'reported', 'configured'}),
    (None, 4096, 4096, None, 4096, {'unattributed', 'configured'}),
    (4096, 4096, 4096, None, 4096, {'reported', 'unattributed', 'configured'}),
    (None, None, 2048, None, 2048, {'configured'}),
    (None, 8192, None, None, 8192, {'unattributed'}),
    (4096, 8192, None, 8192, 8192, {'unattributed'}),
    (4096, 8192, 2048, 8192, 2048, {'configured'}),
    (True, None, None, None, 131072, {'assumption'}),
    (None, None, None, None, 131072, {'assumption'}),
])
def test_context_source_names_only_bounds_determining_the_window(
        reported, stored, limit, props, window, sources):
    b = reported_backend({'context_tokens': reported}, stored=stored, limit=limit)
    if props is not None:
        b._server_props = {'default_generation_settings': {'n_ctx': props}}
    context = rows(b.generation_settings_status(), 'summary')['context_window']
    assert context['value'] == window
    actual_sources = {name for name in ('reported', 'unattributed', 'configured', 'assumption')
                      if name in context['source'].lower()}
    assert actual_sources == sources


@pytest.mark.parametrize('levels', [None, [], ['extended', 'extended'], ['PRIVATE TEXT'],
                                   ['x' * 33], 'extended', [False]])
def test_unaccepted_custom_effort_remains_private(levels):
    b = reported_backend({'reasoning_levels': levels})
    b._active_performance = {'output_tokens': 1024, 'reasoning_effort': 'extended'}
    b._effort = 'private-secret'
    report = b.generation_settings_status()
    assert not rows(report, 'summary')['reasoning_effort']['known']
    assert not rows(report, 'last_prepared_turn')['reasoning_effort']['known']
    assert 'private' not in json.dumps(report, allow_nan=False)
    assert 'extended' not in json.dumps(report, allow_nan=False)


async def test_custom_effort_visibility_expires_on_conflicts_and_model_change():
    b = reported_backend({'reasoning_levels': ['economy', 'extended']})
    b.set_performance_mode('thorough')
    await b.prepare_user_turn()
    b._local_capabilities = {'load': ['reasoning_effort'],
                            'reasoning': {'effort_levels': ['low', 'high']}}
    assert not b.capability_status()['reasoning_levels']['known']
    report = b.generation_settings_status()
    assert not rows(report, 'summary')['reasoning_effort']['known']
    assert not rows(report, 'last_prepared_turn')['reasoning_effort']['known']
    b._local_capabilities = {}
    assert rows(b.generation_settings_status(), 'last_prepared_turn')['reasoning_effort']['value'] == 'extended'
    await b.set_model('different')
    report = b.generation_settings_status()
    assert not rows(report, 'summary')['reasoning_effort']['known']
    assert not rows(report, 'last_prepared_turn')['reasoning_effort']['known']
    assert 'extended' not in json.dumps(report)


async def test_capability_attribution_reads_preserve_state_and_request_payload(monkeypatch):
    b = reported_backend({'context_tokens': 4096,
                          'reasoning_levels': ['economy', 'extended']})
    b.set_performance_mode('thorough')
    await b.prepare_user_turn()
    def forbidden(*args, **kwargs):
        pytest.fail('settings read invoked an effectful operation')
    monkeypatch.setattr(b, 'performance_status', forbidden)
    b._client = SimpleNamespace(get=forbidden, post=forbidden, stream=forbidden)
    before = {key: copy.deepcopy(value) if isinstance(value, (dict, list, tuple, set)) else value
              for key, value in b.__dict__.items()}
    performance_before = copy.deepcopy(b._performance.__dict__)
    for _ in range(3):
        report = b.generation_settings_status()
        assert rows(report, 'summary')['reasoning_effort']['value'] == 'extended'
        assert rows(report, 'summary')['context_window']['value'] == 4096
    assert b.__dict__ == before
    assert b._performance.__dict__ == performance_before
    # The already prepared turn avoids a new selection. Reads must not alter what is sent.
    payload = await captured_request(b)
    untouched = reported_backend({'context_tokens': 4096,
                                  'reasoning_levels': ['economy', 'extended']})
    untouched.set_performance_mode('thorough')
    assert payload == await captured_request(untouched)


@pytest.mark.parametrize('metadata,source', [({'context_tokens': 4096}, 'reported'),
                                           ({}, 'assumption')])
def test_context_attribution_without_runtime_profile(metadata, source):
    b = reported_backend(metadata)
    b.profile = None
    context = rows(b.generation_settings_status(), 'summary')['context_window']
    assert source in context['source'].lower()
    assert 'profile' not in context['source'].lower()


@pytest.mark.parametrize('level', ['low', 'medium', 'high', 'xhigh'])
def test_explicit_builtin_effort_remains_visible_without_a_reported_ladder(level):
    b = reported_backend({})
    b._active_performance = {'output_tokens': 1024, 'reasoning_effort': level}
    assert rows(b.generation_settings_status(), 'last_prepared_turn')['reasoning_effort']['value'] == level


def test_longest_valid_reported_effort_stays_bounded_and_known():
    level = 'a' * 32
    b = reported_backend({'reasoning_levels': [level]})
    b.set_performance_mode('thorough')
    report = b.generation_settings_status()
    assert rows(report, 'summary')['reasoning_effort']['value'] == level


@pytest.mark.parametrize('status,props', [(503, {}), (200, {}),
    (200, {'default_generation_settings': {'n_ctx': 'private-invalid'}})])
async def test_connected_context_fallback_has_unattributed_origin(monkeypatch, status, props):
    import httpx
    monkeypatch.setenv('DREAM_MACHX_CTX', '6000')
    b = reported_backend({})
    b.provider.key = 'machx'
    b.provider.api_key = lambda: 'fixture'
    real_client = httpx.AsyncClient
    requests = []
    def handle(request):
        assert request.method == 'GET' and str(request.url) == 'https://fixture.invalid/props'
        requests.append(str(request.url))
        return httpx.Response(status, json=props)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs:
                        real_client(**kwargs, transport=httpx.MockTransport(handle)))
    try:
        await b.connect()
        assert b.n_ctx == 6000
        assert not b.capability_status()['context_tokens']['known']
        report = b.generation_settings_status()
        context = rows(report, 'summary')['context_window']
        assert context['value'] == 6000
        assert 'unattributed' in context['source'].lower()
        assert 'measured' not in context['source'].lower()
        assert 'reported' not in context['source'].lower()
        assert 'private' not in json.dumps(report)
        # The environment is mutable; its present value cannot establish a past origin.
        monkeypatch.setenv('DREAM_MACHX_CTX', '12000')
        assert b.generation_settings_status() == report
        monkeypatch.delenv('DREAM_MACHX_CTX')
        assert b.generation_settings_status() == report
        assert requests == ['https://fixture.invalid/props']
    finally:
        if b._client is not None:
            await b._client.aclose()
