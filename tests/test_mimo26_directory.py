"""MiMo-V2.6 (model_type mimo_v2) is a MachX directory checkpoint like DeepSeek-V4.1: offered in the picker, flat config
metadata, one request at a time, and the disk-backed streaming preflight (engine docs/mimo26/00_PORT_PLAN.md P3b)."""
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from dream.desktop import startup
from dream.local import machx, model_defaults, models, settings

CAPS = {'architecture': 'mimo_v2', 'memory_planner': 'streaming'}


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    root = tmp_path / 'MiMo-V2.6-Flash-RL'
    root.mkdir()
    (root / 'config.json').write_text(json.dumps({
        'model_type': 'mimo_v2', 'max_position_embeddings': 1048576, 'num_hidden_layers': 48, 'hidden_size': 4096}))
    (root / 'model.safetensors.index.json').write_text(json.dumps({
        'weight_map': {'a': 'model_pp0_ep0_shard0.safetensors', 'b': 'model_mtp.safetensors'}}))
    for name in ('model_pp0_ep0_shard0.safetensors', 'model_mtp.safetensors'):
        with (root / name).open('wb') as out:
            out.truncate(100_000_000)
    monkeypatch.setattr(models, '_base_roots', lambda: [tmp_path, root])
    monkeypatch.setattr(models, '_mounted_volume_roots', lambda: [])
    return root


def test_mimo_directory_discovered(checkpoint):
    found = models.scan_models()
    assert len(found) == 1 and found[0].path == checkpoint
    assert found[0].size_gb == pytest.approx(.2)


def test_mimo_flat_config_metadata(checkpoint):
    meta = model_defaults.read_metadata(checkpoint)
    assert meta['general.architecture'] == 'mimo_v2'
    assert meta['mimo_v2.context_length'] == 1048576
    assert meta['mimo_v2.block_count'] == 48


def test_unknown_directory_architecture_still_refused(checkpoint):
    (checkpoint / 'config.json').write_text(json.dumps({'model_type': 'llama'}))
    with pytest.raises(ValueError):
        models.directory_model_files(checkpoint)


@pytest.fixture
def hardware(monkeypatch):
    from dream import telemetry

    @dataclass
    class Snapshot:
        ok: bool
        devices: list
    snap = Snapshot(True, [dict(name='Arc', integrated=False, vram_total_mib=32768, vram_used_mib=512, procs=[],
                                temp_c=45, vram_temp_c=45, util_pct=0) for _ in range(2)])
    host = {'ram_available_gb': 200}
    monkeypatch.setattr(telemetry, 'GpuSampler', lambda: SimpleNamespace(sample_once=lambda: snap))
    monkeypatch.setattr(model_defaults, 'inspect_hardware', lambda: host)
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    monkeypatch.setattr(machx, 'served_model_id', lambda: None)
    monkeypatch.setattr(startup.time, 'sleep', lambda _: None)
    return snap, host


def test_mimo_uses_disk_backed_streaming_preflight(hardware):
    message = startup.launch_preflight(178, 2, CAPS)   # 178 GB of weights on 64 GB of VRAM: the streaming route
    assert 'disk' in message.lower() and '40 GiB' in message


def test_mimo_keeps_the_host_reserve(hardware):
    hardware[1]['ram_available_gb'] = 39
    with pytest.raises(ValueError, match='40 GiB'):
        startup.launch_preflight(178, 2, CAPS)


def test_mimo_uses_every_card(hardware):
    with pytest.raises(ValueError, match='all visible GPUs'):
        startup.launch_preflight(178, 1, CAPS)


def test_mimo_parallel_is_one():
    caps = {'architecture': 'mimo_v2', 'memory_planner': 'streaming',
            'load': ['parallel'], 'defaults': {'parallel': 1}, 'features': {}}
    parallel = [c for c in settings.available_controls(caps) if c.name == 'parallel']
    assert parallel and parallel[0].maximum == 1
    with pytest.raises(ValueError, match='parallel = 1'):
        settings.validate_options({'parallel': 2}, architecture='mimo_v2')
