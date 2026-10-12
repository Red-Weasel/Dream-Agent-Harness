"""The sidebar's Unload model and Load model (the owner, 2026-10-10): switch the local model in the middle of a build
without closing Dream, which would close every terminal. Fixture engines only: no GPU, no model load."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from dream.desktop import engine_controls, startup
from dream.local import machx
from lease_isolation import isolated_load_lock_dir  # noqa: F401  (DREAM-206: the launch lock under tmp_path)


@pytest.fixture(autouse=True)
def isolated_local_load_port(monkeypatch):
    import uuid
    monkeypatch.setattr(machx, 'PORT', 'fixture-' + uuid.uuid4().hex)


@pytest.fixture
def settings():
    return dict(identity='model-key', revision=None, context_limit=32768, max_gpus=2,
                controls=[dict(name='temperature', choices=[]), dict(name='max_tokens', choices=[])],
                selection=dict(ctx=32768, gpus=2, options={'temperature': .7, 'max_tokens': 4096}))


def local_fixture(monkeypatch, tmp_path, settings, *, ready=True):
    """A local model the startup steps check and 'load': the engine is a Mock process."""
    from dream.local import model_presets
    path = tmp_path / 'fixture.gguf'
    path.write_bytes(b'fixture')
    monkeypatch.setattr(startup, 'model_settings', lambda _: settings)
    monkeypatch.setattr(startup, 'launch_preflight', lambda *_: 'fit')
    monkeypatch.setattr(model_presets, 'model_key', lambda _: 'model-key')
    monkeypatch.setattr(model_presets.Presets, 'save', lambda *a, **k: None)
    monkeypatch.setattr(machx, 'list_models_detailed', lambda: [SimpleNamespace(path=path, size_gb=1, name='fixture')])
    monkeypatch.setattr(machx, 'is_serving', lambda: False)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'Fixture-Model-Q8')
    monkeypatch.setattr(machx, 'log_mark', lambda: 0)
    proc = Mock(pid=4242, poll=Mock(return_value=None))
    calls = []

    def serve(*args, **kwargs):
        calls.append(kwargs)
        return proc
    monkeypatch.setattr(machx, 'serve', serve)
    monkeypatch.setattr(machx, 'wait_ready', lambda _: ready)
    request = dict(workspace=str(tmp_path), choice=dict(kind='local', provider='machx', path=str(path)),
                   selection=settings['selection'], identity='model-key', revision=None)
    return request, proc, calls


# --- what the sidebar shows ---------------------------------------------------------------------------------

def test_load_is_greyed_while_a_model_is_loaded_and_unload_while_none_is():
    loaded = engine_controls.buttons('Qwen3.8-35B', busy=None, session_running=True)
    assert (loaded['unload'], loaded['load']) == (True, False)
    assert loaded['text'] == 'Local model: Qwen3.8-35B' and 'first' in loaded['load_tip']
    empty = engine_controls.buttons(None, busy=None, session_running=True)
    assert (empty['unload'], empty['load']) == (False, True)
    no_session = engine_controls.buttons(None, busy=None, session_running=False)
    assert no_session['load'] is False and 'Home' in no_session['load_tip']
    busy = engine_controls.buttons('Qwen3.8-35B', busy='Unloading Qwen3.8-35B…', session_running=True)
    assert (busy['unload'], busy['load'], busy['text']) == (False, False, 'Unloading Qwen3.8-35B…')


def test_the_served_model_is_read_from_the_engine_and_none_when_nothing_answers():
    class Engine(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({'data': [{'id': 'Qwen3.8-27B-Q8_0'}]}).encode()
            self.send_response(200 if self.path == '/v1/models' else 404)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Engine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f'http://127.0.0.1:{server.server_address[1]}'
        assert engine_controls.served_model(base=base) == 'Qwen3.8-27B-Q8_0'
    finally:
        server.shutdown()
        server.server_close()
    assert engine_controls.served_model(timeout=0.5, base=base) is None


def test_commands_and_results_the_window_reads():
    assert engine_controls.unload_command('/py') == ['/py', '-m', 'dream.desktop.startup', 'unload']
    assert engine_controls.load_command('/py', 'r.json', 's.json')[-3:] == ['load', 'r.json', 's.json']
    said = json.dumps({'stopped': True, 'model': 'M', 'message': 'Unloaded M.'})
    assert engine_controls.unload_result(0, 'noise\n' + said + '\n', '') == 'Unloaded M.'
    assert 'exit 1' in engine_controls.unload_result(1, '', 'Traceback: boom')


def test_load_status_is_read_from_the_status_file(tmp_path):
    path = tmp_path / 'status.json'
    assert engine_controls.load_status(path) is None
    startup.publish(path, 'loading', 'Loading fixture…')
    assert engine_controls.load_status(path) == ('loading', 'Loading fixture…')


# --- unload --------------------------------------------------------------------------------------------------

def pid_file(monkeypatch, tmp_path, pid):
    path = tmp_path / 'machx.pid'
    if pid is not None:
        path.write_text(str(pid))
    monkeypatch.setattr(machx, '_pid_file', lambda: path)
    return path


def test_unload_stops_the_engine_dream_started(monkeypatch, tmp_path):
    pid_file(monkeypatch, tmp_path, 4242)
    monkeypatch.setattr(machx, '_alive', lambda pid: pid == 4242)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'Qwen3.8-35B')
    stop = Mock(return_value=True)
    monkeypatch.setattr(machx, 'stop', stop)
    result = startup.unload()
    stop.assert_called_once_with()
    assert result['stopped'] is True and 'Unloaded Qwen3.8-35B' in result['message']


def test_unload_says_so_when_the_engine_does_not_stop(monkeypatch, tmp_path):
    pid_file(monkeypatch, tmp_path, 4242)
    monkeypatch.setattr(machx, '_alive', lambda pid: True)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'Qwen3.8-35B')
    monkeypatch.setattr(machx, 'stop', Mock(return_value=False))
    result = startup.unload()
    assert result['stopped'] is False and 'still running' in result['message'] and 'force-kill' in result['message']


def test_unload_leaves_an_engine_dream_did_not_start_alone(monkeypatch, tmp_path):
    pid_file(monkeypatch, tmp_path, None)
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'started-by-hand')
    stop = Mock()
    monkeypatch.setattr(machx, 'stop', stop)
    result = startup.unload()
    stop.assert_not_called()
    assert result['stopped'] is False and 'ie stop' in result['message']
    monkeypatch.setattr(machx, 'served_model_id', lambda: None)
    assert startup.unload()['message'] == 'No local model is loaded.'


# --- load into an open session -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_starts_an_engine_that_outlives_the_loader_and_frees_the_lock(monkeypatch, tmp_path, settings):
    request, proc, calls = local_fixture(monkeypatch, tmp_path, settings)
    status = tmp_path / 'private' / 'engine-load-status.json'
    status.parent.mkdir()
    assert await startup.load(request, status) == 'Fixture-Model-Q8'
    assert calls == [dict(gpus=2, ctx=32768, options=calls[0]['options'], keep_hot=True)]
    proc.terminate.assert_not_called()
    assert engine_controls.load_status(status) == (
        'ready', 'Fixture-Model-Q8 is loaded. Open terminals switch to it at their next message.')
    marker = json.loads((status.parent / startup.LOADED_MARKER).read_text())
    assert marker == {'pid': 4242, 'model': 'Fixture-Model-Q8'}
    with startup.local_load_lock():
        pass                               # the loader's own copy of the launch lock is gone


@pytest.mark.asyncio
async def test_a_failed_load_stops_its_engine_and_marks_nothing(monkeypatch, tmp_path, settings):
    request, proc, _ = local_fixture(monkeypatch, tmp_path, settings, ready=False)
    status = tmp_path / 'engine-load-status.json'
    with pytest.raises(ValueError, match='did not become ready'):
        await startup.load(request, status)
    proc.terminate.assert_called_once()
    assert not (tmp_path / startup.LOADED_MARKER).exists()


@pytest.mark.asyncio
async def test_load_takes_local_models_only(tmp_path):
    with pytest.raises(ValueError, match='local model'):
        await startup.load(dict(choice=dict(kind='provider', provider='codex')), tmp_path / 's.json')


@pytest.mark.asyncio
async def test_a_session_lets_go_of_the_launch_lock_once_its_engine_is_ready(monkeypatch, tmp_path, settings):
    """Before this change the session held the lock until it ended, so a second engine could never be loaded."""
    request, proc, _ = local_fixture(monkeypatch, tmp_path, settings)
    proc.poll.return_value = 0                              # the engine has exited by the session's end
    seen = {}

    class App:
        def __init__(self, **kwargs):
            seen['model'] = kwargs['model']

        async def run(self):
            # The sidebar's Load model is another process (`startup load`): try the lock from one.
            import os
            import subprocess
            import sys
            code = ("import sys\nfrom dream.local import machx\nfrom dream.desktop.startup import local_load_lock\n"
                    "machx.PORT = sys.argv[1]\ntry:\n    with local_load_lock():\n        print('free')\n"
                    "except ValueError as exc:\n    print(exc)\n")
            done = subprocess.run([sys.executable, '-c', code, str(machx.PORT)], capture_output=True, text=True,
                                  timeout=60, env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])))
            seen['lock'] = done.stdout.strip() or done.stderr[-300:]

    import dream.tui.app
    monkeypatch.setattr(dream.tui.app, 'App', App)
    await startup.run(request, tmp_path / 'status.json')
    assert seen == {'model': 'Fixture-Model-Q8', 'lock': 'free'}


@pytest.mark.asyncio
async def test_the_session_stops_a_model_the_sidebar_loaded_when_it_ends(monkeypatch, tmp_path):
    monkeypatch.setattr(machx, 'served_model_id', lambda: 'existing')
    monkeypatch.setattr(machx, 'served_model_path', lambda: None)
    status = tmp_path / 'private' / 'startup-status.json'
    status.parent.mkdir()
    (status.parent / startup.LOADED_MARKER).write_text(json.dumps({'pid': 777, 'model': 'loaded-mid-session'}))
    pid_file(monkeypatch, tmp_path, 777)
    monkeypatch.setattr(machx, '_alive', lambda pid: pid == 777)
    stop = Mock(return_value=True)
    monkeypatch.setattr(machx, 'stop', stop)

    class App:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            pass

    import dream.tui.app
    monkeypatch.setattr(dream.tui.app, 'App', App)
    await startup.run(dict(workspace=str(tmp_path), choice=dict(kind='attach', provider='machx', model='existing')),
                      status)
    stop.assert_called_once_with()
    assert not (status.parent / startup.LOADED_MARKER).exists()


def test_a_stale_marker_never_stops_another_engine(monkeypatch, tmp_path):
    status = tmp_path / 'startup-status.json'
    (tmp_path / startup.LOADED_MARKER).write_text(json.dumps({'pid': 777, 'model': 'gone'}))
    pid_file(monkeypatch, tmp_path, 888)                    # the engine running now is another one
    monkeypatch.setattr(machx, '_alive', lambda pid: True)
    stop = Mock()
    monkeypatch.setattr(machx, 'stop', stop)
    startup._stop_loaded(status)
    stop.assert_not_called()


# --- open terminals follow the model ------------------------------------------------------------------------

def session(monkeypatch, served, *, current='Qwen3.8-35B', fail=None):
    from dream.core.backends.openai_compat import OpenAICompatBackend
    from dream.tui.app import App
    backend = object.__new__(OpenAICompatBackend)
    backend.provider = SimpleNamespace(key='machx')
    backend.n_ctx = 262144

    async def probe():
        return 131072
    backend._served_models = AsyncMock(return_value={served} if served else set())
    backend._probe_n_ctx = probe
    backend._refresh_task_schema = Mock()
    said = []

    class Engine:
        model = current

        async def set_model(self, name):
            if fail:
                raise ValueError(fail)
            self.model = name
    engine = Engine()
    engine.backend = backend
    app = object.__new__(App)
    app.engine, app.model = engine, current
    app.renderer = SimpleNamespace(system=lambda text: said.append(('system', text)),
                                   error=lambda text: said.append(('error', text)))
    monkeypatch.setattr(machx, 'served_model_id', lambda: served)
    return app, backend, said


@pytest.mark.asyncio
async def test_an_open_terminal_switches_to_the_newly_loaded_model(monkeypatch):
    app, backend, said = session(monkeypatch, 'Qwen3.8-27B-Q8_0')
    assert await app._follow_local_model() is True
    assert app.engine.model == app.model == 'Qwen3.8-27B-Q8_0'
    assert backend.n_ctx == 131072                     # the new model's window, read again
    backend._refresh_task_schema.assert_called_once()
    assert said == [('system', 'local model changed: Qwen3.8-35B → Qwen3.8-27B-Q8_0')]


@pytest.mark.asyncio
async def test_the_same_model_changes_nothing(monkeypatch):
    app, backend, said = session(monkeypatch, 'Qwen3.8-35B')
    assert await app._follow_local_model() is True
    assert said == [] and backend.n_ctx == 262144


@pytest.mark.asyncio
async def test_with_no_model_loaded_the_turn_is_not_sent_and_says_why(monkeypatch):
    app, _, said = session(monkeypatch, None)
    assert await app._follow_local_model() is False
    assert said == [('error', 'No local model is loaded. Use Load model in the sidebar, then send this again.')]


@pytest.mark.asyncio
async def test_a_session_that_cannot_switch_says_so(monkeypatch):
    app, _, said = session(monkeypatch, 'Other', fail='Model settings require Council handoff')
    assert await app._follow_local_model() is False
    assert said[0][0] == 'error' and 'could not switch' in said[0][1] and app.model == 'Qwen3.8-35B'


@pytest.mark.asyncio
async def test_hosted_sessions_are_never_checked(monkeypatch):
    from dream.tui.app import App
    app = object.__new__(App)
    app.engine = SimpleNamespace(backend=SimpleNamespace(provider=SimpleNamespace(key='anthropic')), model='opus')
    monkeypatch.setattr(machx, 'served_model_id', Mock(side_effect=AssertionError('must not be asked')))
    assert await app._follow_local_model() is True


@pytest.mark.asyncio
async def test_explicit_model_stays_selected_while_still_served(monkeypatch):
    app, backend, said = session(monkeypatch, 'Model-A', current='Model-B')
    backend._served_models = AsyncMock(return_value={'Model-A', 'Model-B'})
    assert await app._follow_local_model() is True
    assert app.engine.model == app.model == 'Model-B'
    assert backend.n_ctx == 262144 and said == []
    backend._refresh_task_schema.assert_not_called()


@pytest.mark.asyncio
async def test_marker_write_failure_stops_the_new_engine(monkeypatch, tmp_path, settings):
    request, proc, _ = local_fixture(monkeypatch, tmp_path, settings)
    status = tmp_path / 'status.json'
    original = Path.write_text
    failure = OSError('fixture: marker write refused')

    def write(path, *args, **kwargs):
        if path.name == startup.LOADED_MARKER:
            raise failure
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', write)
    stop = AsyncMock()
    monkeypatch.setattr(startup, '_stop_child', stop)
    with pytest.raises(OSError, match='marker write refused'):
        await startup.load(request, status)
    stop.assert_awaited_once_with(proc, status, failure)
    assert not (tmp_path / startup.LOADED_MARKER).exists()
