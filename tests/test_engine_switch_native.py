"""The real Dream window's Unload model / Load model buttons, on a private Broadway display (never the owner's screen).

The window runs in the system Python that has GTK; no session, engine or model is started.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import pytest

SCRIPT = r'''
import sys
from dream.desktop import engine_controls
engine_controls.served_model = lambda *a, **k: None          # the owner's engine is never asked
from dream.desktop.window import DreamWindow, Gtk             # the window picks GTK 3

def drain():
    for _ in range(20):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

win = DreamWindow(sys.executable, sys.argv[1], [], autostart=False)
drain()
children = win.sidebar.get_children()
codex = next(i for i, child in enumerate(children)
             if isinstance(child, Gtk.Box) and win.frontier_buttons['codex'] in child.get_children())
assert children[codex + 1].get_style_context().has_class('engine-controls'), 'controls sit right under Codex'
assert [b.get_label() for b in (win.unload_button, win.load_button)] == ['Unload model', 'Load model']

win.running = True                                            # an open session, a model loaded
win.engine_model = 'Qwen3.8-35B-A3B-Q8_0'
win._show_engine_state()
assert win.unload_button.get_sensitive() and not win.load_button.get_sensitive()
assert win.engine_label.get_text() == 'Local model: Qwen3.8-35B-A3B-Q8_0'

win.engine_model = None                                       # unloaded: Load model is the one to press
win._show_engine_state()
assert win.load_button.get_sensitive() and not win.unload_button.get_sensitive()

asked = []
win.onboarding.load_catalog = lambda: asked.append('catalog')
win.load_button.clicked()
drain()
assert asked == ['catalog']
assert win.onboarding.load_only and win.onboarding.headline.get_text() == 'Load a model'
assert win.studio_stack.get_visible_child_name() == 'welcome'
assert win.nav_buttons['home'].get_style_context().has_class('selected')
assert not win.onboarding.council.get_visible()

loaded = []
win.load_model = lambda request: loaded.append(request)       # the selector's button loads, it starts nothing
win.onboarding.catalog_loaded(dict(choices=[
    dict(id='codex', kind='provider', provider='codex', ready=True, label='Codex', note='fixture'),
    dict(id='/m/Qwen.gguf', kind='local', provider='machx', ready=True, path='/m/Qwen.gguf', label='Qwen fixture',
         note='fixture')], council_choices=[]))
assert [c['kind'] for c in win.onboarding.choices] == ['local']
assert win.onboarding.start.get_label() == 'Load model'
win.onboarding.settings = dict(identity='k', revision=None)
win.onboarding.fields = {}
win.onboarding.launch()
assert len(loaded) == 1 and loaded[0]['choice']['kind'] == 'local' and not win.launch_request

win.navigate('chat')                                          # leaving without loading
assert not win.onboarding.load_only and win.onboarding.headline.get_text() == 'Start a conversation'

win.running = False                                           # no session: Load model waits for one
win._show_engine_state()
assert not win.load_button.get_sensitive() and 'Home' in win.load_button.get_tooltip_text()
win.destroy()
print('native engine controls ok')
'''


@pytest.mark.skipif(not shutil.which('broadwayd') or not Path('/usr/bin/python3').exists(),
                    reason='needs broadwayd and the system Python with GTK')
def test_the_real_window_shows_and_wires_unload_and_load(tmp_path):
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='dream-engine-display-') as runtime:
        env = dict(os.environ, XDG_RUNTIME_DIR=runtime, GDK_BACKEND='broadway', BROADWAY_DISPLAY=':31',
                   NO_AT_BRIDGE='1', PYTHONPATH=str(root))
        env.pop('DISPLAY', None)
        env.pop('WAYLAND_DISPLAY', None)
        with open(Path(runtime) / 'display.log', 'w+') as log:
            display = subprocess.Popen([shutil.which('broadwayd'), '--unixsocket', runtime + '/http.socket', ':31'],
                                       env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if display.poll() is not None:
                        log.seek(0)
                        raise AssertionError(log.read())
                    if Path(runtime, 'broadway32.socket').exists():
                        break
                    time.sleep(.02)
                result = subprocess.run(['/usr/bin/python3', '-c', SCRIPT, str(tmp_path)], cwd=root, env=env,
                                        capture_output=True, text=True, timeout=60)
                assert result.returncode == 0, result.stdout + result.stderr
                assert 'native engine controls ok' in result.stdout
            finally:
                display.terminate()
                display.wait(timeout=5)
