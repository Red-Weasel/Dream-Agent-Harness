"""A Dream terminal on the DeepSleep board asks the local endpoint it uses. Its requests go to DREAM_MACHX_URL (the
machx provider); its own local-engine checks -- the served model it asks for before every turn -- go to
dream.local.machx, which reads DREAM_MACHX_HOST and DREAM_MACHX_PORT. login_env drops every DREAM_* variable, so the
board passes the endpoint for both. With only the URL, the child asked the default port 11435: it refused every turn
("No local model is loaded"), or asked an engine it does not use. Nothing is started and no request is made."""
import asyncio
import subprocess
import sys
from dataclasses import replace

import pytest

from dream import config
from dream.core import providers
from dream.gui.deepsleep import DeepSleep

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost', '[::1]'])
async def test_a_dream_terminal_checks_the_endpoint_it_sends_to(tmp_path, monkeypatch, host):
    serving = f'http://{host}:43123/v1'                  # a parent's local endpoint that is not the default port
    monkeypatch.setitem(providers.PROVIDERS, 'machx', replace(providers.PROVIDERS['machx'], base_url=serving))
    monkeypatch.setattr(config, 'ROOT', tmp_path / 'runtime')
    (tmp_path / 'runtime').mkdir()
    workspace = tmp_path / 'ws'
    workspace.mkdir()
    seen = []

    async def spawn(*argv, env, **kwargs):                 # the environment the child would start with
        seen.append(env)
        raise ValueError('fixture: not started')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    board = DeepSleep(lambda: str(workspace), lambda: 'http://127.0.0.1:9')
    node = await board.create({'provider': 'machx', 'profile': 'dream', 'model': 'controlled-model'})
    assert node.state == 'failed' and len(seen) == 1

    # What the child's own modules make of that environment: the provider and the local-engine module agree.
    probe = ('from dream.core.providers import PROVIDERS; from dream.local import machx; '
             'print(PROVIDERS["machx"].base_url); print(machx.BASE_URL)')
    result = subprocess.run([sys.executable, '-c', probe], env=seen[0], cwd=workspace,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [serving, serving]
