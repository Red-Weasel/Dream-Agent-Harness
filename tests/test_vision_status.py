"""Reported support and actual adapter configuration remain separate facts."""
import builtins
import copy
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend


async def unused_handler(args):
    raise AssertionError('Status inspection must not execute a tool')


def backend(enabled, metadata, *, include_see=True):
    tool = SimpleNamespace(name='see', description='Fixture image tool',
        input_schema={'type': 'object', 'properties': {}}, handler=unused_handler)
    return OpenAICompatBackend(provider=SimpleNamespace(key='machx', label='fixture',
        base_url='http://fixture.invalid/v1', multimodal=enabled), model='first',
        system_prompt='private-system-canary', tools=[tool] if include_see else [],
        permission_cb=None, provider_metadata=metadata)


def rows(backend):
    return {row['name']: row for row in backend.generation_settings_status()['summary']}


def snapshot(backend):
    return {key: copy.deepcopy(value) if isinstance(value, (dict, list, tuple, set)) else value
            for key, value in backend.__dict__.items()}


def forbidden(*args, **kwargs):
    raise AssertionError('Status inspection must not perform I/O')


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('include_see', [False, True])
@pytest.mark.parametrize('metadata', [None, {'vision': True}, {'vision': False},
    {'vision': 'private-metadata-canary'}, {'vision': None}, {'vision': 1}],
    ids=['missing', 'supported', 'unsupported', 'string', 'null', 'integer'])
def test_reported_vision_does_not_enable_or_disable_adapter(monkeypatch, enabled, include_see, metadata):
    b = backend(enabled, metadata, include_see=include_see)
    # Resolve imports before the I/O guard; construction isn't a status read.
    b.capability_status()
    b.generation_settings_status()
    before = snapshot(b)
    for obj, name in ((builtins, 'open'), (Path, 'open'), (socket.socket, 'connect'),
                      (socket.socket, 'connect_ex')):
        monkeypatch.setattr(obj, name, forbidden)
    b._client = SimpleNamespace(get=forbidden, post=forbidden)
    before['_client'] = b._client
    caps, active = b.capability_status(), rows(b)
    assert active['tool_images_enabled']['known'] is True
    assert active['tool_images_enabled']['value'] is enabled
    assert active['tool_images_enabled']['source'] == 'Adapter configuration; not reported support'
    assert active['see_registered']['known'] is True
    assert active['see_registered']['value'] is (enabled and include_see)
    assert active['see_registered']['source'] == 'Backend tool registration; not execution permission'
    reported = metadata.get('vision') if metadata else None
    assert caps['vision']['known'] is (type(reported) is bool)
    assert caps['vision']['value'] is (reported if type(reported) is bool else None)
    warnings = [w for w in caps['warnings'] if w.startswith('Reported image input')]
    if type(reported) is bool and reported != enabled:
        assert warnings == [
            'Reported image input is supported, but tool images are disabled.' if reported else
            'Reported image input is unsupported, but tool images are enabled.']
    else:
        assert warnings == []
    assert b.__dict__ == before
    assert 'private' not in json.dumps([caps, active], allow_nan=False)
    assert ('see' in b.tools_by_name) is (enabled and include_see)


@pytest.mark.parametrize('enabled', [False, True])
async def test_model_switch_invalidates_report_and_warning_but_keeps_configuration_and_effort(enabled):
    b = backend(enabled, {'vision': not enabled})
    b.set_effort('high')
    first = b.capability_status()
    configured = rows(b)
    await b.set_model('first')
    assert b.capability_status() == first
    assert rows(b) == configured
    for model in ('second', 'first'):
        await b.set_model(model)
        caps = b.capability_status()
        assert not caps['vision']['known'] and not caps['warnings']
        assert rows(b)['tool_images_enabled'] == configured['tool_images_enabled']
        assert rows(b)['see_registered'] == configured['see_registered']
        assert b._base_effort_params() == {'reasoning_effort': 'high'}


@pytest.mark.parametrize('enabled', [False, True])
def test_unverified_raw_machx_vision_key_does_not_become_support(enabled):
    b = backend(enabled, None)
    b._local_capabilities = {'vision': not enabled, 'private': 'private-metadata-canary'}
    caps = b.capability_status()
    assert caps['vision'] == {'known': False, 'value': None, 'source': 'unreported'}
    assert caps['warnings'] == []
    assert rows(b)['tool_images_enabled']['value'] is enabled
    assert 'private' not in json.dumps(caps)
