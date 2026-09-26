"""Blender diagnostics reach media clients without launching a real renderer."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from dream.media.cli import main
from dream.media.service import MediaService
from dream.media.store import MediaError


def test_media_status_marks_blender_render_support_unverified(tmp_path):
    status = MediaService(tmp_path).status()['blender']
    assert status['probe_status'] == 'unprobed'
    assert status['engines'] is None
    assert status['headless_render'] == 'unverified'


@pytest.mark.parametrize('confirmation', [None, False, 'true', 1])
def test_probe_requires_literal_resource_confirmation(tmp_path, confirmation):
    service = MediaService(tmp_path)
    with pytest.raises(MediaError, match='resource'):
        asyncio.run(service.execute('probe_blender', {'resource_confirmed': confirmation}))
    assert service.store.list_jobs() == []


def test_cli_reports_missing_probe_confirmation_as_error(tmp_path, capsys):
    result = main(['--workspace', str(tmp_path), 'probe_blender'])
    captured = capsys.readouterr()
    assert result == 2
    assert 'resource' in json.loads(captured.err)['error']
    assert not captured.out


def test_confirmed_probe_is_diagnostic_only_and_failures_are_errors(tmp_path, monkeypatch, capsys):
    from dream.media import service as service_module
    observed = []
    report = {'probe_status': 'ok', 'version': '4.0.2',
              'engines': {'CYCLES': {'selectable': True, 'error': None}},
              'devices': 'unverified', 'headless_render': 'unverified'}

    async def fake_probe(*, resource_confirmed=False):
        observed.append(resource_confirmed)
        return report

    monkeypatch.setattr(service_module, 'probe_blender', fake_probe)
    service = MediaService(tmp_path)
    assert asyncio.run(service.execute('probe_blender', {'resource_confirmed': True})) == report
    assert observed == [True]
    assert service.store.list_jobs() == []
    assert main(['--workspace', str(tmp_path), 'probe_blender', '--resource-confirmed']) == 0
    assert json.loads(capsys.readouterr().out)['headless_render'] == 'unverified'
    report = {'probe_status': 'error', 'error': 'Blender diagnostic timed out'}
    assert main(['--workspace', str(tmp_path), 'probe_blender', '--resource-confirmed']) == 2
    assert 'timed out' in json.loads(capsys.readouterr().err)['error']


async def test_read_tool_cannot_launch_probe_and_write_tool_preserves_error(tmp_path, monkeypatch):
    from dream.media import service as service_module
    from dream.tools import media_tools
    calls = []

    async def fake_probe(*, resource_confirmed=False):
        calls.append(resource_confirmed)
        return {'probe_status': 'error', 'error': 'Native Cycles initialization failed'}

    monkeypatch.setattr(service_module, 'probe_blender', fake_probe)
    monkeypatch.setattr(media_tools, 'ctx', lambda: SimpleNamespace(workspace=tmp_path))
    args = {'action': 'probe_blender', 'payload': {'resource_confirmed': True}}
    refused = await media_tools.media_read.handler(args)
    assert refused.get('is_error') is True
    assert calls == []
    result = await media_tools.media_create.handler(args)
    assert result.get('is_error') is True
    assert 'Native Cycles initialization failed' in result['content'][0]['text']
    assert calls == [True]


def test_blender_probe_route_keeps_auth_post_and_confirmation_boundaries(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    from dream.media import service as service_module
    calls = []

    async def fake_probe(*, resource_confirmed=False):
        calls.append(resource_confirmed)
        return {'probe_status': 'ok', 'headless_render': 'unverified'}

    monkeypatch.setattr(service_module, 'probe_blender', fake_probe)
    server = StudioServer(EventBus(), session={'workspace': str(tmp_path)})
    with TestClient(server.app) as client:
        path = '/api/media/probe_blender'
        assert client.post(path, json={'resource_confirmed': True}).status_code == 401
        headers = {'x-dream-token': server.token}
        assert client.get(path, headers=headers).status_code == 405
        assert client.post(path, headers={**headers, 'origin': 'https://foreign.test'}, json={'resource_confirmed': True}).status_code == 403
        assert client.post(path, headers=headers, json={}).status_code == 400
        assert calls == []
        result = client.post(path, headers=headers, json={'resource_confirmed': True})
        assert result.status_code == 200
        assert result.json()['headless_render'] == 'unverified'
        assert calls == [True]


def test_open_external_runs_blender_without_loader_overrides(tmp_path, monkeypatch):
    """DREAM-141: Dream's own host launch of Blender drops LD_LIBRARY_PATH and LD_PRELOAD and
    keeps the rest of the environment."""
    from dream.media import service as service_module
    started = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            started.append((argv, kwargs))

        def wait(self):
            return 0

    monkeypatch.setenv('LD_LIBRARY_PATH', '/opt/intel/oneapi/compiler/2026.1/lib')
    monkeypatch.setenv('LD_PRELOAD', '/nonexistent/dream-test-preload.so')
    monkeypatch.setenv('DREAM_TEST_KEEP', 'kept')
    monkeypatch.setattr(service_module.shutil, 'which', lambda name: '/usr/bin/blender')
    monkeypatch.setattr(service_module.subprocess, 'Popen', FakePopen)
    service = MediaService(tmp_path)
    project = asyncio.run(service.execute('create', {'title': 'Car'}))
    (tmp_path / 'car.blend').write_bytes(b'BLENDER')
    job = asyncio.run(service.execute('open_external', {'project_id': project['id'], 'path': 'car.blend'}))
    assert job['status'] == 'awaiting_user'
    (argv, kwargs), = started
    assert argv == ['/usr/bin/blender', '--disable-autoexec', str((tmp_path / 'car.blend').resolve())]
    env = kwargs['env']
    assert 'LD_LIBRARY_PATH' not in env and 'LD_PRELOAD' not in env
    assert env['DREAM_TEST_KEEP'] == 'kept' and env['PATH'] == __import__('os').environ['PATH']
