"""The supervisor of the desktop's sign-in window, and nothing else (DREAM-246).

`login_supervised_command(argv)` is the one public name. It returns the argv of a small trusted program that runs
`argv` (a provider's fixed sign-in command) on the terminal it was started on. Only
dream.desktop.login_terminal calls it. It is not a general executor: it takes no permission decision, has no
sandbox, no output capture, no timeout and no file descriptor passing. Tools keep using execution.supervised_command.

Why the sign-in window does not use execution.supervised_command:

* That supervisor puts its command in a session of its own with no controlling terminal, so /dev/tty does not
  open, Ctrl-C in the terminal reaches nothing and a resize is not announced.
* That supervisor ends what is left after 200 ms with a signal that cannot be refused. That is right for a tool
  call. It is excluded here: a person is typing into this command, and nothing on the sign-in path may be forced.

What the program does, in order. Any step that fails ends it with status REFUSED before the command is started.

1. Owner. The caller's process id and start time (field 22 of /proc/<pid>/stat) are read in the caller *before* it
   starts the program, and travel as the first two arguments. The program checks that its parent is that process,
   asks to be told when the parent ends (PR_SET_PDEATHSIG), and checks again. A parent that is gone, or a different
   process with the same number, is refused. It never accepts whichever process it finds itself under.
2. Terminal. The program is the leader of a new session (the caller starts it with start_new_session=True) and takes
   the terminal on its standard input as that session's controlling terminal.
3. Command. It forks once. The child becomes its own process group, waits until the parent has made that group the
   terminal's foreground group, puts every signal back to its default and execs the command. So the command opens
   /dev/tty, receives the terminal's Ctrl-C and is told about resizes; the program itself is in the background and
   receives none of them. The program is single-threaded, so this fork is safe; the caller (a GTK process with
   threads) uses no preexec_fn.
   A stop that was asked for first is never followed by the command. From just before the fork until the child has
   been told to go, the three stop signals are held back (blocked). Immediately before the fork, and again
   immediately before the child is told to go, the program looks for a stop it has handled, for one that is held
   back and waiting, and at whether its parent is still the named owner. If any of these says stop, there is no
   fork, or the child is never told to go and ends without an exec; the program ends with REFUSED. The child
   inherits the blocked signal mask (not the parent's pending signals: a fork starts the child with none) and
   unblocks the three only after it has put them back to their defaults, so a stop sent to the child itself
   before its exec ends it rather than being lost in a copy of this program's handler.
4. Ownership. The program is a subreaper (PR_SET_CHILD_SUBREAPER): every process the command starts, detached or
   not, stays under it. It ends only when none of them is left, with the command's own status.
5. Stopping. SIGTERM, SIGHUP (the terminal went away), SIGINT, or the owner ending, is a request to stop. So is the
   command ending while processes it started are still there. The program then sends SIGTERM and SIGCONT to each
   process under it and waits. It sends nothing stronger, ever. A process that does not stop is waited for: after
   NOTICE_SECONDS the program says so on the terminal once, and stays. Asking again sends the same two signals again.

Which processes are "under it" is proved, not read off one pass over /proc. Such a pass is not atomic: a row read
early can name a parent process id that, by the time a later row is read, belongs to a different process. Joining
rows by bare parent id could then put an unrelated process under an owned one. So the pass only proposes
candidates, and each is proved, top down, before it is asked to stop:

* a pidfd is opened for the candidate, and its row is then read again: it must still name the same parent and the
  same start time;
* that parent is either this program itself, or a process already proved in the same way whose own pidfd, looked
  at *after* that second read, shows it has not ended. A process that has not ended has kept its process id the
  whole time, so the parent id in the row meant that process and no other.

The signal goes through the candidate's pidfd, which names either the process whose row was read or one that has
since ended (and then nothing is sent). Anything that fails a step is left alone in that pass. Once under this
program a process stays under it (it is a subreaper), so what was proved stays true. The program collects ended
children only between passes, never during one. A process that has been reaped is never signalled.

What this does not do: read or write the terminal's contents (it writes its own one-line notices to standard error
and nothing else), keep any output, look at credentials, or touch any process it did not start.

REFUSED is also an exit status the command itself may choose. The status alone therefore does not say whether the
command ran; the notice the program writes on the terminal ("... was not started.") does.

Known limits:
* PR_SET_PDEATHSIG follows the *thread* that started the program. The caller must build the command and start it
  from a thread that lives as long as its process; login_supervised_command refuses any thread but the main one.
* A process the command starts under itself is stopped with it. If a provider's CLI starts a new browser process
  as its own descendant (rather than handing the address to a running browser or the desktop), that browser is
  asked to stop when the sign-in window closes. Browsers and other processes that were already running are not
  descendants and are never touched.
* A descendant that /proc does not show to this user (it changed user) cannot be asked to stop; it is still waited
  for.
* A process whose parent ends between the two reads of its row is not proved in that pass. It is found again, under
  its new parent, in the next one (every TICK while stopping).
* A stop that arrives after the last look and before the child's exec is not "seen first": the command starts and
  is asked to stop at once, like any later stop.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Sequence

from .execution import _SUPERVISOR_PYTHON, ExecutionRefused, _trusted_system_file

REFUSED = 125            # the program's exit status when it did not start the command (a command may also end so)
NOTICE_SECONDS = 2.0     # how long a stop may take before the program says so on the terminal


def _start_time(pid: int) -> int:
    """Field 22 of /proc/<pid>/stat: when the process started, in clock ticks since boot. With the process id it
    names one process for the life of the machine."""
    data = Path(f'/proc/{pid}/stat').read_bytes()
    return int(data[data.rindex(b')') + 2:].split()[19])


def _command(argv: Sequence[str], owner_pid: int, owner_start: int) -> list[str]:
    """The program's argv for an owner given explicitly. Tests use this to name an owner that is not the caller."""
    return [_SUPERVISOR_PYTHON, '-I', '-c', _LOGIN_SUPERVISOR_SOURCE, str(int(owner_pid)), str(int(owner_start)),
            *argv]


def login_supervised_command(argv: Sequence[str]) -> list[str]:
    """The argv that runs `argv` under the sign-in supervisor, owned by the calling process.

    Start it at once, from the calling thread, with its standard input, output and error on the slave end of a pty
    and start_new_session=True. `argv` is an absolute file and its fixed arguments. ExecutionRefused is raised when
    this cannot be done safely; nothing has been started then.
    """
    if sys.platform != 'linux':
        raise ExecutionRefused('the sign-in window currently requires Linux')
    if not hasattr(os, 'pidfd_open'):
        raise ExecutionRefused('the sign-in window requires process file descriptors (Linux 5.3, Python 3.9)')
    if threading.current_thread() is not threading.main_thread():
        raise ExecutionRefused('the sign-in command must be started from the main thread')
    if not _trusted_system_file(Path(_SUPERVISOR_PYTHON)):
        raise ExecutionRefused('the sign-in window requires a trusted system Python interpreter')
    command = list(argv) if isinstance(argv, (list, tuple)) else []
    if not command or not all(isinstance(part, str) and '\x00' not in part for part in command):
        raise ExecutionRefused('the sign-in command is not a list of plain arguments')
    if not os.path.isabs(command[0]):
        raise ExecutionRefused('the sign-in command is not an absolute file')
    try:
        owner = os.getpid()
        return _command(command, owner, _start_time(owner))
    except (OSError, ValueError, IndexError) as exc:
        raise ExecutionRefused('could not identify the window that owns the sign-in command') from exc


# The program. It runs from this in-memory text under `python -I -c`, never from an importable file, and imports
# only the standard library. NOTICE_SECONDS and REFUSED are written in from the constants above.
_LOGIN_SUPERVISOR_SOURCE = r'''
import ctypes
import fcntl
import os
import select
import signal
import sys
import termios
import time

REFUSED = @REFUSED@
NOTICE_SECONDS = @NOTICE_SECONDS@
TICK = 0.05
SOFT = (signal.SIGTERM, signal.SIGCONT)      # the only signals this program ever sends
STOPS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)      # what this program takes as a request to stop


def stat_of(pid):
    """(state, parent pid, start time) of a process, or None when it cannot be read."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as handle:
            data = handle.read()
        rest = data[data.rindex(b")") + 2:].split()
        return rest[0], int(rest[1]), int(rest[19])
    except (OSError, ValueError, IndexError):
        return None


def listing():
    """Every process id /proc shows right now."""
    try:
        return [int(name) for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return []


def hold(pid):
    """A pidfd for whichever process has this id at this moment. It keeps meaning that one process."""
    return os.pidfd_open(pid)


def alive(fd):
    """True when the process a pidfd was opened for has not ended. While that is so its process id has been its
    own, without a break, since the pidfd was opened. Anything unclear counts as ended."""
    try:
        watcher = select.poll()
        watcher.register(fd, select.POLLIN)
        return not watcher.poll(0)
    except (OSError, ValueError):
        return False


def soft(fd):
    """Ask the one process a pidfd names to stop."""
    for number in SOFT:
        signal.pidfd_send_signal(fd, number)


def drop(fd):
    try:
        os.close(fd)
    except OSError:
        pass


def sweep(me, asked):
    """Ask every live process proved to be under process `me` (this program) to stop, unless `asked` (pid -> start
    time) says it was asked already. Returns how many were proved. Nothing is collected while this runs.

    One pass over /proc only proposes candidates: its rows are read at different moments, and a parent id in an
    early row may belong to another process by the time a later row is read. Each candidate is proved from the top
    down: after a pidfd for it is open, its row must still name the same parent and start time, and that parent is
    this program or a process proved before it whose pidfd shows, after that read, that it has not ended.
    """
    rows = {}
    for pid in listing():
        entry = stat_of(pid)
        if entry is not None:
            rows[pid] = entry
    proposed = {}                                # parent id -> ids of the live processes whose row named it
    for pid, (state, parent, start) in rows.items():
        if pid != me and state != b"Z":
            proposed.setdefault(parent, []).append(pid)
    held = {}                                    # pid -> pidfd of a process proved to be under this one
    try:
        level = [me]
        while level:
            below = []
            for parent in level:
                for pid in proposed.get(parent, ()):
                    if pid in held:
                        continue
                    start = rows[pid][2]
                    try:
                        fd = hold(pid)
                    except OSError:
                        continue
                    again = stat_of(pid)
                    if (again is None or again[0] == b"Z" or again[1] != parent or again[2] != start
                            or (parent != me and not alive(held[parent]))):
                        drop(fd)
                        continue
                    held[pid] = fd
                    below.append(pid)
                    if asked.get(pid) != start:
                        asked[pid] = start
                        try:
                            soft(fd)
                        except OSError:
                            pass
            level = below
        return len(held)
    finally:
        for fd in held.values():
            drop(fd)


def say(text):
    try:
        os.write(2, ("sign-in supervisor: %s\r\n" % text).encode("ascii", "replace"))
    except OSError:
        pass


def refuse(text):
    say(text + " The sign-in command was not started.")
    return REFUSED


def main(arguments):
    if len(arguments) < 3:
        return refuse("no command was given.")
    try:
        owner, owner_start = int(arguments[0]), int(arguments[1])
    except ValueError:
        return refuse("the owner is not named.")
    command = arguments[2:]
    if not os.path.isabs(command[0]):
        return refuse("the command is not an absolute file.")
    if not (hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")):
        return refuse("this system has no process file descriptors.")

    def owner_is_parent():
        if os.getppid() != owner:
            return False
        entry = stat_of(owner)
        return entry is not None and entry[2] == owner_start and os.getppid() == owner

    if not owner_is_parent():
        return refuse("it was not started by the window it belongs to.")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:                     # PR_SET_CHILD_SUBREAPER
        return refuse("it cannot own the processes the command starts.")
    stops = [0]

    def stop(number, frame):
        stops[0] += 1

    for number in STOPS:
        signal.signal(number, stop)

    def may_start():
        """No stop has been handled, none is held back and waiting, and the parent is still the named owner."""
        if stops[0] or signal.sigpending().intersection(STOPS):
            return False
        return owner_is_parent()

    for number in (signal.SIGTTOU, signal.SIGTTIN, signal.SIGTSTP):
        signal.signal(number, signal.SIG_IGN)               # this program is in the terminal's background
    if libc.prctl(1, int(signal.SIGTERM), 0, 0, 0) != 0:    # PR_SET_PDEATHSIG
        return refuse("it cannot watch the window it belongs to.")
    if not owner_is_parent():                               # the owner ended before the line above took effect
        return refuse("the window it belongs to is gone.")

    try:
        if os.getsid(0) != os.getpid():
            os.setsid()
        if not os.isatty(0):
            return refuse("it has no terminal.")
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        os.close(os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY))
        if os.tcgetpgrp(0) != os.getpgrp():
            return refuse("the terminal belongs to something else.")
    except OSError:
        return refuse("it could not take the window's terminal.")

    # From here until the child has been told to go (or not), a stop is held back rather than handled, so that
    # the two looks below see it either as handled or as waiting, and never miss it in between.
    signal.pthread_sigmask(signal.SIG_BLOCK, STOPS)
    if not may_start():
        return refuse("it was asked to stop, or the window it belongs to ended, first.")
    try:
        gate_read, gate_write = os.pipe()
        child = os.fork()
    except OSError:
        return refuse("it could not start another process.")
    if child == 0:
        try:
            os.setpgid(0, 0)
            os.close(gate_write)
            go = os.read(gate_read, 1)                      # until the parent has put this group in the foreground
            os.close(gate_read)
            if go != b"1":
                os._exit(126)
            for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTTOU,
                           signal.SIGTTIN, signal.SIGTSTP, signal.SIGPIPE, signal.SIGXFSZ, signal.SIGCHLD,
                           signal.SIGWINCH):
                signal.signal(number, signal.SIG_DFL)
            signal.pthread_sigmask(signal.SIG_SETMASK, [])  # a stop held back until now ends this child here
            os.execv(command[0], command)
        except BaseException:
            pass
        say("the command could not be started.")
        os._exit(127)

    os.close(gate_read)
    started = False
    why_not = "it could not give the command the window's terminal."
    try:
        try:
            os.setpgid(child, child)                        # the child does the same; whichever comes first
        except OSError:
            pass
        os.tcsetpgrp(0, child)
        if may_start():                                     # looked at immediately before the go-ahead
            os.write(gate_write, b"1")
            started = True
        else:
            why_not = "it was asked to stop, or the window it belongs to ended, first."
    except OSError:
        pass                                                # the gate closes without the go-ahead: no exec
    finally:
        os.close(gate_write)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, STOPS)   # a stop that was held back is handled from here

    me = os.getpid()
    status = None            # the command's own exit status, once it has been reaped
    asked = {}               # pid -> start time: asked in the current round
    round_seen = 0
    stopping_since = None
    noticed = False
    while True:
        try:
            while True:
                done, raw = os.waitpid(-1, os.WNOHANG)      # the command, and anything left to this program
                if done == 0:
                    break
                if done == child:
                    status = os.waitstatus_to_exitcode(raw)
        except ChildProcessError:
            break                                           # nothing is left under this program
        except OSError:
            pass
        try:
            if stops[0] or status is not None:
                now = time.monotonic()
                if stopping_since is None:
                    stopping_since = now
                if stops[0] != round_seen:                  # asked again: ask everything again
                    round_seen, asked = stops[0], {}
                left = sweep(me, asked)                     # no child is collected while this looks and asks
                if not noticed and now - stopping_since >= NOTICE_SECONDS:
                    noticed = True
                    if left:
                        say("%d process(es) of the sign-in command did not stop when asked. "
                            "Still waiting. Nothing is forced." % left)
                    else:
                        say("something the sign-in command started is still there and could not be asked to "
                            "stop. Still waiting. Nothing is forced.")
        except Exception:
            pass                                            # never leave while something is still owned
        time.sleep(TICK)
    if not started:
        return refuse(why_not)
    if status is None:
        return REFUSED
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''.replace('@REFUSED@', str(REFUSED)).replace('@NOTICE_SECONDS@', repr(NOTICE_SECONDS))
