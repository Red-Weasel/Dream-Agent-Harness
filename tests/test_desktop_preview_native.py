"""The Studio preview in the desktop's real WebKitGTK view (A0): the three checks of tests/desktop_preview_native.py
-- the site with its own css/js/png, the importmap page's notice, the page busy for 5 s -- on a private headless
GTK display (`broadwayd`), the real DreamWindow, the real StudioServer and the real `done` tool. System GTK is
optional in pytest's venv: without the GI bindings or `broadwayd` the tests skip. Run with the system Python
(`run-sys.sh`), like tests/test_desktop_*.py and tests/test_chat_webkit.py. `DREAM_PREVIEW_NATIVE_SHOTS=<dir>`
keeps the snapshots; `DREAM_PREVIEW_NATIVE_X11=:N` uses that X display instead of broadwayd."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest

import desktop_preview_native as native
from _artifact_play import Play

pytestmark = pytest.mark.skipif(importlib.util.find_spec('gi') is None or shutil.which('broadwayd') is None,
                                reason='System GTK/WebKit bindings and broadwayd required')
SHOTS = os.environ.get('DREAM_PREVIEW_NATIVE_SHOTS')
X11 = os.environ.get('DREAM_PREVIEW_NATIVE_X11')


@pytest.fixture
async def desktop(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    from dream.gui import preview as preview_mod
    monkeypatch.setattr(preview_mod, 'display_render_node', lambda: None)
    ws = tmp_path / 'ws'
    ws.mkdir()
    runtime = tempfile.mkdtemp(prefix='dream-pv-')    # short: a unix socket path has 108 characters
    os.chmod(runtime, 0o700)
    env = {'HOME': os.environ.get('HOME', runtime), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8',
           'XDG_RUNTIME_DIR': runtime, 'GDK_BACKEND': 'broadway', 'BROADWAY_DISPLAY': ':6', 'NO_AT_BRIDGE': '1',
           'PYTHONPATH': os.environ.get('PYTHONPATH', ''), 'PYTHONDONTWRITEBYTECODE': '1'}
    log = open(Path(runtime) / 'driver.log', 'wb')
    display = process = None
    shots = Path(SHOTS) if SHOTS else tmp_path / 'shots'
    shots.mkdir(parents=True, exist_ok=True)
    try:
        if X11:
            env.update(GDK_BACKEND='x11', DISPLAY=X11)
        else:
            display = subprocess.Popen(['broadwayd', '--unixsocket', str(Path(runtime) / 'http.socket'), ':6'],
                                       env=env, stdout=log, stderr=log)
            for _ in range(100):                       # its client socket is an abstract one: the log line says it listens
                if b'Listening on' in Path(log.name).read_bytes():
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.skip('broadwayd did not start')
        async with Play(ws, session_id='preview-native') as play:
            process, wk = await native.start_driver(env, log)
            await native.connect(play, wk)
            yield play, wk, shots
    except BaseException:
        log.flush()
        sys.stderr.write(Path(log.name).read_text(errors='replace')[-4000:])
        raise
    finally:
        if process is not None:
            try:
                await asyncio.wait_for(wk.call('quit'), 5)
                await asyncio.wait_for(process.wait(), 5)
            except Exception:
                process.kill()
        if display is not None:
            display.terminate(); display.wait(5)
        log.close()
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.parametrize('name, check, judge', native.CHECKS, ids=[c[0] for c in native.CHECKS])
async def test_the_preview_in_the_real_webkit_view(desktop, name, check, judge):
    play, wk, shots = desktop
    geometry = await wk.call('geometry')
    assert geometry['trusted'], geometry                     # the window recognises its own document
    report = await check(play, wk, shots)
    print(f'WEBKIT {name}:', json.dumps(report))
    assert judge(report) == [], report
