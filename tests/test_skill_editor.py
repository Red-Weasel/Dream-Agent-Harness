"""Skill editing uses disposable roots and real loader discovery."""
import pytest
from dream import config
from dream.skills import editor, loader


def body(name='example', text='Do the work.'):
    return f'---\nname: {name}\ndescription: A fixture skill.\n---\n\n{text}\n'


@pytest.fixture
def roots(tmp_path, monkeypatch):
    external = tmp_path / 'external'
    external.mkdir()
    monkeypatch.setenv('DREAM_SKILL_DIRS', str(external))
    return external


def test_create_read_update_and_real_discovery(roots):
    value = editor.create('example', body())
    assert value['managed'] and value['content'] == body()
    assert loader.discover(config.skill_dirs())[0][0].name == 'example'
    changed = editor.save('example', body(text='Changed.'), value['sha256'])
    assert changed['sha256'] != value['sha256']
    assert editor.read('example')['content'] == body(text='Changed.')
    with pytest.raises(editor.Conflict):
        editor.save('example', body(text='Stale.'), value['sha256'])


def test_external_override_preserves_bundle_and_original(roots):
    package = roots / 'example'
    (package / 'references').mkdir(parents=True)
    (package / 'SKILL.md').write_text(body(text='Read [notes](references/notes.md).'))
    (package / 'references/notes.md').write_text('Reference text')
    old = editor.read('example')
    assert not old['managed']
    saved = editor.save('example', old['content'] + '\nEdited.\n', old['sha256'])
    assert saved['managed']
    assert (package / 'SKILL.md').read_text() == old['content']
    skill = next(s for s in loader.discover(config.skill_dirs())[0] if s.name == 'example')
    assert loader.bundled_file(skill, 'references/notes.md') == 'Reference text'


@pytest.mark.parametrize('name', ['../escape', '/absolute', '.', 'a/b', ''])
def test_create_rejects_unsafe_names(roots, name):
    with pytest.raises(ValueError):
        editor.create(name, body(name))


def test_external_symlink_bundle_refused_without_partial_override(roots, tmp_path):
    package = roots / 'example'
    package.mkdir()
    (package / 'SKILL.md').write_text(body())
    private = tmp_path / 'private'
    private.write_text('private bytes')
    (package / 'leak.md').symlink_to(private)
    old = editor.read('example')
    with pytest.raises(ValueError, match='symlink'):
        editor.save('example', body(text='Edited'), old['sha256'])
    assert not editor.read('example')['managed']


def test_validation_duplicate_and_limits(roots):
    with pytest.raises(ValueError):
        editor.create('example', 'missing metadata')
    with pytest.raises(ValueError):
        editor.create('example', body('other'))
    with pytest.raises(ValueError):
        editor.create('example', body(text='x' * 120001))
    editor.create('example', body())
    with pytest.raises(editor.Conflict):
        editor.create('example', body())


@pytest.mark.parametrize('link', ['../private.md', '/private.md', '%2e%2e/private.md', 'references/missing.md'])
def test_override_refuses_unpreservable_markdown_links(roots, link):
    package = roots / 'example'
    package.mkdir()
    (package / 'SKILL.md').write_text(body())
    old = editor.read('example')
    with pytest.raises(ValueError):
        editor.save('example', body(text=f'Read [reference]({link}).'), old['sha256'])
    assert not editor.read('example')['managed']


def test_managed_directory_symlink_cannot_read_or_overwrite_external_package(roots):
    package = roots / 'example'
    package.mkdir()
    (package / 'SKILL.md').write_text(body())
    managed = config.DATA_DIR / 'skills'
    managed.mkdir(parents=True)
    (managed / 'example').symlink_to(package, target_is_directory=True)
    with pytest.raises(OSError):
        editor.read('example')
    assert (package / 'SKILL.md').read_text() == body()


def test_refuse_disabled_override_without_changing_enable_state(roots, monkeypatch):
    package = roots / 'example'
    package.mkdir()
    (package / 'SKILL.md').write_text(body())
    monkeypatch.setattr(loader, 'enabled', lambda skill: False)
    old = editor.read('example')
    with pytest.raises(ValueError, match='disabled'):
        editor.save('example', body(text='Changed'), old['sha256'])
    assert not editor.read('example')['managed']


def test_concurrent_stale_edits_have_one_winner(roots):
    from concurrent.futures import ThreadPoolExecutor
    old = editor.create('example', body())
    def edit(text):
        try:
            return editor.save('example', body(text=text), old['sha256'])['content']
        except editor.Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ['first', 'second']))
    assert results.count('conflict') == 1
    assert editor.read('example')['content'] in results


def test_failed_atomic_replace_leaves_original_and_no_draft(roots, monkeypatch):
    old = editor.create('example', body())
    def fail(*args, **kwargs):
        raise OSError('fixture replace failure')
    monkeypatch.setattr(editor.os, 'replace', fail)
    with pytest.raises(OSError):
        editor.save('example', body(text='New'), old['sha256'])
    assert editor.read('example')['content'] == old['content']
    assert list((config.DATA_DIR / 'skills/example').iterdir()) == [config.DATA_DIR / 'skills/example/SKILL.md']


@pytest.mark.parametrize('kind', ['hidden', 'large', 'special'])
def test_unsafe_or_oversized_bundle_refuses_without_partial_skill(roots, kind):
    import os
    package = roots / 'example'
    package.mkdir()
    (package / 'SKILL.md').write_text(body())
    if kind == 'hidden':
        (package / '.hidden').write_text('hidden reference')
    elif kind == 'large':
        (package / 'large.bin').write_bytes(b'x' * (editor.MAX_FILE + 1))
    else:
        os.mkfifo(package / 'pipe')
    old = editor.read('example')
    with pytest.raises(ValueError):
        editor.save('example', body(text='New'), old['sha256'])
    assert not editor.read('example')['managed']
    assert not list(config.DATA_DIR.glob('skill-draft-*'))


def test_save_refreshes_existing_installed_cache(roots):
    from dream.tools import installed_skill_tools
    installed_skill_tools.inventory(refresh=True)
    editor.create('example', body())
    assert any(s.name == 'example' for s in installed_skill_tools.installed())
    old = editor.read('example')
    editor.save('example', body(text='Current instructions.'), old['sha256'])
    skill = next(s for s in installed_skill_tools.installed() if s.name == 'example')
    assert 'Current instructions.' in loader.load_body(skill)


def test_data_directory_symlink_is_refused_before_creation(roots, tmp_path, monkeypatch):
    outside = tmp_path / 'outside'
    outside.mkdir()
    linked = tmp_path / 'linked'
    linked.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(config, 'DATA_DIR', linked / 'data')
    with pytest.raises(OSError):
        editor.create('example', body())
    assert not (outside / 'data').exists()


def test_new_and_managed_skills_reject_escaping_references(roots):
    with pytest.raises(ValueError):
        editor.create('example', body(text='[private](/private.md)'))
    old = editor.create('example', body())
    with pytest.raises(ValueError):
        editor.save('example', body(text='[private](../private.md)'), old['sha256'])
    assert editor.read('example')['content'] == old['content']
