"""Graphical startup contract, with fixture models and no GPU/model loads."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from dream.desktop import startup
from dream.local import machx


@pytest.fixture
def settings():
    return dict(identity='model-key', revision=None, context_limit=32768, max_gpus=2,
                controls=[dict(name='temperature', choices=[]), dict(name='max_tokens', choices=[])],
                selection=dict(ctx=32768, gpus=2, options={'temperature': .7, 'max_tokens': 4096}))


def test_selection_rejects_changed_capabilities_and_invalid_counts(settings):
    for values in [dict(ctx=32769, gpus=2, options={}), dict(ctx=100, gpus=3, options={}),
                   dict(ctx=True, gpus=1, options={}), dict(ctx=100, gpus=1, options={'thinking': True})]:
        with pytest.raises(ValueError):
            startup.validate_selection(values, settings)
    assert startup.validate_selection(settings['selection'], settings) == settings['selection']


def test_catalog_prefers_attach_and_never_claims_auth_verified(monkeypatch):
    from dream.tui import picker
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'running-model')
    monkeypatch.setattr(machx, 'available', lambda: False)
    monkeypatch.setattr(picker, 'detect_providers', lambda: [SimpleNamespace(key='codex', label='ChatGPT', ready=True)])
    data = startup.catalog()['choices']
    assert data[0]['kind'] == 'attach'
    assert data[0]['model'] == 'running-model'
    assert 'first message' in data[1]['note']


@pytest.mark.asyncio
async def test_attach_passes_workspace_and_never_starts_or_stops_model(monkeypatch, tmp_path):
    from dream.tui import app
    fake = SimpleNamespace(run=AsyncMock())
    constructor = Mock(return_value=fake)
    monkeypatch.setattr(app, 'App', constructor)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'existing')
    serve, stop = Mock(), Mock()
    monkeypatch.setattr(machx, 'serve', serve)
    monkeypatch.setattr(machx, 'stop', stop)
    await startup.run(dict(workspace=str(tmp_path), choice=dict(kind='attach', provider='machx', model='existing')), tmp_path/'status.json')
    fake.run.assert_awaited_once()
    assert constructor.call_args.kwargs['workspace'] == tmp_path
    assert constructor.call_args.kwargs['gui'] is True
    serve.assert_not_called()
    stop.assert_not_called()


@pytest.mark.asyncio
async def test_changed_attachment_cannot_switch_model_silently(monkeypatch, tmp_path):
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'different')
    with pytest.raises(ValueError, match='changed or stopped'):
        await startup.run(dict(workspace=str(tmp_path), choice=dict(kind='attach', provider='machx', model='old')), tmp_path/'status.json')


@pytest.mark.asyncio
async def test_failed_local_start_stops_only_the_owned_child(monkeypatch, tmp_path, settings):
    from dream.local import model_presets
    path = tmp_path/'fixture.gguf'
    path.write_bytes(b'fixture')
    monkeypatch.setattr(startup, 'model_settings', lambda _: settings)
    monkeypatch.setattr(startup, 'launch_preflight', lambda *_: 'fit')
    monkeypatch.setattr(model_presets, 'model_key', lambda _: 'model-key')
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [SimpleNamespace(path=path, size_gb=1, name='fixture')])
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    proc = Mock(poll=Mock(return_value=None))
    monkeypatch.setattr(machx, 'serve', lambda *a, **k: proc)
    monkeypatch.setattr(machx, 'wait_ready', lambda _: False)
    global_stop = Mock()
    monkeypatch.setattr(machx, 'stop', global_stop)
    request = dict(workspace=str(tmp_path), choice=dict(kind='local', provider='machx', path=str(path)),
                   selection=settings['selection'], identity='model-key', revision=None)
    with pytest.raises(ValueError, match='did not become ready'):
        await startup.run(request, tmp_path/'status.json')
    proc.terminate.assert_called_once()
    global_stop.assert_not_called()


@dataclass
class Snapshot:
    ok: bool
    devices: list


def gpu_fixture(monkeypatch, *, processes=None, used=0, temp=45):
    from dream import telemetry
    from dream.local import model_defaults
    snap = Snapshot(True, [dict(integrated=False, vram_total_mib=32768, vram_used_mib=used,
                               procs=processes or [], temp_c=temp, vram_temp_c=45, util_pct=0)] * 2)
    monkeypatch.setattr(telemetry, 'GpuSampler', lambda: SimpleNamespace(sample_once=lambda: snap))
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    monkeypatch.setattr(machx, 'served_model_id', lambda: None)
    monkeypatch.setattr(model_defaults, 'inspect_hardware', lambda: {'ram_available_gb': 225})
    return snap


def test_supported_glm_streaming_is_not_rejected_by_disk_size(monkeypatch):
    gpu_fixture(monkeypatch)
    settings = dict(architecture='glm5next', memory_policy={'host_banks': 'pinned_auto', 'expert_cache_bytes': 0})
    assert 'host RAM' in startup.launch_preflight(199.7, 2, settings)
    with pytest.raises(ValueError, match='Weights exceed VRAM'):
        startup.launch_preflight(199.7, 2, {})


@pytest.mark.parametrize('architecture', ['deepseek4', 'qwen4exp', 'glm5next'])
def test_engine_streaming_planner_uses_host_ram_not_full_weight_vram(monkeypatch, architecture):
    gpu_fixture(monkeypatch)
    message = startup.launch_preflight(153.4, 2, dict(architecture=architecture, memory_planner='streaming'))
    assert 'host RAM' in message and 'engine' in message.lower()


def test_model_settings_preserve_engine_memory_planner(monkeypatch, tmp_path):
    from dream.local import model_defaults, model_presets
    path = tmp_path / 'fixture.gguf'
    path.write_bytes(b'fixture')
    monkeypatch.setattr(model_presets, 'model_key', lambda _: 'fixture')
    monkeypatch.setattr(model_presets.Presets, 'load', lambda *_: None)
    caps = dict(supported=True, architecture='deepseek4', memory_planner='streaming', sampling=[], load=[])
    monkeypatch.setattr(machx, 'capabilities', lambda _: caps)
    monkeypatch.setattr(model_defaults, 'recommend', lambda *_: dict(
        gpus=2, ctx=8192, options={}, sources={}, notes=[], context_limit=1000000))
    settings = startup.model_settings(path)
    assert settings['memory_planner'] == 'streaming'
    assert 'full model file does not need to fit in VRAM' in settings['notes'][0]


@pytest.mark.parametrize('case', ['occupied', 'hot', 'unknown', 'serving', 'low_ram', 'low_vram'])
def test_streaming_planner_does_not_bypass_resource_guards(monkeypatch, case):
    from dream.local import model_defaults
    snap = gpu_fixture(monkeypatch, processes=[dict(name='benchmark', pid=99)] if case == 'occupied' else [],
                       temp=90 if case == 'hot' else 45, used=30000 if case == 'low_vram' else 0)
    if case == 'unknown':
        snap.ok = False
    if case == 'serving':
        monkeypatch.setattr(machx, 'is_serving', lambda: True)
    if case == 'low_ram':
        monkeypatch.setattr(model_defaults, 'inspect_hardware', lambda: {'ram_available_gb': 40})
    with pytest.raises(ValueError):
        startup.launch_preflight(153.4, 2, dict(architecture='deepseek4', memory_planner='streaming'))


@pytest.mark.parametrize('case', ['occupied', 'hot', 'unknown', 'serving'])
def test_gpu_guard_blocks_unsafe_start(monkeypatch, case):
    snap = gpu_fixture(monkeypatch, processes=[dict(name='ie', pid=99)] if case == 'occupied' else [],
                       temp=90 if case == 'hot' else 45)
    if case == 'unknown':
        snap.ok = False
    if case == 'serving':
        monkeypatch.setattr(machx, 'is_serving', lambda: True)
    with pytest.raises(ValueError):
        startup.launch_preflight(1, 2, {})


def test_catalog_excludes_auxiliary_ggufs(monkeypatch, tmp_path):
    from dream.tui import picker
    monkeypatch.setattr(machx, 'served_model_id', lambda: None)
    monkeypatch.setattr(machx, 'available', lambda: True)
    monkeypatch.setattr(picker, 'detect_providers', lambda: [])
    names = ['mmproj-F16.gguf', 'MMProj-Q8_0.gguf', 'mtp-head.gguf', 'chat-model.gguf']
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [
        SimpleNamespace(path=tmp_path/name, name=name, size_gb=1, volume='fixture') for name in names])
    assert [row['path'] for row in startup.catalog()['choices']] == [str(tmp_path/'chat-model.gguf')]


@pytest.mark.parametrize('field', ['util_pct', 'procs', 'vram_total_mib', 'vram_used_mib', 'temperatures'])
def test_missing_gpu_readings_are_not_treated_as_idle(monkeypatch, field):
    snap = gpu_fixture(monkeypatch)
    device = snap.devices[0]
    if field == 'temperatures':
        device['temp_c'] = device['vram_temp_c'] = None
    else:
        device[field] = None
    with pytest.raises(ValueError, match='unavailable'):
        startup.launch_preflight(1, 2, {})


@pytest.fixture(autouse=True)
def isolated_local_load_port(monkeypatch):
    """Fixture lock names must never contend with an operator's actual server."""
    import uuid
    monkeypatch.setattr(machx, 'PORT', 'fixture-' + uuid.uuid4().hex)


def test_load_lock_contends_across_processes_and_dream_roots(monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    from dream import config

    monkeypatch.setattr(config, 'ROOT', tmp_path/'first-root')
    code = """import sys
from dream.local import machx
from dream.desktop.startup import local_load_lock
machx.PORT = sys.argv[1]
try:
    with local_load_lock():
        print('acquired')
except ValueError as exc:
    print(str(exc))
    raise SystemExit(7)
"""
    env = dict(os.environ, DREAM_ROOT=str(tmp_path/'different-root'))
    def competing_launcher():
        return subprocess.run([sys.executable, '-c', code, str(machx.PORT)], env=env,
                              capture_output=True, text=True, timeout=10)
    with startup.local_load_lock():
        result = competing_launcher()
        assert result.returncode == 7, result.stderr
        assert 'Another Dream window is loading or using a local model' in result.stdout
    result = competing_launcher()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'acquired'


@pytest.mark.parametrize('unsafe', ['symlink', 'hardlink', 'permissions'])
def test_load_lock_rejects_unsafe_files(tmp_path, unsafe):
    import os
    path = Path('/tmp') / f'dream-machx-load-{os.getuid()}-{machx.PORT}.lock'
    target = tmp_path/'target'
    target.write_text('preserve')
    target.chmod(0o600)
    if unsafe == 'symlink':
        path.symlink_to(target)
    elif unsafe == 'hardlink':
        os.link(target, path)
    else:
        path.write_text('preserve')
        path.chmod(0o644)
    try:
        with pytest.raises(ValueError, match='[Ss]afe|Unsafe'):
            with startup.local_load_lock():
                pytest.fail('Unsafe lock acquired')
        assert target.read_text() == 'preserve'
    finally:
        path.unlink()  # No lock was acquired on these unsafe fixture entries.


@pytest.mark.asyncio
async def test_load_lock_precedes_preflight_and_outlives_owned_child(monkeypatch, tmp_path, settings):
    from dream.local import model_presets
    path = tmp_path/'fixture.gguf'
    path.write_bytes(b'fixture')
    def assert_locked():
        with pytest.raises(ValueError, match='Another Dream window'):
            with startup.local_load_lock():
                pytest.fail('Second launcher entered during the owned lifetime')
    def preflight(*_):
        assert_locked()
        return 'fit'
    monkeypatch.setattr(startup, 'model_settings', lambda _: settings)
    monkeypatch.setattr(startup, 'launch_preflight', preflight)
    monkeypatch.setattr(model_presets, 'model_key', lambda _: 'model-key')
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [SimpleNamespace(path=path, size_gb=1, name='fixture')])
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    proc = Mock(poll=Mock(return_value=None))
    proc.wait.side_effect = lambda **_: assert_locked()
    monkeypatch.setattr(machx, 'serve', lambda *a, **k: proc)
    monkeypatch.setattr(machx, 'wait_ready', lambda _: False)
    request = dict(workspace=str(tmp_path), choice=dict(kind='local', provider='machx', path=str(path)),
                   selection=settings['selection'], identity='model-key', revision=None)
    with pytest.raises(ValueError, match='did not become ready'):
        await startup.run(request, tmp_path/'status.json')
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once()
    with startup.local_load_lock():
        pass  # Child cleanup completed, so another launcher can proceed.
