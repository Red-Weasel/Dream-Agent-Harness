"""DREAM-093: Dream shows vision up front.

2026-09-23: in session 20260923-094144-f5ed (MiMo-V2.6 on MachX, whose engine reports no vision for that architecture)
the model told the owner only in its final report that it could not see images. Now the session header says whether
image input is on, the model's Runtime notes say so when it is off, the local model picker shows vision per model, and
a MachX session takes image input from the engine's own report unless the owner's profile overrides it.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core.engine import Engine
from dream.core.profiles import guidance, resolve_profile, session_vision
from dream.core.providers import get_provider
from dream.desktop import startup
from dream.local import machx
from dream.local.models import LocalModel
from dream.local.settings import read_session_capabilities, session_options


def caps(vision):
    features = {'prompt_cache': True} if vision is None else {'prompt_cache': True, 'vision': vision}
    return {'schema_version': 1, 'supported': True, 'architecture': 'fixture', 'sampling': ['max_tokens'], 'load': [],
            'features': features}


@pytest.fixture(autouse=True)
def _no_vision_env(monkeypatch):
    monkeypatch.delenv('DREAM_VISION', raising=False)
    monkeypatch.delenv('DREAM_MACHX_SESSION_OPTIONS', raising=False)


# ---- (d) a MachX session takes image input from the engine's report unless the profile overrides it ---------------

@pytest.mark.parametrize('reported,state', [(True, 'on'), (False, 'off'), (None, 'unreported')])
def test_machx_follows_the_engine_report(reported, state):
    machx_provider = get_provider('machx')
    with session_options({}, 'fixture', capabilities=caps(reported)):
        vision = session_vision(machx_provider, resolve_profile(machx_provider, None, model='fixture'), 'fixture')
    assert vision['state'] == state and vision['enabled'] is (reported is True)
    assert vision['source'] == ('MachX capability report' if reported is not None
                                else 'MachX reported no vision capability for this model')


def test_the_owners_profile_setting_wins_over_the_report(monkeypatch):
    machx_provider = get_provider('machx')
    monkeypatch.setenv('DREAM_VISION', '0')
    with session_options({}, 'fixture', capabilities=caps(True)):
        vision = session_vision(machx_provider, resolve_profile(machx_provider, None, model='fixture'), 'fixture')
    assert vision == {'state': 'off', 'enabled': False, 'source': 'Profile setting'}
    monkeypatch.setenv('DREAM_VISION', '1')
    with session_options({}, 'fixture', capabilities=caps(False)):
        vision = session_vision(machx_provider, resolve_profile(machx_provider, None, model='fixture'), 'fixture')
    assert vision == {'state': 'on', 'enabled': True, 'source': 'Profile setting'}


def test_other_providers_keep_their_declared_default_and_a_rejection_reads_off():
    claude, grok = get_provider('anthropic'), get_provider('grok')
    assert session_vision(claude, resolve_profile(claude, None, model='m'), 'm')['state'] == 'on'
    assert session_vision(grok, resolve_profile(grok, None, model='m'), 'm')['state'] == 'off'
    rejected = session_vision(claude, resolve_profile(claude, None, model='m'), 'm', image_rejected=True)
    assert rejected['state'] == 'off' and 'rejected' in rejected['source']


def test_an_engine_session_turns_image_input_on_from_the_report(tmp_path, monkeypatch):
    with session_options({}, 'fixture', capabilities=caps(True)):
        assert Engine(provider='machx', model='fixture', workspace=tmp_path).provider.multimodal is True
    with session_options({}, 'fixture', capabilities=caps(False)):
        assert Engine(provider='machx', model='fixture', workspace=tmp_path).provider.multimodal is False
    assert Engine(provider='machx', model='fixture', workspace=tmp_path).provider.multimodal is False   # nothing reported
    monkeypatch.setenv('DREAM_VISION', '0')
    with session_options({}, 'fixture', capabilities=caps(True)):
        engine = Engine(provider='machx', model='fixture', workspace=tmp_path)
    assert engine.provider.multimodal is False and engine.vision_status()['source'] == 'Profile setting'


@pytest.mark.asyncio
async def test_attaching_to_the_running_model_captures_its_capabilities(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from dream.tui import app
    seen = {}

    def construct(**kwargs):   # the App is built inside the session scope; record what the backend will read
        seen['caps'] = read_session_capabilities(kwargs['model'])
        return SimpleNamespace(run=AsyncMock())
    monkeypatch.setattr(app, 'App', construct)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'existing')
    served = tmp_path / 'existing-model'
    monkeypatch.setattr(machx, 'served_model_path', lambda: served)
    monkeypatch.setattr(machx, 'capabilities', lambda path: caps(True) if path == served else pytest.fail(str(path)))
    await startup.run(dict(workspace=str(tmp_path), choice=dict(kind='attach', provider='machx', model='existing')),
                      tmp_path / 'status.json')
    assert seen['caps']['features']['vision'] is True


@pytest.mark.asyncio
async def test_attaching_without_a_known_model_path_still_works(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    from dream.tui import app
    seen = {}

    def construct(**kwargs):
        seen['caps'] = read_session_capabilities(kwargs['model'])
        return SimpleNamespace(run=AsyncMock())
    monkeypatch.setattr(app, 'App', construct)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'existing')
    monkeypatch.setattr(machx, 'served_model_path', lambda: None)
    await startup.run(dict(workspace=str(tmp_path), choice=dict(kind='attach', provider='machx', model='existing')),
                      tmp_path / 'status.json')
    assert seen['caps'] == {}


def test_the_served_model_path_comes_from_the_server_process_then_a_unique_name(monkeypatch, tmp_path):
    model = tmp_path / 'MiMo-fixture'
    model.mkdir()
    pid_file = tmp_path / 'machx.pid'
    monkeypatch.setattr(machx, '_pid_file', lambda: pid_file)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'MiMo-fixture')
    monkeypatch.setattr(machx, '_cmdline', lambda pid: ['./build/src/ie', 'serve', str(model), '--port', '11435']
                        if pid == 4242 else None)
    pid_file.write_text('4242')
    assert machx.served_model_path() == model.resolve()
    pid_file.write_text('999')   # a stale pid: fall back to the one local model with the served name
    other = tmp_path / 'other.gguf'
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [LocalModel('MiMo-fixture', model, 1.0, 'home'),
                                                                 LocalModel('other', other, 1.0, 'home')])
    assert machx.served_model_path() == model
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [LocalModel('MiMo-fixture', model, 1.0, 'home'),
                                                                 LocalModel('MiMo-fixture', tmp_path / 'b' / 'MiMo-fixture', 1.0, 'usb')])
    assert machx.served_model_path() is None   # two candidates: unknown, never a guess


# ---- (b) the Runtime notes say image input is off when it is, and nothing when it is on ---------------------------

def test_runtime_notes_name_image_input_only_when_it_is_off():
    provider = get_provider('machx')
    profile = resolve_profile(provider, None, model='m')
    off = guidance(replace(provider, multimodal=False), profile, 'm')
    on = guidance(replace(provider, multimodal=True), profile, 'm')
    assert 'Image input is off' in off
    assert 'Image input' not in on


# ---- (c) the local model picker shows vision per model ------------------------------------------------------------

def test_the_picker_shows_vision_per_local_model(monkeypatch, tmp_path):
    from dream.tui import picker
    seeing, blind, broken = tmp_path / 'seeing.gguf', tmp_path / 'blind', tmp_path / 'broken.gguf'
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'blind')
    monkeypatch.setattr(machx, 'served_model_path', lambda: blind)
    monkeypatch.setattr(machx, 'available', lambda: True)
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [LocalModel('seeing', seeing, 10.0, 'nvme'),
                                                                 LocalModel('blind', blind, 20.0, 'nvme'),
                                                                 LocalModel('broken', broken, 5.0, 'usb')])

    def probe(path):
        if path == broken:
            raise ValueError('MachX capability check failed')
        return caps(path == seeing)
    monkeypatch.setattr(machx, 'capabilities', probe)
    monkeypatch.setattr(picker, 'detect_providers', lambda: [])
    rows = {row['id']: row for row in startup.catalog()['choices']}
    assert rows['running']['vision'] is False and rows['running']['label'].endswith('· text only')
    assert rows[str(seeing)]['vision'] is True and rows[str(seeing)]['label'].endswith('· sees images')
    assert rows[str(blind)]['vision'] is False and rows[str(blind)]['label'].endswith('· text only')
    assert rows[str(broken)]['vision'] is None and rows[str(broken)]['label'].endswith('· vision unreported')


# ---- (a) the session header shows a vision chip -------------------------------------------------------------------

def test_the_session_info_carries_the_vision_state():
    from dream.tui.app import App
    app = object.__new__(App)
    app.workspace = Path('/tmp')
    app.provider_label = 'MachX'
    app.engine = SimpleNamespace(session_id='s', model='m', effort=None,
                                 vision_status=lambda: {'state': 'off', 'enabled': False, 'source': 'MachX capability report'})
    info = app._studio_session_info()
    assert info['vision'] == {'state': 'off', 'enabled': False, 'source': 'MachX capability report'}


@pytest.mark.asyncio
@pytest.mark.parametrize('vision,text', [({'state': 'off', 'enabled': False, 'source': 'MachX capability report'}, 'Vision · off'),
                                         ({'state': 'on', 'enabled': True, 'source': 'Provider default'}, 'Vision · on'),
                                         (None, 'Vision · unreported')])
async def test_the_header_chip_shows_it(monkeypatch, tmp_path, vision, text):
    from playwright.async_api import async_playwright, expect
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    session = {'workspace': str(tmp_path), 'model': 'fixture', 'session_id': 'fixture-session'}
    if vision is not None:
        session['vision'] = vision
    srv = StudioServer(EventBus(), on_prompt=lambda p: None, session=session)
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1400, 'height': 900})
            await page.goto(url + '&companion=1')
            chip = page.locator('#dream-vision')
            await expect(chip).to_have_text(text, timeout=6000)
            if vision is not None:
                assert vision['source'] in (await chip.get_attribute('title'))
            await browser.close()
    finally:
        await srv.stop()
