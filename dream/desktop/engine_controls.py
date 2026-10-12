"""The sidebar's Unload model and Load model (the owner, 2026-10-10): switch the local model in the middle of a build
without closing Dream, which would close every terminal.

What the two buttons show and run. No GTK here, so the window's decisions are tested on their own; the window
draws them. The work itself runs in Dream's Python through `dream.desktop.startup unload` and `load`.
"""
from __future__ import annotations

import json
import os
from urllib.request import ProxyHandler, Request, build_opener

HOST = os.environ.get('DREAM_MACHX_HOST', '127.0.0.1')
PORT = int(os.environ.get('DREAM_MACHX_PORT', '11435'))
LOAD_REQUEST = 'engine-load-request.json'
LOAD_STATUS = 'engine-load-status.json'


def served_model(timeout: float = 2.0, *, base: str | None = None) -> str | None:
    """The model the local engine serves now, or None when nothing answers. Read as `machx.served_model_id` reads
    it (`/v1/models`): one model, or the first of several."""
    url = (base or f'http://{HOST}:{PORT}') + '/v1/models'
    try:
        with build_opener(ProxyHandler({})).open(Request(url), timeout=timeout) as reply:
            data = json.loads(reply.read(65536))
    except Exception:
        return None
    models = [m.get('id') for m in (data.get('data') or []) if isinstance(m, dict) and isinstance(m.get('id'), str)]
    return models[0] if models else None


def buttons(model: str | None, *, busy: str | None, session_running: bool) -> dict:
    """What the sidebar shows: the line about the local model, and which button can be pressed.

    Unload model needs a loaded model. Load model needs none loaded and an open session to switch: with no
    session, Home's own selector loads a model and starts a conversation in one step."""
    if busy:
        return dict(text=busy, unload=False, load=False,
                    unload_tip='Wait for the current step to finish.', load_tip='Wait for the current step to finish.')
    if model:
        return dict(text=f'Local model: {model}', unload=True, load=False,
                    unload_tip=f'Unload {model} and free its GPU memory. Open terminals stay open.',
                    load_tip='Unload the current model first.')
    return dict(text='No local model loaded', unload=False, load=session_running,
                unload_tip='No local model is loaded.',
                load_tip=('Choose a local model to load. Open terminals switch to it at their next message.'
                          if session_running else 'Start a conversation from Home to load a model.'))


def unload_command(python: str) -> list[str]:
    return [python, '-m', 'dream.desktop.startup', 'unload']


def load_command(python: str, request_path: str, status_path: str) -> list[str]:
    return [python, '-m', 'dream.desktop.startup', 'load', request_path, status_path]


def unload_result(returncode: int, stdout: str, stderr: str) -> str:
    """The status line after an unload: the action's own words, or why it could not say."""
    try:
        data = json.loads(stdout.strip().splitlines()[-1])
        if isinstance(data, dict) and isinstance(data.get('message'), str):
            return data['message']
        if isinstance(data, dict) and isinstance(data.get('error'), str):
            return 'Unload failed: ' + data['error']
    except (ValueError, IndexError):
        pass
    tail = (stderr or stdout).strip()[-400:]
    return f'Unload did not report a result (exit {returncode}).' + (f' {tail}' if tail else '')


def load_status(path) -> tuple[str, str] | None:
    """(state, message) from the load action's status file, or None before it writes one."""
    try:
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)
        return str(data['state']), str(data['message'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
