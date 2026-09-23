"""DREAM-096: image input follows the running server's readiness, not architecture support alone.

P11 of the alpha-omega loop after DREAM-093. `ie capabilities <weights>` (`features.vision`) says what the ARCHITECTURE
supports; a loaded MachX server may still have no usable vision (the tower disabled with IE_MIMO26_VISION=0, a staging
failure, a DeepSeek4 file without its sidecar). The engine's GET /props gains `"vision": {"ready": bool, "reason": str,
"image_tokens": N}`; older engines have no such key. Fixtures only: the live server, when one runs, is an older build.
"""
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from dream.core.backends import openai_compat
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.capabilities import capability_report
from dream.core.profiles import PROFILES, guidance, resolve_profile, session_vision
from dream.core.providers import get_provider
from dream.local.settings import session_options
from dream.tools.vision import see

REASON = 'vision tower disabled (IE_MIMO26_VISION=0)'
READY = 'The model server reports image input is ready'
NOT_READY = 'The model server reports image input is not ready'
# What an older engine answers today (DREAM-093 read n_ctx from it live; residency_summary reads memory_residency).
OLDER_ENGINE = {'default_generation_settings': {'n_ctx': 100000},
                'memory_residency': {'host_pinned_bytes': 0, 'host_mmap_bytes': 0, 'gpu_expert_cache_bytes': [0]}}


def caps(vision):
    features = {'prompt_cache': True} if vision is None else {'prompt_cache': True, 'vision': vision}
    return {'schema_version': 1, 'supported': True, 'architecture': 'fixture', 'sampling': ['max_tokens'], 'load': [],
            'features': features}


def props(ready, reason=''):
    return {**OLDER_ENGINE, 'vision': {'ready': ready, 'reason': reason, 'image_tokens': 1024}}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv('DREAM_VISION', raising=False)
    monkeypatch.delenv('DREAM_MACHX_SESSION_OPTIONS', raising=False)


def decide(arch, server_props=None, **kwargs):
    """session_vision for a MachX session whose weights report `arch` and whose server answered `server_props`."""
    machx = get_provider('machx')
    with session_options({}, 'fixture', capabilities=caps(arch)):
        return session_vision(machx, resolve_profile(machx, None, model='fixture'), 'fixture',
                              props=server_props, **kwargs)


# ---- (b) the capability report carries the server's readiness ----------------------------------------------------

def test_the_report_carries_the_servers_readiness_with_its_source():
    ready = capability_report(machx_props=props(True))
    assert ready['vision_ready'] == {'known': True, 'value': True, 'source': 'machx.props.vision.ready', 'reason': ''}
    not_ready = capability_report(machx_props=props(False, REASON))
    assert not_ready['vision_ready'] == {'known': True, 'value': False, 'source': 'machx.props.vision.ready',
                                         'reason': REASON}
    assert ready['warnings'] == [] and not_ready['warnings'] == []
    assert not_ready['context_tokens']['value'] == 100000   # the neighbouring props fact is untouched
    unreported = {'known': False, 'value': None, 'source': 'unreported', 'reason': ''}
    assert capability_report(machx_props=OLDER_ENGINE)['vision_ready'] == unreported   # missing key
    assert capability_report()['vision_ready'] == unreported
    # Provider metadata cannot claim readiness: only the running server's /props feeds this fact.
    assert capability_report(provider_metadata={'vision_ready': True})['vision_ready'] == unreported


@pytest.mark.parametrize('vision', ['yes', True, None, 1, {}, {'ready': 'true'}, {'ready': 1}, {'ready': None}, ['ready']],
                         ids=['string', 'bare-bool', 'null', 'int', 'empty-object', 'ready-string', 'ready-int',
                              'ready-null', 'list'])
def test_a_malformed_readiness_is_a_warning_and_never_on(vision):
    report = capability_report(machx_props={**OLDER_ENGINE, 'vision': vision})
    assert report['vision_ready']['known'] is False and report['vision_ready']['value'] is None
    assert any(w.startswith('malformed.machx.props.vision') for w in report['warnings'])
    state = decide(True, {**OLDER_ENGINE, 'vision': vision})   # even weights that can see do not make it "on"
    assert state['state'] == 'unreported' and state['enabled'] is False
    assert 'readiness not reported' in state['source']


def test_a_reason_that_is_not_text_is_dropped_with_a_warning_and_a_long_one_is_bounded():
    report = capability_report(machx_props={'vision': {'ready': False, 'reason': 5}})
    assert report['vision_ready']['value'] is False and report['vision_ready']['reason'] == ''
    assert 'malformed.machx.props.vision.reason' in report['warnings']
    long = capability_report(machx_props={'vision': {'ready': False, 'reason': 'x\n' * 500}})['vision_ready']['reason']
    assert len(long) <= 200 and '\n' not in long


# ---- (b) session_vision: on only when the running server says ready -----------------------------------------------

@pytest.mark.parametrize('arch', [True, False, None])
def test_image_input_is_on_only_when_the_running_server_says_ready(arch):
    assert decide(arch, props(True)) == {'state': 'on', 'enabled': True, 'source': READY}
    assert decide(arch, props(False, REASON)) == {'state': 'off', 'enabled': False, 'source': NOT_READY + ': ' + REASON}
    assert decide(arch, props(False)) == {'state': 'off', 'enabled': False, 'source': NOT_READY}


def test_architecture_support_alone_reads_unreported_with_readiness_not_reported():
    # The server reported on image input without saying whether it is ready (the key missing): support alone is
    # not proof the tower is loaded.
    for report in ({}, {'reason': 'vision tower still staging', 'image_tokens': 1024}):
        alone = decide(True, {**OLDER_ENGINE, 'vision': report})
        assert alone['state'] == 'unreported' and alone['enabled'] is False
        assert 'readiness not reported' in alone['source']
    # DREAM-093's answers stand where readiness adds nothing: the weights cannot see, or reported nothing.
    assert decide(False, {**OLDER_ENGINE, 'vision': {}}) == {'state': 'off', 'enabled': False,
                                                              'source': 'MachX capability report'}
    assert decide(None, {**OLDER_ENGINE, 'vision': {}}) == {'state': 'unreported', 'enabled': False,
                                                             'source': 'MachX reported no vision capability for this model'}


def test_an_older_engine_without_the_field_reads_exactly_as_dream_093():
    # (c): /props without the field (an older engine), and no props read at all (DREAM-093's own tests; an Engine
    # before it connects), keep the architecture report's answer -- tests/test_local_vision_capabilities.py pins the
    # connected case too.
    for server_props in (OLDER_ENGINE, None):
        assert decide(True, server_props) == {'state': 'on', 'enabled': True, 'source': 'MachX capability report'}
        assert decide(False, server_props) == {'state': 'off', 'enabled': False, 'source': 'MachX capability report'}
        assert decide(None, server_props) == {'state': 'unreported', 'enabled': False,
                                              'source': 'MachX reported no vision capability for this model'}


def test_the_owners_profile_setting_still_wins(monkeypatch):
    monkeypatch.setenv('DREAM_VISION', '0')
    assert decide(True, props(True)) == {'state': 'off', 'enabled': False, 'source': 'Profile setting'}
    monkeypatch.setenv('DREAM_VISION', '1')
    assert decide(False, props(False, REASON)) == {'state': 'on', 'enabled': True, 'source': 'Profile setting'}


def test_a_rejected_image_still_reads_off_even_when_the_server_said_ready():
    rejected = decide(True, props(True), image_rejected=True)
    assert rejected['state'] == 'off' and rejected['enabled'] is False and 'rejected' in rejected['source']


# ---- (b) the Runtime note carries the server's reason ---------------------------------------------------------------

def test_the_runtime_note_carries_the_servers_reason():
    machx = get_provider('machx')
    profile = resolve_profile(machx, None, model='fixture')
    off = {'state': 'off', 'enabled': False, 'source': NOT_READY + ': ' + REASON}
    note = guidance(replace(machx, multimodal=False), profile, 'fixture', vision=off)
    assert 'Image input is off in this session' in note and REASON in note and 'measure_image' in note
    on = guidance(replace(machx, multimodal=True), profile, 'fixture',
                  vision={'state': 'on', 'enabled': True, 'source': READY})
    assert 'Image input' not in on
    # The session's decision drives the note, not the provider flag; without a decision the flag still does (DREAM-093).
    assert REASON in guidance(replace(machx, multimodal=True), profile, 'fixture', vision=off)
    assert 'Image input is off' in guidance(replace(machx, multimodal=False), profile, 'fixture')


# ---- (b) the backend's image switch and the header share one answer -----------------------------------------------

def serve_props(monkeypatch, payload, *, status=200):
    """Only HTTP is fake: GET /props answers `payload`; nothing else is reachable."""
    def transport(request):
        assert request.method == 'GET' and request.url.path == '/props'
        return httpx.Response(status, json=payload)
    client = httpx.AsyncClient

    class FixtureClient(client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(openai_compat.httpx, 'AsyncClient', FixtureClient)


async def connected(monkeypatch, arch, payload, *, profile_vision=None, status=200):
    serve_props(monkeypatch, payload, status=status)
    with session_options({}, 'fixture', capabilities=caps(arch)):
        backend = OpenAICompatBackend(
            provider=replace(get_provider('machx'), base_url='http://fixture.invalid/v1'), model='fixture',
            system_prompt='Synthetic fixture', tools=[see], permission_cb=None,
            profile=replace(PROFILES['balanced'], vision=profile_vision, auto_filer=False))
    await backend.connect()
    return backend


@pytest.mark.parametrize('arch,payload,enabled,state,words', [
    (True, props(False, REASON), False, 'off', REASON),
    (False, props(True), True, 'on', READY),                                  # the server's word beats the weights
    (None, props(True), True, 'on', READY),
    (True, {**OLDER_ENGINE, 'vision': {}}, False, 'unreported', 'readiness not reported'),   # missing key: support alone
    (True, OLDER_ENGINE, True, 'on', 'MachX capability report'),              # the older engine: DREAM-093 (c)
    (False, OLDER_ENGINE, False, 'off', 'MachX capability report'),           # the older engine, MiMo-shaped: DREAM-093
    (None, OLDER_ENGINE, False, 'unreported', 'no vision capability'),
    (True, {**OLDER_ENGINE, 'vision': 'yes'}, False, 'unreported', 'readiness not reported'),
], ids=['not-ready-with-reason', 'ready-beats-text-only-weights', 'ready-unknown-weights', 'missing-ready-key',
        'older-engine-seeing-weights', 'older-engine-text-only', 'older-engine-nothing-reported', 'malformed'])
async def test_the_image_switch_and_the_header_share_one_answer(monkeypatch, arch, payload, enabled, state, words):
    b = await connected(monkeypatch, arch, payload)
    try:
        status = b.vision_status()   # what Engine.vision_status hands the header
        assert status['state'] == state and status['enabled'] is enabled and words in status['source']
        assert b.provider.multimodal is enabled and ('see' in b.tools_by_name) is enabled
        report = b.capability_status()
        readiness = payload.get('vision')
        assert report['vision_ready']['known'] is (isinstance(readiness, dict) and type(readiness.get('ready')) is bool)
        assert report['vision']['known'] is (arch is not None)   # architecture support stays a separate fact
        if arch is not None:
            assert report['vision']['value'] is arch
    finally:
        await b.disconnect()


async def test_a_readiness_that_explains_the_configuration_is_not_a_contradiction_warning(monkeypatch):
    b = await connected(monkeypatch, True, props(False, REASON))
    try:
        warnings = b.capability_status()['warnings']
        assert 'Reported image input is supported, but tool images are disabled.' not in warnings
        assert not any(w.startswith('malformed') for w in warnings)
    finally:
        await b.disconnect()


@pytest.mark.parametrize('override,payload', [(True, props(False, REASON)), (False, props(True))])
async def test_the_profile_setting_wins_at_the_backend_too(monkeypatch, override, payload):
    b = await connected(monkeypatch, True, payload, profile_vision=override)
    try:
        assert b.provider.multimodal is override and ('see' in b.tools_by_name) is override
        assert b.vision_status() == {'state': 'on' if override else 'off', 'enabled': override, 'source': 'Profile setting'}
    finally:
        await b.disconnect()


async def test_a_server_that_does_not_answer_props_keeps_the_dream_093_answer(monkeypatch):
    b = await connected(monkeypatch, True, {}, status=500)
    try:
        assert b.vision_status() == {'state': 'on', 'enabled': True, 'source': 'MachX capability report'}
        assert b.provider.multimodal is True
        assert 'unavailable.machx.props' in b.capability_status()['warnings']   # the Controls pane says why
    finally:
        await b.disconnect()


# ---- (b) a started Engine: header, Runtime note and image switch agree --------------------------------------------

@pytest.fixture
def hermetic(tmp_path, monkeypatch):
    """Every path Dream writes goes to tmp (as tests/test_workspace_prompt.py does); the real OpenAI-compatible backend
    talks to the fake /props only. Engine.start sets the global tool context: hand it back afterwards."""
    import dream.config as config
    import dream.core.engine as engine_mod
    from dream.tools import context as tool_context
    data, mem, var = tmp_path / 'data', tmp_path / 'memory', tmp_path / 'var'
    for name, value in dict(SEMANTIC_MEMORY=False, DATA_DIR=data, SESSIONS_DIR=data / 'sessions',
                            DB_PATH=data / 'dream.db', MEMORY_DIR=mem, SEMANTIC_DIR=mem / 'semantic',
                            PROCEDURAL_DIR=mem / 'procedural', EPISODIC_DIR=mem / 'episodic',
                            IDENTITY_FILE=mem / 'IDENTITY.md', THREADS_FILE=mem / 'THREADS.md',
                            INSTRUCTIONS_FILE=mem / 'INSTRUCTIONS.md', VAR_DIR=var, LOG_DIR=var / 'logs',
                            LOOP_DIR=var / 'loops', SCREENSHOT_DIR=var / 'screenshots').items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(engine_mod, 'get_browser', lambda: None)
    monkeypatch.setattr(tool_context, '_CTX', tool_context._CTX)
    workspace = tmp_path / 'project'
    workspace.mkdir()
    return workspace


@pytest.mark.parametrize('payload,enabled', [(props(False, REASON), False), (props(True), True)],
                         ids=['not-ready', 'ready'])
async def test_a_started_engine_tells_the_model_what_the_header_shows(hermetic, monkeypatch, payload, enabled):
    from dream.core.engine import Engine
    serve_props(monkeypatch, payload)
    with session_options({}, 'fixture', capabilities=caps(True)):   # the weights can see; only the server knows if it is loaded
        engine = Engine(provider='machx', model='fixture', workspace=hermetic)
        assert engine.provider.multimodal is True   # before the server is asked: the architecture report (DREAM-093)
        await engine._start()
    try:
        header = engine.vision_status()
        assert header['enabled'] is enabled and header['state'] == ('on' if enabled else 'off')
        assert engine.backend.provider.multimodal is enabled
        assert engine._tool_context.multimodal is enabled
        assert engine.provider.multimodal is enabled
        note = engine.backend.messages[0]['content']
        assert note == engine._assembled_system_prompt
        if enabled:
            assert 'Image input is off' not in note
        else:
            assert 'Image input is off in this session' in note and REASON in note
            assert header['source'] in note   # the words the chip's tooltip shows
    finally:
        await engine._cleanup()


# ---- (c) the desktop attach survives a report Dream cannot validate (the DREAM-093 gate's note) --------------------

async def test_attaching_with_a_report_dream_cannot_validate_still_opens_the_session(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from dream.desktop import startup
    from dream.local import machx
    from dream.local.settings import read_session_capabilities
    from dream.tui import app
    seen, statuses = {}, []

    def construct(**kwargs):   # the App is built inside the session scope; record what the backend will read
        seen['caps'] = read_session_capabilities(kwargs['model'])
        return SimpleNamespace(run=AsyncMock())
    monkeypatch.setattr(app, 'App', construct)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'existing')
    monkeypatch.setattr(machx, 'served_model_path', lambda: tmp_path / 'existing-model')
    monkeypatch.setattr(machx, 'capabilities', lambda path: caps('yes'))   # accepted by machx.capabilities, not by Dream
    real_publish = startup.publish

    def publish(path, state, message):
        statuses.append((state, message))
        real_publish(path, state, message)
    monkeypatch.setattr(startup, 'publish', publish)
    await startup.run(dict(workspace=str(tmp_path), choice=dict(kind='attach', provider='machx', model='existing')),
                      tmp_path / 'status.json')
    assert seen['caps'] == {}   # attached with nothing reported, exactly as an unknown model does
    assert any(state == 'starting' and 'capabilities were not read' in message for state, message in statuses)
    assert statuses[-1][0] == 'ended'
