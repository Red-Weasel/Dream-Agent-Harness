"""DREAM-086: a session that dies from a fatal signal (a segfault in native code) leaves a traceback of every thread.

The desktop window names a file in DREAM_CRASH_LOG for each session it starts; the session enables faulthandler on it
and removes it again on a clean exit, so a file that stays behind is the record of a crash.
"""
import faulthandler
import os
import sys

ENV = 'DREAM_CRASH_LOG'


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
