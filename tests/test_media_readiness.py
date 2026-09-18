"""Presence and configured scope cannot be promoted into runtime qualification."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core.execution import ExecutionScope, execution_context
from dream.media.service import MediaService
from dream.tools.context import bind_context
from dream.tools.media_tools import media_read


def test_status_presence_does_not_claim_encoder_or_shell_qualification(tmp_path, monkeypatch):
    import dream.media.service as service
    monkeypatch.setattr(service.shutil, 'which', lambda name: '/fixture/bin/' + name)
    def forbidden(*args, **kwargs):
        pytest.fail('Passive status must not execute a probe')
    monkeypatch.setattr(service.subprocess, 'Popen', forbidden)
    report = MediaService(tmp_path).status()
    assert report['renderer']['ffmpeg'] is True
    assert report['renderer']['execution_verified'] is None
    assert report['workspace'] == str(tmp_path)
    assert 'host' in report['renderer']['evidence_source'].lower()


@pytest.mark.parametrize('images', [True, False])
async def test_tool_status_reports_actual_bound_workspace_and_configured_scope(tmp_path, images):
    import json
    read_root = tmp_path / 'references'
    read_root.mkdir()
    scope = ExecutionScope(tmp_path, read_roots=(read_root,))
    with bind_context(SimpleNamespace(workspace=tmp_path, multimodal=images)), execution_context(scope):
        result = await media_read.handler({'action': 'status'})
    assert not result.get('is_error')
    report = json.loads(result['content'][0]['text'])
    session = report['session_readiness']
    assert session['tool_images_enabled'] is images
    assert session['image_acceptance_verified'] is None
    assert session['shell']['workspace'] == str(tmp_path)
    assert session['shell']['read_roots'] == [str(read_root)]
    assert session['shell']['availability_verified'] is None


async def test_mismatched_execution_scope_is_not_reported_ready(tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    with bind_context(SimpleNamespace(workspace=tmp_path, multimodal=False)), execution_context(ExecutionScope(other)):
        result = await media_read.handler({'action': 'status'})
    assert result.get('is_error') is True
    assert 'another workspace' in result['content'][0]['text']
