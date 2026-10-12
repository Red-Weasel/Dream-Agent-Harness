"""Pure packaged-skill snapshot and lookup contracts; synthetic folders only."""
import hashlib
import importlib
import importlib.util

import pytest


def skills_api():
    module_name = 'dream.local_eval_skills'
    assert importlib.util.find_spec(module_name) is not None, 'Packaged skill snapshot module is absent.'
    module = importlib.import_module(module_name)
    names = ('load_packaged_skills', 'find_skills', 'open_skill')
    for name in names:
        assert callable(getattr(module, name, None)), f'Packaged skill API {name} is absent.'
    return tuple(getattr(module, name) for name in names)


def write_skill(root, relative, *, name, description, procedure='Follow the saved procedure.\n'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f'---\nname: "{name}"\ndescription: "{description}"\n---\n\n# Procedure\n{procedure}'
    path.write_bytes(text.encode('utf-8'))
    return path, text


def test_packaged_skill_api_is_explicit():
    skills_api()


def test_metadata_complete_utf8_body_hash_and_relative_source(tmp_path):
    load, _, _ = skills_api()
    _, body = write_skill(tmp_path, 'csv-check/SKILL.md', name='csv-check',
                          description='Check tabular records', procedure='Read café rows.\n\nKeep spacing.\n')
    rows = load(tmp_path)
    assert isinstance(rows, tuple) and len(rows) == 1
    row = rows[0]
    assert row.name == 'csv-check'
    assert row.description == 'Check tabular records'
    assert row.body == body
    assert row.sha256 == hashlib.sha256(body.encode('utf-8')).hexdigest()
    assert str(row.source) == 'csv-check/SKILL.md'


def test_both_supported_packaged_layouts_are_loaded(tmp_path):
    load, _, _ = skills_api()
    write_skill(tmp_path, 'row-check/SKILL.md', name='row-check', description='Check rows')
    write_skill(tmp_path, 'domains/summarize/SKILL.md', name='summarize', description='Summarize records')
    rows = load(tmp_path)
    assert isinstance(rows, tuple)
    assert {row.name: str(row.source) for row in rows} == {
        'row-check': 'row-check/SKILL.md', 'summarize': 'domains/summarize/SKILL.md'}


def test_loaded_rows_are_frozen(tmp_path):
    load, _, _ = skills_api()
    write_skill(tmp_path, 'csv-check/SKILL.md', name='csv-check', description='Check records')
    row = load(tmp_path)[0]
    with pytest.raises((AttributeError, TypeError)):
        row.description = 'Changed after snapshot'
    assert row.description == 'Check records'


def test_open_and_find_use_snapshot_after_source_is_changed_and_removed(tmp_path):
    load, find, open_skill = skills_api()
    path, original = write_skill(tmp_path, 'csv-check/SKILL.md', name='csv-check', description='Check records')
    rows = load(tmp_path)
    path.write_text('Changed content that must not be reopened.\n', encoding='utf-8')
    assert open_skill(rows, 'csv-check') == original
    path.unlink()
    assert open_skill(rows, 'csv-check') == original
    assert find(rows, 'check records') == rows
    assert rows[0].sha256 == hashlib.sha256(original.encode('utf-8')).hexdigest()


def test_find_matches_all_terms_across_name_and_description_case_insensitively(tmp_path):
    load, find, _ = skills_api()
    write_skill(tmp_path, 'csv-check/SKILL.md', name='csv-check', description='Validate tabular records')
    write_skill(tmp_path, 'csv-export/SKILL.md', name='csv-export', description='Export tabular records')
    write_skill(tmp_path, 'text-check/SKILL.md', name='text-check', description='Validate prose')
    rows = load(tmp_path)
    found = find(rows, '  CSV\tVALIDATE\nrecords  ')
    assert isinstance(found, tuple)
    assert tuple(row.name for row in found) == ('csv-check',)
    assert found[0] == next(row for row in rows if row.name == 'csv-check')
    assert find(rows, 'csv validate nonexistent') == ()


def test_find_does_not_search_procedure_text(tmp_path):
    load, find, open_skill = skills_api()
    _, body = write_skill(tmp_path, 'csv-check/SKILL.md', name='csv-check', description='Check records',
                          procedure='Body-only sentinel: secretprocedureterm.\n')
    rows = load(tmp_path)
    assert find(rows, 'secretprocedureterm') == ()
    assert find(rows, 'check secretprocedureterm') == ()
    assert open_skill(rows, 'csv-check') == body


def test_unknown_open_raises_instead_of_guessing_or_returning_placeholder(tmp_path):
    load, _, open_skill = skills_api()
    write_skill(tmp_path, 'csv-check/SKILL.md', name='csv-check', description='Check records')
    rows = load(tmp_path)
    with pytest.raises(ValueError):
        open_skill(rows, 'missing-skill')


def test_empty_packaged_root_has_no_search_results(tmp_path):
    load, find, open_skill = skills_api()
    rows = load(tmp_path)
    assert rows == ()
    assert find(rows, 'records') == ()
    with pytest.raises(ValueError):
        open_skill(rows, 'missing-skill')
