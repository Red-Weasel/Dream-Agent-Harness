"""DREAM-086: a session that dies from a fatal signal (a segfault in native code) leaves a traceback of every thread.

The desktop window names a file in DREAM_CRASH_LOG for each session it starts; the session enables faulthandler on it
and removes it again on a clean exit, so a file that stays behind is the record of a crash.
"""
import faulthandler
import os
import signal
import sys

ENV = 'DREAM_CRASH_LOG'


def arm_window():
    """The desktop window's own evidence. 2026-09-24 20:13:34 the window process exited mid-turn without a word on its
    stderr (the desktop menu connects it to the journal). Now a fatal signal leaves every thread's stack there under
    "Fatal Python error", and SIGTERM or SIGHUP leaves every thread's stack there (no "Fatal" line) before its default
    action runs. Both handlers are faulthandler's, in C: they fire at once even while the main thread is stuck in a C
    call, where a Python-level handler would wait and a hung window would not die. A stop that leaves nothing at all
    was a SIGKILL or a hard exit."""
    if sys.stderr is None:   # started with fd 2 closed: nothing to write to, and the window must still start
        return
    faulthandler.enable(all_threads=True)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        faulthandler.register(sig, all_threads=True, chain=True)   # chain: then the previous (default) action


def enable():
    """Start recording; returns the function that stops recording and removes the (empty) file on a clean exit."""
    path = os.environ.pop(ENV, None)   # not inherited: a Dream child process must not truncate or remove this file
    if not path:
        return lambda: None
    try:
        handle = open(path, 'w', encoding='utf-8')
    except OSError as exc:
        print(f'Crash traceback will not be recorded: {exc}', file=sys.stderr, flush=True)
        return lambda: None
    faulthandler.enable(file=handle, all_threads=True)

    def stop():
        faulthandler.disable()
        handle.close()
        if os.path.getsize(path) == 0:
            os.unlink(path)
    return stop
