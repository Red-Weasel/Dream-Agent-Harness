import base64
import csv
import hashlib
import io
import json
import zipfile

import pytest


def test_missing_and_incompatible_dependencies_are_actionable():
    from importlib.metadata import PackageNotFoundError
    from dream.diagnostics import check_dependencies
    def version(name):
        if name == 'httpx':
            raise PackageNotFoundError(name)
        return '0.1'
    rows = check_dependencies(['httpx>=0.28', 'anyio>=4'], version_getter=version)
    assert [(r['id'], r['code']) for r in rows] == [
        ('dependency:httpx', 'dependency_missing'), ('dependency:anyio', 'dependency_incompatible')]


def test_settings_fixture_rejects_future_state_without_modifying_input(tmp_path):
    from dream.diagnostics import check_settings_fixture
    fixture = {'version': 99, 'private': '/home/secret/token'}
    before = json.dumps(fixture)
    row = check_settings_fixture(fixture)
    assert row['status'] == 'fail'
    assert json.dumps(fixture) == before
    assert '/home/secret' not in str(row)
    assert check_settings_fixture({'version': 1, 'profile': 'lean'})['status'] == 'pass'
    assert check_settings_fixture({})['status'] == 'pass'


def test_export_strips_unknown_fields_paths_and_error_text_and_refuses_overwrite(tmp_path):
    from dream.diagnostics import export_report, render_text
    report = {'schema_version': 1, 'token': 'sk-private', 'config': {'path': '/home/private'},
              'checks': [{'id': 'package', 'status': 'fail', 'code': 'package_invalid',
                          'detail': '/home/private/token', 'exception': 'sk-private'}],
              'private_prompt': 'sensitive prompt'}
    target = tmp_path / 'report.json'
    export_report(report, target)
    raw = target.read_text()
    assert 'private' not in raw and 'sensitive' not in raw
    assert 'package_invalid' in raw
    assert 'private' not in render_text(report)
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        export_report(report, target)
    assert target.read_text() == raw


def _wheel(path, *, missing=False, corrupted=False, traversal=False, additions=None, omitted=()):
    # Hand-built wheel fixture includes the release-required assets and RECORD.
    static = ('index.html', 'controls.js', 'controls.css', 'attachments.js', 'attachments.css',
              'media.js', 'media.css', 'companion.js', 'companion.css', 'turn-timing.js', 'turn-timing.css',
              'workflows.js', 'workflows.css', 'projects.js', 'projects.css', 'feed.js', 'feed.css',
              'council.js', 'council.css', 'theme.css', 'workspace.css', 'workspace.js', 'library.css', 'library.js',
              'prompt_optimizer.js', 'prompt_optimizer.css')
    files = {f'dream/gui/static/{name}': b'fixture' for name in static}
    files.update({f'dream/{name}': b'fixture' for name in ('__init__.py', '__main__.py', 'diagnostics.py', 'environment.py', 'agent_activity.py', 'core/capabilities.py')})
    files.update({f'dream/{name}': b'fixture' for name in (
        'core/inference_coordination.py', 'local/load_lock.py', 'workflows/service.py',
        'workflows/store.py', 'workflows/validation.py', 'projects/workspace.py',
        'projects/recovery.py', 'gui/workflow_routes.py', 'gui/project_routes.py')})
    files['dream/eval_fixtures/harness_cases.json'] = json.dumps([
        {'id': name, 'task': 'Fixture task', 'category': 'fixture', 'expected': 'fixture'}
        for name in ('coding-boundary', 'documents-facts', 'data-totals', 'recovery-ambiguous-edit', 'tool-use-scope')]).encode()
    for skill in ('coding', 'research', 'writing', 'documents', 'data-analysis', 'media', 'library', 'verifying', 'illuminati-handshake'):
        files[f'dream/resources/skills/{skill}/SKILL.md'] = b'fixture'
        if skill != 'illuminati-handshake':
            files[f'dream/resources/skills/{skill}/manifest.json'] = json.dumps({'format':'dream-skill/v1', 'name':skill}).encode()
            files[f'dream/resources/skills/{skill}/references/examples.md'] = b'fixture'
    files['dream-0.1.0.dist-info/METADATA'] = b'Metadata-Version: 2.3\nName: dream\nVersion: 0.1.0\n'
    files['dream-0.1.0.dist-info/WHEEL'] = b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    if missing:
        del files['dream/gui/static/controls.js']
    if traversal:
        files['../private'] = b'escape'
    files.update(additions or {})
    for name in omitted:
        files.pop(name, None)
    record = io.StringIO()
    writer = csv.writer(record)
    for name, raw in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()
        writer.writerow([name, 'sha256=' + digest, len(raw)])
    writer.writerow(['dream-0.1.0.dist-info/RECORD', '', ''])
    files['dream-0.1.0.dist-info/RECORD'] = record.getvalue().encode()
    if corrupted:
        files['dream/gui/static/index.html'] = b'tampered'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)


@pytest.mark.parametrize('fault,code', [('missing', 'wheel_assets_missing'), ('corrupted', 'wheel_record_invalid'), ('traversal', 'wheel_invalid')])
def test_wheel_faults_are_detected_without_extracting(tmp_path, fault, code):
    from dream.diagnostics import inspect_wheel
    path = tmp_path / 'private-package.whl'
    _wheel(path, **{fault: True})
    before = set(tmp_path.iterdir())
    row = inspect_wheel(path)
    assert row['status'] == 'fail' and row['code'] == code
    assert 'private-package' not in str(row)
    assert set(tmp_path.iterdir()) == before


def test_valid_wheel_fixture_checks_record_and_assets(tmp_path):
    from dream.diagnostics import inspect_wheel
    path = tmp_path / 'fixture.whl'
    _wheel(path)
    assert inspect_wheel(path)['status'] == 'pass'
    path.write_bytes(b'not a zip')
    assert inspect_wheel(path)['code'] == 'wheel_invalid'


def test_offline_grading_excludes_record_names_and_prompts(tmp_path):
    from dream.diagnostics import check_records
    path = tmp_path / 'secret.json'
    path.write_text(json.dumps({'schema_version':1, 'origin':'external-recording', 'run_id':'private prompt', 'cases':[]}))
    row = check_records(path)
    assert row['status'] == 'fail'
    assert row['counts'] == {'passed': 0, 'total': 5}
    assert 'private' not in str(row)
    path.write_text('{')
    assert check_records(path)['code'] == 'records_invalid'


def test_default_diagnostics_has_no_backend_subprocess_network_or_live_state(tmp_path, monkeypatch):
    from dream import diagnostics
    import socket
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError('diagnostics attempted an external operation')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setenv('DREAM_ROOT', str(tmp_path / 'must-not-exist'))
    report = diagnostics.collect_report()
    assert report['schema_version'] == 1
    assert not (tmp_path / 'must-not-exist').exists()
    assert any(r['id'] == 'settings_schema' and r['status'] == 'pass' for r in report['checks'])
    assert len(diagnostics.render_text(report)) < 65536


@pytest.mark.parametrize('name,raw', [
    ('data/private.json', b'private'),
    ('.', b'invalid'),
    ('dream//private.txt', b'invalid'),
    ('dream/eval_fixtures/harness_cases.json', b'{'),
    ('dream/resources/skills/coding/manifest.json', b'{}'),
])
def test_wheel_rejects_private_state_or_invalid_packaged_manifests(tmp_path, name, raw):
    from dream.diagnostics import inspect_wheel
    target = tmp_path / 'invalid.whl'
    _wheel(target, additions={name: raw})
    assert inspect_wheel(target)['code'] == 'wheel_invalid'


def test_cli_export_failure_does_not_print_private_path(tmp_path, capsys):
    from dream.diagnostics import main
    target = tmp_path / 'private-secret.json'
    target.write_text('preserve')
    assert main(['--export', str(target)]) == 2
    output = capsys.readouterr().out
    assert 'private-secret' not in output and 'export_failed' in output
    assert target.read_text() == 'preserve'
