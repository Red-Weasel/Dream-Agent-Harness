"""DREAM-278: local identity, discovery and explicit Browse without model loads."""
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dream.desktop import startup
from dream.desktop.protocol import recommended_card
from dream.local import machx, models, model_defaults, model_presets
from test_model_presets import gguf
from lease_isolation import isolated_load_lock_dir  # noqa: F401


@pytest.mark.parametrize('folder,expected,size', [
    ('Holo4-27B-GGUF', 'Holo4 27B', '27B'),
    ('Holo4-35B-A3B-GGUF', 'Holo4 35B A3B', '35B-A3B')])
def test_quantization_only_filename_and_generic_header_use_model_folder(tmp_path, folder, expected, size):
    root = tmp_path / folder
    root.mkdir()
    path = gguf(root / 'q4_k_m.gguf', {'general.name': 'Source_Model',
                'general.architecture': 'qwen35', 'general.size_label': '256x2.6B',
                'qwen35.context_length': 262144})
    details = models.model_details(path)
    assert details['display_name'] == expected
    assert details['parameter_size'] == size
    assert details['quantization'] == 'Q4_K_M'
    assert details['metadata_source'] == 'Filename and folder'
    assert details['context_limit'] == 262144
    assert details['filename'] == 'q4_k_m.gguf'
    assert path.name == 'q4_k_m.gguf'


def test_header_identity_and_derivative_filename_remain_distinct(tmp_path):
    path = gguf(tmp_path / 'Aster-27B-abliterated-Q6_K.gguf',
                {'general.name': 'Aster 27B', 'general.architecture': 'other'})
    details = models.model_details(path)
    assert details['display_name'] == 'Aster 27B Abliterated'
    assert details['variant'] == 'Abliterated'
    assert details['quantization'] == 'Q6_K'
    assert details['context_limit'] is None and details['vision'] is None


def test_unknown_quant_file_in_library_does_not_invent_model_identity(tmp_path):
    root = tmp_path / 'models'
    root.mkdir()
    path = root / 'Q4_K_M.gguf'
    path.write_bytes(b'not a header')
    details = models.model_details(path)
    assert details['display_name'] == 'Unidentified model'
    assert details['family'] is None and details['context_limit'] is None
    with pytest.raises(ValueError, match='GGUF'):
        models.inspect_model(path)


@pytest.mark.parametrize('name,kind', [('Bonsai-27B-mmproj-Q8_0.gguf', 'projector'),
    ('mmproj-F16.gguf', 'projector'), ('mtp-MiMo-Flash-MXFP4.gguf', 'draft'),
    ('mtp-head.gguf', 'draft'), ('Aster-mtp-head.gguf', 'draft')])
def test_auxiliary_weights_are_reported_but_cannot_be_browsed_as_main(tmp_path, name, kind):
    path = gguf(tmp_path / name, {'general.name': 'Fixture'})
    assert models.model_details(path)['auxiliary_kind'] == kind
    with pytest.raises(ValueError, match='auxiliary'):
        models.inspect_model(path)


def test_main_model_containing_draft_letters_is_not_auxiliary(tmp_path):
    path = gguf(tmp_path / 'PromptPower-27B-Q8_0.gguf', {'general.name': 'PromptPower 27B'})
    assert models.inspect_model(path)[1]['auxiliary_kind'] is None
    path = gguf(tmp_path / 'Aster-MTP-27B.gguf', {'general.name': 'Aster MTP 27B'})
    assert models.inspect_model(path)[1]['auxiliary_kind'] is None


def test_derivative_filename_is_not_overwritten_by_exported_base_header(tmp_path):
    path = gguf(tmp_path / 'Holo4-27B-Q4_K_M.gguf', {'general.name': 'Qwen3.5 27B'})
    assert models.model_details(path)['display_name'] == 'Holo4 27B'
    assert models.model_details(path)['header_name'] == 'Qwen3.5 27B'


def test_browse_split_redirects_to_first_and_rejects_missing_or_inconsistent_shards(tmp_path):
    first = gguf(tmp_path / 'Aster-Q4_K_M-00001-of-00002.gguf', {'general.name': 'Aster'})
    second = gguf(tmp_path / 'Aster-Q4_K_M-00002-of-00002.gguf', {'general.name': 'Aster'})
    item, details = models.inspect_model(second)
    assert item.path == first and details['shard_count'] == 2
    assert item.size_gb == (first.stat().st_size + second.stat().st_size) / 1e9
    second.unlink()
    with pytest.raises(ValueError, match='missing'):
        models.inspect_model(first)
    second.write_bytes(b'shard')
    (tmp_path / 'Aster-Q4_K_M-00003-of-00003.gguf').write_bytes(b'extra')
    with pytest.raises(ValueError, match='disagree'):
        models.inspect_model(first)


def test_metadata_arrays_and_template_are_skipped_for_catalog(tmp_path):
    path = gguf(tmp_path / 'Aster.gguf', {'general.name': 'Aster', 'tokenizer.chat_template': 'template'})
    assert 'tokenizer.chat_template' not in model_defaults.read_metadata(path, include_template=False)
    assert model_defaults.read_metadata(path)['tokenizer.chat_template'] == 'template'
    path.write_bytes(b'GGUF' + struct.pack('<IQQQ', 3, 0, 1, 2**60))
    assert models.model_details(path)['display_name'] == 'Aster'
    with pytest.raises(ValueError, match='budget'):
        models.inspect_model(path)


def test_catalog_keeps_duplicate_models_separate_and_never_queries_engine_per_file(tmp_path, monkeypatch):
    from dream.tui import picker
    entries = []
    for folder, name in [('disk-a', 'Aster-Q4_K_M'), ('disk-b', 'Aster-Q4_K_M'), ('disk-c', 'Aster-mmproj-Q8_0')]:
        root = tmp_path / folder
        root.mkdir()
        path = gguf(root / f'{name}.gguf', {'general.name': 'Aster'})
        entries.append(models.LocalModel(name, path, 10, folder))
    monkeypatch.setattr(machx, 'served_model_id', lambda: None)
    monkeypatch.setattr(machx, 'available', lambda: True)
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: entries)
    monkeypatch.setattr(machx, 'capabilities', lambda *_: pytest.fail('Catalog must not run engine inspection'))
    monkeypatch.setattr(models, '_base_roots', lambda: [tmp_path])
    monkeypatch.setattr(picker, 'detect_providers', lambda: [])
    data = startup.catalog()
    assert len(data['choices']) == 2 and len(data['auxiliary']) == 1
    assert data['choices'][0]['display_name'] == data['choices'][1]['display_name']
    assert data['choices'][0]['id'] != data['choices'][1]['id']
    assert all(row['vision'] is None for row in data['choices'])
    assert data['discovery']['roots'] == [str(tmp_path)]


def test_context_and_speculation_facts_are_independent_of_recommended_sampling():
    facts = model_defaults.model_facts({'context_limit': 262144},
        {'architecture': 'qwen35', 'features': {'speculative': True, 'vision': False}},
        {'ctx': 32768, 'options': {'speculative': False, 'spec_k': 3}})
    card = recommended_card(None, [], facts=facts)
    rows = dict(card['facts'])
    assert rows['Model context limit'] == '256K tokens'
    assert rows['Launch context'] == '32K tokens'
    assert rows['Speculative decoding'] == 'Supported · Off'
    assert rows['Draft model'] == 'Not reported'
    assert rows['Draft tokens per step'] == '3'
    assert card['empty']
    unknown = model_defaults.model_facts({}, {'architecture': 'qwen35'}, {'ctx': 4096})
    assert unknown['speculative']['supported'] is None
    assert dict(recommended_card(None, [], facts=unknown)['facts'])['Model context limit'] == 'Not reported'


def test_browse_checkpoint_reports_real_shards_and_limit(tmp_path):
    root = tmp_path / 'Aster-Flash'
    root.mkdir()
    (root / 'config.json').write_text(json.dumps({'model_type': 'mimo_v2', 'max_position_embeddings': 1048576}))
    (root / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'a': 'one.safetensors', 'b': 'two.safetensors'}}))
    for name in ('one.safetensors', 'two.safetensors'):
        (root / name).write_bytes(b'fixture')
    item, details = models.inspect_model(root)
    assert item.path == root and details['format'] == 'Safetensors'
    assert details['shard_count'] == 2 and details['context_limit'] == 1048576
    (root / 'two.safetensors').unlink()
    with pytest.raises(ValueError, match='missing'):
        models.inspect_model(root)


def test_inspect_choice_returns_canonical_local_contract_without_launch(tmp_path, monkeypatch):
    path = gguf(tmp_path / 'Aster.gguf', {'general.name': 'Aster'})
    alias = tmp_path / 'alias.gguf'
    alias.symlink_to(path)
    monkeypatch.setattr(startup, 'model_settings', lambda p: {'identity': model_presets.model_key(p)})
    monkeypatch.setattr(machx, 'serve', lambda *_: pytest.fail('Inspection cannot launch'))
    result = startup.inspect_choice(alias)
    assert result['choice']['id'] == str(path)
    assert result['choice']['path'] == str(path)
    assert result['choice']['discovered_by'] == 'browse'
    assert result['settings']['identity'] == model_presets.model_key(path)


@pytest.mark.asyncio
@pytest.mark.parametrize('defect', [None, 'replaced', 'wrong_id', 'auxiliary'])
async def test_explicit_browse_launch_rechecks_identity_and_guards(tmp_path, monkeypatch, defect):
    path = gguf(tmp_path / ('mmproj-Q8_0.gguf' if defect == 'auxiliary' else 'Aster.gguf'), {'general.name': 'Aster'})
    identity = model_presets.model_key(path)
    selection = {'ctx': 4096, 'gpus': 1, 'options': {}}
    settings = {'identity': identity, 'context_limit': 32768, 'max_gpus': 1, 'controls': [], 'selection': selection}
    monkeypatch.setattr(machx, 'PORT', 'identity-fixture-' + tmp_path.name)
    monkeypatch.setattr(startup, 'model_settings', lambda _: settings)
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: pytest.fail('Explicit Browse must not rescan drives'))
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    preflight = Mock(return_value='fixture fit')
    monkeypatch.setattr(startup, 'launch_preflight', preflight)
    proc = Mock(poll=Mock(return_value=None))
    serve = Mock(return_value=proc)
    monkeypatch.setattr(machx, 'serve', serve)
    monkeypatch.setattr(machx, 'wait_ready', lambda _: False)
    monkeypatch.setattr(machx, 'launch_error', lambda _: '')
    monkeypatch.setattr(machx, 'log_mark', lambda: 0)
    request = {'workspace': str(tmp_path), 'choice': {'kind': 'local', 'provider': 'machx',
               'path': str(path), 'id': 'wrong' if defect == 'wrong_id' else str(path), 'discovered_by': 'browse'},
               'selection': selection, 'identity': 'old' if defect == 'replaced' else identity, 'revision': None}
    expected = {None: 'did not become ready', 'replaced': 'Model files changed',
                'wrong_id': 'path changed', 'auxiliary': 'auxiliary'}[defect]
    with pytest.raises(ValueError, match=expected):
        await startup.run(request, tmp_path / 'status.json')
    if defect is None:
        preflight.assert_called_once()
        serve.assert_called_once_with(path, gpus=1, ctx=4096, options={})
        proc.terminate.assert_called_once()
    else:
        preflight.assert_not_called()
        serve.assert_not_called()


@pytest.mark.asyncio
async def test_browsed_model_completes_existing_session_path_and_saves_exact_preset(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from dream.tui import app
    path = gguf(tmp_path / 'Aster.gguf', {'general.name': 'Aster'})
    identity = model_presets.model_key(path)
    selection = {'ctx': 4096, 'gpus': 1, 'options': {}}
    settings = {'identity': identity, 'context_limit': 32768, 'max_gpus': 1, 'controls': [], 'selection': selection}
    monkeypatch.setenv('DREAM_MODEL_PRESETS', str(tmp_path / 'presets.json'))
    monkeypatch.setattr(machx, 'PORT', 'identity-complete-' + tmp_path.name)
    monkeypatch.setattr(startup, 'model_settings', lambda _: settings)
    monkeypatch.setattr(startup, 'launch_preflight', lambda *_: 'fixture fit')
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: pytest.fail('No scan for explicit path'))
    proc = Mock(poll=Mock(return_value=0))
    monkeypatch.setattr(machx, 'serve', Mock(return_value=proc))
    monkeypatch.setattr(machx, 'wait_ready', lambda _: True)
    monkeypatch.setattr(machx, 'log_mark', lambda: 0)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'fixture-model')
    session = SimpleNamespace(run=AsyncMock())
    constructor = Mock(return_value=session)
    monkeypatch.setattr(app, 'App', constructor)
    request = {'workspace': str(tmp_path), 'choice': {'kind': 'local', 'provider': 'machx',
               'path': str(path), 'id': str(path), 'discovered_by': 'browse'},
               'selection': selection, 'identity': identity, 'revision': None}
    await startup.run(request, tmp_path / 'status.json')
    session.run.assert_awaited_once()
    assert constructor.call_args.kwargs['model'] == 'fixture-model'
    assert model_presets.Presets().load(path)['selection'] == selection
    assert json.loads((tmp_path / 'status.json').read_text())['state'] == 'ended'


@pytest.mark.asyncio
async def test_browse_file_replaced_during_preflight_is_not_loaded(tmp_path, monkeypatch):
    path = gguf(tmp_path / 'Aster.gguf', {'general.name': 'Aster'})
    identity = model_presets.model_key(path)
    selection = {'ctx': 4096, 'gpus': 1, 'options': {}}
    monkeypatch.setattr(machx, 'PORT', 'identity-race-' + tmp_path.name)
    monkeypatch.setattr(startup, 'model_settings', lambda _: {'identity': identity,
        'context_limit': 32768, 'max_gpus': 1, 'controls': [], 'selection': selection})
    def replace_during_preflight(*_):
        path.write_bytes(b'replaced')
        return 'fixture fit'
    monkeypatch.setattr(startup, 'launch_preflight', replace_during_preflight)
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    monkeypatch.setattr(machx, 'serve', lambda *_: pytest.fail('Changed file must not launch'))
    request = {'workspace': str(tmp_path), 'choice': {'kind': 'local', 'provider': 'machx',
               'path': str(path), 'id': str(path), 'discovered_by': 'browse'},
               'selection': selection, 'identity': identity, 'revision': None}
    with pytest.raises(ValueError, match='state changed during preflight'):
        await startup.run(request, tmp_path / 'status.json')


def test_mixed_quantization_labels_keep_all_declared_types(tmp_path):
    path = gguf(tmp_path / 'Aster-F16-F32-MXFP4.gguf', {'general.name': 'Aster'})
    assert models.model_details(path)['quantization'] == 'F16 / F32 / MXFP4'


def test_stale_checkpoint_does_not_abort_provider_catalog(tmp_path, monkeypatch):
    from dream.tui import picker
    root = tmp_path / 'removed-checkpoint'
    root.mkdir()
    monkeypatch.setattr(machx, 'served_model_id', lambda: None)
    monkeypatch.setattr(machx, 'available', lambda: True)
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [models.LocalModel('Aster', root, 10, 'fixture')])
    monkeypatch.setattr(models, '_base_roots', lambda: [tmp_path])
    monkeypatch.setattr(picker, 'detect_providers', lambda: [SimpleNamespace(key='codex', label='ChatGPT', ready=True)])
    result = startup.catalog()
    assert [row['id'] for row in result['choices']] == ['codex']
    assert result['unavailable'][0]['path'] == str(root)
    assert result['discovery']['unavailable_count'] == 1


def test_browse_snapshot_single_gguf_symlink_uses_named_package_and_canonical_identity(tmp_path, monkeypatch):
    blob = gguf(tmp_path / '0123456789abcdef', {'general.name': 'Source_Model'})
    root = tmp_path / 'Holo4-27B-GGUF'
    root.mkdir()
    link = root / 'q4_k_m.gguf'
    link.symlink_to(blob)
    item, details = models.inspect_model(link)
    assert item.path == blob and details['selected_path'] == str(link)
    assert details['display_name'] == 'Holo4 27B'
    monkeypatch.setattr(startup, 'model_settings', lambda p: {'identity': model_presets.model_key(p)})
    result = startup.inspect_choice(link)
    assert result['choice']['path'] == str(blob)
    assert result['settings']['model_details']['filename'] == 'q4_k_m.gguf'


def test_browse_split_snapshot_links_reject_loss_of_named_sibling_layout(tmp_path):
    for number in (1, 2):
        blob = gguf(tmp_path / f'blob{number}', {'general.name': 'Aster'})
        (tmp_path / f'Aster-0000{number}-of-00002.gguf').symlink_to(blob)
    with pytest.raises(ValueError, match='named sibling shards'):
        models.inspect_model(tmp_path / 'Aster-00002-of-00002.gguf')


@pytest.mark.asyncio
@pytest.mark.parametrize('when', ['before_launch', 'during_preflight'])
async def test_browse_snapshot_link_retarget_is_rejected(tmp_path, monkeypatch, when):
    first = gguf(tmp_path / 'blob1', {'general.name': 'Aster'})
    second = gguf(tmp_path / 'blob2', {'general.name': 'Aster'})
    link = tmp_path / 'Aster.gguf'
    link.symlink_to(first)
    selection = {'ctx': 4096, 'gpus': 1, 'options': {}}
    identity = model_presets.model_key(first)
    monkeypatch.setattr(machx, 'PORT', 'identity-link-' + tmp_path.name)
    monkeypatch.setattr(startup, 'model_settings', lambda _: {'identity': identity,
        'context_limit': 32768, 'max_gpus': 1, 'controls': [], 'selection': selection})
    def retarget(*_):
        link.unlink()
        link.symlink_to(second)
        return 'fixture fit'
    monkeypatch.setattr(startup, 'launch_preflight', retarget)
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    monkeypatch.setattr(machx, 'serve', lambda *_: pytest.fail('Changed symlink must not launch'))
    if when == 'before_launch':
        retarget()
    request = {'workspace': str(tmp_path), 'choice': {'kind': 'local', 'provider': 'machx',
               'path': str(first), 'id': str(first), 'selected_path': str(link), 'discovered_by': 'browse'},
               'selection': selection, 'identity': identity, 'revision': None}
    with pytest.raises(ValueError, match='path changed|link changed'):
        await startup.run(request, tmp_path / 'status.json')
