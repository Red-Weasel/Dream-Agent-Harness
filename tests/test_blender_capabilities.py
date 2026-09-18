"""Blender diagnostics through CPU-only boundary fixtures."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

from dream.media import blender, blender_probe


def fake_bpy(monkeypatch, *, build=True, registration_error=False):
    state = {'registered': False, 'calls': []}
    class Render:
        _engine = 'ORIGINAL'
        @property
        def engine(self):
            return self._engine
        @engine.setter
        def engine(self, value):
            if value == 'CYCLES' and not state['registered']:
                raise TypeError('Cycles not registered')
            if value == 'BLENDER_EEVEE':
                raise TypeError('Legacy engine unavailable')
            self._engine = value
    addons = {}
    render = Render()
    bpy = SimpleNamespace(app=SimpleNamespace(version_string='4.5.1', build_options=SimpleNamespace(cycles=build, cycles_osl=False)), context=SimpleNamespace(scene=SimpleNamespace(render=render), preferences=SimpleNamespace(addons=addons)))
    def enable(name, **kwargs):
        state['calls'].append((name, kwargs))
        if registration_error:
            raise RuntimeError('registration failed')
        state['registered'] = True
        addons[name] = object()
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'addon_utils', SimpleNamespace(enable=enable))
    return state, render


def test_registration_and_actual_engine_selection(monkeypatch):
    state, render = fake_bpy(monkeypatch)
    result = blender_probe.collect()
    assert result['cycles']['registration'] == 'enabled'
    assert result['engines']['CYCLES']['selectable'] is True
    assert result['engines']['BLENDER_EEVEE_NEXT']['selectable'] is True
    assert result['engines']['BLENDER_EEVEE']['selectable'] is False
    assert render.engine == 'ORIGINAL'
    assert state['calls'][0][0] == 'cycles'
    assert state['calls'][0][1]['default_set'] is False
    assert state['calls'][0][1]['persistent'] is False
    assert callable(state['calls'][0][1]['handle_error'])
    assert result['devices'] == result['headless_render'] == 'unverified'


def test_registration_error_preserved(monkeypatch):
    fake_bpy(monkeypatch, registration_error=True)
    result = blender_probe.collect()
    assert result['cycles']['build_enabled'] is True
    assert result['cycles']['registration'] == 'failed'
    assert 'registration failed' in result['cycles']['error']
    assert result['engines']['CYCLES']['selectable'] is False


def test_passive_never_launches(monkeypatch):
    monkeypatch.setattr(blender.shutil, 'which', lambda name: '/fixture/blender')
    def forbidden(*args, **kwargs):
        pytest.fail('passive status launched a process')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    status = blender.blender_status()
    assert status['available'] is True
    assert status['probe_status'] == 'unprobed'
    assert status['cycles'] is status['engines'] is status['version'] is None


@pytest.mark.parametrize('confirmation', [False, None, 1, 'true'])
async def test_confirmation_must_be_literal_true(monkeypatch, confirmation):
    monkeypatch.setattr(blender.shutil, 'which', lambda name: '/fixture/blender')
    assert (await blender.probe_blender(resource_confirmed=confirmation))['probe_status'] == 'confirmation_required'


def executable(tmp_path, monkeypatch, body):
    path = tmp_path / 'fake-blender'
    path.write_text('#!' + sys.executable + '\n' + body)
    path.chmod(0o700)
    monkeypatch.setattr(blender.shutil, 'which', lambda name: str(path))
    return path


async def test_fixed_isolated_launch(monkeypatch, tmp_path):
    fake_bpy(monkeypatch)
    result = blender_probe.collect()
    body = "import os,sys\nassert sys.argv[1:7] == ['--background','--factory-startup','--disable-autoexec','--python-exit-code','1','--python']\nassert sys.argv[7].endswith('/dream/media/blender_probe.py')\nassert os.environ['BLENDER_USER_CONFIG'].startswith(os.getcwd())\nprint(" + repr(blender_probe.MARKER + json.dumps(result)) + ')\n'
    executable(tmp_path, monkeypatch, body)
    status = await blender.probe_blender(resource_confirmed=True)
    assert status['probe_status'] == 'ok'
    assert status['version'] == '4.5.1'


@pytest.mark.parametrize('body', ["print('garbage')", "print('DREAM_BLENDER_CAPABILITIES_V1:{')", "print('DREAM_BLENDER_CAPABILITIES_V1:{}')", "import sys; sys.exit(2)"])
async def test_bad_process_results(monkeypatch, tmp_path, body):
    executable(tmp_path, monkeypatch, body)
    assert (await blender.probe_blender(resource_confirmed=True))['probe_status'] == 'error'


@pytest.mark.parametrize('mode', ['timeout', 'output', 'cancel'])
async def test_owned_process_cleanup(monkeypatch, tmp_path, mode):
    marker = tmp_path / 'pid'
    body = f"import os,time\nopen({str(marker)!r},'w').write(str(os.getpid()))\n"
    body += "while True: os.write(1,b'x'*4096)\n" if mode == 'output' else 'time.sleep(30)\n'
    executable(tmp_path, monkeypatch, body)
    monkeypatch.setattr(blender, 'PROBE_TIMEOUT', 0.2 if mode == 'timeout' else 5)
    task = asyncio.create_task(blender.probe_blender(resource_confirmed=True))
    for _ in range(100):
        if marker.exists():
            break
        await asyncio.sleep(0.01)
    assert marker.exists()
    pid = int(marker.read_text())
    if mode == 'cancel':
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        result = await task
        assert result['probe_status'] == 'error'
        assert ('timed out' if mode == 'timeout' else 'output limit') in result['error']
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_swallowed_registration_error(monkeypatch):
    fake_bpy(monkeypatch)
    def enable(*args, handle_error, **kwargs):
        try:
            raise RuntimeError('native initialization rejected')
        except RuntimeError:
            handle_error()
        return None
    monkeypatch.setitem(sys.modules, 'addon_utils', SimpleNamespace(enable=enable))
    result = blender_probe.collect()
    assert result['cycles']['registration'] == 'failed'
    assert 'native initialization rejected' in result['cycles']['error']


@pytest.mark.parametrize('change', [
    lambda data: data.update(schema_version=True),
    lambda data: data['engines']['CYCLES'].update(selectable='yes'),
    lambda data: data.update(devices=['invented GPU']),
    lambda data: data.update(headless_render='verified'),
    lambda data: data.update(cycles={}),
])
def test_invalid_evidence_rejected(monkeypatch, change):
    fake_bpy(monkeypatch)
    data = blender_probe.collect()
    change(data)
    with pytest.raises(ValueError):
        blender._parse((blender_probe.MARKER + json.dumps(data)).encode())


async def test_cancellation_during_cleanup_is_not_success(monkeypatch, tmp_path):
    fake_bpy(monkeypatch)
    data = blender_probe.collect()
    executable(tmp_path, monkeypatch, 'print(' + repr(blender_probe.MARKER + json.dumps(data)) + ')')
    started = asyncio.Event()
    release = asyncio.Event()
    original = blender._cleanup
    async def cleanup(process):
        started.set()
        await release.wait()
        await original(process)
    monkeypatch.setattr(blender, '_cleanup', cleanup)
    task = asyncio.create_task(blender.probe_blender(resource_confirmed=True))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.skipif(os.name != 'posix', reason='POSIX session escape fixture')
async def test_escaped_descendant_cannot_hold_probe_open(monkeypatch, tmp_path):
    import time
    body = "import subprocess,sys\nsubprocess.Popen([sys.executable,'-c','import time; time.sleep(1)'], start_new_session=True)\n"
    executable(tmp_path, monkeypatch, body)
    monkeypatch.setattr(blender, 'PROBE_TIMEOUT', 0.1)
    monkeypatch.setattr(blender, 'CLEANUP_GRACE', 0.05, raising=False)
    start = time.monotonic()
    result = await blender.probe_blender(resource_confirmed=True)
    elapsed = time.monotonic() - start
    assert result['probe_status'] == 'error'
    assert 'timed out' in result['error']
    assert elapsed < 0.7


async def test_system_startup_overrides_removed(monkeypatch, tmp_path):
    fake_bpy(monkeypatch)
    data = blender_probe.collect()
    for name in ('BLENDER_SYSTEM_SCRIPTS', 'BLENDER_SYSTEM_PYTHON', 'BLENDER_SYSTEM_DATAFILES'):
        monkeypatch.setenv(name, '/untrusted/fixture')
    body = "import os\nassert not any(k.startswith('BLENDER_SYSTEM_') for k in os.environ)\nprint(" + repr(blender_probe.MARKER + json.dumps(data)) + ')\n'
    executable(tmp_path, monkeypatch, body)
    assert (await blender.probe_blender(resource_confirmed=True))['probe_status'] == 'ok'
