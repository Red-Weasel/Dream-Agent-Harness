"""Actual Workflow input and approval reads through an isolated, model-free PTY."""
import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream.tui.app import _SHIFT_ENTER_SEQUENCE


DRIVER = r'''
import asyncio, json, os, socket
from pathlib import Path
from types import SimpleNamespace
from prompt_toolkit import PromptSession
from dream.tui.app import App

def forbidden(*args, **kwargs):
    raise AssertionError("fixture attempted a provider connection")
socket.socket.connect = socket.socket.connect_ex = forbidden

report = Path(os.environ['DREAM_ROOT']) / 'input-report.json'
state = {'prompts': []}
def save():
    pending = report.with_suffix('.pending')
    pending.write_text(json.dumps(state))
    pending.replace(report)

async def main():
    app = App(provider='machx', model='fixture', workspace=os.environ['DREAM_ROOT'], gui=False)
    app.engine = SimpleNamespace(model='fixture', store=object(), _moe=None)
    app._status_data = lambda: dict(mode_label='ask', mode_color='#f1a76a', ws='fixture', effort='default')
    save()
    for i in range(6):
        state['prompts'].append(await app._next_input())
        if state['prompts'][-1].startswith('/council'):
            await app._command(state['prompts'][-1])
        save()
    state['phase'] = 'ordinary'; save()
    state['ordinary'] = await PromptSession().prompt_async('ordinary > ')
    state['phase'] = 'permission'; save()
    state['permission'] = await app._read_answer('PERMISSION [y/N] > ')
    save()

asyncio.run(main())
'''


def test_workflow_modified_enter_paste_navigation_and_approval_boundary(tmp_path):
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 90, 0, 0))
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    env = {**os.environ, 'DREAM_ROOT': str(runtime), 'DREAM_GUI': '0',
           'DREAM_MONITOR': '0', 'DREAM_SEMANTIC_MEMORY': '0', 'DREAM_RERANK': '0',
           'DREAM_DB': str(runtime / 'data/dream.db'),
           'DREAM_LIBRARY_DB': str(runtime / 'data/library/library.db'),
           'DREAM_EXTENSION_SETTINGS': str(runtime / 'extensions.json'),
           'DREAM_MCP_CONFIG': str(runtime / 'mcp.json'),
           'DREAM_PLUGINS_DIR': str(runtime / 'plugins'), 'TERM': 'xterm-256color'}
    child = subprocess.Popen([sys.executable, '-c', DRIVER], env=env, stdin=slave,
                             stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    output = bytearray()
    report = runtime / 'input-report.json'

    def drain(seconds=.15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], .02)
            if not ready:
                continue
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            output.extend(data)
            for _ in range(data.count(b'\x1b[6n')):
                os.write(master, b'\x1b[1;1R')

    def state():
        return json.loads(report.read_text()) if report.exists() else {'prompts': []}

    def wait(predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                (tmp_path / 'pty-output.txt').write_bytes(output)
                raise AssertionError((state(), output.decode(errors='replace')))
            drain()

    def send(data):
        os.write(master, data.encode())
        drain()

    def assert_pending(count):
        drain(.25)
        assert len(state()['prompts']) == count
        assert child.poll() is None

    try:
        wait(lambda: b'dream-prompt;0' in output)
        send('first' + _SHIFT_ENTER_SEQUENCE + 'second')
        assert_pending(0)
        # Vertical navigation edits the first line, then returns to the second.
        send('\x1b[A\x1b[HX\x1b[B\x1b[F!\r')
        wait(lambda: len(state()['prompts']) == 1)
        assert state()['prompts'][0] == 'Xfirst\nsecond!'

        send('alt\x1b\rline')
        assert_pending(1)
        send('\r')
        wait(lambda: len(state()['prompts']) == 2)
        assert state()['prompts'][1] == 'alt\nline'

        send('\x1b[200~pasteA\npasteB\r\npasteC\x1b[201~')
        assert_pending(2)
        send('\r')
        wait(lambda: len(state()['prompts']) == 3)
        assert state()['prompts'][2] == 'pasteA\npasteB\npasteC'

        send('normal\r')
        wait(lambda: len(state()['prompts']) == 4)
        send('/help\r')
        wait(lambda: len(state()['prompts']) == 5)
        send('/council ask the board\r')
        wait(lambda: state().get('phase') == 'ordinary')
        assert state()['prompts'][3:] == ['normal', '/help', '/council ask the board']
        assert b'No request was sent' in output

        # Global parsing consumes the explicit sequence, but other PTK prompts
        # retain their original bindings: it neither inserts nor accepts input.
        send('a' + _SHIFT_ENTER_SEQUENCE + 'b')
        assert 'ordinary' not in state()
        send('\r')
        wait(lambda: state().get('phase') == 'permission')
        assert state()['ordinary'] == 'ab'
        wait(lambda: b'PERMISSION [y/N]' in output)
        send('y' + _SHIFT_ENTER_SEQUENCE)
        drain(.25)
        assert 'permission' not in state() and child.poll() is None
        send('\x15n\r')  # discard the incomplete approval line, then decline
        wait(lambda: 'permission' in state())
        assert state()['permission'].strip() == 'n'
        wait(lambda: child.poll() is not None)
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)


@pytest.mark.asyncio
@pytest.mark.parametrize('configuration', [None, SimpleNamespace(advisors=[])])
async def test_council_without_advisors_explains_connections_without_dispatch(configuration, tmp_path):
    from dream.tui.app import App

    app = App(provider='machx', model='fixture', workspace=str(tmp_path), gui=False)
    app.engine = SimpleNamespace(_moe=configuration, store=object())
    output = StringIO()
    app.renderer.console = Console(file=output, width=180, color_system=None)

    def forbidden(*args, **kwargs):
        raise AssertionError('Council without advisors attempted dispatch')

    app._consult_council = app._run_turn = forbidden
    await app._command('/council ask the board')
    await app._command('/council ask the board again')
    message = output.getvalue()
    assert message.count('No request was sent.') == 2
    assert message.count('use the Message Board.') == 2
