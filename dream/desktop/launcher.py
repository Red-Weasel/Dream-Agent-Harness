"""Launch the optional Linux desktop without putting GTK in the harness venv."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

PROBE = """import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Vte', '2.91')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, Vte, WebKit2
"""


def gtk_python() -> str | None:
    """Choose an installed interpreter with all three native typelibs."""
    candidates = [sys.executable, '/usr/bin/python3', shutil.which('python3')]
    for exe in dict.fromkeys(p for p in candidates if p):
        try:
            if subprocess.run([exe, '-c', PROBE], capture_output=True, timeout=10).returncode == 0:
                return exe
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def launch(args: list[str] | None = None) -> int:
    args = list(args or [])
    if args == ['--help']:
        print('Usage: dream desktop [Dream CLI arguments]\n\n'
              'Opens graphical agent/model selection, Chat, Terminal and Browser in one window.\n'
              'Examples: dream desktop\n'
              '          dream desktop --provider codex --workspace /path/to/project\n'
              '          dream desktop local --keep-hot\n\n'
              'dream desktop --check verifies desktop dependencies. Bare dream remains the CLI.')
        return 0
    interpreter = gtk_python()
    if not interpreter:
        print('Dream Desktop needs GTK 3, VTE and WebKitGTK 4.1.\n'
              'On Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-3.0 '
              'gir1.2-vte-2.91 gir1.2-webkit2-4.1\n'
              'Your terminal is still available with: dream', file=sys.stderr)
        return 1
    if args == ['--check']:
        print(f'Dream Desktop ready: GTK 3 / VTE / WebKitGTK 4.1\nDesktop Python: {interpreter}\nHarness Python: {sys.executable}')
        return 0
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        print('Dream Desktop needs a graphical session. Run dream for the terminal.', file=sys.stderr)
        return 1
    root = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [root, env.get('PYTHONPATH')]))
    command = [interpreter, '-m', 'dream.desktop.window', '--python', sys.executable,
               '--cwd', os.getcwd(), '--', *args]
    try:
        # Ctrl+C in the launching terminal must not tear down a live PTY and
        # bypass Dream's save/unload lifecycle. The GUI owns its own lifetime.
        process = subprocess.Popen(command, env=env, start_new_session=True)
        return process.wait()
    except KeyboardInterrupt:
        print('Dream Desktop is still open. Close its window to finish the session cleanly.', file=sys.stderr)
        return 130
    except OSError as exc:
        print(f'Dream Desktop could not launch: {exc}', file=sys.stderr)
        return 1
