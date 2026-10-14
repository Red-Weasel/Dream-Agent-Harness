"""Modified Enter through actual GTK/VTE into an owned raw PTY, no model."""
import os
from pathlib import Path
import subprocess
import tempfile
import time

import pytest


@pytest.mark.skipif(not Path('/usr/bin/broadwayd').exists() or not Path('/usr/bin/python3').exists(),
                    reason='needs broadwayd and the system Python with GTK')
def test_native_vte_modified_enter_is_one_safe_sequence():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='dream-multiline-display-') as runtime:
        env = dict(os.environ, XDG_RUNTIME_DIR=runtime, GDK_BACKEND='broadway',
                   BROADWAY_DISPLAY=':79', NO_AT_BRIDGE='1', PYTHONPATH=str(root))
        with open(Path(runtime) / 'display.log', 'w+') as log:
            display = subprocess.Popen(['/usr/bin/broadwayd', '--unixsocket', runtime + '/http.socket', ':79'],
                                       env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if display.poll() is not None:
                        log.seek(0)
                        raise AssertionError(log.read())
                    if Path(runtime, 'broadway80.socket').exists():
                        break
                    time.sleep(.02)
                result = subprocess.run(['/usr/bin/python3', '-X', 'faulthandler', '-u', '-c', NATIVE_CHECK],
                    cwd=root, env=env, capture_output=True, text=True, timeout=15)
                assert result.returncode == 0, result.stdout + result.stderr
                assert 'VTE_MULTILINE_OK' in result.stdout
            finally:
                display.terminate()
                display.wait(timeout=5)


NATIVE_CHECK = r'''
import os, select, tty
from types import SimpleNamespace
from dream.desktop.window import DreamWindow, Gdk, Gtk, Vte

assert Gtk.init_check()[0]
window = Gtk.Window()
terminal = Vte.Terminal()
window.add(terminal)
owner = SimpleNamespace(terminal=terminal, running=True)
terminal.connect('key-press-event', lambda widget, event: DreamWindow._terminal_keys(owner, widget, event))
master, slave = os.openpty()
tty.setraw(slave)
terminal.set_pty(Vte.Pty.new_foreign_sync(master, None))
window.show_all()
terminal.grab_focus()
while Gtk.events_pending(): Gtk.main_iteration_do(False)

def key(keyval, state):
    event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
    event.window = terminal.get_window()
    event.keyval = keyval
    event.state = state
    event.hardware_keycode = 36
    return terminal.emit('key-press-event', event)

def received():
    while Gtk.events_pending(): Gtk.main_iteration_do(False)
    ready, _, _ = select.select([slave], [], [], .1)
    return os.read(slave, 128) if ready else b''

try:
    for code in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
        assert key(code, Gdk.ModifierType.SHIFT_MASK)
        assert received() == b'\x1b[13;2u'
        assert received() == b''
    # Plain Enter is still processed by the actual VTE widget.
    key(Gdk.KEY_Return, 0)
    assert received() == b'\r'
    for modifiers in (0, Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK,
                      Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.MOD1_MASK,
                      Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.SUPER_MASK):
        assert not DreamWindow._terminal_keys(owner, terminal,
            SimpleNamespace(keyval=Gdk.KEY_Return, state=modifiers))
    owner.running = False
    assert key(Gdk.KEY_Return, Gdk.ModifierType.SHIFT_MASK)
    assert received() == b''
    print('VTE_MULTILINE_OK')
finally:
    window.destroy()
    while Gtk.events_pending(): Gtk.main_iteration_do(False)
    os.close(slave)
'''
