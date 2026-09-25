"""DREAM-086: a crashed session says so and leaves a traceback.

2026-09-23 08:45:57 the desktop session process died of SIGSEGV mid-turn; the window said only "Session ended", and
nothing recorded where it crashed. The window names a crash file for each session it starts (DREAM_CRASH_LOG); the
session records a traceback of every thread there if a fatal signal kills it and removes the empty file on a clean exit;
a session killed by a signal is reported as such, with how to continue.

System GTK is optional in pytest's venv (the window tests skip there); also run with system Python directly.
"""
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dream.desktop import crash_log

try:
    from dream.desktop.window import DreamWindow, LOG_DIR
except (ImportError, ValueError):
    DreamWindow = None

CHILD = r'''
import ctypes, os, signal, sys, threading
sys.path.insert(0, sys.argv[2])
from dream.desktop import crash_log
ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)   # PR_SET_DUMPABLE 0: this deliberate crash leaves no core dump or crash report
stop = crash_log.enable()
print(repr(os.environ.get(crash_log.ENV)), flush=True)
if sys.argv[1] == 'crash':
    threading.Thread(target=lambda: threading.Event().wait(), name='worker', daemon=True).start()
    def deep_in_the_turn():
        os.kill(os.getpid(), signal.SIGSEGV)
    deep_in_the_turn()
stop()
'''


def run_child(mode, log):
    env = dict(os.environ, **({crash_log.ENV: str(log)} if log else {}))
    return subprocess.run([sys.executable, '-c', CHILD, mode, str(ROOT)], env=env, capture_output=True, text=True,
                          timeout=60)


# The desktop window's own process (DREAM-086 follow-up, 2026-09-24 20:13:34: the window exited mid-turn without a word
# on its stderr, which the desktop menu connects to the journal). faulthandler's C-level handlers leave every thread's
# stack on stderr for a fatal signal AND for SIGTERM/SIGHUP, then the signal's default action runs at once -- also while
# the main thread is stuck in C, where a Python-level handler would never run and the window would not die.
WINDOW_CHILD = r'''
import ctypes, os, signal, sys, threading, time
sys.path.insert(0, sys.argv[2])
from dream.desktop import crash_log
ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
def filler_wait():
    threading.Event().wait()
threading.Thread(target=filler_wait, name='filler', daemon=True).start()   # a second thread, as the window has
crash_log.arm_window()
print('armed', flush=True)
if sys.argv[1] == 'segv':
    def deep_in_the_window():
        os.kill(os.getpid(), signal.SIGSEGV)
    deep_in_the_window()
time.sleep(30)
'''


def run_window_child(mode, sig=None):
    proc = subprocess.Popen([sys.executable, '-c', WINDOW_CHILD, mode, str(ROOT)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline() == 'armed\n'
        if sig is not None:
            proc.send_signal(sig)
        _, err = proc.communicate(timeout=60)
    finally:
        proc.kill()
    return proc.returncode, err


class WindowProcessEvidence(unittest.TestCase):
    def test_a_fatal_signal_leaves_every_threads_traceback_on_stderr(self):
        code, err = run_window_child('segv')
        self.assertEqual(code, -signal.SIGSEGV)
        self.assertIn('Segmentation fault', err)
        self.assertIn('deep_in_the_window', err)

    def test_a_stopping_signal_dumps_the_stacks_then_ends_the_process_by_that_signal(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig=sig.name):
                code, err = run_window_child('wait', sig)
                self.assertEqual(code, -sig)          # the default action still runs: the wait status names the signal
                self.assertEqual(err.count('Current thread'), 1)           # one dump ...
                self.assertIn('filler_wait', err)                          # ... of every thread
                self.assertNotIn('Fatal Python error', err)                # a stop reads differently from a crash

    def test_nothing_is_written_while_it_runs(self):
        code, err = run_window_child('wait', signal.SIGKILL)
        self.assertEqual(code, -signal.SIGKILL)
        self.assertEqual(err, '')

    def test_a_closed_stderr_at_start_does_not_stop_the_window_from_arming(self):
        script = f'import sys; sys.path.insert(0, {str(ROOT)!r}); from dream.desktop import crash_log; crash_log.arm_window(); print("up")'
        proc = subprocess.run(['bash', '-c', f'exec 2>&-; exec "$0" -c "$1"', sys.executable, script],
                              stdout=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual((proc.returncode, proc.stdout), (0, 'up\n'))


# The real main(): the display check faked, the window stubbed, Gtk.main replaced -- by a C call that never returns to
# Python (a hung window) or by an immediate return (a clean quit). System GTK required; no window is opened.
MAIN_CHILD = r'''
import sys, time
sys.path.insert(0, sys.argv[2])
from dream.desktop import window
from gi.repository import GLib, Gtk
Gtk.init_check = lambda argv: (True, argv)
window.DreamWindow = lambda *a, **k: None
if sys.argv[1] == 'hang':
    def stuck_in_c():
        print('ready', flush=True)
        GLib.usleep(30_000_000)   # g_usleep restarts on EINTR: Python-level signal handlers never run here
    Gtk.main = stuck_in_c
else:
    Gtk.main = lambda: None
sys.argv = ['window', '--python', 'python3', '--cwd', '/tmp']
window.main()
'''


def run_main_child(mode):
    proc = subprocess.Popen([sys.executable, '-c', MAIN_CHILD, mode, str(ROOT)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    started = None
    try:
        if mode == 'hang':
            assert proc.stdout.readline() == 'ready\n'
            started = time.monotonic()
            proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=60)
    finally:
        proc.kill()
    return proc.returncode, err, (time.monotonic() - started if started else 0.0)


@unittest.skipIf(DreamWindow is None, 'System GTK/VTE/WebKit bindings required')
class WindowMainArmsItself(unittest.TestCase):
    def test_a_hung_window_still_dies_on_sigterm_and_leaves_the_stuck_stack(self):
        code, err, took = run_main_child('hang')
        self.assertEqual(code, -signal.SIGTERM)
        self.assertLess(took, 5.0, err)                       # not after the 30 s C call: the handler is C-level
        self.assertIn('most recent call first', err)
        self.assertIn('stuck_in_c', err)                      # main() armed the process before the loop ran
        self.assertNotIn('Fatal Python error', err)

    def test_a_clean_quit_leaves_a_dated_line(self):
        code, err, _ = run_main_child('clean')
        self.assertEqual(code, 0, err)
        self.assertRegex(err, r'\[dream desktop 20\d\d-\d\d-\d\d \d\d:\d\d:\d\d\] window main loop ended\n$')


class SessionCrashLog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = Path(self.dir.name) / 'session-crash.log'

    def tearDown(self):
        self.dir.cleanup()

    def test_a_fatal_signal_leaves_a_traceback_of_every_thread(self):
        proc = run_child('crash', self.log)
        self.assertEqual(proc.returncode, -signal.SIGSEGV)
        text = self.log.read_text()
        self.assertIn('Segmentation fault', text)
        self.assertIn('Current thread 0x', text)
        self.assertIn('deep_in_the_turn', text)       # the crashing thread's Python stack
        self.assertIn('\nThread 0x', text)            # and the other threads'

    def test_a_clean_exit_leaves_nothing(self):
        proc = run_child('clean', self.log)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(self.log.exists())

    def test_the_path_is_not_inherited_by_the_sessions_own_children(self):
        proc = run_child('clean', self.log)
        self.assertEqual(proc.stdout.strip(), 'None')   # a Dream child process must not truncate or remove the file

    def test_without_the_variable_nothing_is_recorded(self):
        proc = run_child('clean', None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(list(Path(self.dir.name).iterdir()), [])


@unittest.skipIf(DreamWindow is None, 'System GTK/VTE/WebKit bindings required')
class WindowReportsTheCrash(unittest.TestCase):
    def window(self, crash_file=None):
        return Mock(closing=False, launch_request=None, crash_log=crash_file)

    def test_a_crash_is_named_with_how_to_continue_and_where_the_traceback_is(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'session-crash.log'
            log.write_text('Fatal Python error: Segmentation fault\n')
            window = self.window(log)
            DreamWindow._child_exited(window, None, signal.SIGSEGV)   # the raw wait status of a SIGSEGV death
            text = window.status.call_args.args[0]
            self.assertIn('crashed (SIGSEGV)', text)
            self.assertIn('/resume', text)
            self.assertIn(str(log), text)
            window.session_label.set_text.assert_called_with('Session crashed')
            window.welcome_description.set_text.assert_called_with(text)
            window.onboarding.message.set_text.assert_called_with(text)

    def test_a_signal_without_a_traceback_removes_the_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'session-crash.log'
            log.touch()
            window = self.window(log)
            DreamWindow._child_exited(window, None, signal.SIGKILL)
            text = window.status.call_args.args[0]
            self.assertIn('stopped by SIGKILL', text)
            self.assertNotIn(str(log), text)
            self.assertFalse(log.exists())

    def test_a_clean_exit_reads_as_before(self):
        window = self.window()
        DreamWindow._child_exited(window, None, 0)
        window.session_label.set_text.assert_called_with('Session ended')
        window.status.assert_called_with('Session ended. Choose an agent to start again; Terminal history is preserved.')

    def test_the_sessions_exit_is_one_dated_line_on_stderr(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            DreamWindow._child_exited(self.window(), None, signal.SIGKILL)
        self.assertRegex(err.getvalue(), r'^\[dream desktop 20\d\d-\d\d-\d\d \d\d:\d\d:\d\d\] session process exited, wait status 9\n$')

    def test_a_dead_stderr_does_not_break_the_exit_handling(self):
        import contextlib

        class Dead:
            def write(self, _text):
                raise OSError(5, 'Input/output error')   # the terminal that started a checkout launch has gone

            def flush(self):
                raise OSError(5, 'Input/output error')
        window = self.window()
        window.closing = True
        with contextlib.redirect_stderr(Dead()):
            DreamWindow._child_exited(window, None, 0)
        window.session_label.set_text.assert_called_with('Session ended')
        window.destroy.assert_called_once_with()

    def test_each_session_is_started_with_its_own_crash_file(self):
        window = Mock(running=False, stopped=False, launch_request=Path('/tmp/request.json'), cwd='/tmp',
                      python='python3', child_env=lambda: {})
        DreamWindow.start_terminal(window)
        env = window.terminal.spawn_async.call_args.args[3]
        self.assertIn(f'{crash_log.ENV}={window.crash_log}', env)
        self.assertEqual(window.crash_log.parent, LOG_DIR)


if __name__ == '__main__':
    unittest.main()
