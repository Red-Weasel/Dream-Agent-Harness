"""Desktop discovery, entry points, and Studio lifecycle without an engine."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import uvicorn

from dream import config
from dream.gui.bus import EventBus
from dream.gui.desktop_bridge import DesktopDiscovery
from dream.gui.server import StudioServer


@pytest.fixture(autouse=True)
def private_discovery(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    monkeypatch.setenv("DREAM_DESKTOP_SESSION_FILE", str(path))
    return path


def test_discovery_is_atomic_private_and_clears_only_its_own_record(private_discovery, monkeypatch):
    first, second = DesktopDiscovery(), DesktopDiscovery()
    first.publish("http://127.0.0.1:1234/?token=first")
    old = json.loads(private_discovery.read_text())
    replace = os.replace

    def checked_replace(source, target):
        assert json.loads(Path(source).read_text())["url"].endswith("token=second")
        assert json.loads(Path(target).read_text()) == old
        assert Path(source).stat().st_mode & 0o777 == 0o600
        replace(source, target)

    monkeypatch.setattr(os, "replace", checked_replace)
    second.publish("http://127.0.0.1:1235/?token=second")
    first.clear()  # same PID, but a newer server owns the record
    assert json.loads(private_discovery.read_text()) == {
        "url": "http://127.0.0.1:1235/?token=second", "pid": os.getpid(),
    }
    assert private_discovery.stat().st_mode & 0o777 == 0o600
    assert not list(private_discovery.parent.glob(".session.json.*"))
    second.clear()
    second.clear()
    assert not private_discovery.exists()


def test_discovery_is_opt_in(private_discovery, monkeypatch):
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE")
    bridge = DesktopDiscovery()
    bridge.publish("http://127.0.0.1:1234/?token=t")
    bridge.clear()
    assert not private_discovery.exists()


def test_failed_publication_keeps_existing_record_and_removes_temporary(private_discovery, monkeypatch):
    private_discovery.write_text('{"pid": 42}')
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("rename refused")))
    bridge = DesktopDiscovery()
    with pytest.raises(OSError, match="rename refused"):
        bridge.publish("http://127.0.0.1:1234/?token=t")
    bridge.clear()
    assert private_discovery.read_text() == '{"pid": 42}'
    assert not list(private_discovery.parent.glob(".session.json.*"))


@pytest.mark.asyncio
async def test_real_readiness_publishes_reachable_url_and_preserves_signals(private_discovery):
    previous = signal.getsignal(signal.SIGINT)
    srv = StudioServer(EventBus(), session={"session_id": "one", "model": "scripted"})
    assert not private_discovery.exists()
    try:
        url = await srv.start()
        assert srv.ready and srv.port != 0
        assert json.loads(private_discovery.read_text()) == {"url": url, "pid": os.getpid()}
        assert signal.getsignal(signal.SIGINT) is previous
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f"http://127.0.0.1:{srv.port}/api/desktop",
                                        headers={"X-Dream-Token": srv.token})
        assert response.status_code == 200
        assert response.json()["ready"] is True
        assert response.json()["session"]["session_id"] == "one"
        assert response.json()["clients"] == 0
    finally:
        await srv.stop()
    assert not srv.ready and not private_discovery.exists()
    await srv.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "exception", "exit", "returned", "no_socket"])
async def test_startup_failure_is_bounded_observed_and_never_published(failure, private_discovery, monkeypatch):
    finished = asyncio.Event()

    class Server:
        started = False
        servers = []

        def __init__(self, config):
            pass

        async def serve(self):
            try:
                if failure == "exception":
                    raise OSError("bind failed")
                if failure == "exit":
                    raise SystemExit(1)
                if failure == "returned":
                    return
                self.started = failure == "no_socket"
                await asyncio.Event().wait()
            finally:
                finished.set()

    monkeypatch.setattr(uvicorn, "Server", Server)
    srv = StudioServer(EventBus())
    expected = TimeoutError if failure in {"timeout", "no_socket"} else (OSError, RuntimeError)
    with pytest.raises(expected):
        await asyncio.wait_for(srv.start(timeout=0.03), 1)
    assert finished.is_set()
    assert not srv.ready and srv._task is None
    assert srv._error
    assert not private_discovery.exists()


@pytest.mark.asyncio
async def test_late_server_exception_invalidates_discovery(private_discovery, monkeypatch):
    fail = asyncio.Event()

    class Server:
        started = False
        servers = [SimpleNamespace(sockets=[SimpleNamespace(getsockname=lambda: ("127.0.0.1", 1234))],
                                   close=Mock())]

        def __init__(self, config):
            pass

        async def serve(self):
            self.started = True
            await fail.wait()
            raise OSError("server lost")

    monkeypatch.setattr(uvicorn, "Server", Server)
    srv = StudioServer(EventBus())
    await srv.start()
    fail.set()
    with pytest.raises(OSError, match="server lost"):
        await srv._task
    assert not srv.ready and not private_discovery.exists()
    assert "server lost" in srv._error
    await srv.stop()


def test_desktop_dispatch_precedes_engine_parsing(monkeypatch):
    from dream.__main__ import main

    launcher = ModuleType("dream.desktop.launcher")
    launcher.launch = Mock(return_value=7)
    monkeypatch.setitem(sys.modules, "dream.desktop.launcher", launcher)
    monkeypatch.setattr(sys, "argv", ["dream", "desktop", "--native-option", "value"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 7
    launcher.launch.assert_called_once_with(["--native-option", "value"])


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    from dream.tui import app as app_mod

    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    monkeypatch.setattr(app_mod.checkpoints, "default_store", Mock(return_value=None))
    monkeypatch.setattr(app_mod, "PromptSession", Mock())
    monkeypatch.setattr(app_mod, "Renderer", Mock())
    return lambda **kw: app_mod.App(workspace=tmp_path, **kw)


@pytest.mark.parametrize("configured,explicit,expected", [(True, None, True), (False, None, False),
                                                         (True, False, False), (False, True, True)])
def test_app_inherits_gui_unless_explicitly_overridden(app_factory, monkeypatch, configured, explicit, expected):
    monkeypatch.setattr(config, "GUI", configured)
    assert app_factory(gui=explicit).gui_enabled is expected


@pytest.mark.asyncio
async def test_session_metadata_follows_the_current_engine_without_restarting_studio(app_factory):
    app = app_factory(gui=True)
    app.engine = SimpleNamespace(session_id="first", model="old", effort="low")
    srv = StudioServer(EventBus(), session=app._studio_session_info)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://test") as client:
        headers = {"X-Dream-Token": srv.token}
        assert (await client.get("/api/desktop", headers=headers)).json()["session"]["session_id"] == "first"
        app.engine = SimpleNamespace(session_id="next", model="new", effort="high")
        metadata = (await client.get("/api/desktop", headers=headers)).json()["session"]
        assert metadata["session_id"] == "next" and metadata["model"] == "new"
        assert metadata["reasoning_effort"] == "high"
        assert (await client.get("/api/session", headers=headers)).json() == metadata


def test_app_funnel_retains_show_without_a_connected_browser(app_factory):
    from dream.core.backends.base import Event

    app = app_factory(gui=True)
    app.studio = StudioServer(app.bus)
    app._render_event(Event("studio", {"op": "show", "path": "x.html", "content": "latest"}))
    assert app.bus.subscriber_count == 0
    assert app.studio._last_show["data"]["content"] == "latest"


@pytest.mark.asyncio
async def test_tool_and_app_funnel_count_each_show_once(app_factory, monkeypatch, tmp_path):
    from dream.tools import context, studio as studio_tools

    app = app_factory(gui=True)
    app.studio = StudioServer(app.bus)
    app.studio._ready = True
    app.studio._task = SimpleNamespace(done=lambda: False)
    monkeypatch.setattr(context, "_STUDIO", app.studio)
    monkeypatch.setattr(context, "_CTX", context.ToolContext(
        store=None, working=None, browser=None, session_id="test", workspace=tmp_path, emit=app._render_event))
    monkeypatch.setattr(studio_tools, "_load", AsyncMock(return_value=[]))
    (tmp_path / "show.html").write_text("<p>show</p>")
    await studio_tools.show_to_user.handler({"path": "show.html"})
    assert app.studio._show_sequence == 1
    await studio_tools.done.handler({"path": "show.html"})
    assert app.studio._show_sequence == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "codex", "machx"])
async def test_shared_picker_and_local_tail_propagates_gui(monkeypatch, provider):
    from dream.local import launcher
    from dream.tui import app as app_mod

    monkeypatch.setattr(config, "GUI", True)
    monkeypatch.setattr(launcher, "_pick_workspace", AsyncMock(return_value=None))
    monkeypatch.setattr(launcher.searxng, "ensure_up", AsyncMock(return_value=False))
    factory = Mock(return_value=SimpleNamespace(run=AsyncMock()))
    monkeypatch.setattr(app_mod, "App", factory)
    await launcher._run_harness(Mock(), provider=provider, model=None,
                                consolidate_on_exit=False, keep_hot=True)
    assert factory.call_args.kwargs["gui"] is True
    factory.return_value.run.assert_awaited_once()


def test_local_gui_flag_reaches_the_shared_launcher(monkeypatch):
    from dream.__main__ import main
    from dream.local import launcher

    monkeypatch.setattr(config, "GUI", False)
    received = []

    async def run_local(**kw):
        received.append(config.GUI)

    monkeypatch.setattr(launcher, "run_local", run_local)
    monkeypatch.setattr(sys, "argv", ["dream", "local", "--gui"])
    main()
    assert received == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("browser_result", [False, OSError("no browser")])
async def test_browser_launch_failure_is_reported_without_losing_studio(app_factory, monkeypatch, browser_result):
    import webbrowser
    from dream.gui import server
    from dream.tools import context

    app = app_factory(gui=True)
    app.engine = SimpleNamespace(session_id="s", model="scripted", effort=None)
    fake = SimpleNamespace(start=AsyncMock(return_value="http://127.0.0.1:1234"), stop=AsyncMock())
    monkeypatch.setattr(server, "StudioServer", Mock(return_value=fake))
    monkeypatch.setattr(config, "GUI_OPEN", True)
    monkeypatch.setattr(context, "_STUDIO", None)
    opener = Mock(side_effect=browser_result) if isinstance(browser_result, Exception) else Mock(return_value=browser_result)
    monkeypatch.setattr(webbrowser, "open", opener)
    await app._start_studio()
    assert app.studio is fake and context.studio() is fake
    messages = " ".join(str(call) for call in app.renderer.system.call_args_list)
    assert "browser" in messages and "Open the URL above" in messages


@pytest.mark.asyncio
async def test_failed_studio_start_clears_context_and_cleans_up(app_factory, monkeypatch):
    from dream.gui import server
    from dream.tools import context

    app = app_factory(gui=True)
    app.engine = SimpleNamespace(session_id="s", model="scripted", effort=None)
    fake = SimpleNamespace(start=AsyncMock(side_effect=TimeoutError("not ready")), stop=AsyncMock())
    monkeypatch.setattr(server, "StudioServer", Mock(return_value=fake))
    monkeypatch.setattr(context, "_STUDIO", object())
    await app._start_studio()
    assert app.studio is None and context.studio() is None
    fake.stop.assert_awaited_once()
    assert "not ready" in str(app.renderer.error.call_args)


@pytest.mark.asyncio
async def test_native_protocol_discovers_direct_child_and_quits_through_prompt(private_discovery, tmp_path):
    """Use the native poller's real protocol against a separate, model-free App.

    The parent sends the same /quit request as the native close action and waits
    for a normal child exit. No inference, GPU discovery, or process killing.
    """
    import websockets
    from dream.desktop.protocol import session_address, session_status

    script = r'''
import asyncio
from types import SimpleNamespace
from dream.core.backends.base import Event
from dream.tui.app import App

async def main():
    app = App(consolidate_on_exit=False)
    async def stopped(**kwargs):
        return None
    async def start():
        app.engine = SimpleNamespace(session_id="native-test", model="scripted", effort=None, store=None, stop=stopped)
        app.provider_label = "scripted"
        await app._start_studio()
        if app.studio is None:
            raise RuntimeError("Studio did not start")
        app._render_event(Event("studio", {"op": "show", "path": "preview.html", "content": "<p>native</p>"}))
    async def idle(*args, **kwargs):
        await asyncio.Future()
    async def decline(*args, **kwargs):
        return "n"
    app.start = start
    app.session.prompt_async = idle
    app._read_answer = decline
    try:
        await asyncio.wait_for(app.run(), 12)
    finally:
        if app.studio is not None:
            await app.studio.stop()

asyncio.run(main())
'''
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", script,
        env={**os.environ, "DREAM_ROOT": str(tmp_path / "child"), "DREAM_GUI": "1",
             "DREAM_GUI_OPEN": "0", "DREAM_MONITOR": "0", "DREAM_SEMANTIC_MEMORY": "0",
             "DREAM_RERANK": "0", "DREAM_CONSOLIDATE": "0",
             "DREAM_DESKTOP_SESSION_FILE": str(private_discovery)},
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(6):
            while (address := session_address(private_discovery, child.pid)) is None:
                await asyncio.sleep(0.02)
        base, token = address
        assert session_address(private_discovery, child.pid + 1) is None
        state = await asyncio.to_thread(session_status, base, token)
        assert state["pid"] == child.pid and state["ready"] is True
        assert state["session"]["model"] == "scripted"
        assert state["session"]["reasoning_effort"] is None
        assert state["session"]["workspace"] == str(tmp_path / "child")
        assert state["show_sequence"] == 1
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f"{base}/?token={token}&companion=1")
            assert response.status_code == 200
            async with websockets.connect(base.replace("http:", "ws:") + f"/ws?token={token}", proxy=None) as ws:
                assert json.loads(await asyncio.wait_for(ws.recv(), 1))["data"]["session_id"] == "native-test"
                assert json.loads(await asyncio.wait_for(ws.recv(), 1))["data"]["content"] == "<p>native</p>"
                state = await asyncio.to_thread(session_status, base, token)
                assert state["clients"] == 1 and state["show_sequence"] == 1
                response = await client.post(f"{base}/api/prompt", headers={"X-Dream-Token": token},
                                             json={"prompt": "/quit"})
                assert response.status_code == 200
                await asyncio.wait_for(ws.wait_closed(), 3)
        stdout, stderr = await asyncio.wait_for(child.communicate(), 4)
        assert child.returncode == 0, (stdout + stderr).decode(errors="replace")
        assert not private_discovery.exists()
    finally:
        # The child has its own bounded run/finally if any parent assertion fails.
        if child.returncode is None:
            await asyncio.wait_for(child.communicate(), 15)
