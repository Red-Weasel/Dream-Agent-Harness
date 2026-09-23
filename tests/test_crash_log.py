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

    def test_each_session_is_started_with_its_own_crash_file(self):
        window = Mock(running=False, stopped=False, launch_request=Path('/tmp/request.json'), cwd='/tmp',
                      python='python3', child_env=lambda: {})
        DreamWindow.start_terminal(window)
        env = window.terminal.spawn_async.call_args.args[3]
        self.assertIn(f'{crash_log.ENV}={window.crash_log}', env)
        self.assertEqual(window.crash_log.parent, LOG_DIR)


if __name__ == '__main__':
    unittest.main()
