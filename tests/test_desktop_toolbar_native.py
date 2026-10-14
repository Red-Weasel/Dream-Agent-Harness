"""Run the actual native header/account widgets on a private Broadway display."""
import os
from pathlib import Path
import subprocess
import tempfile
import time

import pytest


@pytest.mark.skipif(not Path('/usr/bin/broadwayd').exists() or not Path('/usr/bin/python3').exists(),
                    reason='needs broadwayd and the system Python with GTK')
def test_private_native_toolbar_rendering():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='dream-toolbar-display-') as runtime:
        env = dict(os.environ, XDG_RUNTIME_DIR=runtime, GDK_BACKEND='broadway',
                   BROADWAY_DISPLAY=':27', NO_AT_BRIDGE='1',
                   DREAM_TOOLBAR_NATIVE_SHOTS='/tmp/dream271-native-toolbar')
        with open(Path(runtime) / 'display.log', 'w+') as log:
            display = subprocess.Popen(['/usr/bin/broadwayd', '--unixsocket', runtime + '/http.socket', ':27'],
                                       env=env, stdout=log, stderr=log)
            try:
                for _ in range(50):
                    if display.poll() is not None:
                        log.seek(0)
                        raise AssertionError(log.read())
                    if Path(runtime, 'broadway28.socket').exists():
                        break
                    time.sleep(.02)
                env['PYTHONPATH'] = str(root)
                result = subprocess.run(['/usr/bin/python3', '-m', 'unittest', 'discover', '-s', 'tests',
                                         '-p', 'test_desktop_toolbar.py', '-v'], cwd=root, env=env,
                                        capture_output=True, text=True, timeout=45)
                assert result.returncode == 0, result.stdout + result.stderr
                assert 'skipped' not in result.stderr, result.stderr
            finally:
                display.terminate()
                display.wait(timeout=5)
