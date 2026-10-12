"""The desktop's separate sign-in window (DREAM-246), without a display: stand-in Gtk/Vte/GLib objects, stand-in
processes, one real synthetic child, and the real request record on a real Studio server. No provider CLI is run:
every "installed CLI" is a file this test wrote into a temporary folder.
"""
from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import signal
import stat
import struct
import subprocess
import sys
import termios
import time
from types import SimpleNamespace

import pytest

from dream.core import provider_login as pl
from dream.desktop import login_terminal as lt
from dream.desktop.protocol import LoginTracker, login_ack, login_request


# --- stand-ins for the gi modules: only what login_terminal uses ---------------------------------------------------

class Widget:
    def __init__(self, **properties):
        self.properties, self.children, self.handlers, self.calls = properties, [], {}, []
        self.text = properties.get('label', '')

    def connect(self, name, handler):
        self.handlers[name] = handler

    def pack_start(self, child, *_):
        self.children.append(child)

    pack_end = add = pack_start

    def set_text(self, text):
        self.text = text

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return lambda *args: self.calls.append((name, args))


class Window(Widget):
    destroyed = False
    made: list = []                           # every window built in the current test

    def __init__(self, **properties):
        super().__init__(**properties)
        Window.made.append(self)

    def destroy(self):
        if not self.destroyed:
            self.destroyed = True
            self.handlers['destroy'](self)

    def close_button(self):
        """What GTK does for the window's close button: destroy only when delete-event returns False."""
        if not self.handlers['delete-event'](self, None):
            self.destroy()


class Loop:
    """GLib's timers, fired by the test."""
    Error = type('Error', (Exception,), {})

    def __init__(self):
        self.sources, self.next = {}, 1

    def timeout_add(self, milliseconds, function, *args):
        self.sources[self.next] = (milliseconds, function, args)
        self.next += 1
        return self.next - 1

    def source_remove(self, source):
        del self.sources[source]              # removing a source twice is a bug, and fails the test

    def fire(self, milliseconds):
        for source, (interval, function, args) in list(self.sources.items()):
            if interval == milliseconds and source in self.sources and not function(*args):
                self.sources.pop(source, None)


class ForeignPty:
    """Vte's pty object. As the real one, it owns the descriptor it was made from and closes it when released."""

    def __init__(self, fd):
        self.fd, self.released = fd, False

    def release(self):
        if not self.released:
            self.released = True
            os.close(self.fd)


class Vte:
    Format = SimpleNamespace(TEXT='text')

    class Terminal(Widget):
        pty = None

        def set_pty(self, pty):
            """As the real terminal: letting go of a pty object releases it."""
            self.calls.append(('set_pty', (pty,)))
            if self.pty is not None and self.pty is not pty:
                self.pty.release()
            self.pty = pty

    class Pty:
        fds: list = []                        # every descriptor number Vte was handed
        made: list = []                       # every pty object Vte made
        fail = None                           # None, or what Vte does with the descriptor before it raises

        @classmethod
        def new_foreign_sync(cls, fd, cancellable):
            assert cancellable is None and os.isatty(fd)
            cls.fds.append(fd)
            if cls.fail is not None:
                cls.fail(fd)
                raise Loop.Error('no pty')
            cls.made.append(ForeignPty(fd))
            return cls.made[-1]


Gtk = SimpleNamespace(Window=Window, Box=Widget, Label=Widget, Button=Widget,
                      Orientation=SimpleNamespace(VERTICAL='vertical'), Align=SimpleNamespace(START='start'))


class Boom(RuntimeError):
    """The injected failure. Nothing in the module names it, so it is never one the module expects."""


def boom(*_args, **_options):
    raise Boom('injected')


def is_closed(fd):
    """True when the number names no open descriptor at this moment."""
    try:
        os.fstat(fd)
    except OSError as exc:
        return exc.errno == errno.EBADF
    return False


class Child:
    """A process that ends only when the test says so."""
    count = 0

    def __init__(self, command, **options):
        Child.count += 1
        self.command, self.options, self.pid = command, options, 40000 + Child.count
        self.returncode, self.signals = None, []
        self.tty = [os.isatty(options[name]) for name in ('stdin', 'stdout', 'stderr')]
        self.size = struct.unpack('HHHH', fcntl.ioctl(options['stdin'], termios.TIOCGWINSZ, bytes(8)))[:2]

    def poll(self):
        return self.returncode

    def send_signal(self, number):
        if self.returncode is None:           # as subprocess.Popen: a reaped process is never signalled
            self.signals.append(number)


class Main:
    """The main window's session state. Nothing in the sign-in window may change or call any of it."""
    child_pid, running, connected, closing, stopped = 777, True, True, False, False

    def __getattr__(self, name):
        raise AssertionError(f'the sign-in window used the main window: {name}')


@pytest.fixture
def desk(tmp_path, monkeypatch):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name in ('claude', 'codex', 'gemini'):
        path = bin_dir / name
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex-home'))
    monkeypatch.setenv('PATH', str(bin_dir))
    monkeypatch.delenv('NVM_DIR', raising=False)
    Vte.Pty.fds, Vte.Pty.made, Vte.Pty.fail, Child.count, Window.made = [], [], None, 0, []
    loop, main, notes, children, ptys = Loop(), Main(), [], [], []
    real_openpty = os.openpty

    def openpty():
        ptys.append(real_openpty())           # (master, slave) of every pty the module opens
        return ptys[-1]

    monkeypatch.setattr(os, 'openpty', openpty)

    def popen(command, **options):
        children.append(Child(command, **options))
        return children[-1]

    environ = {'HOME': str(tmp_path), 'PATH': str(bin_dir), 'DISPLAY': ':99', 'DREAM_GUI': '1',
               'DREAM_DESKTOP_SESSION_FILE': str(tmp_path / 'session.json'), 'CRASHVAR': 'x',
               'PYTHONPATH': str(tmp_path / 'repo')}
    terminal = lt.LoginTerminal(main, Gtk, Vte, loop, notify=notes.append, repo_root=str(tmp_path / 'repo'),
                                strip_env=('CRASHVAR',), environ=environ, popen=popen,
                                supervise=lambda argv: ['SUPERVISOR', *argv])
    yield SimpleNamespace(terminal=terminal, loop=loop, main=main, notes=notes, children=children,
                          bin=bin_dir, home=tmp_path, environ=environ, ptys=ptys, openpty=real_openpty)
    # Only what a pty object still owns is closed here (a window that is still open at the end of a test). A
    # number that Vte was handed and did not keep is the module's to close, and the tests look at it themselves;
    # closing it here by number could hide a leak, or close something else that was given the number since.
    for made in Vte.Pty.made:
        try:
            made.release()
        except OSError:
            pass


def main_untouched(main):
    return (main.child_pid, main.running, main.connected, main.closing, main.stopped) == (777, True, True, False, False)


def launch(desk, provider='claude', cwd=None):
    reports = []
    desk.terminal.launch(provider, cwd or str(desk.home), reports.append)
    return reports


# --- 4/5. the window and the process it owns ------------------------------------------------------------------------

def test_launch_runs_the_supervised_fixed_command_on_its_own_pty_with_a_clean_environment(desk):
    reports = launch(desk, 'claude')
    assert reports == ['launched']
    child, session = desk.children[0], desk.terminal.session
    assert child.command == ['SUPERVISOR', str(desk.bin / 'claude'), 'auth', 'login']
    assert child.tty == [True, True, True]
    assert child.size == (lt.ROWS, lt.COLUMNS)                    # the size the CLI sees first
    assert child.options['start_new_session'] is True and child.options['close_fds'] is True
    assert child.options['cwd'] == str(desk.home)
    env = child.options['env']
    assert env['DISPLAY'] == ':99' and env['HOME'] == str(desk.home)
    assert not [name for name in env if name.startswith('DREAM_')] and 'CRASHVAR' not in env
    assert 'PYTHONPATH' not in env
    assert desk.environ['DREAM_GUI'] == '1'                       # the parent's mapping was copied, not edited
    (master, slave), = desk.ptys
    assert child.options['stdin'] == child.options['stdout'] == child.options['stderr'] == slave
    assert is_closed(master) and is_closed(slave)                 # this process kept no end of the pty but Vte's
    assert Vte.Pty.fds == [Vte.Pty.made[0].fd] and not Vte.Pty.made[0].released and os.isatty(Vte.Pty.fds[0])
    assert session.terminal.calls.count(('set_pty', (Vte.Pty.made[0],))) == 1
    assert ('show_all', ()) in session.window.calls
    assert 'claude auth login' in session.window.children[0].children[0].text
    assert 'does not check' in session.window.children[0].children[0].text
    assert not [name for name, _ in session.terminal.calls if 'feed' in name or 'get_text' in name]
    assert main_untouched(desk.main)


def test_gemini_opens_antigravity_and_a_missing_folder_falls_back_to_home(desk):
    executable = desk.bin / 'agy'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    assert launch(desk, 'gemini', cwd=str(desk.home / 'gone')) == ['launched']
    child = desk.children[0]
    assert child.command == ['SUPERVISOR', str(executable)]  # Retired gemini is also installed; never use it.
    assert child.options['cwd'] == str(desk.home)


@pytest.mark.parametrize('provider, expected', [
    ('grok', 'unavailable'), ('agy', 'unavailable'), ('sh', 'failed'), ('claude --help', 'failed'),
    ('/bin/sh', 'failed'), ('', 'failed'), (None, 'failed'),
])
def test_missing_cli_and_unknown_names_start_nothing(desk, provider, expected):
    assert launch(desk, provider) == [expected]
    assert desk.children == [] and desk.terminal.session is None and Vte.Pty.fds == []
    assert desk.loop.sources == {}


def test_second_request_is_busy_and_does_not_disturb_the_open_one(desk):
    launch(desk, 'claude')
    first = desk.terminal.session
    assert launch(desk, 'codex') == ['busy']
    assert desk.terminal.session is first and len(desk.children) == 1 and desk.children[0].signals == []
    assert ('present', ()) in first.window.calls


def test_closing_the_window_stops_only_its_own_child_and_waits_without_blocking(desk):
    reports = launch(desk)
    child, session = desk.children[0], desk.terminal.session
    session.window.close_button()
    assert child.signals == [signal.SIGTERM]                      # SIGTERM to its own supervisor, nothing stronger
    assert not session.window.destroyed and desk.terminal.session is session      # kept until the child is gone
    assert 'Stopping' in session.state.text and reports == ['launched']
    desk.loop.fire(lt.POLL_MS)
    assert not session.finished                                   # still running: still owned
    child.returncode = 143
    desk.loop.fire(lt.POLL_MS)
    assert session.finished and session.window.destroyed and desk.terminal.session is None
    assert reports == ['launched', 'closed'] and desk.loop.sources == {}
    assert main_untouched(desk.main)
    assert launch(desk, 'codex') == ['launched']                  # the slot is free again


def test_a_child_that_does_not_stop_is_shown_and_kept_not_reported_clean(desk):
    reports = launch(desk)
    child, session = desk.children[0], desk.terminal.session
    session.window.close_button()
    desk.loop.fire(lt.STOP_MS)
    assert session.stuck and not session.finished and not session.window.destroyed
    assert 'did not stop' in session.state.text and str(child.pid) in session.state.text
    assert desk.terminal.session is session and reports == ['launched']
    assert launch(desk, 'codex') == ['busy']
    session.window.close_button()                                 # asking again sends SIGTERM again, never SIGKILL
    assert child.signals == [signal.SIGTERM, signal.SIGTERM]
    child.returncode = 143
    desk.loop.fire(lt.POLL_MS)
    assert session.finished and reports == ['launched', 'closed'] and desk.loop.sources == {}


def test_command_ending_by_itself_reports_closed_once_and_claims_nothing(desk):
    reports = launch(desk)
    child, session = desk.children[0], desk.terminal.session
    child.returncode = 0
    desk.loop.fire(lt.POLL_MS)
    assert reports == ['launched'] and child.signals == []        # not 'closed': the window is still on screen
    assert not session.window.destroyed                           # the user can still read what the CLI said
    assert 'did not check' in session.state.text
    assert desk.terminal.session is session and desk.loop.sources == {}
    assert launch(desk, 'codex') == ['busy'] and ('present', ()) in session.window.calls
    desk.loop.fire(lt.POLL_MS), desk.loop.fire(lt.STOP_MS)        # nothing closes it but the user
    assert reports == ['launched'] and not session.window.destroyed and len(desk.children) == 1
    session.window.close_button()
    assert session.finished and session.window.destroyed and desk.terminal.session is None
    assert reports == ['launched', 'closed'] and child.signals == []
    session.window.close_button()
    desk.terminal._window_destroyed(session), desk.terminal._finish(session)
    assert reports == ['launched', 'closed']                      # exactly once
    assert main_untouched(desk.main)
    assert launch(desk, 'codex') == ['launched']                  # the slot is free only now


def test_an_ended_window_destroyed_from_outside_is_reported_closed_once(desk):
    reports = launch(desk)
    child, session = desk.children[0], desk.terminal.session
    child.returncode = 0
    desk.loop.fire(lt.POLL_MS)
    assert reports == ['launched'] and desk.terminal.session is session
    session.window.destroy()                                      # for example together with the main window
    assert session.finished and desk.terminal.session is None
    assert reports == ['launched', 'closed'] and child.signals == []


NEVER_RAN = ('nothing was run', 'nothing ran', 'did not run', 'was not run', 'was not started', 'never ran')


def test_the_refused_status_is_reported_as_either_not_started_or_the_clis_own_and_closed_once(desk):
    """The supervisor ends with this status when it did not start the command, and a CLI may end with it too. The
    window is given the status and nothing else, so it says both are possible and points at its terminal."""
    from dream.core import login_execution as le
    assert lt.REFUSED_STATUS == le.REFUSED
    reports = launch(desk)
    child, session = desk.children[0], desk.terminal.session
    child.returncode = lt.REFUSED_STATUS
    desk.loop.fire(lt.POLL_MS)
    assert reports == ['launched'] and child.signals == []        # the window is still open: not 'closed' yet
    text = session.state.text
    assert 'status 125' in text and 'could not be started' in text and 'ran and ended that way itself' in text
    assert 'The terminal may show more details' in text and 'says which' not in text
    assert 'claude auth login' in text
    assert 'did not check' in text and not [claim for claim in NEVER_RAN if claim in text]
    assert not session.window.destroyed and desk.loop.sources == {}
    assert desk.terminal.session is session
    session.window.close_button()
    assert session.finished and desk.terminal.session is None and reports == ['launched', 'closed']
    assert main_untouched(desk.main)


def test_the_default_supervisor_is_the_sign_in_one_and_never_the_tool_one(monkeypatch):
    from dream.core import execution, login_execution as le
    monkeypatch.setattr(le, 'login_supervised_command', lambda argv: ['LOGIN', *argv])
    monkeypatch.setattr(execution, 'supervised_command',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('the tool supervisor was used')))
    assert lt._supervise(('/bin/true', 'x')) == ['LOGIN', '/bin/true', 'x']


def test_window_destroyed_from_outside_still_stops_and_owns_its_child(desk):
    reports = launch(desk)
    child, session = desk.children[0], desk.terminal.session
    session.window.destroy()                                      # for example together with the main window
    assert child.signals == [signal.SIGTERM] and not session.finished
    assert desk.terminal.session is session                       # ownership is kept until the child is gone
    desk.loop.fire(lt.STOP_MS)
    assert session.stuck and len(desk.notes) == 1 and 'did not stop' in desk.notes[0]
    assert launch(desk, 'codex') == ['busy'] and child.signals == [signal.SIGTERM, signal.SIGTERM]
    child.returncode = 143
    desk.loop.fire(lt.POLL_MS)
    assert session.finished and desk.terminal.session is None and reports == ['launched', 'closed']


def test_stale_callbacks_of_an_old_session_never_touch_a_new_one(desk):
    launch(desk)
    old, old_child = desk.terminal.session, desk.children[0]
    old_timers = list(desk.loop.sources.values())
    old.window.close_button()
    old_stop = [entry for entry in desk.loop.sources.values() if entry not in old_timers]
    old_child.returncode = 143
    desk.loop.fire(lt.POLL_MS)
    assert old.finished
    new_reports = launch(desk, 'codex')
    new, new_child = desk.terminal.session, desk.children[1]
    for _, function, args in old_timers + old_stop:               # the old session's timers fire late
        function(*args)
    desk.terminal._close_requested(old)
    desk.terminal._window_destroyed(old)
    desk.terminal._finish(old)
    assert desk.terminal.session is new and not new.finished and not new.window.destroyed
    assert new_child.signals == [] and new_reports == ['launched'] and old_child.signals == [signal.SIGTERM]


def test_main_window_shutdown_stops_the_child_without_waiting_and_refuses_new_launches(desk):
    launch(desk)
    child = desk.children[0]
    desk.terminal.shutdown()
    assert child.signals == [signal.SIGTERM]
    assert launch(desk, 'codex') == ['failed'] and len(desk.children) == 1
    assert main_untouched(desk.main)


# --- a start that fails before any process exists: every descriptor is closed by the time launch() returns ---------
#
# Each check below is made immediately after launch(), before the fixture cleans anything up, and names every
# descriptor of the attempt: both ends of the pty the module opened, and the duplicate it handed to Vte.

def nothing_was_started(desk):
    assert desk.children == [] and desk.terminal.session is None and desk.loop.sources == {}
    assert len(Window.made) == 1 and Window.made[0].destroyed
    (master, slave), = desk.ptys
    assert is_closed(master) and is_closed(slave)
    assert desk.notes == [] and main_untouched(desk.main)
    return Vte.Pty.fds[0]


def test_vte_refusing_the_pty_and_keeping_the_descriptor_open_leaves_nothing_open(desk):
    Vte.Pty.fail = lambda fd: None                                # raises, and has not closed what it was handed
    assert launch(desk) == ['failed']
    assert is_closed(nothing_was_started(desk)) and Vte.Pty.made == []


def test_vte_refusing_the_pty_after_closing_the_descriptor_is_not_closed_a_second_time(desk, monkeypatch):
    closed = []
    real_close = os.close

    def close(fd):
        closed.append(fd)
        real_close(fd)

    Vte.Pty.fail = real_close                                     # Vte closed it itself, then raised
    monkeypatch.setattr(os, 'close', close)
    assert launch(desk) == ['failed']
    monkeypatch.setattr(os, 'close', real_close)
    handed = nothing_was_started(desk)
    assert is_closed(handed) and handed not in closed             # the module did not even try


@pytest.mark.parametrize('given_to', ['a file', 'another pty'])
def test_a_number_vte_closed_and_something_else_was_given_is_never_closed(desk, given_to):
    """Vte closes the duplicate and raises; by then the number names something else. The module may close it only
    when the kernel says it is still a descriptor of the pty it opened, and here it is not."""
    other = desk.openpty() if given_to == 'another pty' else (os.open(os.devnull, os.O_RDWR), -1)
    Vte.Pty.fail = lambda fd: os.dup2(other[0], fd)               # closes the duplicate; the number is in use again
    try:
        assert launch(desk) == ['failed']
        handed = nothing_was_started(desk)
        assert not is_closed(handed) and os.path.sameopenfile(handed, other[0])       # untouched
    finally:
        for fd in (Vte.Pty.fds[0] if Vte.Pty.fds else -1, *other):
            if fd >= 0:
                os.close(fd)


@pytest.mark.parametrize('error', [OSError('no'), ValueError('no'), Boom('injected')])
def test_a_process_that_could_not_be_started_leaves_nothing_open(desk, error):
    """Vte's pty object exists by now and owns the duplicate: the terminal is told to let go of it."""
    def popen(*_args, **_options):
        raise error

    desk.terminal._popen = popen
    reports = []
    if isinstance(error, Boom):                                   # an error the module does not expect is not hidden
        with pytest.raises(Boom):
            desk.terminal.launch('claude', str(desk.home), reports.append)
    else:
        desk.terminal.launch('claude', str(desk.home), reports.append)
    assert reports == ['failed']                                  # and the request is answered either way
    assert is_closed(nothing_was_started(desk))
    pty, = Vte.Pty.made
    assert pty.released and Window.made[0].children[0].children[-1].calls[-1] == ('set_pty', (None,))


def test_a_refused_supervisor_command_opens_nothing_at_all(desk):
    desk.terminal._supervise = lambda argv: (_ for _ in ()).throw(RuntimeError('refused'))
    assert launch(desk) == ['failed']
    assert desk.ptys == [] and Window.made == [] and Vte.Pty.fds == [] and desk.children == []
    assert desk.terminal.session is None and desk.loop.sources == {}


@pytest.mark.skipif(sys.platform != 'linux', reason='pty numbers are asked of the Linux kernel')
def test_two_descriptors_are_the_same_pty_only_when_the_kernel_says_so():
    """The check that guards the one close above. It uses fstat and the TIOCGPTN ioctl; os.ptsname is not there
    before Python 3.13."""
    same = lt.LoginTerminal._same_pty
    master, slave = os.openpty()
    other_master, other_slave = os.openpty()
    twin, null = os.dup(master), os.open(os.devnull, os.O_RDWR)
    try:
        assert lt._pty_identity(twin) == lt._pty_identity(master) != lt._pty_identity(other_master)
        assert same(twin, master)
        assert not same(other_master, master)                     # a pty, but another one
        assert not same(slave, master)                            # this pty's other end is not a master
        assert not same(null, master)                             # a character device that is no pty
        assert not same(master, master) and not same(-1, master) and not same(twin, -1)
        os.close(twin)
        assert not same(twin, master)                             # a number that names nothing
        with pytest.raises(OSError):
            lt._pty_identity(twin)
        with pytest.raises(OSError):
            lt._pty_identity(null)
    finally:
        for fd in (master, slave, other_master, other_slave, null):
            os.close(fd)
        if not is_closed(twin):
            os.close(twin)


def test_when_the_kernel_cannot_be_asked_the_descriptor_is_left_alone_and_the_start_still_fails_cleanly(
        desk, monkeypatch):
    """The ioctl is refused (an unknown machine, a restricted process). The module then cannot show that the
    number is its own, so it does not close it: one descriptor stays open, nothing of anyone else's is closed,
    and launch() answers 'failed' without raising."""
    def refused(*_args):
        raise OSError(errno.ENOTTY, 'not this one')

    monkeypatch.setattr(lt.fcntl, 'ioctl', lambda fd, request, *rest: (
        refused() if request == lt._tiocgptn() else fcntl_ioctl(fd, request, *rest)))
    Vte.Pty.fail = lambda fd: None
    try:
        assert launch(desk) == ['failed']
        assert desk.children == [] and desk.terminal.session is None and Window.made[0].destroyed
        (master, slave), = desk.ptys
        assert is_closed(master) and is_closed(slave)
        assert not is_closed(Vte.Pty.fds[0])                      # the stated cost: left open, not guessed at
    finally:
        for fd in Vte.Pty.fds:
            if not is_closed(fd):
                os.close(fd)


fcntl_ioctl = fcntl.ioctl                     # the real one, kept for the test above


def test_the_ioctl_number_is_the_named_one_or_the_linux_value_for_this_machine(monkeypatch):
    monkeypatch.setattr(lt.termios, 'TIOCGPTN', 0x1234, raising=False)
    assert lt._tiocgptn() == 0x1234
    monkeypatch.delattr(lt.termios, 'TIOCGPTN')
    for machine, value in (('x86_64', 0x80045430), ('aarch64', 0x80045430), ('riscv64', 0x80045430),
                           ('ppc64le', 0x40045430), ('mips64', 0x40045430), ('sparc64', 0x40045430)):
        monkeypatch.setattr(lt.os, 'uname', lambda machine=machine: SimpleNamespace(machine=machine))
        assert lt._tiocgptn() == value


# --- a step that raises after the process was started: it stays owned, and no window is claimed -------------------

def fail_step(desk, monkeypatch, step):
    """Make one of the steps between starting the process and reporting it raise."""
    if step == 'timer':
        real, left = desk.loop.timeout_add, [1]

        def once(*args):
            if left[0]:
                left[0] -= 1
                raise Boom('injected')
            return real(*args)

        desk.loop.timeout_add = once          # the first timer only: the ones set while recovering work
    elif step == 'show':
        monkeypatch.setattr(Window, 'show_all', boom, raising=False)
    else:
        monkeypatch.setattr(Vte.Terminal, 'grab_focus', boom, raising=False)


@pytest.mark.parametrize('step', ['timer', 'show', 'focus'])
def test_a_step_that_raises_after_the_start_is_answered_failed_and_the_command_stays_owned(desk, monkeypatch, step):
    fail_step(desk, monkeypatch, step)
    reports = []
    with pytest.raises(Boom):                                     # not hidden from the caller
        desk.terminal.launch('claude', str(desk.home), reports.append)
    (child,), session = desk.children, desk.terminal.session
    assert reports == ['failed']                                  # never 'launched': no usable window was opened
    assert session is not None and session.proc is child and session.setup_failed and not session.finished
    assert child.signals == [signal.SIGTERM]                      # asked to stop, with nothing stronger
    note, = desk.notes                                            # and the user is told what is still there
    assert 'could not be opened' in note and str(child.pid) in note and 'asked to stop' in note
    assert 'not been seen to end' in note and 'has ended' not in note
    assert not session.window.destroyed and session.problems == []
    assert sorted(entry[0] for entry in desk.loop.sources.values()) == [lt.POLL_MS, lt.STOP_MS]
    (master, slave), = desk.ptys
    assert is_closed(master) and is_closed(slave)
    # Its slot is kept: a new request starts nothing, asks again, and does not raise a window that never opened.
    assert launch(desk, 'codex') == ['busy'] and len(desk.children) == 1
    assert child.signals == [signal.SIGTERM, signal.SIGTERM] and ('present', ()) not in session.window.calls
    desk.loop.fire(lt.POLL_MS)
    assert not session.finished and desk.terminal.session is session and len(desk.notes) == 1
    child.returncode = 143
    desk.loop.fire(lt.POLL_MS)
    assert session.finished and session.window.destroyed and desk.terminal.session is None
    assert reports == ['failed'] and desk.loop.sources == {}      # no 'closed' for a window never reported open
    assert len(desk.notes) == 2 and 'has ended' in desk.notes[1] and str(child.pid) in desk.notes[1]
    assert main_untouched(desk.main)


def test_a_command_that_does_not_stop_after_a_failed_setup_is_said_to_be_there_and_is_kept(desk, monkeypatch):
    fail_step(desk, monkeypatch, 'show')
    reports = []
    with pytest.raises(Boom):
        desk.terminal.launch('claude', str(desk.home), reports.append)
    child, session = desk.children[0], desk.terminal.session
    desk.loop.fire(lt.STOP_MS)
    assert session.stuck and not session.finished and desk.terminal.session is session
    assert len(desk.notes) == 2 and 'did not stop' in desk.notes[1] and str(child.pid) in desk.notes[1]
    assert 'Ask for that sign-in again' in desk.notes[1]
    assert launch(desk, 'codex') == ['busy'] and child.signals == [signal.SIGTERM, signal.SIGTERM]
    assert sorted(entry[0] for entry in desk.loop.sources.values()) == [lt.POLL_MS, lt.STOP_MS]   # looking again
    child.returncode = 143
    desk.loop.fire(lt.POLL_MS)
    assert session.finished and desk.terminal.session is None and desk.loop.sources == {}
    assert reports == ['failed'] and 'has ended' in desk.notes[-1] and len(desk.children) == 1


def test_a_launched_report_that_raises_is_not_made_again_and_closed_follows_once(desk):
    reports = []

    def report(status):
        reports.append(status)
        if status == 'launched':
            raise Boom('injected')

    with pytest.raises(Boom):
        desk.terminal.launch('claude', str(desk.home), report)
    child, session = desk.children[0], desk.terminal.session
    assert reports == ['launched']                                # tried once; neither repeated nor turned into 'failed'
    assert session.proc is child and session.setup_failed and child.signals == [signal.SIGTERM]
    assert desk.notes == [] and 'Stopping' in session.state.text  # the window is on screen and says so itself
    assert not session.window.destroyed
    assert sorted(entry[0] for entry in desk.loop.sources.values()) == [lt.POLL_MS, lt.STOP_MS]
    child.returncode = 143
    desk.loop.fire(lt.POLL_MS)
    assert reports == ['launched', 'closed'] and session.finished and session.window.destroyed
    assert desk.terminal.session is None and desk.loop.sources == {} and desk.notes == []


def test_a_closed_report_that_raises_is_made_once_and_leaves_nothing_half_done(desk):
    reports = []

    def report(status):
        reports.append(status)
        if status == 'closed':
            raise Boom('injected')

    desk.terminal.launch('claude', str(desk.home), report)
    child, session = desk.children[0], desk.terminal.session
    child.returncode = 0
    desk.loop.fire(lt.POLL_MS)
    assert session.exited and session.watch is None and 'did not check' in session.state.text
    assert reports == ['launched'] and not session.window.destroyed
    with pytest.raises(Boom):
        session.window.close_button()                             # the report is the last step of the close
    assert session.finished and session.window.destroyed and desk.terminal.session is None
    assert desk.loop.sources == {} and reports == ['launched', 'closed']
    session.window.close_button()                                 # were it asked again, it reports nothing again
    desk.terminal._window_destroyed(session), desk.terminal._finish(session)
    desk.loop.fire(lt.POLL_MS)
    assert reports == ['launched', 'closed'] and child.signals == []
    assert launch(desk, 'codex') == ['launched']                  # the slot was freed before the report raised


def test_a_closed_report_that_raises_from_the_timer_leaves_nothing_half_done(desk):
    reports = []

    def report(status):
        reports.append(status)
        if status == 'closed':
            raise Boom('injected')

    desk.terminal.launch('claude', str(desk.home), report)
    child, session = desk.children[0], desk.terminal.session
    session.window.close_button()
    child.returncode = 143
    with pytest.raises(Boom):
        desk.loop.fire(lt.POLL_MS)                                # GLib drops a timer whose function raised
    assert session.finished and session.window.destroyed and desk.terminal.session is None
    assert session.watch is None and session.stop_timer is None
    desk.loop.fire(lt.POLL_MS), desk.loop.fire(lt.STOP_MS)
    assert reports == ['launched', 'closed'] and child.signals == [signal.SIGTERM]


def test_when_no_timer_can_be_set_the_next_request_looks_once_and_the_slot_is_freed_only_by_the_end(desk):
    desk.loop.timeout_add = boom                                  # every timer, also the ones tried while recovering
    reports = []
    with pytest.raises(Boom):
        desk.terminal.launch('claude', str(desk.home), reports.append)
    child, session = desk.children[0], desk.terminal.session
    assert reports == ['failed'] and child.signals == [signal.SIGTERM] and len(desk.notes) == 1
    assert session.proc is child and session.watch is None and session.stop_timer is None
    assert session.problems == ['watch: Boom', 'stop_timer: Boom'] and desk.loop.sources == {}
    assert launch(desk, 'codex') == ['busy']                      # still there: asked again, nothing new started
    assert len(desk.children) == 1 and child.signals == [signal.SIGTERM, signal.SIGTERM]
    assert desk.terminal.session is session and not session.finished and len(desk.notes) == 1
    child.returncode = 143
    del desk.loop.timeout_add                                     # timers work again
    assert launch(desk, 'codex') == ['launched']                  # seen to have ended in that one look
    assert session.finished and session.window.destroyed and len(desk.children) == 2
    assert child.signals == [signal.SIGTERM, signal.SIGTERM]      # a process that has ended is not signalled
    assert desk.children[1].signals == [] and desk.terminal.session.proc is desk.children[1]
    assert reports == ['failed'] and len(desk.notes) == 2 and 'has ended' in desk.notes[1]


def test_what_fails_while_recovering_is_kept_by_name_and_never_raised_over_the_first_error(desk, monkeypatch):
    fail_step(desk, monkeypatch, 'show')
    desk.terminal._notify = lambda text: (_ for _ in ()).throw(KeyError('status bar'))

    def report(status):
        raise ValueError('bridge')

    with pytest.raises(Boom):                                     # the step that failed, not what failed after it
        desk.terminal.launch('claude', str(desk.home), report)
    child, session = desk.children[0], desk.terminal.session
    assert session.problems == ['report: ValueError', 'notify: KeyError']       # names only, never their text
    assert session.proc is child and child.signals == [signal.SIGTERM]
    assert sorted(entry[0] for entry in desk.loop.sources.values()) == [lt.POLL_MS, lt.STOP_MS]
    child.returncode = 143
    desk.loop.fire(lt.POLL_MS)                                    # the end is still seen, and nothing raises here
    assert session.finished and desk.terminal.session is None and desk.loop.sources == {}
    assert session.problems[-1] == 'notify: KeyError' and len(session.problems) == 3


# --- the window's size and layout ----------------------------------------------------------------------------------

def test_the_window_opens_at_a_fixed_readable_size_with_a_wrapped_explanation_and_a_terminal_that_grows(desk):
    launch(desk, 'claude')
    session = desk.terminal.session
    window, (box,) = session.window, session.window.children
    assert (lt.WIDTH, lt.HEIGHT, lt.MARGIN) == (860, 600, 10) and 70 <= lt.PURPOSE_CHARS <= 85
    assert window.calls.count(('set_default_size', (860, 600))) == 1
    assert window.calls.index(('set_default_size', (860, 600))) < window.calls.index(('show_all', ()))
    for side in ('top', 'bottom', 'start', 'end'):
        assert (f'set_margin_{side}', (10,)) in box.calls
    purpose, bar, terminal = box.children
    assert terminal is session.terminal and bar.children[0] is session.state
    assert ('set_line_wrap', (True,)) in purpose.calls and ('set_max_width_chars', (lt.PURPOSE_CHARS,)) in purpose.calls
    assert ('set_halign', ('start',)) in purpose.calls and purpose.properties['xalign'] == 0
    assert ('set_line_wrap', (True,)) in session.state.calls
    assert ('set_hexpand', (True,)) in terminal.calls and ('set_vexpand', (True,)) in terminal.calls
    assert ('set_size', (lt.COLUMNS, lt.ROWS)) in terminal.calls and ('set_scrollback_lines', (2000,)) in terminal.calls
    # Nothing shortens or hides text: no ellipsis, no fixed pixel size on a label or on the terminal.
    for widget in (purpose, session.state, terminal, box, bar):
        assert not [name for name, _ in widget.calls if name in ('set_ellipsize', 'set_size_request', 'set_lines',
                                                                 'set_single_line_mode', 'set_no_show_all')]


def test_the_module_never_uses_the_main_terminal_api_or_a_stronger_signal():
    import inspect
    import io
    import tokenize
    source = io.StringIO(inspect.getsource(lt))
    names = {token.string for token in tokenize.generate_tokens(source.readline) if token.type == tokenize.NAME}
    assert {'send_signal', 'SIGTERM', 'set_pty'} <= names         # the check below is reading real code
    for forbidden in ('start_terminal', 'open_terminal', '_child_exited', '_close', 'child_pid', 'running',
                      'SIGKILL', 'kill', 'killpg', 'feed_child', 'feed', 'get_text', 'spawn_async', 'shell',
                      'wait', 'communicate', 'ptsname'):          # ptsname: not in the os module before Python 3.13
        assert forbidden not in names, forbidden


@pytest.mark.skipif(sys.platform != 'linux', reason='the supervisor is Linux-only')
def test_a_real_synthetic_child_gets_the_fixed_argv_on_a_terminal_and_is_stopped_with_its_children(desk):
    """The real sign-in supervisor and a real process, with a stand-in `codex` this test wrote. Nothing else is on
    PATH. The terminal itself (/dev/tty, Ctrl-C, resize) is covered in tests/test_login_execution.py."""
    from dream.core.execution import ExecutionRefused
    from dream.core.login_execution import login_supervised_command
    try:
        login_supervised_command(['/bin/true'])
    except ExecutionRefused as exc:
        pytest.skip(str(exc))
    script = desk.bin / 'codex'
    script.write_text('#!/bin/sh\n'
                      '{ printf "%s\\n" "$@"; [ -t 0 ] && echo tty; echo "gui=${DREAM_GUI:-unset}";\n'
                      '  ( : < /dev/tty ) 2>/dev/null && echo ctty; } > "$HOME/seen"\n'
                      '/bin/sleep 60 &\n'
                      'echo $! > "$HOME/grandchild"\n'
                      'exec /bin/sleep 60\n')
    assert pl.launch_plan(pl.resolve('codex'), {}).argv[0] == str(script)        # never a real installed CLI
    desk.terminal._popen, desk.terminal._supervise = subprocess.Popen, lt._supervise
    reports = launch(desk, 'chatgpt')
    assert reports == ['launched']
    session = desk.terminal.session
    try:
        deadline = time.monotonic() + 10
        while not (desk.home / 'grandchild').exists() or not (desk.home / 'grandchild').read_text().strip():
            assert time.monotonic() < deadline and session.proc.poll() is None
            time.sleep(0.05)
        assert (desk.home / 'seen').read_text().split() == ['login', 'tty', 'gui=unset', 'ctty']
        grandchild = int((desk.home / 'grandchild').read_text())
        session.window.close_button()
        while not session.finished:
            assert time.monotonic() < deadline
            desk.loop.fire(lt.POLL_MS)
            time.sleep(0.05)
        assert reports == ['launched', 'closed'] and desk.terminal.session is None
        gone = time.monotonic() + 3
        while os.path.exists(f'/proc/{grandchild}') and time.monotonic() < gone:
            time.sleep(0.05)
        assert not os.path.exists(f'/proc/{grandchild}')          # what the CLI started went with it
    finally:
        if session.proc.poll() is None:
            session.proc.terminate()
            session.proc.wait(10)


@pytest.mark.skipif(sys.platform != 'linux', reason='the supervisor is Linux-only')
def test_a_real_cli_that_ends_with_status_125_itself_is_never_said_not_to_have_run(desk):
    """The real supervisor and a real stand-in `codex` that runs, leaves a mark and ends with 125 of its own
    accord: the same status the supervisor uses when it starts nothing."""
    from dream.core.execution import ExecutionRefused
    from dream.core.login_execution import login_supervised_command
    try:
        login_supervised_command(['/bin/true'])
    except ExecutionRefused as exc:
        pytest.skip(str(exc))
    (desk.bin / 'codex').write_text('#!/bin/sh\necho ran > "$HOME/ran"\nexit 125\n')
    desk.terminal._popen, desk.terminal._supervise = subprocess.Popen, lt._supervise
    reports = launch(desk, 'chatgpt')
    assert reports == ['launched']
    session = desk.terminal.session
    try:
        deadline = time.monotonic() + 10
        while not session.exited:
            assert time.monotonic() < deadline
            desk.loop.fire(lt.POLL_MS)
            time.sleep(0.05)
        assert session.proc.returncode == lt.REFUSED_STATUS == 125
        assert (desk.home / 'ran').read_text().strip() == 'ran'   # it did run
        text = session.state.text
        assert 'status 125' in text and 'ran and ended that way itself' in text
        assert 'could not be started' in text and 'The terminal may show more details' in text
        assert 'says which' not in text and 'did not check' in text
        assert not [claim for claim in NEVER_RAN if claim in text]
        assert reports == ['launched'] and not session.window.destroyed       # open, so not 'closed'
        assert desk.terminal.session is session
        session.window.close_button()                             # dismissed by hand, and only then 'closed'
        assert session.finished and session.window.destroyed and desk.terminal.session is None
        assert reports == ['launched', 'closed']
    finally:
        if session.proc.poll() is None:
            session.proc.terminate()
            session.proc.wait(10)


# --- 3. the window's side of the request bridge ---------------------------------------------------------------------

GOOD = {'id': 'ab' * 8, 'provider': 'anthropic'}


@pytest.mark.parametrize('state', [
    None, [], {}, {'login_request': None}, {'login_request': 'claude'}, {'login_request': ['ab' * 8, 'anthropic']},
    {'login_request': {'id': 'ab' * 8}}, {'login_request': {'provider': 'anthropic'}},
    {'login_request': {**GOOD, 'command': 'sh'}}, {'login_request': {**GOOD, 'args': ['-c', 'id']}},
    {'login_request': {**GOOD, 'provider': 'claude'}},            # only canonical names cross the bridge
    {'login_request': {**GOOD, 'provider': 'sh'}}, {'login_request': {**GOOD, 'provider': ['anthropic']}},
    {'login_request': {**GOOD, 'id': 'AB' * 8}}, {'login_request': {**GOOD, 'id': 'ab' * 9}},
    {'login_request': {**GOOD, 'id': '../../etc/passwd'}}, {'login_request': {**GOOD, 'id': 7}},
])
def test_a_malformed_status_asks_for_nothing(state):
    assert login_request(state) is None
    assert LoginTracker().take((1, ('http://127.0.0.1:1', 't')), state) is None


def test_a_request_is_taken_once_per_session_and_answers_stay_with_their_session():
    tracker, one, two = LoginTracker(), (11, ('http://127.0.0.1:1', 'a')), (12, ('http://127.0.0.1:2', 'b'))
    state = {'login_request': dict(GOOD), 'show_sequence': 3}
    assert tracker.take(one, state) == (GOOD['id'], 'anthropic')
    assert tracker.take(one, state) is None and tracker.take(one, state) is None      # every later poll
    assert not tracker.report(one, GOOD['id'], 'codex', 'launched')                   # another provider
    assert not tracker.report(one, 'cd' * 8, 'anthropic', 'launched')                 # an id never taken
    assert not tracker.report(one, GOOD['id'], 'anthropic', 'signed-in')              # not a window status
    assert not tracker.report(two, GOOD['id'], 'anthropic', 'launched')               # another session
    assert tracker.due() == []
    assert tracker.report(one, GOOD['id'], 'anthropic', 'launched')
    assert tracker.due() == [(GOOD['id'], 'anthropic', 'launched')]
    assert tracker.report(one, GOOD['id'], 'anthropic', 'closed')                     # the newer answer replaces it
    tracker.sent(one, GOOD['id'], 'launched')                                         # the older delivery returns
    assert tracker.due() == [(GOOD['id'], 'anthropic', 'closed')]
    tracker.sent(one, GOOD['id'], 'closed')
    assert tracker.due() == []
    tracker.report(one, GOOD['id'], 'anthropic', 'closed')
    assert tracker.take(two, {}) is None                          # the session changed: old answers are dropped
    assert tracker.due() == [] and not tracker.report(one, GOOD['id'], 'anthropic', 'closed')
    assert tracker.take(two, state) == (GOOD['id'], 'anthropic')  # a new session's request is its own


def test_window_wiring_launches_once_and_reports_through_the_tracker(desk):
    """DreamWindow._login_poll's own steps, with the real tracker and terminal (the window class needs a display)."""
    tracker, binding = LoginTracker(), (777, ('http://127.0.0.1:1', 't'))
    state = {'login_request': {'id': 'ab' * 8, 'provider': 'codex'}}

    def poll(current_binding=binding):
        request = tracker.take(current_binding, state)
        if request:
            request_id, provider = request
            desk.terminal.launch(provider, str(desk.home), lambda status: tracker.report(
                current_binding, request_id, provider, status))

    poll(), poll(), poll()
    assert len(desk.children) == 1 and tracker.due() == [('ab' * 8, 'codex', 'launched')]
    later = (778, ('http://127.0.0.1:2', 'u'))
    tracker.take(later, {})                                       # a new session starts while the window is open
    desk.children[0].returncode = 0
    desk.loop.fire(lt.POLL_MS)
    assert tracker.due() == []
    desk.terminal.session.window.close_button()                   # 'closed' is reported when the window goes
    assert desk.terminal.session is None
    assert tracker.due() == []                                    # its "closed" is not sent to the new session
    assert main_untouched(desk.main)


async def test_answers_reach_the_real_session_over_http_with_fixed_fields_only(tmp_path, monkeypatch):
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    monkeypatch.setenv('DREAM_DESKTOP_SESSION_FILE', str(tmp_path / 'session.json'))
    server = StudioServer(EventBus(), session={})
    await server.start()
    base = f'http://127.0.0.1:{server.port}'
    try:
        asked = server.login.request('grok')
        tracker, binding = LoginTracker(), (os.getpid(), (base, server.token))
        assert tracker.take(binding, {'login_request': server.login.desktop_view()}) == (asked['id'], 'grok')
        assert await asyncio.to_thread(login_ack, base, server.token, asked['id'], 'grok', 'launched') is True
        assert server.login.status(asked['id'])['request']['status'] == 'launched'
        assert await asyncio.to_thread(login_ack, base, server.token, asked['id'], 'grok', 'launched') is True
        # Refused for good (wrong provider, wrong token, unknown id): answered, so not sent again; nothing changes.
        assert await asyncio.to_thread(login_ack, base, server.token, asked['id'], 'codex', 'closed') is True
        assert await asyncio.to_thread(login_ack, base, 'wrong-token', asked['id'], 'grok', 'closed') is True
        assert await asyncio.to_thread(login_ack, base, server.token, 'cd' * 8, 'grok', 'closed') is True
        assert server.login.status(asked['id'])['request']['status'] == 'launched'
        assert await asyncio.to_thread(login_ack, base, server.token, asked['id'], 'grok', 'closed') is True
        assert server.login.status(asked['id'])['request']['status'] == 'closed'
        for bad in (('nope', 'grok', 'closed'), (asked['id'], 'sh', 'closed'), (asked['id'], 'grok', 'signed-in')):
            with pytest.raises(ValueError):
                login_ack(base, server.token, *bad)
    finally:
        await server.stop()
    with pytest.raises(OSError):                                  # the session is gone: the caller retries later
        await asyncio.to_thread(login_ack, base, server.token, asked['id'], 'grok', 'closed')
