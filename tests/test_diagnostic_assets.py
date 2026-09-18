"""Check the inspected entry page, not a duplicated release-file inventory."""
from pathlib import Path
import socket
import subprocess
import zipfile

import pytest

from dream import diagnostics
from test_diagnostics import _wheel


CURRENT_PAGE = (Path(__file__).parents[1] / 'dream/gui/static/index.html').read_bytes()


def inspect(tmp_path, monkeypatch, mode, html, *, omitted=(), additions=None, prepare=None):
    wheel = tmp_path / 'private-package.whl'
    _wheel(wheel, additions={'dream/gui/static/index.html': html, **(additions or {})}, omitted=omitted)
    if mode == 'source':
        root = tmp_path / 'source'
        # Fixture setup only. Diagnostics itself must never extract an archive.
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(name))
        monkeypatch.setattr(diagnostics, '__file__', str(root / 'dream/diagnostics.py'))
        if prepare:
            prepare(root)
    def forbidden(*args, **kwargs):
        raise AssertionError('asset inspection attempted execution, networking or extraction')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(zipfile.ZipFile, 'extract', forbidden)
    monkeypatch.setattr(zipfile.ZipFile, 'extractall', forbidden)
    if mode == 'wheel':
        return diagnostics.inspect_wheel(wheel)
    return next(row for row in diagnostics.collect_report()['checks'] if row['id'] == 'assets')


@pytest.mark.parametrize('mode', ['source', 'wheel'])
@pytest.mark.parametrize('name', ['council.js', 'council.css'])
@pytest.mark.parametrize('html', [CURRENT_PAGE, b'fixture'], ids=['current_page', 'no_refs'])
def test_council_assets_are_required_even_without_page_reference(tmp_path, monkeypatch, mode, name, html):
    row = inspect(tmp_path, monkeypatch, mode, html, omitted=['dream/gui/static/' + name])
    assert row['status'] == 'fail'
    assert row['code'] == ('assets_missing' if mode == 'source' else 'wheel_assets_missing')


@pytest.mark.parametrize('mode', ['source', 'wheel'])
@pytest.mark.parametrize('tag', [b'<script src="/assets/private-future.js?v=1&amp;x=2#frag"></script>',
                                 b'<link rel="stylesheet" href="/assets/private-future.css?x=1#frag">',
                                 b'<script src="/assets/private&#45;future.js"></script>'])
def test_new_page_reference_is_required_without_inventory_update(tmp_path, monkeypatch, mode, tag):
    row = inspect(tmp_path, monkeypatch, mode, CURRENT_PAGE + tag)
    assert row['status'] == 'fail'
    assert row['counts']['missing'] == 1
    assert 'private' not in str(row)


@pytest.mark.parametrize('mode', ['source', 'wheel'])
def test_complete_current_page_and_new_assets_pass(tmp_path, monkeypatch, mode):
    html = CURRENT_PAGE + b'<script src="/assets/future.js?v=1&amp;x=2#frag"></script><link href="/assets/nested/future.css">'
    row = inspect(tmp_path, monkeypatch, mode, html, additions={
        'dream/gui/static/future.js': b'fixture', 'dream/gui/static/nested/future.css': b'fixture'})
    assert row['status'] == 'pass'


@pytest.mark.parametrize('mode', ['source', 'wheel'])
@pytest.mark.parametrize('url', [b'/assets/../private.js', b'/assets/%2e%2e/private.js',
                                b'/assets/%252e%252e/private.js', b'/assets/a%2f..%2fprivate.js',
                                b'/assets/private%00.js', b'/assets/private\\escape.js',
                                b'/assets\\private.js', b'/./assets/private.js',
                                b'/prefix/../assets/private.js', b'/%2e/assets/private.js',
                                b'/assets//private.js', b'/%2fassets/private.js'])
def test_unsafe_asset_references_fail_without_exporting_paths(tmp_path, monkeypatch, mode, url):
    row = inspect(tmp_path, monkeypatch, mode, CURRENT_PAGE + b'<script src="' + url + b'"></script>')
    assert row['status'] == 'fail'
    assert row['code'] == ('assets_invalid' if mode == 'source' else 'wheel_invalid')
    assert 'private' not in str(row)


@pytest.mark.parametrize('mode', ['source', 'wheel'])
@pytest.mark.parametrize('html', [b'\xff', b'<base href="https://private.invalid/">', b'x' * 4_000_001],
                         ids=['encoding', 'base', 'oversized'])
def test_invalid_or_unsupported_entry_page_is_bounded_and_redacted(tmp_path, monkeypatch, mode, html):
    row = inspect(tmp_path, monkeypatch, mode, html)
    assert row['status'] == 'fail'
    assert row['code'] == ('assets_invalid' if mode == 'source' else 'wheel_invalid')
    assert 'private' not in str(row)


@pytest.mark.parametrize('mode', ['source', 'wheel'])
def test_external_and_nonresource_links_are_not_fetched(tmp_path, monkeypatch, mode):
    html = CURRENT_PAGE + b'<script src="https://private.invalid/a.js"></script><a href="/assets/private-link">link</a>'
    assert inspect(tmp_path, monkeypatch, mode, html)['status'] == 'pass'


@pytest.mark.parametrize('mode', ['source', 'wheel'])
@pytest.mark.parametrize('provided', [False, True])
def test_unicode_space_in_filename_is_not_trimmed(tmp_path, monkeypatch, mode, provided):
    html = CURRENT_PAGE + b'<script src="/assets/future.js&#160;"></script>'
    additions = {'dream/gui/static/future.js': b'wrong filename'}
    if provided:
        additions['dream/gui/static/future.js\u00a0'] = b'correct filename'
    row = inspect(tmp_path, monkeypatch, mode, html, additions=additions)
    assert row['status'] == ('pass' if provided else 'fail')
    if not provided:
        assert row['counts']['missing'] == 1


@pytest.mark.parametrize('nested', [False, True])
def test_source_asset_symlink_cannot_satisfy_local_package_requirement(tmp_path, monkeypatch, nested):
    outside = tmp_path / 'private'
    outside.mkdir()
    (outside / 'future.js').write_bytes(b'private fixture; never read')
    def prepare(root):
        static = root / 'dream/gui/static'
        if nested:
            (static / 'nested').symlink_to(outside, target_is_directory=True)
        else:
            (static / 'future.js').symlink_to(outside / 'future.js')
    html = CURRENT_PAGE + b'<script src="/assets/' + (b'nested/' if nested else b'') + b'future.js"></script>'
    row = inspect(tmp_path, monkeypatch, 'source', html, prepare=prepare)
    assert row['code'] == 'assets_missing' and row['counts']['missing'] == 1
    assert 'private' not in str(row)
