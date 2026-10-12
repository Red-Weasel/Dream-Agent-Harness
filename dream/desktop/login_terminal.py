"""The desktop's separate sign-in window (DREAM-246).

One small window with its own terminal runs one provider's fixed sign-in command (core.provider_login). It shares
nothing with the Dream session's terminal: its own window, its own pty, its own child process, its own timers. It
never calls into the main window except to put a line in the status bar, and it never reads, copies, stores or
feeds what is typed or shown in its terminal. The user signs in inside the CLI or in their browser.

How the command is run, and why it is not Vte's spawn_async:

* The command is `login_supervised_command([found file, fixed arguments])` (core.login_execution): a supervisor
  made for this window only. It checks that this process started it, makes the window's pty the CLI's controlling
  terminal with the CLI in the foreground (so /dev/tty opens, Ctrl-C reaches the CLI and a resize is announced),
  keeps everything the CLI starts under itself, and asks it all to stop with SIGTERM when the window closes or this
  process ends. It never forces anything: what does not stop is waited for, and the supervisor stays while it is.
* The supervisor stops when its parent goes away (PR_SET_PDEATHSIG), and Linux counts the *thread* that started it
  as that parent. Vte's spawn_async starts a child from a helper thread that may end while the window lives, which
  would stop a sign-in half way. So this module starts the supervisor itself, from the GTK main thread, on a pty
  it opened, and hands Vte the other end (Vte.Pty.new_foreign_sync, Vte.Terminal.set_pty).
* Starting it this way is synchronous: there is never a spawn "in flight" that a close could lose, and no callback
  is left pending. The call returns when the trusted system Python interpreter has been exec'd (or could not be);
  that is one local fork and exec of a root-owned file, with no provider code, no network and no handshake. The
  GTK loop is held for that long and no longer; there is no deadline on it, so a stalled disk would stall the
  window. Everything after that (the supervisor's own checks, the CLI's start) happens in the supervisor and is
  seen by the timer below. A supervisor that refuses to run the command ends with REFUSED_STATUS, but a CLI may
  end with that status of its own accord too. This module is given no other evidence, so for that status the
  window says both are possible and that its terminal may show more (the supervisor writes "was not started"
  there when it refused; a CLI may end without a word). It never says that nothing ran.
* 'closed' means the window is gone. A command that ends by itself leaves its window open so that what it
  printed can be read; that window is still the sign-in window, keeps its slot (a new request is 'busy' and
  raises it) and is not reported 'closed' until it is closed by the user or goes with the main window.
* The Popen object is the supervisor's identity from the first moment and is the only thing this module ever
  signals, and only with SIGTERM; a process that has been reaped is never signalled, and no bare pid is.

Stopping never blocks the GTK loop: closing the window sends SIGTERM and keeps the window until the supervisor is
seen to have ended (a short timer polls), which it does only when nothing it started is left. If it has not ended
in time, the window stays and says so; nothing is reported as cleaned up that was not.

Descriptors. launch() opens one pty and closes both of its ends before it returns, whatever happens. Vte is given a
duplicate of the master. If Vte does not take it (creating its pty object fails), launch() closes the duplicate
itself, after checking that the number still names this pty (the pty's number, asked of the kernel with the
TIOCGPTN ioctl; os.ptsname does not exist before Python 3.13). A number that cannot be shown to be this pty is left
alone: one descriptor may then stay open, and nothing that belongs to something else is ever closed. Once Vte's pty
object exists it owns the duplicate and closes it when it is released; on a failed start the terminal is told to
let go of it and this module drops its own reference.

After the supervisor has been started it is this module's, whatever fails next. The session is recorded before any
further step. 'launched' means "the sign-in window is open": it is reported only after the timer is set, the window
shown and the terminal focused. If one of those steps raises, there is no usable window, so the receipt is 'failed'
(a process having been started is not a window having been opened), and the status bar is told, in words, that a
command was started and has been asked to stop; a second line follows when it has been seen to end. No 'closed' is
reported for a window that was never reported 'launched'. If the 'launched' report itself raises, it is not tried
again; 'closed' is tried once when the command has ended and its window is gone. In every one of these cases the supervisor is asked to
stop (SIGTERM, as everywhere), the timers that look for its end are set if they can be, and the exception is raised
again to the caller. Nothing waits and nothing is called cleaned up before it has been seen to end. If no timer
could be set, the next launch() looks once, without waiting, asks again and tries the timers again;
`session.problems` keeps the names of what failed. Until the supervisor has been seen to end, the session keeps its
slot (a new request is 'busy') and keeps the Popen object; when this process ends, the supervisor is told by the
kernel as always.

Size. The window opens at WIDTH x HEIGHT whatever its contents would ask for. The explanation wraps at
PURPOSE_CHARS characters, the state line wraps to the window, and the terminal takes all the room that is left and
follows the window when it is resized. Nothing the CLI prints is cut off by this: Vte tells the CLI the size the
terminal really has, and keeps its scrollback.
"""
from __future__ import annotations

import fcntl
import os
import signal
import stat
import struct
import subprocess
import termios
from typing import Callable, Mapping

from ..core.provider_login import launch_plan, resolve

POLL_MS = 250          # how often a running sign-in command is checked for having ended
STOP_MS = 5000         # how long a stop may take before the window says it did not work
COLUMNS, ROWS = 100, 30
WIDTH, HEIGHT = 860, 600   # the window's size when it opens, in pixels
MARGIN = 10                # around everything in the window, in pixels
PURPOSE_CHARS = 78         # where the explanation wraps
REFUSED_STATUS = 125   # core.login_execution.REFUSED: the supervisor did not start the command, or the CLI's own


def _tiocgptn() -> int:
    """The ioctl that gives a pty master's number. Python's termios does not always name it; on Linux it is
    _IOR('T', 0x30, unsigned int), whose value differs only where the direction bits do."""
    known = getattr(termios, 'TIOCGPTN', None)
    if isinstance(known, int):
        return known
    machine = os.uname().machine.lower()
    if machine.startswith(('mips', 'ppc', 'powerpc', 'sparc', 'alpha')):
        return 0x40045430
    return 0x80045430


def _pty_identity(fd: int) -> tuple[int, int, int]:
    """What names one pty for as long as it is open: the devpts it is on, its device, and its number there. Raises
    OSError when `fd` is not an open pty master."""
    info = os.fstat(fd)
    if not stat.S_ISCHR(info.st_mode):
        raise OSError('not a character device')
    number = struct.unpack('I', fcntl.ioctl(fd, _tiocgptn(), bytes(4)))[0]
    return info.st_dev, info.st_rdev, number


def _supervise(argv):
    from ..core.login_execution import login_supervised_command
    return login_supervised_command(list(argv))


class _Session:
    """One sign-in window and the one process it owns."""

    def __init__(self, target, report: Callable[[str], None]) -> None:
        self.target = target
        self.report = report
        self.proc = None            # the supervisor's Popen: the identity of everything this window started
        self.window = None
        self.terminal = None
        self.state = None           # the line under the explanation
        self.watch = None           # GLib source ids
        self.stop_timer = None
        self.exited = False         # the process was seen to have ended
        self.closing = False        # the user (or the main window) asked for this window to close
        self.stuck = False          # a stop was sent and the process did not end in time
        self.window_gone = False    # the window was destroyed from outside before the process ended
        self.finished = False       # nothing is left: the process ended and the window is gone
        self.receipt = None         # 'launched' or 'failed': the one answer given (or tried) for the start
        self.told_closed = False
        self.setup_failed = False   # a step after the process was started raised; the process is being stopped
        self.problems: list[str] = []   # what could not be done while recovering from that, by name only


class LoginTerminal:
    """At most one sign-in window at a time. `Gtk`, `Vte` and `GLib` are the gi modules, passed in by the window."""

    def __init__(self, owner, Gtk, Vte, GLib, *, notify: Callable[[str], None] | None = None,
                 repo_root: str | None = None, strip_env: tuple[str, ...] = (),
                 environ: Mapping[str, str] | None = None, popen=subprocess.Popen, supervise=_supervise) -> None:
        self.owner, self.Gtk, self.Vte, self.GLib = owner, Gtk, Vte, GLib
        self._notify = notify or (lambda text: None)
        self._repo_root, self._strip_env = repo_root, tuple(strip_env)
        self._environ, self._popen, self._supervise = environ, popen, supervise
        self.session: _Session | None = None
        self.stopped = False

    # -- opening

    def launch(self, provider: str, cwd: str, report: Callable[[str], None]) -> bool:
        """Open the sign-in window for one provider name. `report` is called with 'launched', 'failed',
        'unavailable' or 'busy' before this returns, and with 'closed' later, once, when a launched command has
        ended and its window is gone (a command that ends by itself leaves its window open until it is closed).
        None of these says anything about the account. 'launched' means the window is open.

        If a step after the command was started raises, the command is asked to stop and stays owned, and the
        exception is raised from here. The receipt is then 'failed' (and the status bar says a command is being
        stopped), unless the step that raised was the 'launched' report itself."""
        try:
            target = resolve(provider)
        except ValueError:
            report('failed')
            return False
        if self.stopped:
            report('failed')
            return False
        current = self.session
        if current is not None and current.setup_failed:
            self._recover(current)           # look once, ask again, set the timers that are missing
            current = self.session           # None when it was seen to have ended just now
        if current is not None:
            if current.window_gone:
                self._stop(current)          # its window is gone and it has not ended: ask again
            elif not current.setup_failed:
                current.window.present()
            report('busy')
            return False
        try:
            plan = launch_plan(target, os.environ if self._environ is None else self._environ,
                               repo_root=self._repo_root, strip=self._strip_env)
            command = self._supervise(plan.argv) if plan is not None else None
        except (ValueError, OSError, RuntimeError, ImportError):
            report('failed')
            return False
        if plan is None:
            report('unavailable')
            return False
        session = _Session(target, report)
        master = slave = handed = -1
        pty = None
        try:
            self._build(session)
            master, slave = os.openpty()
            # The size the CLI sees first; Vte keeps it up to date from then on.
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', ROWS, COLUMNS, 0, 0))
            # Vte owns the descriptor it is given, so it gets a duplicate and this function closes its own two.
            handed = os.dup(master)
            pty = self.Vte.Pty.new_foreign_sync(handed, None)
            handed = -1                       # Vte's pty object owns it now and closes it when it is released
            session.terminal.set_pty(pty)
            session.proc = self._popen(command, stdin=slave, stdout=slave, stderr=slave, cwd=self._cwd(cwd),
                                       env=plan.env, start_new_session=True, close_fds=True)
        except (self.GLib.Error, OSError, ValueError, TypeError):
            # Nothing was started (starting the process is the last step above), so there is nothing to own.
            self._abandon(session, handed, master, pty)
            report('failed')
            return False
        except BaseException:
            self._abandon(session, handed, master, pty)     # the same; an error of any other kind is not hidden
            try:
                report('failed')              # still answered: nothing was started and no window is left
            except Exception:
                pass                          # the error being raised is the one the caller gets
            raise
        finally:
            pty = None                        # this function's reference to Vte's pty object
            for fd in (master, slave):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        self.session = session                # owned from here on, whatever the steps below do
        try:
            session.watch = self.GLib.timeout_add(POLL_MS, self._watch, session)
            session.window.show_all()
            session.terminal.grab_focus()
            session.receipt = 'launched'      # before the call: a report that raises is not made a second time
            report('launched')
        except BaseException:
            self._setup_failed(session)
            raise
        return True

    def _abandon(self, session: _Session, handed: int, master: int, pty) -> None:
        """A start that failed before any process existed: leave no window and no descriptor behind."""
        session.finished = True
        if handed >= 0 and self._same_pty(handed, master):
            try:
                os.close(handed)              # Vte did not take it
            except OSError:
                pass
        if pty is not None and session.terminal is not None:
            try:
                session.terminal.set_pty(None)    # Vte lets go of the pty object, which closes its descriptor
            except Exception:
                pass                          # the failure that brought us here is the one that is reported
        if session.window is not None:
            session.window.destroy()

    @staticmethod
    def _same_pty(fd: int, master: int) -> bool:
        """True when `fd` is open and is a descriptor of the same pty as `master`. The duplicate handed to a Vte
        call that then failed is closed only when this holds, so a number that Vte closed already (and that
        something else may have been given since) is never closed a second time."""
        if fd < 0 or master < 0 or fd == master:
            return False
        try:
            return _pty_identity(fd) == _pty_identity(master)
        except (OSError, ValueError, OverflowError, struct.error):
            return False                      # closed, or not a pty master, or it cannot be asked: left alone

    def _setup_failed(self, session: _Session) -> None:
        """A step after the process was started raised. The process is still this session's: ask it to stop and
        keep looking for its end. Does not block and reports nothing as finished. If the window was never reported
        open, the receipt is 'failed' and the status bar says that a command was started and is being stopped. The
        caller raises the exception again; what fails in here is noted in session.problems, not raised over it."""
        session.setup_failed = True
        session.closing = True                # the window goes when the process is seen to have ended
        self._ask(session)
        if session.receipt is None:
            session.receipt = 'failed'        # no window was opened; that a process was started is said below
            self._attempt(session, 'report', lambda: session.report('failed'))
            self._attempt(session, 'notify', lambda: self._notify(
                f'The {session.target.label} sign-in window could not be opened. Its sign-in command (process '
                f'{session.proc.pid}) had been started and has been asked to stop; it has not been seen to end '
                'yet.'))
        self._attempt(session, 'state', lambda: session.state.set_text(
            'This window could not be set up. Stopping the sign-in command…'))
        self._recover(session, ask=False)

    def _recover(self, session: _Session, ask: bool = True) -> None:
        """For a session whose setup failed: ask again, look once without waiting if no timer is looking, and set
        the timers that are missing."""
        if session.finished:
            return
        if ask and not session.exited:
            self._ask(session)
        if session.watch is None and not session.exited:
            self._attempt(session, 'look', lambda: self._watch(session))
            if session.finished or session.exited:
                return
        for name, interval, function in (('watch', POLL_MS, self._watch), ('stop_timer', STOP_MS, self._stop_overdue)):
            if getattr(session, name) is None and not session.exited and not session.finished:
                self._attempt(session, name, lambda: setattr(
                    session, name, self.GLib.timeout_add(interval, function, session)))

    @staticmethod
    def _attempt(session: _Session, name: str, step: Callable[[], object]) -> None:
        """One recovery step. A failure is kept by name (never its text) and the next step is still taken."""
        try:
            step()
        except Exception as exc:
            session.problems.append(f'{name}: {type(exc).__name__}')

    @staticmethod
    def _cwd(cwd: str) -> str:
        """The folder the main window was started with (never one from a request); the home folder if that is
        not a folder any more."""
        if isinstance(cwd, str) and os.path.isabs(cwd) and os.path.isdir(cwd):
            return cwd
        return os.path.expanduser('~')

    def _build(self, session: _Session) -> None:
        Gtk, target = self.Gtk, session.target
        window = Gtk.Window(title=f'Sign in — {target.label}')
        session.window = window
        if self.owner is not None:
            window.set_transient_for(self.owner)
            window.set_destroy_with_parent(True)
        # The size is chosen here, not left to the contents: one long label would otherwise make the window as
        # wide as its text. The terminal is the only part that grows and shrinks with the window.
        window.set_default_size(WIDTH, HEIGHT)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(MARGIN)
        box.set_margin_bottom(MARGIN)
        box.set_margin_start(MARGIN)
        box.set_margin_end(MARGIN)
        purpose = Gtk.Label(label=(
            f'This window runs  {target.command}  so you can sign in to {target.label}. Follow its steps here or '
            'in your browser, then close this window. Dream does not read what you type here and does not check '
            'whether you are signed in.'), xalign=0)
        purpose.set_line_wrap(True)
        purpose.set_max_width_chars(PURPOSE_CHARS)      # with the wrap: lines of a readable length
        purpose.set_halign(Gtk.Align.START)             # as wide as its text asks, not stretched to one line
        box.pack_start(purpose, False, False, 0)
        bar = Gtk.Box(spacing=6)
        session.state = Gtk.Label(label='The sign-in command is running.', xalign=0)
        session.state.set_line_wrap(True)
        session.state.set_max_width_chars(PURPOSE_CHARS - 20)   # a long state wraps beside the two buttons
        bar.pack_start(session.state, True, True, 0)
        terminal = self.Vte.Terminal()
        session.terminal = terminal
        copy = Gtk.Button(label='Copy selection')
        copy.connect('clicked', lambda _button: terminal.copy_clipboard_format(self.Vte.Format.TEXT))
        paste = Gtk.Button(label='Paste')
        paste.connect('clicked', lambda _button: terminal.paste_clipboard())
        bar.pack_end(paste, False, False, 0)
        bar.pack_end(copy, False, False, 0)
        box.pack_start(bar, False, False, 0)
        terminal.set_size(COLUMNS, ROWS)
        terminal.set_scrollback_lines(2000)
        terminal.set_hexpand(True)
        terminal.set_vexpand(True)
        box.pack_start(terminal, True, True, 0)       # all the room that is left, and all of any resize
        window.add(box)
        window.connect('delete-event', lambda *_: self._close_requested(session))
        window.connect('destroy', lambda *_: self._window_destroyed(session))

    # -- the life of one session. Every callback gets its own session and touches nothing else.

    def _watch(self, session: _Session) -> bool:
        if session.finished:
            session.watch = None
            return False
        status = session.proc.poll()
        if status is None:
            return True
        first = not session.exited
        session.watch = None
        session.exited = True
        self._drop_timer(session, 'stop_timer')
        if first and session.receipt == 'failed':
            # The start was answered 'failed' with a note that a command was being stopped. This is its end.
            self._attempt(session, 'notify', lambda: self._notify(
                f'The {session.target.label} sign-in command that had been started (process {session.proc.pid}) '
                'has ended.'))
        if session.closing or session.window_gone or session.setup_failed:
            self._finish(session)             # the window goes now, and 'closed' is reported there
        elif status == REFUSED_STATUS:
            # The supervisor's "not started" status, which a CLI may also choose for itself. Nothing here can
            # tell the two apart, so neither is claimed, and the terminal is not said to settle it: a command may
            # end without printing anything.
            session.state.set_text(
                f'The sign-in command ended with status {REFUSED_STATUS}. Either it could not be started, or it '
                'ran and ended that way itself. The terminal may show more details. Dream did not check '
                f'whether you are signed in. You can also run  {session.target.command}  in a terminal of your '
                'own. You can close this window.')
        else:
            session.state.set_text('The command has ended. Dream did not check whether you are signed in. '
                                   'You can close this window.')
        # A window left open here is still the sign-in window: no 'closed' yet, and its slot is kept, so a new
        # request is 'busy' and raises it. Nothing closes it but the user (or the main window going away).
        return False

    def _close_requested(self, session: _Session) -> bool:
        """The window's close button. True keeps the window: it goes only when the process is known to be gone."""
        if session.finished:
            return False
        if session.exited:
            self._finish(session)
            return True
        session.closing = True
        session.state.set_text('Stopping the sign-in command…')
        self._stop(session)
        return True

    def _window_destroyed(self, session: _Session) -> None:
        """The window went away without the close button (for example with the main window)."""
        if session.finished:
            return
        session.window_gone = True
        if session.exited:
            self._finish(session)
        else:
            self._stop(session)              # the watch timer finishes the session when the process has ended

    @staticmethod
    def _ask(session: _Session) -> None:
        """Ask this session's own supervisor to stop, with SIGTERM and nothing stronger. Popen.send_signal does
        nothing once the process has been reaped, so a recycled process id can never be hit."""
        try:
            session.proc.send_signal(signal.SIGTERM)
        except OSError:
            pass                              # the stop timer says so if it is still there

    def _stop(self, session: _Session) -> None:
        """Ask, and say so if it has not ended in time."""
        self._ask(session)
        if session.stop_timer is None and not session.finished:
            session.stop_timer = self.GLib.timeout_add(STOP_MS, self._stop_overdue, session)

    def _stop_overdue(self, session: _Session) -> bool:
        session.stop_timer = None
        if session.exited or session.finished:
            return False
        session.stuck = True
        session.closing = False               # the close button asks again
        text = (f'The {session.target.label} sign-in command (process {session.proc.pid}) did not stop when '
                'asked. It is still running.')
        if session.window_gone or session.setup_failed:      # no window that is sure to be on screen
            self._notify(text + ' Ask for that sign-in again to retry stopping it.')
        else:
            session.state.set_text(text + ' Close this window again to retry.')
        return False

    def _drop_timer(self, session: _Session, name: str) -> None:
        source = getattr(session, name)
        if source is not None:
            setattr(session, name, None)
            self.GLib.source_remove(source)

    def _finish(self, session: _Session) -> None:
        """The window goes (or is gone already) and the slot is freed. 'closed' is reported here and nowhere
        else: once, last, only after a 'launched' receipt, and only when the supervisor was seen to have ended.
        Everything else is done before the report, so a report that raises leaves nothing half done."""
        if session.finished:
            return
        session.finished = True
        try:
            self._drop_timer(session, 'watch')
            self._drop_timer(session, 'stop_timer')
            if not session.window_gone:
                session.window.destroy()
        finally:
            if self.session is session:       # never a newer session's slot
                self.session = None
        if session.exited and session.receipt == 'launched' and not session.told_closed:
            session.told_closed = True        # before the call: a report that raises is not made a second time
            session.report('closed')

    # -- the main window is going away

    def shutdown(self) -> None:
        """Called once from the main window's destroy. Sends the stop and returns at once. The supervisor asks
        what it started to stop, and is also told by the kernel when this process ends. It keeps owning anything
        that does not stop; nothing is forced, here or there."""
        self.stopped = True
        session = self.session
        if session is not None and not session.exited and not session.finished:
            self._ask(session)
