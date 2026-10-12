"""Private child overlays preserve originals; no actual CLI is launched."""
import json
from pathlib import Path
import stat

import pytest

from dream.core.gemini_thinking import thinking_environment


@pytest.fixture
def fixture(tmp_path):
    package = tmp_path / 'package'
    (package / 'bundle').mkdir(parents=True)
    executable = package / 'bundle/gemini.js'
    executable.write_text('fixture executable')
    (package / 'package.json').write_text(json.dumps({'name': '@google/gemini-cli', 'version': '0.52.0'}))
    root = tmp_path / 'workspace'
    root.mkdir()
    paths = {'system': root / 'system/settings.json', 'defaults': root / 'system/system-defaults.json',
             'user': tmp_path / 'home/.gemini/settings.json', 'workspace': root / '.gemini/settings.json'}
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'fixture_layer': name}))
    env = {'HOME': str(tmp_path / 'home'), 'PATH': '/fixture-path', 'AUTH_FIXTURE': 'private-fixture',
           'GEMINI_CLI_SYSTEM_SETTINGS_PATH': 'system/settings.json'}
    return root, executable, paths, env


def overlay(fixture, model='gemini-3.8-flash', level='on', **kwargs):
    root, executable, _, env = fixture
    return thinking_environment(model, level, cwd=str(root), executable=str(executable), env=env, **kwargs)


@pytest.mark.parametrize('model,reduced', [('gemini-3.5-flash-lite', 'MINIMAL'),
    ('gemini-3.8-flash', 'LOW'), ('gemini-3.1-pro-preview', 'LOW')])
@pytest.mark.parametrize('level', ['on', 'off'])
def test_exact_overlay_preserves_environment_originals_and_private_modes(fixture, model, reduced, level):
    _, _, paths, env = fixture
    originals = {p: p.read_bytes() for p in paths.values()}
    before = dict(env)
    with overlay(fixture, model, level) as child:
        assert child is not env and env == before
        assert all(child[k] == v for k, v in before.items() if k != 'GEMINI_CLI_SYSTEM_SETTINGS_PATH')
        assert child['GEMINI_CLI_SYSTEM_DEFAULTS_PATH'] == str(paths['defaults'])
        target = Path(child['GEMINI_CLI_SYSTEM_SETTINGS_PATH'])
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        body = json.loads(target.read_text())
        assert body == {'fixture_layer': 'system', 'modelConfigs': {'customOverrides': [{
            'match': {'model': model, 'overrideScope': 'core'},
            'modelConfig': {'generateContentConfig': {'thinkingConfig': {
                'thinkingLevel': 'HIGH' if level == 'on' else reduced}}}}]}}
        assert all(p.read_bytes() == b for p, b in originals.items())
    assert not target.exists() and not target.parent.exists()
    assert env == before


def test_default_control_never_reads_or_resolves(monkeypatch):
    import dream.core.gemini_thinking as module
    def forbidden(*args, **kwargs):
        raise AssertionError('Provider default must not inspect configuration')
    monkeypatch.setattr(module, '_read_object', forbidden)
    env = {'HOME': '/nonexistent-fixture'}
    with thinking_environment(None, None, cwd='/missing', executable='/missing', env=env) as child:
        assert child is env
    with thinking_environment(None, None, cwd='/missing', executable=None) as child:
        assert child is None


def test_resolved_executable_link_and_explicit_defaults_are_preserved(fixture):
    root, executable, paths, env = fixture
    link = root / 'gemini'
    link.symlink_to(executable)
    env['GEMINI_CLI_SYSTEM_DEFAULTS_PATH'] = 'system/system-defaults.json'
    with thinking_environment('gemini-3.8-flash', 'off', cwd=str(root), executable=str(link), env=env) as child:
        assert child['GEMINI_CLI_SYSTEM_DEFAULTS_PATH'] == str(paths['defaults'])


def test_exception_cleans_overlay(fixture):
    with pytest.raises(RuntimeError, match='owned fixture'):
        with overlay(fixture) as child:
            target = Path(child['GEMINI_CLI_SYSTEM_SETTINGS_PATH'])
            raise RuntimeError('owned fixture')
    assert not target.exists() and not target.parent.exists()


@pytest.mark.parametrize('layer', ['system', 'defaults', 'user', 'workspace'])
@pytest.mark.parametrize('contents', ['{"modelConfigs":{"customOverrides":[]}}', '/* comment */ {}',
    '[]', '{"value":NaN}', '{"x":1,"x":2}'])
def test_all_layers_refuse_conflicts_or_nonplain_json(fixture, layer, contents):
    path = fixture[2][layer]
    path.write_text(contents)
    with pytest.raises(ValueError):
        with overlay(fixture):
            pytest.fail('Unsafe settings admitted')
    assert path.read_text() == contents


@pytest.mark.parametrize('kind', ['symlink', 'dangling', 'directory', 'oversize'])
def test_unsafe_system_file_refused(fixture, kind):
    path = fixture[2]['system']
    path.unlink()
    if kind == 'symlink': path.symlink_to(fixture[2]['defaults'])
    elif kind == 'dangling': path.symlink_to(path.parent / 'missing')
    elif kind == 'directory': path.mkdir()
    else: path.write_text(' ' * 1_000_001)
    with pytest.raises(ValueError):
        with overlay(fixture):
            pytest.fail('Unsafe file admitted')


@pytest.mark.parametrize('field,value', [('name', 'other/package'), ('version', '0.51.0')])
def test_other_package_identity_refused(fixture, field, value):
    path = fixture[1].parent.parent / 'package.json'
    body = json.loads(path.read_text()); body[field] = value
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError):
        with overlay(fixture):
            pytest.fail('Unsupported package admitted')


def test_missing_settings_supported_without_creating_originals(fixture):
    for path in fixture[2].values(): path.unlink()
    with overlay(fixture) as child:
        assert set(json.loads(Path(child['GEMINI_CLI_SYSTEM_SETTINGS_PATH']).read_text())) == {'modelConfigs'}
    assert all(not p.exists() for p in fixture[2].values())
