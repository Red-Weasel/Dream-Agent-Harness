"""Directory checkpoints must reach the picker without loading tensors."""
import json
from pathlib import Path

import pytest

from dream.local import models, model_defaults, model_presets


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    root = tmp_path / 'DeepSeek-V4.1-Flash'
    root.mkdir()
    (root / 'config.json').write_text(json.dumps({
        'model_type': 'deepseek_v41', 'text_config': {
            'max_position_embeddings': 1048576, 'num_hidden_layers': 40}}))
    (root / 'model.safetensors.index.json').write_text(json.dumps({
        'weight_map': {'a': 'model-1.safetensors', 'b': 'model-2.safetensors',
                       'c': 'model-2.safetensors'}}))
    for name in ('model-1.safetensors', 'model-2.safetensors'):
        with (root / name).open('wb') as out:
            out.truncate(100_000_000)
    monkeypatch.setattr(models, '_base_roots', lambda: [tmp_path, root])
    monkeypatch.setattr(models, '_mounted_volume_roots', lambda: [])
    return root


def test_directory_checkpoint_discovered_once_with_total_weight_size(checkpoint):
    found = models.scan_models()
    assert len(found) == 1
    assert found[0].path == checkpoint
    assert 'DeepSeek-V4.1-Flash' in found[0].name
    assert 'Safetensors' in found[0].name
    assert found[0].size_gb == pytest.approx(.2)


def test_directory_context_read_without_gguf_fallback(checkpoint):
    meta = model_defaults.read_metadata(checkpoint)
    assert meta['general.architecture'] == 'deepseek_v41'
    assert meta['deepseek_v41.context_length'] == 1048576
    recommendation = model_defaults.recommend(checkpoint, {}, hardware={})
    assert recommendation['context_limit'] == 1048576
    assert not any('unavailable' in n for n in recommendation['notes'])


def test_preset_identity_detects_in_place_shard_change(checkpoint):
    before = model_presets.model_key(checkpoint)
    with (checkpoint / 'model-2.safetensors').open('ab') as out:
        out.write(b'changed')
    assert model_presets.model_key(checkpoint) != before


@pytest.mark.parametrize('defect', ['missing', 'empty', 'bad_index', 'traversal', 'unsupported'])
def test_incomplete_or_unsupported_directory_not_offered(checkpoint, defect):
    if defect == 'missing':
        (checkpoint / 'model-2.safetensors').unlink()
    elif defect == 'empty':
        (checkpoint / 'model-2.safetensors').write_bytes(b'')
    elif defect == 'bad_index':
        (checkpoint / 'model.safetensors.index.json').write_text('{')
    elif defect == 'traversal':
        (checkpoint / 'model.safetensors.index.json').write_text(json.dumps({
            'weight_map': {'a': '../outside.safetensors'}}))
    else:
        (checkpoint / 'config.json').write_text('{"model_type":"unsupported"}')
    assert models.scan_models() == []


def test_catalog_includes_directory_checkpoint(checkpoint, monkeypatch):
    from dream.desktop import startup
    from dream.tui import picker
    monkeypatch.setattr(models.machx, 'served_model_id', lambda: None)
    monkeypatch.setattr(models.machx, 'available', lambda: True)
    monkeypatch.setattr(picker, 'detect_providers', lambda: [])
    rows = startup.catalog()['choices']
    assert len(rows) == 1
    assert rows[0]['path'] == str(checkpoint)
    assert rows[0]['kind'] == 'local'


def test_preset_identity_tracks_engine_prepared_banks(checkpoint):
    bank = checkpoint / 'ie_experts_tail.ieslot'
    bank.write_bytes(b'bank')
    before = model_presets.model_key(checkpoint)
    bank.write_bytes(b'changed bank')
    assert model_presets.model_key(checkpoint) != before


def test_checkpoint_metadata_read_is_bounded(checkpoint):
    with (checkpoint / 'config.json').open('wb') as stream:
        stream.truncate(33 * 1024 * 1024)
    assert models.scan_models() == []
    with pytest.raises(ValueError, match='budget'):
        model_defaults.read_metadata(checkpoint)


@pytest.mark.asyncio
async def test_cli_directory_preflight_receives_full_weight_size(checkpoint, monkeypatch):
    from types import SimpleNamespace
    from dream.local import launcher, machx, preflight
    from rich.console import Console
    from io import StringIO
    monkeypatch.setattr(machx, 'capabilities', lambda _: {'supported': True})
    async def choose(*args):
        return {'gpus': 1, 'ctx': 4096, 'options': {}}, None
    async def decline(*args):
        return 'n'
    monkeypatch.setattr(launcher, '_choose_model_settings', choose)
    monkeypatch.setattr(launcher, '_ask', decline)
    measured = []
    def check(size, gpus):
        measured.append(size)
        return SimpleNamespace(should_load=False, reason='fixture refuses load',
                               verdict=preflight.Verdict.WONT_FIT)
    monkeypatch.setattr(preflight, 'check_live', check)
    monkeypatch.setattr(machx, 'serve', lambda *a, **kw: pytest.fail('must not load'))
    renderer = SimpleNamespace(system=lambda _: None, error=lambda msg: pytest.fail(msg))
    await launcher.serve_and_run(Console(file=StringIO()), renderer, 'fixture', checkpoint)
    assert measured == [pytest.approx(200_000_000 / 2**30)]
