"""Packaged-skill storage boundaries using only controlled temporary files."""
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path

import pytest


def loader():
    module = 'dream.local_eval_skills'
    assert importlib.util.find_spec(module) is not None, 'Packaged-skills module is absent.'
    function = getattr(importlib.import_module(module), 'load_packaged_skills', None)
    assert callable(function), 'Bounded load_packaged_skills API is absent.'
    return function


def skill(root, relative='sample/SKILL.md', *, name='sample', body='Generic fixture instructions.\n'):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ('---\nname: ' + name + '\ndescription: Generic fixture description.\n---\n' + body).encode()
    path.write_bytes(data)
    return path


def test_packaged_storage_api_absence_is_explicit():
    loader()


def test_exact_file_provenance_and_two_supported_layouts(tmp_path):
    load = loader()
    root = tmp_path / 'packaged'
    first = skill(root)
    second = skill(root, 'domains/second/SKILL.md', name='second', body='Second fixture.\n')
    rows = load(root)
    assert isinstance(rows, tuple)
    assert {row.name for row in rows} == {'sample', 'second'}
    by_source = {str(row.source): row for row in rows}
    for path in (first, second):
        row = by_source[path.relative_to(root).as_posix()]
        assert row.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert str(tmp_path) not in str(row.source)
    assert 'Generic fixture instructions.' in by_source['sample/SKILL.md'].body


@pytest.mark.parametrize('position', ['root', 'ancestor', 'skill', 'domains', 'domain_skill', 'file'])
def test_symlink_in_selected_path_or_supported_layout_refuses(tmp_path, position):
    load = loader()
    root = tmp_path / 'real' / 'packaged'
    path = skill(root, 'domains/sample/SKILL.md' if position in {'domains', 'domain_skill'} else 'sample/SKILL.md')
    selected = root
    if position in {'root', 'ancestor'}:
        target = root if position == 'root' else root.parent
        link = tmp_path / 'alias'
        link.symlink_to(target, target_is_directory=True)
        selected = link if position == 'root' else link / 'packaged'
    else:
        target = {'skill': root / 'sample', 'domains': root / 'domains',
                  'domain_skill': root / 'domains/sample', 'file': path}[position]
        moved = tmp_path / 'moved'
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises((ValueError, OSError)):
        load(selected)


@pytest.mark.parametrize('kind', ['fifo', 'directory'])
def test_nonregular_skill_file_refuses_before_read(tmp_path, kind):
    load = loader()
    root = tmp_path / 'packaged'
    path = root / 'sample/SKILL.md'
    path.parent.mkdir(parents=True)
    if kind == 'fifo':
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises((ValueError, OSError)):
        load(root)


@pytest.mark.parametrize('data', [
    b'No frontmatter.\n',
    b'---\nname: sample\n---\nMissing description.\n',
    b'---\ndescription: Missing name.\n---\nBody.\n',
    b'---\nname: sample\ndescription: Unterminated header.\n',
    b'---\nname: [sample]\ndescription: Generic.\n---\nBody.\n',
    b'---\nname: sample\ndescription: [not, text]\n---\nBody.\n',
    b'---\nname: sample\ndescription: Generic.\n---\n\xff',
])
def test_malformed_or_non_utf8_skill_refuses(tmp_path, data):
    load = loader()
    root = tmp_path / 'packaged'
    path = skill(root)
    path.write_bytes(data)
    with pytest.raises((ValueError, UnicodeError)):
        load(root)


def test_casefold_duplicate_across_layouts_refuses(tmp_path):
    load = loader()
    root = tmp_path / 'packaged'
    skill(root, name='Sample')
    skill(root, 'domains/other/SKILL.md', name='sample')
    with pytest.raises(ValueError):
        load(root)


@pytest.mark.parametrize('field,size', [('name', 129), ('description', 4097)])
def test_header_field_bounds_refuse(tmp_path, field, size):
    load = loader()
    root = tmp_path / 'packaged'
    path = skill(root)
    values = {'name': 'sample', 'description': 'Generic.'}
    values[field] = 'x' * size
    path.write_text('---\nname: ' + values['name'] + '\ndescription: ' + values['description'] + '\n---\nBody.\n')
    with pytest.raises(ValueError):
        load(root)


def test_per_file_byte_limit_refuses_even_with_small_character_count(tmp_path):
    load = loader()
    root = tmp_path / 'packaged'
    path = skill(root, body='界' * 44_000)
    assert len(path.read_bytes()) > 128 * 1024
    with pytest.raises(ValueError):
        load(root)


def test_aggregate_byte_limit_counts_all_supported_files(tmp_path):
    load = loader()
    root = tmp_path / 'packaged'
    paths = [skill(root, f'item{i}/SKILL.md', name=f'item{i}', body='x' * 125_000) for i in range(17)]
    assert all(path.stat().st_size < 128 * 1024 for path in paths)
    assert sum(path.stat().st_size for path in paths) > 2 * 1024 * 1024
    with pytest.raises(ValueError):
        load(root)


def test_skill_count_limit_refuses_129_tiny_files(tmp_path):
    load = loader()
    root = tmp_path / 'packaged'
    for i in range(129):
        skill(root, f'item{i}/SKILL.md', name=f'item{i}')
    with pytest.raises(ValueError):
        load(root)


def test_nested_scripts_references_and_configuration_are_not_parsed(tmp_path):
    load = loader()
    root = tmp_path / 'packaged'
    skill(root)
    for relative in ('sample/scripts/SKILL.md', 'sample/references/SKILL.md',
                     'domains/sample/scripts/SKILL.md', '.config/private/SKILL.md'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'\xff Not a skill header.')
    rows = load(root)
    assert [row.name for row in rows] == ['sample']
