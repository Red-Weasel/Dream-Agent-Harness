"""V4.1 disk-backed residency must not require all checkpoint bytes in RAM."""
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from dream.desktop import startup
from dream.local import machx, model_defaults, settings

CAPS = {'architecture': 'deepseek_v41', 'memory_planner': 'streaming'}


@pytest.fixture
def hardware(monkeypatch):
    from dream import telemetry
    @dataclass
    class Snapshot:
        ok: bool
        devices: list
    snap = Snapshot(True, [dict(name='Arc', integrated=False, vram_total_mib=32768,
                              vram_used_mib=512, procs=[], temp_c=45,
                              vram_temp_c=45, util_pct=0) for _ in range(2)])
    host = {'ram_available_gb': 200}
    monkeypatch.setattr(telemetry, 'GpuSampler', lambda: SimpleNamespace(sample_once=lambda: snap))
    monkeypatch.setattr(model_defaults, 'inspect_hardware', lambda: host)
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    monkeypatch.setattr(machx, 'served_model_id', lambda: None)
    monkeypatch.setattr(startup.time, 'sleep', lambda _: None)
    return snap, host


def test_v41_can_use_disk_backed_experts_when_weights_exceed_ram(hardware):
    message = startup.launch_preflight(475, 2, CAPS)
    assert 'disk' in message.lower()
    assert '40 GiB' in message


@pytest.mark.parametrize('available', [None, 0, 39, 40, float('nan')])
def test_v41_preserves_host_reserve(hardware, available):
    hardware[1]['ram_available_gb'] = available
    with pytest.raises(ValueError):
        startup.launch_preflight(475, 2, CAPS)


@pytest.mark.parametrize('condition', ['busy', 'hot', 'unknown', 'gpu_memory', 'serving'])
def test_v41_does_not_bypass_resource_guards(hardware, monkeypatch, condition):
    snap, _ = hardware
    if condition == 'busy':
        snap.devices[1]['procs'] = [{'name': 'other-model', 'pid': 123}]
    elif condition == 'hot':
        snap.devices[1]['temp_c'] = 90
    elif condition == 'unknown':
        snap.ok = False
    elif condition == 'gpu_memory':
        snap.devices[1]['vram_used_mib'] = 30000
    else:
        monkeypatch.setattr(machx, 'is_serving', lambda: True)
    with pytest.raises(ValueError):
        startup.launch_preflight(475, 2, CAPS)


def test_v41_cannot_check_one_card_then_let_engine_use_all(hardware):
    with pytest.raises(ValueError, match='all.*GPU'):
        startup.launch_preflight(475, 1, CAPS)


@pytest.mark.parametrize('caps', [dict(architecture='deepseek4', memory_planner='streaming'),
                                 dict(architecture='deepseek_v41', memory_planner='resident'),
                                 dict(architecture='deepseek_v41')])
def test_other_memory_planners_keep_existing_weight_fit_check(hardware, caps):
    with pytest.raises(ValueError):
        startup.launch_preflight(475, 2, caps)


def test_v41_parallel_control_is_limited_to_one():
    controls = settings.available_controls({**CAPS, 'load': ['parallel'], 'defaults': {'parallel': 1}})
    control = next(c for c in controls if c.name == 'parallel')
    assert control.maximum == 1
    with pytest.raises(ValueError):
        settings.parse_value(control, '2')


def test_v41_native_selection_rejects_multiple_slots():
    metadata = {**CAPS, 'controls': [{'name': 'parallel', 'choices': []}]}
    with pytest.raises(ValueError, match='parallel'):
        startup.validate_selection(dict(ctx=4096, gpus=2, options={'parallel': 2}), metadata)


def test_v41_reserve_applies_even_to_small_checkpoint(hardware):
    hardware[1]['ram_available_gb'] = 30
    with pytest.raises(ValueError, match='40 GiB'):
        startup.launch_preflight(1, 2, CAPS)


@pytest.mark.asyncio
async def test_cli_v41_uses_shared_preflight_and_never_offers_override(tmp_path, monkeypatch):
    from io import StringIO
    from rich.console import Console
    from dream.local import launcher
    path = tmp_path / 'fixture.gguf'
    path.write_bytes(b'fixture')
    monkeypatch.setattr(machx, 'capabilities', lambda _: {**CAPS, 'supported': True})
    async def choose(*args):
        return {'ctx': 4096, 'gpus': 2, 'options': {'parallel': 1}}, None
    monkeypatch.setattr(launcher, '_choose_model_settings', choose)
    checked = []
    def refuse(size, gpus, caps):
        checked.append((size, gpus, caps))
        raise ValueError('other model is busy')
    monkeypatch.setattr(startup, 'launch_preflight', refuse)
    monkeypatch.setattr(launcher, '_ask', lambda *a, **kw: pytest.fail('no override'))
    monkeypatch.setattr(machx, 'serve', lambda *a, **kw: pytest.fail('must not load'))
    errors = []
    renderer = SimpleNamespace(system=lambda _: None, error=errors.append)
    await launcher.serve_and_run(Console(file=StringIO()), renderer, 'fixture', path)
    assert len(checked) == 1 and checked[0][1] == 2
    assert errors == ['other model is busy']
