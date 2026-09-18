"""Capability propagation using CPU objects and fake transports only."""
from contextlib import nullcontext
import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.local.settings import session_options, read_session_options
from test_compaction import _FakeClient, _text_round


def caps():
    return {'supported': True, 'sampling': ['max_tokens'], 'load': ['reasoning_effort'],
            'defaults': {'reasoning_effort': 'max'},
            'reasoning': {'effort_levels': ['low', 'high', 'max'], 'default_effort': 'max'}}


def backend(**kwargs):
    return OpenAICompatBackend(provider=SimpleNamespace(key='machx', label='fixture',
        base_url='http://fixture.invalid/v1', multimodal=False, api_key=lambda: 'fixture'),
        model='fixture', system_prompt='fixture', tools=[], permission_cb=None, **kwargs)


def test_capability_session_is_scoped_detached_and_restored(monkeypatch):
    from dream.local.settings import read_session_capabilities
    monkeypatch.delenv('DREAM_MACHX_SESSION_OPTIONS', raising=False)
    original = caps()
    with session_options({'max_tokens': 12000}, 'fixture', capabilities=original):
        original['reasoning']['effort_levels'].clear()
        assert read_session_options('fixture') == {'max_tokens': 12000}
        assert read_session_capabilities('other') == {}
        first = read_session_capabilities('fixture')
        assert first['reasoning']['effort_levels'] == ['low', 'high', 'max']
        first['reasoning']['effort_levels'].clear()
        with session_options({}, 'other'):
            assert read_session_capabilities('fixture') == {}
        assert read_session_capabilities('fixture')['reasoning']['effort_levels'] == ['low', 'high', 'max']
    assert read_session_capabilities('fixture') == {}


@pytest.mark.parametrize('invalid', [[], {'load': 'reasoning_effort'},
    {'load': ['reasoning_effort'], 'reasoning': {'effort_levels': ['ultra']}}, {'notes': 'x' * 70000}])
def test_invalid_session_capabilities_fail_before_environment_mutation(monkeypatch, invalid):
    monkeypatch.setenv('DREAM_MACHX_SESSION_OPTIONS', 'preserve')
    with pytest.raises(ValueError):
        with session_options({}, 'fixture', capabilities=invalid):
            pytest.fail('invalid metadata accepted')
    assert __import__('os').environ['DREAM_MACHX_SESSION_OPTIONS'] == 'preserve'


@pytest.mark.asyncio
async def test_reported_ladder_changes_actual_next_turn_payloads_and_restores_custom():
    with session_options({'reasoning_effort': 'max', 'max_tokens': 12000}, 'fixture', capabilities=caps()):
        b = backend()
    b.n_ctx = 131072
    b._client = _FakeClient([_text_round()] * 4)
    assert b.capability_status()['reasoning_levels']['value'] == ['low', 'high', 'max']
    for mode in ('quick', 'balanced', 'thorough', 'custom'):
        b.set_performance_mode(mode)
        [event async for event in b.ask('fixture')]
    assert [p['reasoning_effort'] for p in b._client.payloads] == ['low', 'high', 'max', 'max']
    assert [p['max_tokens'] for p in b._client.payloads] == [2048, 8192, 12000, 12000]


@pytest.mark.asyncio
async def test_props_are_reused_without_extra_requests_and_switch_clears_facts():
    with session_options({}, 'fixture', capabilities=caps()):
        b = backend()
    requests = []
    async def get(url, **kwargs):
        requests.append(url)
        return SimpleNamespace(status_code=200, json=lambda: {'default_generation_settings': {'n_ctx': 8192}})
    b._client = SimpleNamespace(get=get)
    b.n_ctx = await b._probe_n_ctx()
    for _ in range(3):
        assert b.capability_status()['context_tokens']['value'] == 8192
        assert b.performance_status()['reasoning_supported']
    assert requests == ['http://fixture.invalid/props']
    await b.set_model('different')
    assert b.n_ctx is None
    assert not b.capability_status()['context_tokens']['known']
    assert not b.performance_status()['reasoning_supported']
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('props', [[], {'default_generation_settings': {'n_ctx': True}}])
async def test_malformed_props_warn_and_configured_fallback_is_not_reported_capability(monkeypatch, props):
    monkeypatch.setenv('DREAM_MACHX_CTX', '4096')
    b = backend()
    async def get(*args, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: props)
    b._client = SimpleNamespace(get=get)
    assert await b._probe_n_ctx() == 4096
    report = b.capability_status()
    assert report['warnings'] and not report['context_tokens']['known']


def test_explicit_provider_metadata_is_supported_without_model_name_guesses():
    b = backend(provider_metadata={'vision': False, 'tool_calling': True, 'reasoning_levels': ['low', 'high']})
    assert b.capability_status()['tool_calling']['value'] is True
    assert b.capability_status()['vision']['value'] is False
    assert backend().capability_status()['tool_calling']['known'] is False


@pytest.mark.asyncio
async def test_terminal_launcher_passes_already_fetched_capabilities(monkeypatch, tmp_path):
    from dream.local import launcher, machx, preflight, model_presets
    from dream.local.settings import read_session_capabilities
    path = tmp_path / 'fixture.gguf'
    path.write_bytes(b'fixture')
    calls = []
    def metadata(_):
        calls.append('metadata')
        return caps()
    monkeypatch.setattr(machx, 'capabilities', metadata)
    async def choose(*args):
        return {'gpus': 1, 'ctx': 8192, 'options': {'reasoning_effort': 'max'}}, None
    monkeypatch.setattr(launcher, '_choose_model_settings', choose)
    monkeypatch.setattr(preflight, 'check_live', lambda *args: SimpleNamespace(should_load=True, reason='CPU fixture'))
    monkeypatch.setattr(machx, 'serve', lambda *args, **kwargs: object())
    monkeypatch.setattr(machx, 'wait_ready', lambda _: True)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'fixture')
    monkeypatch.setattr(model_presets.Presets, 'save', lambda *args, **kwargs: None)
    async def harness(*args, **kwargs):
        assert read_session_capabilities('fixture')['reasoning']['effort_levels'] == ['low', 'high', 'max']
    monkeypatch.setattr(launcher, '_run_harness', harness)
    renderer = SimpleNamespace(system=lambda _: None, error=lambda message: pytest.fail(message))
    await launcher.serve_and_run(Console(file=io.StringIO()), renderer, 'fixture', path, keep_hot=True)
    assert calls == ['metadata']


@pytest.mark.asyncio
async def test_desktop_passes_refetched_capabilities_to_session(monkeypatch, tmp_path):
    from dream.desktop import startup
    from dream.local import machx, model_defaults, model_presets
    from dream.local.settings import read_session_capabilities
    from dream.tui import app
    path = tmp_path / 'fixture.gguf'
    path.write_bytes(b'fixture')
    calls = []
    monkeypatch.setattr(machx, 'capabilities', lambda _: calls.append('metadata') or caps())
    monkeypatch.setattr(model_defaults, 'recommend', lambda *args: {
        'ctx': 8192, 'gpus': 1, 'options': {'reasoning_effort': 'max'}, 'sources': {}, 'notes': [], 'context_limit': 8192})
    monkeypatch.setattr(model_presets.Presets, 'load', lambda *args: None)
    monkeypatch.setattr(model_presets.Presets, 'save', lambda *args, **kwargs: None)
    monkeypatch.setattr(startup, 'local_load_lock', nullcontext)
    monkeypatch.setattr(startup, 'launch_preflight', lambda *args: 'CPU fixture')
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [SimpleNamespace(path=path, name='fixture', size_gb=1)])
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    monkeypatch.setattr(machx, 'serve', lambda *args, **kwargs: SimpleNamespace(poll=lambda: 0))
    monkeypatch.setattr(machx, 'wait_ready', lambda _: True)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'fixture')
    async def run_app():
        assert read_session_capabilities('fixture')['reasoning']['effort_levels'] == ['low', 'high', 'max']
    monkeypatch.setattr(app, 'App', lambda **kwargs: SimpleNamespace(run=run_app))
    settings = startup.model_settings(path)
    await startup.run({'workspace': str(tmp_path), 'choice': {'provider': 'machx', 'kind': 'local', 'path': str(path)},
        'selection': settings['selection'], 'identity': settings['identity']}, tmp_path / 'status.json')
    assert calls == ['metadata', 'metadata']  # settings display, then the existing run revalidation
