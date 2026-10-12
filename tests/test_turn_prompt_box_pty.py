"""End to end, in a real pseudo-terminal: the real Dream TUI on the real local-model backend, a controlled model
server, no GPU (the owner, 2026-10-11: [feedback]).

1. A typed prompt is read twice by the optimizer; its rewrite shows with Send and Edit; S sends the rewrite.
2. While that turn works, the prompt box stays at the bottom between drawn lines (never \\u2500 escapes). A message
   typed there goes into the running turn: the model's next request carries it.
3. A /command typed during the turn waits (shown as queued) and runs when the turn ends; it never reaches the model."""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import threading
import time
import fcntl
import termios
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pty
import re

from test_deepsleep_direct_links_live import _text
from test_pty_e2e import _drain, _wait_for

DRIVER = Path(__file__).with_name("pty_turn_driver.py")
REWRITTEN = "REWRITTEN-TASK: read the notes, then stop"


def _screen(buf) -> str:
    """What the terminal shows as text: the colour and cursor codes taken out."""
    return re.sub(r'\x1b\][^\x07]*\x07|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[()][A-Z0-9]|\x1b[=>78]', '',
                  bytes(buf).decode('utf-8', 'replace'))


def _shown(master, buf, text, timeout, state) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if text in _screen(buf):
            return True
        _drain(master, buf, 0.3, state)
    return text in _screen(buf)


class TurnModel(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        data = ({'object': 'list', 'data': [{'id': 'controlled-model', 'object': 'model'}]} if self.path.startswith('/v1/models')
                else {'total_slots': 1, 'slot_ctx': 32768, 'prompt_cache_slots': 1, 'lanes': 1, 'lanes_active': 0,
                      'inflight': 0, 'queued': 0, 'vision': {'ready': False, 'reason': 'controlled text fixture'}})
        self._send(json.dumps(data).encode(), 'application/json')

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(payload)
        messages = payload.get('messages', [])
        system = ' '.join(_text(m) for m in messages if m.get('role') == 'system')
        users = [_text(m) for m in messages if m.get('role') == 'user']
        tools = [t.get('function', {}).get('name', '') for t in payload.get('tools') or []]
        call = None
        if 'list what would force the agent to guess' in system:
            text = json.dumps({'clear': False, 'findings': ['no scope'], 'situation': 'ok', 'note': 'vague'})
        elif 'A first review of the prompt is given' in system:
            text = json.dumps({'rewrite': True, 'problems': ['no scope'], 'rewritten': REWRITTEN})
        elif any(m.get('role') == 'tool' for m in messages):
            text = 'Done.'
        elif users and REWRITTEN in users[-1]:
            self.server.main_seen.set()
            time.sleep(6)   # the turn works; the owner types meanwhile
            name = next(n for n in tools if n.endswith('read_file'))
            call = {'index': 0, 'id': 'call_notes', 'type': 'function',
                    'function': {'name': name, 'arguments': json.dumps({'path': 'notes.txt'})}}
            text = None
        else:
            text = 'Okay.'
        delta = {'role': 'assistant', 'tool_calls': [call]} if call else {'role': 'assistant', 'content': text}
        chunks = [dict(id='fixture', object='chat.completion.chunk', model='controlled-model',
                       choices=[{'index': 0, 'delta': delta, 'finish_reason': None}]),
                  dict(id='fixture', object='chat.completion.chunk', model='controlled-model',
                       choices=[{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if call else 'stop'}],
                       usage={'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13})]
        self._send(''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks).encode() + b'data: [DONE]\n\n',
                   'text/event-stream')

    def _send(self, raw, kind):
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_optimizer_send_then_typing_during_the_turn_in_a_real_terminal(tmp_path):
    http = ThreadingHTTPServer(('127.0.0.1', 0), TurnModel)
    http.requests, http.main_seen = [], threading.Event()
    threading.Thread(target=http.serve_forever, daemon=True).start()
    (tmp_path / 'notes.txt').write_text('fixture notes')
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    env = {**os.environ, "DREAM_ROOT": str(tmp_path), "TERM": "xterm-256color", "DREAM_GUI": "0",
           "DREAM_MONITOR": "0", "DREAM_SEMANTIC_MEMORY": "0", "DREAM_RERANK": "0",
           "DREAM_DB": str(tmp_path / "data" / "dream.db"),
           "DREAM_LIBRARY_DB": str(tmp_path / "data" / "library" / "library.db"),
           "DREAM_EXTENSION_SETTINGS": str(tmp_path / "extensions.json"),
           "DREAM_MCP_CONFIG": str(tmp_path / "mcp.json"),
           "DREAM_PLUGINS_DIR": str(tmp_path / "plugins"),
           "DREAM_FAKE_MODEL_URL": f"http://127.0.0.1:{http.server_port}/v1",
           "DREAM_PROMPT_OPTIMIZER": "1"}
    env.pop("DREAM_CONSOLIDATE", None)
    proc = subprocess.Popen([sys.executable, str(DRIVER)], stdin=slave, stdout=slave, stderr=slave,
                            env=env, close_fds=True)
    os.close(slave)
    buf, state = bytearray(), {"cpr_seen_at": 0}
    try:
        assert _shown(master, buf, "[ Optimizer: on ]  [ Skip next: off ]", 60, state), _screen(buf)[-2000:]
        os.write(master, b"tidy the notes please\r")
        assert _wait_for(master, buf, b"Rewritten prompt", 30, state), buf[-2000:]
        assert _wait_for(master, buf, b"[S] Send", 10, state), buf[-2000:]
        os.write(master, b"s")
        end = time.time() + 30
        while not http.main_seen.is_set() and time.time() < end:
            _drain(master, buf, 0.3, state)
        assert http.main_seen.is_set(), buf[-2000:]
        mark = len(buf)
        assert _wait_for(master, buf, b"type here: Enter sends it to the agent now", 5, state), buf[-2000:]
        os.write(master, b"ALSO-STEER check the tests\r")
        assert _wait_for(master, buf, b"sent into this turn", 10, state), buf[-2000:]
        os.write(master, b"/optimizer off\r")
        assert _shown(master, buf, "queued › /optimizer off", 10, state), _screen(buf)[-2000:]
        working = _screen(buf[mark:])
        assert "─" * 60 in working and "\\u2500" not in _screen(buf)      # drawn lines, never escapes
        assert _wait_for(master, buf, b"Done.", 30, state), buf[-2000:]
        assert _wait_for(master, buf, b"typed during the last turn", 15, state), buf[-2000:]
        assert _wait_for(master, buf, b"prompt optimizer off", 15, state), buf[-2000:]
        os.write(master, b"\x04")
        _drain(master, buf, 2.0, state)
        if b"commit this session" in buf:
            os.write(master, b"n\n")
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        os.close(master)
        http.shutdown()
        http.server_close()

    def users(request):
        return [_text(m) for m in request.get('messages', []) if m.get('role') == 'user']
    main = [r for r in http.requests if any(REWRITTEN in u for u in users(r))]
    assert main, 'the rewrite was never sent as the prompt'
    def system(request):
        return ' '.join(_text(m) for m in request.get('messages', []) if m.get('role') == 'system')
    reads = [r for r in http.requests if 'list what would force the agent to guess' in system(r)
             or 'A first review of the prompt is given' in system(r)]
    assert len(reads) == 2 and all(r.get('reasoning_effort') == 'low' for r in reads)   # the reads ask for low effort
    assert all(r.get('reasoning_effort') != 'low' for r in main)                       # the turn keeps its own
    steered = [r for r in main if any('ALSO-STEER check the tests' in u for u in users(r))]
    assert steered, 'the message typed during the turn never reached the model'
    assert not any('/optimizer off' in u for r in http.requests for u in users(r))   # the command ran locally
