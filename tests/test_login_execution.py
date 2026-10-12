"""The sign-in window's supervisor (dream.core.login_execution) on real ptys with real processes (DREAM-246).

No provider CLI, no model, no network and no credential is involved: the "CLI" is one small script this test writes
into a temporary HOME, and everything it reports is a few ASCII words in files there. Every fixture process ends by
itself within a minute, ends at once when the test writes the `release` file, and is otherwise only ever reached
through the supervisor's own Popen object with SIGTERM. Nothing here signals a bare process id. (Where a stop has
to arrive at one exact place, the supervisor under test raises it for itself there.) The rules for which process
may be asked to stop are also run against a scripted /proc, with no process at all.
"""
from __future__ import annotations

import fcntl
import json
import os
import select
import shlex
import signal
import stat
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

import pytest

from dream.core import login_execution as le
from dream.core.execution import ExecutionRefused

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='the sign-in supervisor is Linux-only')

REPO = Path(le.__file__).resolve().parents[2]
WAIT = 10.0          # the longest any single step may take

# The stand-in CLI. Its first argument picks what it does.
FIXTURE = r'''
import json
import os
import signal
import sys
import time

home = os.environ["HOME"]


def put(name, value):
    with open(os.path.join(home, name + ".tmp"), "w") as handle:
        json.dump(value, handle)
    os.replace(os.path.join(home, name + ".tmp"), os.path.join(home, name))


def idle(seconds=60):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not os.path.exists(os.path.join(home, "release")):
        time.sleep(0.05)


mode = sys.argv[1]
put("started", {"pid": os.getpid(), "args": sys.argv[1:]})

if mode == "tty":
    fd = os.open("/dev/tty", os.O_RDWR)
    os.write(fd, b"via-dev-tty\r\n")
    size = os.get_terminal_size(fd)
    put("seen", {
        "name": os.ttyname(0), "isatty": [os.isatty(n) for n in (0, 1, 2)],
        "foreground": os.tcgetpgrp(fd) == os.getpgrp(), "group_leader": os.getpgrp() == os.getpid(),
        "session": os.getsid(0), "parent": os.getppid(), "size": [size.columns, size.lines],
        "default_signals": all(signal.getsignal(n) == signal.SIG_DFL for n in (
            signal.SIGTTOU, signal.SIGTTIN, signal.SIGTSTP, signal.SIGTERM, signal.SIGHUP)),
        "gui": os.environ.get("DREAM_GUI", "unset")})
    sys.exit(0)

if mode == "interrupt":
    def interrupted(*_):
        put("interrupted", True)
        os._exit(7)
    signal.signal(signal.SIGINT, interrupted)
    put("ready", True)
    idle()
    sys.exit(1)

if mode == "exit125":
    sys.exit(125)

if mode == "plain":
    put("ready", True)
    idle()
    sys.exit(1)

if mode == "resize":
    def resized(*_):
        now = os.get_terminal_size(0)
        put("size", [now.columns, now.lines])
    signal.signal(signal.SIGWINCH, resized)
    first = os.get_terminal_size(0)
    put("ready", [first.columns, first.lines])
    idle()
    sys.exit(0)

if mode in ("detach", "detach-exit"):
    if os.fork() == 0:
        os.setsid()
        if os.fork() == 0:
            put("detached", os.getpid())
            os.execv("/bin/sleep", ["sleep", "60"])
        os._exit(0)
    os.wait()
    while not os.path.exists(os.path.join(home, "detached")):
        time.sleep(0.02)
    put("ready", True)
    if mode == "detach-exit":
        sys.exit(0)
    idle()
    sys.exit(1)

if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    put("ready", True)
    idle(30)
    put("released", True)
    sys.exit(0)

sys.exit(2)
'''


def resize(fd, rows, columns):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))


def stat_fields(pid):
    data = Path(f'/proc/{pid}/stat').read_bytes()
    return data[data.rindex(b')') + 2:].split()


def gone(pid):
    """No such process, or one that has ended and only waits to be collected by a parent that is not ours."""
    try:
        return stat_fields(pid)[0] == b'Z'
    except (OSError, ValueError, IndexError):
        return True


def wait_gone(pid, seconds=WAIT):
    deadline = time.monotonic() + seconds
    while not gone(pid):
        assert time.monotonic() < deadline, f'process {pid} is still there'
        time.sleep(0.05)


class Rig:
    def __init__(self, home: Path):
        self.home, self.master, self.proc, self.output, self.tty_name = home, -1, None, b'', ''
        (home / 'fixture.py').write_text(FIXTURE, encoding='ascii')
        self.script = home / 'fixture-cli'
        self.script.write_text('#!/bin/sh\nexec %s %s "$@"\n' % (
            shlex.quote(sys.executable), shlex.quote(str(home / 'fixture.py'))), encoding='ascii')
        self.script.chmod(self.script.stat().st_mode | stat.S_IXUSR)
        self.env = {'HOME': str(home), 'CODEX_HOME': str(home / 'codex-home'), 'PATH': '/usr/bin:/bin',
                    'TERM': 'xterm-256color', 'LANG': 'C'}

    def pty(self):
        self.master, slave = os.openpty()
        resize(self.master, 30, 100)
        self.tty_name = os.ttyname(slave)
        return slave

    def start(self, mode, command=le.login_supervised_command):
        slave = self.pty()
        try:
            self.proc = subprocess.Popen(command([str(self.script), mode]), stdin=slave, stdout=slave, stderr=slave,
                                         cwd=str(self.home), env=self.env, start_new_session=True, close_fds=True)
        finally:
            os.close(slave)
        return self.proc

    def read(self, until: bytes, seconds=WAIT):
        """What the terminal shows, read until `until` appears."""
        deadline = time.monotonic() + seconds
        while until not in self.output:
            assert time.monotonic() < deadline, f'the terminal never showed {until!r}: {self.output!r}'
            if select.select([self.master], [], [], 0.1)[0]:
                try:
                    self.output += os.read(self.master, 4096)
                except OSError:
                    time.sleep(0.05)              # nothing holds the other end at this moment
        return self.output

    def drain(self):
        """Everything the terminal has shown so far. For use once whatever wrote to it has ended."""
        while select.select([self.master], [], [], 0.2)[0]:
            try:
                chunk = os.read(self.master, 4096)
            except OSError:
                break                             # nothing holds the other end any more
            if not chunk:
                break
            self.output += chunk
        return self.output

    def seen(self, name, seconds=WAIT):
        deadline = time.monotonic() + seconds
        while not (self.home / name).exists():
            assert time.monotonic() < deadline, f'the fixture never wrote {name}'
            assert self.proc is None or self.proc.poll() is None, f'the supervisor ended ({self.proc.returncode})'
            time.sleep(0.05)
        return json.loads((self.home / name).read_text())

    def close(self):
        (self.home / 'release').write_text('1')           # every fixture ends by itself on this
        left = None
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)         # through its own Popen object, and nothing stronger
            try:
                self.proc.wait(WAIT)
            except subprocess.TimeoutExpired:
                left = self.proc.pid
        if self.master >= 0:
            os.close(self.master)
        assert left is None, f'supervisor {left} did not end; its fixtures end by themselves within a minute'


@pytest.fixture
def rig(tmp_path, monkeypatch):
    try:
        le.login_supervised_command(['/bin/true'])
    except ExecutionRefused as exc:
        pytest.skip(str(exc))
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('CODEX_HOME', str(home / 'codex-home'))
    rig = Rig(home)
    yield rig
    rig.close()


# --- the command and what is refused before anything runs ----------------------------------------------------------

def test_the_command_names_the_caller_as_owner_and_carries_the_argv_unchanged():
    try:
        command = le.login_supervised_command(['/bin/true', 'auth', 'login'])
    except ExecutionRefused as exc:
        pytest.skip(str(exc))
    assert command[1:3] == ['-I', '-c'] and command[3] == le._LOGIN_SUPERVISOR_SOURCE
    assert os.path.isabs(command[0])
    assert command[4:6] == [str(os.getpid()), str(int(stat_fields(os.getpid())[19]))]
    assert command[6:] == ['/bin/true', 'auth', 'login']


@pytest.mark.parametrize('argv', [[], ['true'], ['relative/cli', 'login'], ['/bin/true', 'a\x00b'], ['/bin/true', 3],
                                  '/bin/true', None])
def test_a_command_that_is_not_an_absolute_file_and_plain_arguments_is_refused(argv):
    with pytest.raises(ExecutionRefused):
        le.login_supervised_command(argv)


def test_only_the_main_thread_may_ask_for_the_command():
    caught = []

    def work():
        try:
            le.login_supervised_command(['/bin/true'])
        except ExecutionRefused as exc:
            caught.append(exc)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join(WAIT)
    assert len(caught) == 1


def test_the_module_sends_two_soft_signals_through_a_pidfd_and_nothing_else():
    text = Path(le.__file__).read_text(encoding='utf-8')
    compile(le._LOGIN_SUPERVISOR_SOURCE, '<sign-in supervisor>', 'exec')
    for forbidden in ('SIGKILL', 'SIGSTOP', 'SIGQUIT)', 'killpg', 'os.kill', '.kill(', '.terminate(',
                      'subprocess', 'shell=', 'os.system', 'os.read(0', 'os.read(1', 'sys.stdin', 'input('):
        assert forbidden not in text, forbidden
    assert 'SOFT = (signal.SIGTERM, signal.SIGCONT)' in text
    assert text.count('pidfd_send_signal(') == 1 and 'signal.pidfd_send_signal(fd, number)' in text
    assert text.count('os.read(') == 1                    # the start gate between the two halves of the fork
    assert f'REFUSED = {le.REFUSED}' in le._LOGIN_SUPERVISOR_SOURCE and '@' not in le._LOGIN_SUPERVISOR_SOURCE
    assert text.count('soft(fd)') == 2                    # its definition, and the one call: on a proved process
    assert text.count('os.pidfd_open(') == 1              # only in hold(): no signal goes by a bare process id


# --- which processes may be asked to stop: /proc as a script, no real process ---------------------------------------

ME = 100             # the supervisor's own process id in the scripts below


def supervisor_functions():
    """The supervisor's own functions, from its real text. Nothing runs: its main() is behind a __main__ guard."""
    space = {'__name__': 'sign_in_supervisor_under_test'}
    exec(compile(le._LOGIN_SUPERVISOR_SOURCE, '<sign-in supervisor>', 'exec'), space)
    return space


class ScriptedProc:
    """/proc and pidfds as a script. `rows[pid]` is what each successive read of that process's stat row gives
    (the last one repeats; None is a row that cannot be read). `ended` holds the pids whose pidfd shows the process
    it was opened for has ended. `sent` is every pid whose pidfd was asked to stop, in order."""

    def __init__(self, space, rows, ended=(), unopenable=()):
        self.rows = {pid: list(reads) for pid, reads in rows.items()}
        self.read_count, self.ended, self.unopenable = {}, set(ended), set(unopenable)
        self.sent, self.opened, self.open = [], [], set()
        space.update(listing=lambda: list(self.rows), stat_of=self.stat_of, hold=self.hold, alive=self.alive,
                     soft=self.soft, drop=self.drop)
        self.sweep = space['sweep']

    def stat_of(self, pid):
        reads = self.rows.get(pid)
        if not reads:
            return None
        count = self.read_count.get(pid, 0)
        self.read_count[pid] = count + 1
        return reads[min(count, len(reads) - 1)]

    def hold(self, pid):
        if pid in self.unopenable:
            raise ProcessLookupError(pid)
        assert 1000 + pid not in self.open
        self.opened.append(pid)
        self.open.add(1000 + pid)
        return 1000 + pid

    def alive(self, fd):
        assert fd in self.open
        return fd - 1000 not in self.ended

    def soft(self, fd):
        assert fd in self.open                            # only ever through a descriptor that is held
        self.sent.append(fd - 1000)

    def drop(self, fd):
        self.open.remove(fd)                              # closing one twice, or one never opened, fails the test


def joined_by_parent_id(rows, me=ME):
    """What one pass joined by bare parent ids would call "under me": the hazard the scripts below set up."""
    first = {pid: reads[0] for pid, reads in rows.items() if reads and reads[0] is not None}
    found, grew = set(), True
    while grew:
        grew = False
        for pid, (state, parent, start) in first.items():
            if pid != me and pid not in found and (parent == me or parent in found):
                found.add(pid)
                grew = True
    return found


def test_every_live_process_under_the_supervisor_is_asked_once_per_round_however_deep():
    rows = {ME: [(b'S', 1, 5)], 200: [(b'S', ME, 20)], 250: [(b'S', 200, 25)], 300: [(b'S', 250, 30)],
            310: [(b'Z', ME, 31)],                        # ended, not yet collected: nothing to ask
            400: [(b'S', 1, 40)], 410: [(b'S', 400, 41)]}  # somebody else's
    proc = ScriptedProc(supervisor_functions(), rows)
    asked = {}
    assert proc.sweep(ME, asked) == 3
    assert proc.sent == [200, 250, 300] and asked == {200: 20, 250: 25, 300: 30}
    assert set(proc.opened) == {200, 250, 300} and proc.open == set()     # nothing foreign was even opened
    assert proc.sweep(ME, asked) == 3 and proc.sent == [200, 250, 300]    # the same round: not asked twice
    assert proc.sweep(ME, {}) == 3 and proc.sent == [200, 250, 300] * 2   # a new round asks again
    assert proc.open == set()


def test_a_stale_row_that_names_a_parent_id_since_reused_by_an_owned_process_is_not_joined():
    """300 is somebody else's. Its row was read while its parent, also somebody else's, had process id 250. That
    parent then ended and a process the command started was given 250. One pass now shows 300 "under" an owned
    250. Read again after its pidfd is open, 300's row names the parent it has now."""
    rows = {200: [(b'S', ME, 20)], 250: [(b'S', 200, 25)], 300: [(b'S', 250, 30), (b'S', 1, 30)]}
    assert 300 in joined_by_parent_id(rows)               # the mistake this pass must not make
    proc = ScriptedProc(supervisor_functions(), rows)
    assert proc.sweep(ME, {}) == 2
    assert proc.sent == [200, 250] and proc.open == set()


def test_a_child_of_a_foreign_process_that_reused_a_proved_parents_id_is_not_joined():
    """250 was proved to be under the supervisor, then ended; a foreign process was given 250 and started 300.
    Both reads of 300's row name parent 250. The pidfd held for the proved 250 shows that it has ended, so the 250
    in that row is not known to be it, and 300 is left alone."""
    rows = {200: [(b'S', ME, 20)], 250: [(b'S', 200, 25)], 300: [(b'S', 250, 30)]}
    assert 300 in joined_by_parent_id(rows)
    proc = ScriptedProc(supervisor_functions(), rows, ended={250})
    assert proc.sweep(ME, {}) == 2
    assert 300 not in proc.sent and proc.sent == [200, 250] and proc.open == set()
    # (asking the ended 250 through its own pidfd reaches nothing: the pidfd names that process only)


@pytest.mark.parametrize('second_read', [(b'S', 200, 31), (b'Z', 200, 30), None])
def test_a_candidate_whose_row_changed_or_went_away_is_not_asked(second_read):
    """Another start time (the id was given to another process), ended, or unreadable."""
    rows = {200: [(b'S', ME, 20)], 300: [(b'S', 200, 30), second_read], 350: [(b'S', 300, 35)]}
    proc = ScriptedProc(supervisor_functions(), rows)
    assert proc.sweep(ME, {}) == 1
    assert proc.sent == [200] and proc.open == set()      # and nothing below the unproved one either


def test_a_candidate_that_cannot_be_opened_is_skipped_with_everything_below_it():
    rows = {200: [(b'S', ME, 20)], 300: [(b'S', 200, 30)], 350: [(b'S', 300, 35)], 210: [(b'S', ME, 21)]}
    proc = ScriptedProc(supervisor_functions(), rows, unopenable={300})
    assert proc.sweep(ME, {}) == 2
    assert sorted(proc.sent) == [200, 210] and proc.open == set()


def test_a_failed_ask_still_closes_every_descriptor():
    space = supervisor_functions()
    proc = ScriptedProc(space, {200: [(b'S', ME, 20)], 250: [(b'S', 200, 25)]})

    def refused(fd):
        proc.sent.append(fd - 1000)
        raise PermissionError(fd)

    space['soft'] = refused
    assert proc.sweep(ME, {}) == 2 and proc.sent == [200, 250] and proc.open == set()


# --- the terminal the command gets ---------------------------------------------------------------------------------

def test_the_command_has_the_pty_as_controlling_terminal_and_is_its_foreground_group(rig):
    proc = rig.start('tty')
    assert proc.wait(WAIT) == 0
    seen = json.loads((rig.home / 'seen').read_text())
    assert seen['name'] == rig.tty_name and seen['isatty'] == [True, True, True]
    assert seen['foreground'] is True and seen['group_leader'] is True
    assert seen['session'] == proc.pid and seen['parent'] == proc.pid        # the supervisor leads; the CLI is under it
    assert seen['size'] == [100, 30] and seen['default_signals'] is True and seen['gui'] == 'unset'
    assert b'via-dev-tty' in rig.read(b'via-dev-tty')                        # what it wrote to /dev/tty is on our pty
    assert json.loads((rig.home / 'started').read_text())['args'] == ['tty']


def test_ctrl_c_typed_in_the_terminal_reaches_the_command_and_not_the_supervisor(rig):
    proc = rig.start('interrupt')
    rig.seen('ready')
    os.write(rig.master, b'\x03')
    assert proc.wait(WAIT) == 7                                   # the CLI's own handler ran and chose this status
    assert json.loads((rig.home / 'interrupted').read_text()) is True


def test_ctrl_c_ends_a_command_that_does_not_handle_it(rig):
    proc = rig.start('plain')
    rig.seen('ready')
    os.write(rig.master, b'\x03')
    assert proc.wait(WAIT) == 130


def test_resizing_the_pty_tells_the_command(rig):
    proc = rig.start('resize')
    assert rig.seen('ready') == [100, 30]
    resize(rig.master, 43, 132)
    deadline = time.monotonic() + WAIT
    while not (rig.home / 'size').exists() or json.loads((rig.home / 'size').read_text()) != [132, 43]:
        assert time.monotonic() < deadline and proc.poll() is None
        time.sleep(0.05)
    assert proc.poll() is None                                    # the supervisor is not in the foreground group
    (rig.home / 'release').write_text('1')
    assert proc.wait(WAIT) == 0


# --- ownership -------------------------------------------------------------------------------------------------------

def test_stopping_ends_the_command_and_a_detached_process_it_started(rig):
    proc = rig.start('detach')
    rig.seen('ready')
    cli, detached = rig.seen('started')['pid'], rig.seen('detached')
    assert int(stat_fields(detached)[1]) == proc.pid              # it left its session and is still under the supervisor
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(WAIT) == 143
    assert gone(cli) and gone(detached)                           # the supervisor ended only after collecting both


def test_a_command_that_ends_by_itself_does_not_leave_what_it_started(rig):
    proc = rig.start('detach-exit')
    assert proc.wait(WAIT) == 0                                   # the command's own status, after its leftovers
    assert gone(json.loads((rig.home / 'detached').read_text()))


def test_a_process_that_does_not_stop_is_never_forced_and_stays_owned(rig):
    proc = rig.start('stubborn')
    rig.seen('ready')
    cli = rig.seen('started')['pid']
    proc.send_signal(signal.SIGTERM)
    rig.read(b'did not stop when asked')                          # said once on the terminal after NOTICE_SECONDS
    assert proc.poll() is None and not gone(cli)                  # long past any 200 ms: nothing was forced
    proc.send_signal(signal.SIGTERM)                              # asking again changes nothing for a process that refuses
    time.sleep(0.5)
    assert proc.poll() is None and not gone(cli) and not (rig.home / 'released').exists()
    assert rig.output.count(b'did not stop when asked') == 1
    (rig.home / 'release').write_text('1')                        # the fixture ends by itself
    assert proc.wait(WAIT) == 0
    assert json.loads((rig.home / 'released').read_text()) is True and gone(cli)


def test_the_owner_ending_stops_the_command_and_what_it_started(rig):
    """A stand-in for the desktop process: it starts the supervisor as the real window does, then ends. This test
    keeps the pty's other end, so the only thing the supervisor is told is that its owner is gone."""
    slave = rig.pty()
    helper = ('import os, subprocess, sys\n'
              'from dream.core import login_execution as le\n'
              'slave = int(sys.argv[1])\n'
              'proc = subprocess.Popen(le.login_supervised_command(sys.argv[2:]), stdin=slave, stdout=slave,\n'
              '                        stderr=slave, start_new_session=True, close_fds=True)\n'
              'os.close(slave)\n'
              'sys.stdout.write("%d\\n" % proc.pid)\n'
              'sys.stdout.flush()\n'
              'sys.stdin.read()\n'
              'os._exit(0)\n')
    try:
        owner = subprocess.Popen([sys.executable, '-c', helper, str(slave), str(rig.script), 'detach'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=str(rig.home),
                                 env=dict(rig.env, PYTHONPATH=str(REPO)), pass_fds=(slave,))
    finally:
        os.close(slave)
    try:
        supervisor = int(owner.stdout.readline())
        deadline = time.monotonic() + WAIT
        while not (rig.home / 'ready').exists():
            assert time.monotonic() < deadline and not gone(supervisor)
            time.sleep(0.05)
        cli = json.loads((rig.home / 'started').read_text())['pid']
        detached = json.loads((rig.home / 'detached').read_text())
        assert int(stat_fields(cli)[1]) == supervisor and int(stat_fields(supervisor)[1]) == owner.pid
    finally:
        owner.stdin.close()                                       # the owner ends
        owner.wait(WAIT)
        owner.stdout.close()
    for pid in (cli, detached, supervisor):
        wait_gone(pid)


# --- an owner that is not the one named never gets the command run --------------------------------------------------

def _wrong_start(argv):
    return le._command(argv, os.getpid(), le._start_time(os.getpid()) + 1)


def _another_process(argv):
    other = os.getppid()                          # alive, really there, and not the process that starts the supervisor
    if other <= 0:
        pytest.skip('this test process has no visible parent to name')
    return le._command(argv, other, le._start_time(other))


def _a_process_that_is_gone(argv):
    ended = subprocess.Popen(['/bin/true'])
    ended.wait(WAIT)
    return le._command(argv, ended.pid, 0)


@pytest.mark.parametrize('command', [_wrong_start, _another_process, _a_process_that_is_gone])
def test_a_supervisor_whose_parent_is_not_the_named_owner_runs_nothing(rig, command):
    proc = rig.start('tty', command=command)
    assert proc.wait(WAIT) == le.REFUSED
    assert b'was not started' in rig.read(b'was not started')
    assert not (rig.home / 'started').exists() and not (rig.home / 'seen').exists()


def test_a_supervisor_that_lost_its_owner_before_it_looked_does_not_take_a_new_one(rig):
    """The command names this test as owner, but another process starts it and ends at once. Whatever the
    supervisor then finds as its parent, it is not the named owner: nothing runs."""
    slave = rig.pty()
    between = 'import subprocess, sys\nsubprocess.Popen(sys.argv[1:], start_new_session=True)\n'
    try:
        command = le.login_supervised_command([str(rig.script), 'tty'])
        middle = subprocess.Popen([sys.executable, '-c', between, *command], stdin=slave, stdout=slave,
                                  stderr=slave, cwd=str(rig.home), env=rig.env, close_fds=True)
    finally:
        os.close(slave)
    assert middle.wait(WAIT) == 0
    assert b'was not started' in rig.read(b'was not started')
    time.sleep(0.3)
    assert not (rig.home / 'started').exists() and not (rig.home / 'seen').exists()


# --- the status alone does not say whether the command ran ----------------------------------------------------------

def test_a_command_that_runs_and_ends_with_the_refused_status_is_not_called_unstarted(rig):
    proc = rig.start('exit125')
    assert proc.wait(WAIT) == le.REFUSED == 125                   # the CLI's own status, passed on as any other
    assert json.loads((rig.home / 'started').read_text())['args'] == ['exit125']      # and it did run
    assert b'was not started' not in rig.drain() and b'sign-in supervisor' not in rig.output


# --- a stop, or the owner ending, in the windows before the command is started --------------------------------------
#
# Nothing here is timed. The supervisor's real text is run with a few lines put in at one exact place, so the stop
# (or the owner's end) happens at that place and nowhere else. The places are named by the line of the supervisor
# they sit next to; if that line changes, the test fails rather than quietly testing nothing.

BEFORE_FORK = '    signal.pthread_sigmask(signal.SIG_BLOCK, STOPS)\n'    # stops are held back from this line on
BEFORE_GO = '        os.tcsetpgrp(0, child)\n'                           # the child exists and waits at the gate


def with_lines(anchor, lines, before=False):
    """The supervisor's text with `lines` put directly before or after the one line `anchor`."""
    source = le._LOGIN_SUPERVISOR_SOURCE
    assert source.count(anchor) == 1, anchor
    indent = ' ' * (len(anchor) - len(anchor.lstrip(' ')))
    block = ''.join(indent + line + '\n' for line in lines)
    compile(source.replace(anchor, block + anchor if before else anchor + block), '<changed supervisor>', 'exec')
    return source.replace(anchor, block + anchor if before else anchor + block)


def running(source):
    """A command builder for Rig.start: the real command, with the changed text in place of the real one."""
    def command(argv):
        made = le.login_supervised_command(argv)
        assert made[3] == le._LOGIN_SUPERVISOR_SOURCE
        return made[:3] + [source] + made[4:]
    return command


def reached(home):
    """The line that records, from inside the supervisor, that the place was reached."""
    return 'open(%r, "w").close()' % str(home / 'reached')


def test_the_changed_text_runs_the_command_when_nothing_is_injected(rig):
    """The control for the tests below: both places are on the path of a normal start."""
    source = with_lines(BEFORE_FORK, ['open(%r, "w").close()' % str(rig.home / 'reached-fork')])
    assert source.count(BEFORE_GO) == 1
    source = source.replace(BEFORE_GO, BEFORE_GO + '        open(%r, "w").close()\n' % str(rig.home / 'reached-go'))
    proc = rig.start('tty', command=running(source))
    assert proc.wait(WAIT) == 0
    assert (rig.home / 'reached-fork').exists() and (rig.home / 'reached-go').exists()
    assert (rig.home / 'started').exists() and (rig.home / 'seen').exists()


@pytest.mark.parametrize('anchor, before, held_back', [
    (BEFORE_FORK, True, False),      # while the terminal was being set up: the stop is handled before the look
    (BEFORE_FORK, False, True),      # immediately before the fork: the stop is held back and waiting
    (BEFORE_GO, False, True),        # after the fork, immediately before the go-ahead: the child never execs
])
def test_a_stop_seen_before_the_command_is_started_means_it_never_is(rig, anchor, before, held_back):
    lines = [reached(rig.home),
             'signal.raise_signal(signal.SIGTERM)',
             'open(%r, "w").write(repr([stops[0], signal.SIGTERM in signal.sigpending()]))' % str(rig.home / 'how')]
    proc = rig.start('tty', command=running(with_lines(anchor, lines, before=before)))
    assert proc.wait(WAIT) == le.REFUSED                          # not 143: nothing was there to be stopped
    assert (rig.home / 'reached').exists()
    assert (rig.home / 'how').read_text() == repr([0, True] if held_back else [1, False])
    assert b'was not started' in rig.read(b'was not started')
    assert not (rig.home / 'started').exists() and not (rig.home / 'seen').exists()


OWNER = r'''
import os, subprocess, sys, time
from dream.core import login_execution as le
slave, source, reached = int(sys.argv[1]), sys.argv[2], sys.argv[3]
command = le.login_supervised_command(sys.argv[4:])          # names this process as the owner
with open(source) as handle:
    command[3] = handle.read()
proc = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, start_new_session=True, close_fds=True)
os.close(slave)
sys.stdout.write("%d\n" % proc.pid)
sys.stdout.flush()
end = time.monotonic() + 30
while not os.path.exists(reached) and proc.poll() is None and time.monotonic() < end:
    time.sleep(0.01)
os._exit(0)                                                  # the owner ends exactly when the place is reached
'''


@pytest.mark.parametrize('anchor', [BEFORE_FORK, BEFORE_GO])
@pytest.mark.parametrize('signal_taken_away', [False, True])
def test_the_owner_ending_before_the_command_is_started_means_it_never_is(rig, anchor, signal_taken_away):
    """The supervisor stops at the place, says so, and goes on only when its parent is no longer the owner. With
    `signal_taken_away` the kernel's notice of the owner's end is then removed, so only the look at who the parent
    is can refuse; without it, both that look and the waiting notice can."""
    lines = [reached(rig.home), 'while os.getppid() == owner:', '    time.sleep(0.01)']
    if signal_taken_away:
        lines.append('signal.sigtimedwait([signal.SIGTERM], 5)')
    (rig.home / 'changed-supervisor').write_text(with_lines(anchor, lines), encoding='utf-8')
    slave = rig.pty()
    try:
        owner = subprocess.Popen(
            [sys.executable, '-c', OWNER, str(slave), str(rig.home / 'changed-supervisor'), str(rig.home / 'reached'),
             str(rig.script), 'tty'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, cwd=str(rig.home),
            env=dict(rig.env, PYTHONPATH=str(REPO)), pass_fds=(slave,))
    finally:
        os.close(slave)
    try:
        supervisor = int(owner.stdout.readline())
        assert owner.wait(WAIT) == 0
    finally:
        owner.stdout.close()
        if owner.poll() is None:
            owner.wait(40)                                        # it ends by itself within 30 seconds
    assert (rig.home / 'reached').exists()                        # the owner ended because the place was reached
    wait_gone(supervisor)                                         # after which nothing it started is left either
    assert b'was not started' in rig.read(b'was not started')
    assert not (rig.home / 'started').exists() and not (rig.home / 'seen').exists()
