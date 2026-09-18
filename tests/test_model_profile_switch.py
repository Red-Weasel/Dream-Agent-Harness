"""Named switches apply destination limits without reconnecting native sessions."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dream import config
from dream.core import profiles
from dream.core.engine import Engine
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.providers import Provider
from test_compaction import _FakeClient, _text_round


@pytest.fixture
def settings(monkeypatch):
    import os
    for key in tuple(os.environ):
        if key.startswith('DREAM_') and key != 'DREAM_EXTENSION_SETTINGS':
            monkeypatch.delenv(key)
    monkeypatch.setattr(config, 'MAX_OUTPUT_TOKENS', 16384)
    saved = {'profile': 'frontier', 'overrides': {}, 'models': {
        'openai:A': {'context_limit': 32768, 'output_tokens': 4096},
        'openai:B': {'context_limit': 4096, 'output_tokens': 512},
    }}
    monkeypatch.setattr(profiles, 'read_settings', lambda: saved)
    return saved


def http_engine(tmp_path, **kwargs):
    provider = Provider('openai', 'Offline fixture', 'openai', base_url='https://fixture.invalid/v1')
    e = Engine(provider=provider, model='A', workspace=tmp_path)
    e.backend = OpenAICompatBackend(provider=e.provider, model=e.model,
        profile=e.profile, system_prompt='fixture', tools=[], permission_cb=None, **kwargs)
    return e


@pytest.mark.parametrize('destination,window,output', [
    ({'context_limit': 4096, 'output_tokens': 512}, 4096, 512),
    ({'context_limit': 65536, 'output_tokens': 8192}, 65536, 8192),
    (None, 131072, 16384),
])
async def test_destination_limits_reach_actual_request(settings, tmp_path, destination, window, output):
    if destination is None:
        del settings['models']['openai:B']
    else:
        settings['models']['openai:B'] = destination
    e = http_engine(tmp_path)
    b = e.backend
    b._client = _FakeClient([_text_round()])
    await e.set_model('B')
    events = [event async for event in b.ask('Keep this request')]
    assert not next(event.data for event in events if event.kind == 'result')['is_error']
    assert b._client.payloads[0]['model'] == 'B'
    assert b._client.payloads[0]['max_tokens'] == output
    assert b.context_report['window'] == window
    assert e.profile is b.profile
    assert e.store is None


@pytest.mark.parametrize('target', [None, '', '   ', 4, False, [], 'B\n', 'B\x7f', 'B'*257])
async def test_invalid_name_never_changes_selection(settings, tmp_path, target):
    e = http_engine(tmp_path)
    old = e.profile
    with pytest.raises(ValueError):
        await e.set_model(target)
    assert e.model == e.backend.model == 'A'
    assert e.profile is e.backend.profile is old


async def test_normalized_same_model_is_noop(settings, tmp_path, monkeypatch):
    e = http_engine(tmp_path)
    b = e.backend
    old = e.profile
    b._local_options = {'max_tokens': 3333}
    b.n_ctx = 23456
    b.context_report = {'window': 23456}
    b.set_effort('high')
    b.set_performance_mode('quick')
    cache = (b._performance, b.context_report, b._local_options)
    def unreadable():
        raise AssertionError('same model must not reload settings')
    monkeypatch.setattr(profiles, 'read_settings', unreadable)
    await e.set_model('  A  ')
    assert e.model == b.model == 'A'
    assert e.profile is b.profile is old
    assert (b._performance, b.context_report, b._local_options) == cache
    assert b.n_ctx == 23456 and b._effort == 'high'


@pytest.mark.parametrize('field,value', [
    ('max_parallel', 2), ('subagent_timeout_s', 88), ('idle_timeout_s', 89),
    ('vision', False), ('wake_tokens', 800),
    ('name', 'other'), ('prompt_style', 'compact'), ('assumed_context', 65536),
])
async def test_each_construction_field_refuses_mixed_changes(settings, tmp_path, monkeypatch, field, value):
    from dream.core import engine as module
    e = http_engine(tmp_path)
    old = e.profile
    destination = replace(old, context_limit=4096, output_tokens=512, **{field: value})
    monkeypatch.setattr(module, 'resolve_profile', lambda *a, **k: destination)
    with pytest.raises(ValueError, match=field) as exc:
        await e.set_model('B')
    assert 'Council' in str(exc.value)
    assert e.model == e.backend.model == 'A'
    assert e.profile is e.backend.profile is old


@pytest.mark.parametrize('state', ['run', 'pending', 'unavailable'])
async def test_busy_or_unavailable_selection_is_retained(settings, tmp_path, state):
    e = http_engine(tmp_path)
    old = e.profile
    if state == 'run':
        e._run_meter = object()
    elif state == 'pending':
        e._tool_bridge = SimpleNamespace(_pending=[SimpleNamespace(done=lambda: False)], _slots=object())
    else:
        e._handoff_unavailable = 'Council rollback failed. Restart Dream.'
    with pytest.raises(RuntimeError):
        await e.set_model('B')
    assert e.model == e.backend.model == 'A'
    assert e.profile is e.backend.profile is old


async def test_global_and_environment_precedence_reaches_request(settings, tmp_path, monkeypatch):
    settings['overrides'] = {'context_limit': 20000, 'output_tokens': 2000}
    e = http_engine(tmp_path)
    monkeypatch.setenv('DREAM_CONTEXT_WINDOW', '8192')
    monkeypatch.setenv('DREAM_MAX_TOKENS', '700')
    e.backend._client = _FakeClient([_text_round()])
    await e.set_model('  B  ')
    [event async for event in e.backend.ask('request')]
    assert e.model == e.backend.model == 'B'
    assert e.backend.context_report['window'] == 8192
    assert e.backend._client.payloads[0]['max_tokens'] == 700
    del settings['models']['openai:B']
    monkeypatch.delenv('DREAM_CONTEXT_WINDOW')
    monkeypatch.delenv('DREAM_MAX_TOKENS')
    await e.set_model('C')
    [event async for event in e.backend.ask('next')]
    assert e.backend.context_report['window'] == 20000
    assert e.backend._client.payloads[-1]['max_tokens'] == 2000


@pytest.mark.parametrize('invalid', [{'output_tokens': -1}, {'context_limit': False}, {'unexpected': 3}])
async def test_invalid_destination_settings_preserve_all_state(settings, tmp_path, invalid):
    e = http_engine(tmp_path)
    old = e.profile
    settings['models']['openai:B'] = invalid
    with pytest.raises(ValueError):
        await e.set_model('B')
    assert e.model == e.backend.model == 'A'
    assert e.profile is e.backend.profile is old


async def test_unstarted_engine_applies_profile_without_backend(settings, tmp_path):
    e = Engine(provider='openai', model='A', workspace=tmp_path)
    await e.set_model(' B ')
    assert e.model == 'B' and e.profile.context_limit == 4096
    assert e.profile.output_tokens == 512
    assert e.store is None and e.backend is None and not e._started


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel'])
async def test_sdk_keeps_native_client_and_local_publication_order(settings, tmp_path, outcome):
    from dream.core.backends.anthropic import AnthropicBackend
    from dream.core.providers import get_provider
    settings['models']['anthropic:A'] = settings['models']['openai:A']
    settings['models']['anthropic:B'] = settings['models']['openai:B']
    e = Engine(provider=get_provider('anthropic'), model='A', workspace=tmp_path)
    b = AnthropicBackend(system_prompt='native history', mcp_server={},
        preapproved_tool_ids=[], agents={}, permission_cb=None, model='A')
    e.backend = b
    e.set_effort('high')
    old = e.profile
    calls = []
    class NativeClient:
        async def set_model(self, model):
            calls.append(model)
            assert e.model == b.model == 'A' and e.profile is old
            if outcome == 'failure':
                raise RuntimeError('native rejected')
            if outcome == 'cancel':
                raise asyncio.CancelledError()
        async def connect(self):
            raise AssertionError('must preserve client')
        async def disconnect(self):
            raise AssertionError('must preserve client')
    client = b.client = NativeClient()
    if outcome == 'success':
        await e.set_model(' B ')
        assert e.model == b.model == 'B' and e.profile.output_tokens == 512
    else:
        with pytest.raises(RuntimeError if outcome == 'failure' else asyncio.CancelledError):
            await e.set_model('B')
        assert e.model == b.model == 'A' and e.profile is old
    assert calls == ['B']
    assert b.client is client and b.system_prompt == 'native history'
    assert e.effort == b._effort == 'high'


async def test_cli_keeps_session_prompt_and_permission_configuration(settings, tmp_path):
    from dream.core.backends.cli_agent import CliAgentBackend, CodexAdapter
    settings['models']['codex:B'] = {'context_limit': 4096, 'output_tokens': 512}
    e = Engine(provider='codex', model='A', workspace=tmp_path)
    mcp, sandbox = {'fixture': 'retained'}, lambda: 'read-only'
    b = CliAgentBackend(CodexAdapter(), system_prompt='native history', cwd=str(tmp_path),
                        model='A', mcp_config=mcp, sandbox_getter=sandbox)
    b._session_id = 'native-session-fixture'
    e.backend = b
    e.set_effort('high')
    await e.set_model(' B ')
    assert e.model == b.model == 'B' and e.profile.output_tokens == 512
    assert b._session_id == 'native-session-fixture' and b._proc is None
    assert b.mcp_config is mcp and b.sandbox_getter is sandbox
    assert b.system_prompt == 'native history' and e.effort == b._effort == 'high'


async def test_http_invalidation_preserves_history_effort_permissions_and_completed_evidence(settings, tmp_path):
    from dream.telemetry.runtime import RunMeter
    e = http_engine(tmp_path, provider_metadata={'context_tokens': 100000})
    b = e.backend
    e.set_effort('high')
    history = b.messages
    history.append({'role': 'assistant', 'content': 'Prior completed answer'})
    e.runtime_meter = RunMeter(e.session_id, 1, e.profile)
    e.runtime_meter.usage({'input_tokens': 17, 'output_tokens': 5})
    old_meter = e.runtime_meter
    e.turn_timing = object()
    e.total_cost_usd, e.session_tokens, e.last_context_tokens = 0.5, 22, 17
    timing, provider, permission, tools, slots = e.turn_timing, e.provider, b.permission_cb, b.tools, b._task_slots
    b.n_ctx = 100000
    b._local_options = {'max_tokens': 65000}
    b._active_performance = {'output_tokens': 65000}
    b.context_report = {'window': 100000}
    b._last_prompt_tokens = 10000
    b.set_performance_mode('quick')
    await e.set_model('B')
    summary = {r['name']: r for r in b.generation_settings_status()['summary']}
    assert summary['context_window']['value'] == 4096
    assert summary['output_ceiling']['value'] == 512
    assert summary['reasoning_effort']['value'] == 'high'
    assert b.messages is history and history[-1]['content'] == 'Prior completed answer'
    assert e.runtime_meter is old_meter and old_meter.profile.context_limit == 32768
    assert (old_meter.prompt_tokens, old_meter.output_tokens) == (17, 5)
    assert (e.total_cost_usd, e.session_tokens, e.last_context_tokens) == (0.5, 22, 17)
    assert e.turn_timing is timing and e.provider is provider
    assert b.permission_cb is permission and b.tools is tools and b._task_slots is slots
    assert e.effort == b._effort == 'high'
    assert b.n_ctx is None and b.context_report is None and b._active_performance is None
    assert b._last_prompt_tokens == 0 and b._local_options == {} and b._performance is None
    assert not b.capability_status()['context_tokens']['known']


async def test_schema_fraction_changes_next_selection_without_changing_available_tools(settings, tmp_path):
    from test_schema_deferral import _tool
    e = http_engine(tmp_path)
    b = e.backend = OpenAICompatBackend(provider=e.provider, model=e.model, profile=e.profile,
        system_prompt='fixture', tools=[_tool('fixture_tool', nprops=120)], permission_cb=None)
    settings['models']['openai:B'] = {'context_limit': 32768, 'output_tokens': 4096, 'schema_fraction': .01}
    before = b._request_tools()
    assert 'fixture_tool' in {s['function']['name'] for s in before}
    membership, schemas = b.tools_by_name, b.tool_schemas
    await e.set_model('B')
    after = b._request_tools()
    assert 'fixture_tool' not in {s['function']['name'] for s in after}
    assert 'fixture_tool' in b._deferred_now
    assert b.tools_by_name is membership and b.tool_schemas is schemas


async def test_queued_filer_retains_old_profile_and_client(settings, tmp_path, monkeypatch):
    e = http_engine(tmp_path, subagents={'filer': SimpleNamespace(description='fixture')})
    b = e.backend
    jobs, seen = [], []
    b._client = object()
    b._idle_work = SimpleNamespace(enqueue=lambda job, **kw: jobs.append(job),
                                   snapshot=lambda: {'foreground': False})
    async def run_filer(worker, text):
        seen.append((worker.model, worker.profile, worker._client, text))
        return []
    monkeypatch.setattr(OpenAICompatBackend, '_run_filer', run_filer)
    await b._finish_filing('old completed turn')
    old, client = e.profile, b._client
    assert len(jobs) == 1
    settings['models']['openai:B']['auto_filer'] = False
    await e.set_model('B')
    assert not b._filing_enabled()
    await b._finish_filing('new completed turn')
    assert len(jobs) == 1
    await jobs[0]()
    assert seen == [('A', old, client, 'old completed turn')]
    assert b._client is client


@pytest.mark.parametrize('enabled', [False, True])
async def test_explicit_filing_override_remains_authoritative(settings, tmp_path, monkeypatch, enabled):
    from dream.core.backends import openai_compat
    e = http_engine(tmp_path, subagents={'filer': SimpleNamespace(description='fixture')})
    settings['models']['openai:B']['auto_filer'] = not enabled
    monkeypatch.setenv('DREAM_AUTO_FILE', str(int(enabled)))
    monkeypatch.setattr(openai_compat, '_AUTO_FILE', enabled)
    await e.set_model('B')
    assert e.backend._filing_enabled() is enabled


async def test_idle_bridge_identity_and_limits_survive(settings, tmp_path):
    e = http_engine(tmp_path)
    bridge = e._tool_bridge = SimpleNamespace(_pending=[SimpleNamespace(done=lambda: True)],
        _slots=object(), timeout=900, _max_pending=16, _interrupting=0, token='fixture')
    before = vars(bridge).copy()
    await e.set_model('B')
    assert e._tool_bridge is bridge and vars(bridge) == before


from test_council_handoff import engine  # noqa: E402,F401


async def test_next_ordinary_turn_uses_new_budget_and_keeps_prior_meter(settings, engine):
    from test_council_handoff import Backend
    from dream.telemetry.runtime import RunLimit
    e = engine
    e.backend = Backend('fixture', [])
    async def named(model):
        e.backend.model = model
    e.backend.set_model = named
    [event async for event in e.ask('first completed turn')]
    old = e.runtime_meter
    old.usage({'input_tokens': 10, 'output_tokens': 2})
    settings['models']['machx:B'] = {'max_run_tokens': 4, 'max_run_tools': 1, 'max_run_seconds': 1}
    await e.set_model('B')
    assert e.runtime_meter is old and old.prompt_tokens == 10 and old.output_tokens == 2
    [event async for event in e.ask('next ordinary turn')]
    new = e.runtime_meter
    assert new is not old and new.profile is e.profile
    new.before_tool('first')
    with pytest.raises(RunLimit, match='tool budget'):
        new.before_tool('second')
    new.tools = 0
    new.usage({'input_tokens': 4})
    with pytest.raises(RunLimit, match='token budget'):
        new.check()
    new.prompt_tokens = 0
    new.started -= 2
    with pytest.raises(RunLimit, match='time budget'):
        new.check()
    assert old.profile.max_run_tools == 400 and old.prompt_tokens == 10


@pytest.mark.parametrize('line', ['/model B', '/model', '/model C'])
async def test_app_persists_successful_engine_identity_and_keeps_selection_on_refusal(settings, tmp_path, line):
    import io
    from rich.console import Console
    from dream.tui.app import App
    e = http_engine(tmp_path)
    settings['models']['openai:C'] = {'vision': True}
    output = io.StringIO()
    app = SimpleNamespace(engine=e, model='A', renderer=SimpleNamespace(console=Console(file=output, color_system=None)))
    await App._command(app, line)
    expected = 'B' if line == '/model B' else 'A'
    assert app.model == e.model == e.backend.model == expected
    if line == '/model C':
        assert 'could not switch' in output.getvalue() and 'Council' in output.getvalue()
    else:
        assert expected in output.getvalue()


async def test_smaller_destination_refuses_oversized_current_request_before_transport(settings, tmp_path):
    e = http_engine(tmp_path)
    b = e.backend
    b._client = _FakeClient([_text_round()])
    prompt = 'Preserve the entire current request. ' + 'x' * 30000
    [event async for event in b.ask(prompt)]
    assert len(b._client.payloads) == 1
    await e.set_model('B')
    events = [event async for event in b.ask(prompt)]
    assert len(b._client.payloads) == 1
    assert next(event.data for event in events if event.kind == 'result')['is_error']
    assert prompt in str(b.messages)


async def test_explicit_profile_selection_survives_saved_preset_change(settings, tmp_path):
    e = Engine(provider='openai', model='A', workspace=tmp_path, profile='frontier')
    settings['profile'] = 'lean'
    await e.set_model('B')
    assert e.profile.name == 'frontier'
    assert e.profile.context_limit == 4096 and e.profile.output_tokens == 512


async def test_actual_saved_preset_change_refuses_without_partial_limits(settings, tmp_path):
    e = http_engine(tmp_path)
    old = e.profile
    settings['profile'] = 'lean'
    with pytest.raises(ValueError, match='Council'):
        await e.set_model('B')
    assert e.profile is e.backend.profile is old and e.model == e.backend.model == 'A'


async def test_switch_invokes_real_http_method_once_and_same_model_never_calls(settings, tmp_path, monkeypatch):
    e = http_engine(tmp_path)
    b, old = e.backend, e.profile
    real = b.set_model
    calls = []
    async def observed(model):
        calls.append(model)
        assert e.model == 'A' and e.profile is b.profile is old
        await real(model)
        assert e.model == 'A' and e.profile is b.profile is old
    monkeypatch.setattr(b, 'set_model', observed)
    await e.set_model(' B ')
    assert e.profile is b.profile and e.profile is not old and calls == ['B']
    await e.set_model(' B ')
    assert calls == ['B']


@pytest.fixture(autouse=True)
def prohibit_live_start_and_model_loading(monkeypatch):
    from dream.memory.embeddings import Embedder, Reranker
    attempts = []
    def blocked(*args, **kwargs):
        attempts.append('live start or model load')
        raise AssertionError(attempts[-1])
    monkeypatch.setattr(Engine, 'start', blocked)
    monkeypatch.setattr(Embedder, '_ensure', blocked)
    monkeypatch.setattr(Reranker, '_ensure', blocked)
    yield
    assert not attempts
