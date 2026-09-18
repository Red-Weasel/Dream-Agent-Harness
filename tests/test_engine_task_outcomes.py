"""CPU deliverable checks through the actual Engine and native file tools."""
from dataclasses import asdict
import asyncio
import copy
import hashlib
import json
import os
import zipfile

import pytest

from dream.harness_eval import capture_workspace_manifest, grade_deliverable, load_cases
from test_engine_profile_task import Transport, _text_round, task_engine, tool_round


DATA_CASE = next(case for case in load_cases() if case['id'] == 'data-totals')
DOCUMENT_CASE = next(case for case in load_cases() if case['id'] == 'documents-facts')
RECOVERY_CASE = next(case for case in load_cases() if case['id'] == 'recovery-ambiguous-edit')
CORRECT = {'totals': {'North, Central': '12.30', 'South': '20.00'}, 'rows': 4, 'excluded': 1}


def _engine_calls(events):
    pending, calls = {}, []
    for event in events:
        if event.kind == 'tool_use':
            pending[event.data['id']] = event.data
        elif event.kind == 'tool_result':
            use = pending.pop(event.data['id'])
            calls.append({
                'name': use['name'],
                'arguments': use['input'],
                'result': {
                    'content': [{'type': 'text', 'text': event.data['content']}],
                    'is_error': event.data['is_error'],
                },
            })
    assert pending == {}
    return calls


async def _ask_scripted(engine, rounds, prompt):
    transport = Transport(engine.backend, rounds)
    engine.backend._client = transport
    try:
        async with asyncio.timeout(20):
            events = [event async for event in engine.ask(prompt)]
    finally:
        await engine.backend._idle_work.close()
    assert any(
        event.kind == 'result' and event.data.get('subtype') == 'success'
        and not event.data.get('is_error') for event in events
    )
    return transport, events


def _assistant_json(events):
    answer = next(event.data for event in reversed(events) if event.kind == 'assistant_done')
    return json.loads(answer)


@pytest.mark.parametrize(
    ('control', 'artifact_passed', 'workspace_preserved', 'passed'),
    [
        ('correct', True, True, True),
        ('false_done', False, True, False),
        ('wrong_total', False, True, False),
        ('forbidden_mutation', True, False, False),
    ],
)
async def test_actual_engine_protocol_success_is_separate_from_deliverable_grade(
        task_engine, control, artifact_passed, workspace_preserved, passed):
    engine, _, _ = await task_engine(history=False)
    engine.workspace.joinpath('shipping.py').unlink()
    engine.workspace.joinpath('sales.csv').write_text(DATA_CASE['input'])
    before = capture_workspace_manifest(engine.workspace)

    answer = copy.deepcopy(CORRECT)
    if control == 'wrong_total':
        answer['totals']['North, Central'] = '12.31'
    rounds = [
        tool_round('read_file', {'path': 'sales.csv'}, 1),
        tool_round('write_file', {'path': 'totals.json', 'content': json.dumps(answer)}, 2),
        tool_round('read_file', {'path': 'totals.json'}, 3),
    ]
    if control == 'false_done':
        rounds = []
    if control == 'forbidden_mutation':
        rounds.append(tool_round('write_file', {'path': 'untouched.bin', 'content': 'changed'}, 4))
    rounds.append(_text_round('DONE. Saved correct totals.'))
    transport = Transport(engine.backend, rounds)
    engine.backend._client = transport

    try:
        async with asyncio.timeout(20):
            events = [event async for event in engine.ask(DATA_CASE['task'])]
    finally:
        await engine.backend._idle_work.close()
    protocol_success = any(
        event.kind == 'result' and event.data.get('subtype') == 'success'
        and not event.data.get('is_error') for event in events
    )
    artifact_path = engine.workspace / 'totals.json'
    artifact = artifact_path.read_text() if artifact_path.exists() else ''
    outputs = json.loads(artifact) if artifact else {}
    outputs['artifact'] = artifact
    records = {
        'schema_version': 1,
        'origin': 'external-recording',
        'run_id': engine.session_id,
        'cases': [{'id': 'data-totals', 'outputs': outputs, 'tools': _engine_calls(events)}],
    }
    outcome = grade_deliverable(
        records,
        case_id='data-totals',
        workspace_before=before,
        workspace_after=capture_workspace_manifest(engine.workspace),
        allowed_outputs={'totals.json'},
    )

    assert protocol_success is True
    assert outcome['artifact_passed'] is artifact_passed
    assert outcome['workspace_preserved'] is workspace_preserved
    assert outcome['passed'] is passed
    assert 'protocol_success' not in outcome
    assert asdict(events[-1])['data']['subtype'] == 'success'


@pytest.mark.parametrize(
    ('control', 'artifact_passed', 'workspace_preserved', 'passed'),
    [
        ('correct', True, True, True),
        ('false_done', False, True, False),
        ('wrong_facts', False, True, False),
        ('forbidden_mutation', True, False, False),
    ],
)
async def test_actual_engine_document_facts_require_read_evidence_and_preservation(
        task_engine, control, artifact_passed, workspace_preserved, passed):
    engine, _, _ = await task_engine(history=False)
    engine.workspace.joinpath('shipping.py').unlink()
    document_xml = ''.join(
        f'<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>'
        for paragraph in DOCUMENT_CASE['paragraphs']
    )
    with zipfile.ZipFile(engine.workspace / 'brief.docx', 'w') as archive:
        archive.writestr(
            '[Content_Types].xml',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            'word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{document_xml}</w:body></w:document>',
        )
    before = capture_workspace_manifest(engine.workspace)
    answer = {'facts': dict(DOCUMENT_CASE['expected']), 'source': 'brief.docx'}
    if control == 'wrong_facts':
        answer['facts']['budget_usd'] = 12500.0
    rounds = [tool_round('read_file', {'path': 'brief.docx'}, 1)]
    if control == 'false_done':
        rounds = []
    if control == 'forbidden_mutation':
        rounds.append(tool_round('write_file', {'path': 'untouched.bin', 'content': 'changed'}, 2))
    rounds.append(_text_round(json.dumps(answer)))

    transport, events = await _ask_scripted(engine, rounds, DOCUMENT_CASE['task'])
    outputs = _assistant_json(events)
    records = {
        'schema_version': 1,
        'origin': 'external-recording',
        'run_id': engine.session_id,
        'cases': [{'id': 'documents-facts', 'outputs': outputs, 'tools': _engine_calls(events)}],
    }
    after = capture_workspace_manifest(engine.workspace)
    outcome = grade_deliverable(
        records,
        case_id='documents-facts',
        workspace_before=before,
        workspace_after=after,
        allowed_outputs=set(),
    )
    evidence = {'control': control, 'payloads': transport.payloads,
                'events': [asdict(event) for event in events], 'records': records,
                'workspace_before': before, 'workspace_after': after, 'outcome': outcome}
    engine.workspace.parent.joinpath(
        f'{engine.workspace.name}-documents-{control}-evidence.json'
    ).write_text(json.dumps(evidence, indent=2))

    assert outputs == answer
    assert outcome['artifact_passed'] is artifact_passed
    assert outcome['workspace_preserved'] is workspace_preserved
    assert outcome['passed'] is passed
    calls = records['cases'][0]['tools']
    if control != 'false_done':
        assert calls[0]['name'] == 'read_file'
        assert all(paragraph in calls[0]['result']['content'][0]['text']
                   for paragraph in DOCUMENT_CASE['paragraphs'])
    else:
        assert calls == []


@pytest.mark.parametrize(
    ('control', 'artifact_passed', 'workspace_preserved', 'passed'),
    [
        ('correct', True, True, True),
        ('false_done', False, True, False),
        ('incorrect_edit', False, True, False),
        ('forbidden_mutation', True, False, False),
    ],
)
async def test_actual_engine_ambiguous_edit_requires_inspection_specific_retry_and_preservation(
        task_engine, control, artifact_passed, workspace_preserved, passed):
    engine, _, _ = await task_engine(history=False)
    engine.workspace.joinpath('shipping.py').unlink()
    engine.workspace.joinpath('config.txt').write_text(RECOVERY_CASE['input'])
    before = capture_workspace_manifest(engine.workspace)
    rounds = [
        tool_round('str_replace_edit', {
            'path': 'config.txt', 'old_string': '30', 'new_string': '60',
        }, 1),
        tool_round('read_file', {'path': 'config.txt'}, 2),
        tool_round('str_replace_edit', {
            'path': 'config.txt',
            'old_string': 'backup=30' if control == 'incorrect_edit' else 'primary=30',
            'new_string': 'backup=60' if control == 'incorrect_edit' else 'primary=60',
        }, 3),
    ]
    if control == 'false_done':
        rounds = []
    if control == 'forbidden_mutation':
        rounds.append(tool_round('write_file', {'path': 'untouched.bin', 'content': 'changed'}, 4))
    rounds.append(_text_round('DONE.'))

    transport, events = await _ask_scripted(engine, rounds, RECOVERY_CASE['task'])
    saved_source = engine.workspace.joinpath('config.txt').read_bytes().decode('utf-8')
    records = {
        'schema_version': 1,
        'origin': 'external-recording',
        'run_id': engine.session_id,
        'cases': [{'id': 'recovery-ambiguous-edit',
                   'outputs': {'source': saved_source}, 'tools': _engine_calls(events)}],
    }
    after = capture_workspace_manifest(engine.workspace)
    outcome = grade_deliverable(
        records,
        case_id='recovery-ambiguous-edit',
        workspace_before=before,
        workspace_after=after,
        allowed_outputs={'config.txt'},
    )
    evidence = {'control': control, 'saved_source': saved_source,
                'payloads': transport.payloads, 'events': [asdict(event) for event in events],
                'records': records, 'workspace_before': before, 'workspace_after': after,
                'outcome': outcome}
    engine.workspace.parent.joinpath(
        f'{engine.workspace.name}-recovery-{control}-evidence.json'
    ).write_text(json.dumps(evidence, indent=2))

    assert outcome['artifact_passed'] is artifact_passed
    assert outcome['workspace_preserved'] is workspace_preserved
    assert outcome['passed'] is passed
    calls = records['cases'][0]['tools']
    if control != 'false_done':
        assert calls[0]['result']['is_error'] is True
        assert 'matches 2 time(s)' in calls[0]['result']['content'][0]['text']
        assert calls[1]['name'] == 'read_file'
        assert RECOVERY_CASE['input'].strip() in calls[1]['result']['content'][0]['text']
        assert calls[2]['arguments'] != calls[0]['arguments']
    else:
        assert calls == [] and saved_source == RECOVERY_CASE['input']


@pytest.mark.asyncio
async def test_valid_alternate_output_path_passes_the_same_artifact_contract():
    from dream import harness_eval

    records = await harness_eval.run_self_test()
    data = next(case for case in records['cases'] if case['id'] == 'data-totals')
    for call in data['tools']:
        if call['arguments'].get('path') == 'totals.json':
            call['arguments']['path'] = 'summary.json'
    digest = hashlib.sha256(DATA_CASE['input'].encode()).hexdigest()
    before = {'sales.csv': {'size': len(DATA_CASE['input'].encode()), 'sha256': digest}}
    after = dict(before)
    encoded = data['outputs']['artifact'].encode()
    after['summary.json'] = {'size': len(encoded), 'sha256': hashlib.sha256(encoded).hexdigest()}

    outcome = grade_deliverable(
        records,
        case_id='data-totals',
        workspace_before=before,
        workspace_after=after,
        allowed_outputs={'totals.json', 'summary.json'},
    )

    assert outcome['artifact_passed'] is True
    assert outcome['workspace_preserved'] is True
    assert outcome['passed'] is True


@pytest.mark.asyncio
async def test_recorded_artifact_cannot_pass_when_required_output_is_absent_from_manifest():
    from dream import harness_eval

    records = await harness_eval.run_self_test()
    before = {'sales.csv': {'size': 1, 'sha256': '0' * 64}}
    outcome = grade_deliverable(
        records,
        case_id='data-totals',
        workspace_before=before,
        workspace_after=before,
        allowed_outputs={'totals.json'},
    )
    assert outcome['artifact_passed'] is False
    assert outcome['passed'] is False
    assert 'manifest' in ' '.join(outcome['artifact_failures']).lower()


@pytest.mark.asyncio
async def test_correct_record_cannot_pass_when_manifest_proves_saved_bytes_are_wrong():
    from dream import harness_eval

    records = await harness_eval.run_self_test()
    before = {'sales.csv': {'size': 1, 'sha256': '0' * 64}}
    wrong_bytes = b'{"totals":{"North, Central":"99.99"},"rows":4,"excluded":1}'
    after = {
        **before,
        'totals.json': {
            'size': len(wrong_bytes),
            'sha256': hashlib.sha256(wrong_bytes).hexdigest(),
        },
    }

    outcome = grade_deliverable(
        records,
        case_id='data-totals',
        workspace_before=before,
        workspace_after=after,
        allowed_outputs={'totals.json'},
    )

    assert outcome['artifact_passed'] is False
    assert outcome['passed'] is False
    assert 'bytes' in ' '.join(outcome['artifact_failures']).lower()


@pytest.mark.asyncio
async def test_write_bearing_fixture_cannot_use_empty_allowlist_to_skip_artifact_binding():
    from dream import harness_eval

    records = await harness_eval.run_self_test()
    outcome = grade_deliverable(
        records,
        case_id='coding-boundary',
        workspace_before={},
        workspace_after={},
        allowed_outputs=set(),
    )

    assert outcome['artifact_passed'] is False
    assert outcome['passed'] is False
    assert 'allowed output path' in ' '.join(outcome['artifact_failures']).lower()


@pytest.mark.parametrize(
    'bad_manifest',
    [None, {'../escape': {'size': 1, 'sha256': '0' * 64}},
     {'sales.csv': {'size': True, 'sha256': '0' * 64}},
     {'sales.csv': {'size': 1, 'sha256': 'malformed'}}],
)
@pytest.mark.asyncio
async def test_missing_or_malformed_workspace_evidence_cannot_pass(bad_manifest):
    from dream import harness_eval

    records = await harness_eval.run_self_test()
    valid = {'sales.csv': {'size': 1, 'sha256': '0' * 64},
             'totals.json': {'size': 1, 'sha256': '1' * 64}}
    with pytest.raises(ValueError):
        grade_deliverable(
            records,
            case_id='data-totals',
            workspace_before=bad_manifest,
            workspace_after=valid,
            allowed_outputs={'totals.json'},
        )


@pytest.mark.asyncio
async def test_supplied_manifest_cannot_bypass_collection_bounds():
    from dream import harness_eval

    records = await harness_eval.run_self_test()
    oversized = {'large.bin': {'size': 512 * 1024 * 1024 + 1, 'sha256': '0' * 64}}
    with pytest.raises(ValueError, match='byte limit'):
        grade_deliverable(
            records,
            case_id='documents-facts',
            workspace_before=oversized,
            workspace_after={},
            allowed_outputs=set(),
        )


def test_manifest_contains_only_relative_paths_sizes_and_hashes(tmp_path):
    tmp_path.joinpath('nested').mkdir()
    tmp_path.joinpath('nested', 'note.txt').write_text('private synthetic text')

    manifest = capture_workspace_manifest(tmp_path)

    assert manifest == {
        'nested/note.txt': {
            'size': 22,
            'sha256': 'e4aa366cc86efbb90255cbf2fedafbb19cdf3807a83f2650f667a8e1d0f394f7',
        }
    }
    assert 'private synthetic text' not in json.dumps(manifest)


def test_manifest_rejects_symlinks_and_nonregular_entries(tmp_path):
    target = tmp_path / 'target.txt'
    target.write_text('synthetic')
    link = tmp_path / 'link.txt'
    link.symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        capture_workspace_manifest(tmp_path)
    link.unlink()
    fifo = tmp_path / 'events.fifo'
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match='regular file'):
        capture_workspace_manifest(tmp_path)


def test_manifest_enforces_explicit_file_and_byte_bounds(tmp_path):
    tmp_path.joinpath('one.txt').write_bytes(b'12')
    tmp_path.joinpath('two.txt').write_bytes(b'34')
    with pytest.raises(ValueError, match='file limit'):
        capture_workspace_manifest(tmp_path, max_files=1)
    with pytest.raises(ValueError, match='byte limit'):
        capture_workspace_manifest(tmp_path, max_total_bytes=3)


def test_manifest_bounds_empty_directories_as_visited_entries(tmp_path):
    tmp_path.joinpath('one').mkdir()
    tmp_path.joinpath('two').mkdir()
    with pytest.raises(ValueError, match='entry limit'):
        capture_workspace_manifest(tmp_path, max_entries=1)


def test_manifest_enforces_byte_limit_against_file_growth_during_collection(tmp_path, monkeypatch):
    target = tmp_path / 'growing.bin'
    target.write_bytes(b'a')
    real_open = os.open
    appended = False

    def grow_then_open(path, flags):
        nonlocal appended
        if not appended and os.fspath(path) == os.fspath(target):
            appended = True
            with target.open('ab') as stream:
                stream.write(b'b' * 8)
        return real_open(path, flags)

    monkeypatch.setattr(os, 'open', grow_then_open)
    with pytest.raises(ValueError, match='byte limit'):
        capture_workspace_manifest(tmp_path, max_total_bytes=4)


@pytest.mark.asyncio
async def test_read_only_contract_allows_no_new_files_but_requires_explicit_empty_set():
    from dream import harness_eval

    records = await harness_eval.run_self_test()
    manifest = {'brief.docx': {'size': 1, 'sha256': '0' * 64}}
    outcome = grade_deliverable(
        records,
        case_id='documents-facts',
        workspace_before=manifest,
        workspace_after=manifest,
        allowed_outputs=set(),
    )
    assert outcome['passed'] is True
    with pytest.raises(ValueError, match='allowed_outputs'):
        grade_deliverable(
            records,
            case_id='documents-facts',
            workspace_before=manifest,
            workspace_after=manifest,
            allowed_outputs=None,
        )
