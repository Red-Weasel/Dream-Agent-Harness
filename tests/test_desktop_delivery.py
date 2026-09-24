"""Browser connection accounting and explicit show replay, with no model loading."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tools import context, studio as studio_tools
from dream.tools.context import ToolContext


def _text(result):
    return result["content"][0]["text"]


@pytest.fixture(autouse=True)
def isolated_studio(monkeypatch):
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    monkeypatch.setattr(context, "_STUDIO", None)
    monkeypatch.setattr(context, "_CTX", None)


def test_desktop_state_requires_auth_and_counts_only_live_websockets():
    session = {"session_id": "s1", "provider": "scripted", "model": "none", "workspace": "/tmp"}
    srv = StudioServer(EventBus(), session=session)
    with srv.bus.subscribe(), TestClient(srv.app) as client:
        assert client.get("/api/desktop").status_code == 401
        assert client.get("/api/desktop?token=wrong").status_code == 401
        headers = {"X-Dream-Token": srv.token}
        state = client.get("/api/desktop", headers=headers)
        assert state.json()["session"] == session
        assert state.json()["clients"] == 0  # internal listener is not a browser
        assert state.headers["cache-control"] == "no-store"
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            assert ws.receive_json() == {"kind": "hello", "data": session}
            assert client.get(f"/api/desktop?token={srv.token}").json()["clients"] == 1
        deadline = time.monotonic() + 1
        while client.get("/api/desktop", headers=headers).json()["clients"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert srv.client_count == 0
        assert srv.bus.subscriber_count == 1  # only the internal listener remains


def test_latest_explicit_show_replays_after_hello_on_each_connection():
    srv = StudioServer(EventBus())
    srv.retain_show(Event("studio", {"op": "show", "path": "old.html", "content": "old"}))
    latest = {"op": "show", "path": "latest.html", "content": "<p>latest</p>"}
    srv.retain_show(Event("studio", latest))
    srv.retain_show(Event("studio", {"op": "eval", "id": "expired", "code": "1"}))
    srv.retain_show(Event("tool_result", {"content": "private history"}))
    latest["content"] = "mutated by caller"
    with TestClient(srv.app) as client:
        for _ in range(2):
            with client.websocket_connect(f"/ws?token={srv.token}") as ws:
                assert ws.receive_json()["kind"] == "hello"
                replay = ws.receive_json()
                assert replay == {"kind": "studio", "data": {
                    "op": "show", "path": "latest.html", "content": "<p>latest</p>",
                }}
                srv.bus.publish(Event("system", "after replay"))
                assert ws.receive_json() == {"kind": "system", "data": "after replay"}


def test_reconnect_replays_history_permission_and_show_in_order():
    srv = StudioServer(EventBus())
    event = Event("text_delta", "retained answer")
    srv.bus.publish(event)
    show = {"op": "show", "path": "latest.html", "content": "<p>preview</p>"}
    srv.retain_show(Event("studio", show))
    with TestClient(srv.app) as client:
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            assert ws.receive_json() == {"kind": "hello", "data": {}}
            assert ws.receive_json() == {"kind": "studio", "data": show}
            pending = client.portal.start_task_soon(
                srv.request_permission, "write_file", {"path": "a.txt"},
                "Allow file edit", {"y": "Allow once", "n": "Deny"},
            )
            permission = ws.receive_json()
            assert permission["kind"] == "permission"
        assert srv.client_count == srv.bus.subscriber_count == 0
        assert not pending.done()
        with client.websocket_connect(f"/ws?token={srv.token}&history=1") as ws:
            assert ws.receive_json() == {"kind": "hello", "data": {}}
            assert ws.receive_json() == {"kind": "history", "data": {
                "events": [{"kind": "text_delta", "data": "retained answer"}], "trimmed": False,
            }}
            assert ws.receive_json() == permission
            assert ws.receive_json() == {"kind": "studio", "data": show}
            assert not pending.done()
            response = client.post(
                "/api/permission", headers={"X-Dream-Token": srv.token},
                json={"id": permission["data"]["id"], "choice": "n"},
            )
            assert response.status_code == 200
            assert pending.result(timeout=2) == "n"
            assert ws.receive_json() == {"kind": "permission_done", "data": {
                "id": permission["data"]["id"],
            }}
        assert srv.client_count == srv.bus.subscriber_count == 0


def test_show_sequence_counts_explicit_handoffs_only_and_never_replay():
    srv = StudioServer(EventBus())
    with TestClient(srv.app) as client:
        def sequence():
            response = client.get(f"/api/desktop?token={srv.token}").json()
            assert type(response["show_sequence"]) is int
            return response["show_sequence"]

        assert sequence() == 0
        event = Event("studio", {"op": "show", "path": "same.html", "content": "one"})
        srv.retain_show(event)
        srv.retain_show(event)  # helper + App funnel see the same event
        assert sequence() == 1
        for other in [Event("studio", {"op": "eval", "id": "expired"}),
                      Event("tool_use", {"name": "write_file"}),
                      Event("tool_result", {"content": "new file"})]:
            srv.retain_show(other)
        assert sequence() == 1
        for _ in range(2):
            with client.websocket_connect(f"/ws?token={srv.token}") as ws:
                ws.receive_json()
                assert ws.receive_json()["data"]["op"] == "show"
                assert sequence() == 1
        srv.retain_show(Event("studio", {"op": "show", "path": "same.html", "content": "two"}))
        assert sequence() == 2
        # DREAM-104: a mirrored show is the model's own view -- retained for replay, but
        # not a handoff, or the desktop would navigate to Studio on every model edit.
        srv.retain_show(Event("studio", {"op": "show", "path": "same.html", "content": "three", "source": "mirror"}))
        assert sequence() == 2
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            ws.receive_json()
            assert ws.receive_json()["data"]["content"] == "three"
            assert sequence() == 2


@pytest.mark.asyncio
async def test_a_failed_hello_also_releases_the_client_and_subscription():
    srv = StudioServer(EventBus())
    ws = SimpleNamespace(query_params={"token": srv.token}, accept=AsyncMock(),
                         send_text=AsyncMock(side_effect=RuntimeError("closed")))
    # A real WebSocket is identity-hashable.
    class Socket:
        query_params = ws.query_params
        accept = ws.accept
        send_text = ws.send_text

    await srv._websocket(Socket())
    assert srv.client_count == srv.bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_scope_cancel_during_disconnect_cleanup_keeps_its_provenance(monkeypatch):
    """The audit's exact interleaving: scope cancellation races bare child cancel."""
    srv = StudioServer(EventBus())
    sent = []
    owned = []
    real_wait = asyncio.wait

    class Socket:
        query_params = {"token": srv.token}

        async def accept(self):
            pass

        async def send_text(self, text):
            sent.append(json.loads(text))

        async def receive(self):
            await asyncio.sleep(0)
            return {"type": "websocket.disconnect"}

    with anyio.CancelScope() as scope:
        async def observe_wait(tasks, **kwargs):
            owned.extend(tasks)
            result = await real_wait(tasks, **kwargs)
            asyncio.get_running_loop().call_soon(scope.cancel)
            return result

        monkeypatch.setattr("dream.gui.server.asyncio.wait", observe_wait)
        await srv._websocket(Socket())

    assert scope.cancel_called
    assert len(owned) == 2 and all(task.done() for task in owned)
    assert sent == [{"kind": "hello", "data": {}}]
    assert srv.client_count == srv.bus.subscriber_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_while_waiting", [False, True])
async def test_repeated_direct_cancel_joins_workers_and_preserves_first_reason(
    monkeypatch, cancel_while_waiting,
):
    srv = StudioServer(EventBus())
    started = asyncio.Event()
    disconnect = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()
    owned = []
    real_wait = asyncio.wait

    class Socket:
        query_params = {"token": srv.token}

        async def accept(self):
            pass

        async def send_text(self, text):
            if json.loads(text)["kind"] == "hello":
                srv.bus.publish(Event("system", "start sending"))
                return
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()

        async def receive(self):
            await disconnect.wait()
            return {"type": "websocket.disconnect"}

    async def observe_wait(tasks, **kwargs):
        owned.extend(tasks)
        return await real_wait(tasks, **kwargs)

    monkeypatch.setattr("dream.gui.server.asyncio.wait", observe_wait)
    handler = asyncio.create_task(srv._websocket(Socket()))
    try:
        async with asyncio.timeout(2):
            await started.wait()
            if cancel_while_waiting:
                handler.cancel("original stop")
            else:
                disconnect.set()
            await cleaning.wait()
            assert srv.client_count == srv.bus.subscriber_count == 1
            reasons = ["repeat stop", "third stop"] if cancel_while_waiting else [
                "original stop", "repeat stop", "third stop",
            ]
            for reason in reasons:
                handler.cancel(reason)
                # One checkpoint delivers this cancel before the next one.
                await asyncio.sleep(0)
                assert not handler.done()
                assert srv.client_count == srv.bus.subscriber_count == 1
            release.set()
            with pytest.raises(asyncio.CancelledError) as caught:
                await handler
            assert caught.value.args == ("original stop",)
            assert handler.cancelling() == 3
            assert cleaned.is_set()
            assert len(owned) == 2 and all(task.done() for task in owned)
            assert srv.client_count == srv.bus.subscriber_count == 0
    finally:
        release.set()
        disconnect.set()
        if not handler.done():
            handler.cancel("test teardown")
        await asyncio.gather(handler, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at,error_type", [
    ("hello", ValueError), ("send", ValueError), ("receive", ValueError),
    ("cleanup", ValueError), ("cleanup", RuntimeError),
])
async def test_unexpected_websocket_failure_is_visible_after_workers_settle(
    monkeypatch, failure_at, error_type,
):
    srv = StudioServer(EventBus())
    started = asyncio.Event()
    owned = []
    real_wait = asyncio.wait
    error = error_type(f"broken {failure_at}")

    class Socket:
        query_params = {"token": srv.token}

        async def accept(self):
            pass

        async def send_text(self, text):
            if json.loads(text)["kind"] == "hello":
                if failure_at == "hello":
                    raise error
                srv.bus.publish(Event("system", "send"))
                return
            started.set()
            if failure_at == "send":
                raise error
            try:
                await asyncio.Event().wait()
            finally:
                if failure_at == "cleanup":
                    raise error

        async def receive(self):
            await started.wait()
            if failure_at == "receive":
                raise error
            if failure_at == "cleanup":
                return {"type": "websocket.disconnect"}
            await asyncio.Event().wait()

    async def observe_wait(tasks, **kwargs):
        owned.extend(tasks)
        return await real_wait(tasks, **kwargs)

    monkeypatch.setattr("dream.gui.server.asyncio.wait", observe_wait)
    async with asyncio.timeout(2):
        with pytest.raises(error_type) as caught:
            await srv._websocket(Socket())
    assert caught.value is error
    assert all(task.done() for task in owned)
    assert srv.client_count == srv.bus.subscriber_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_while_waiting", [False, True])
async def test_anyio_level_cancel_finishes_slow_worker_cleanup(monkeypatch, cancel_while_waiting):
    srv = StudioServer(EventBus())
    started = asyncio.Event()
    disconnect = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()
    owned = []
    real_wait = asyncio.wait
    parent = asyncio.current_task()
    initial_cancels = parent.cancelling()

    class Socket:
        query_params = {"token": srv.token}

        async def accept(self):
            pass

        async def send_text(self, text):
            if json.loads(text)["kind"] == "hello":
                srv.bus.publish(Event("system", "send"))
                return
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                await anyio.lowlevel.checkpoint()
                cleaned.set()

        async def receive(self):
            await disconnect.wait()
            return {"type": "websocket.disconnect"}

    async def observe_wait(tasks, **kwargs):
        owned.extend(tasks)
        return await real_wait(tasks, **kwargs)

    monkeypatch.setattr("dream.gui.server.asyncio.wait", observe_wait)
    async with asyncio.timeout(2):
        with anyio.CancelScope() as scope:
            async def cancel_and_release():
                try:
                    await started.wait()
                    if cancel_while_waiting:
                        scope.cancel()
                    else:
                        disconnect.set()
                    await cleaning.wait()
                    scope.cancel()
                    for _ in range(3):
                        await anyio.lowlevel.checkpoint()
                        assert srv.client_count == srv.bus.subscriber_count == 1
                        assert not cleaned.is_set()
                finally:
                    release.set()

            controller = asyncio.create_task(cancel_and_release())
            try:
                await srv._websocket(Socket())
            finally:
                release.set()
        await controller
    assert scope.cancel_called
    assert cleaned.is_set()
    assert len(owned) == 2 and all(task.done() for task in owned)
    assert srv.client_count == srv.bus.subscriber_count == 0
    assert parent.cancelling() == initial_cancels


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["hello", "send", "receive"])
@pytest.mark.parametrize("error_type", [RuntimeError, WebSocketDisconnect])
async def test_expected_socket_closure_releases_workers(failure_at, error_type):
    srv = StudioServer(EventBus())
    started = asyncio.Event()

    class Socket:
        query_params = {"token": srv.token}

        async def accept(self):
            pass

        async def send_text(self, text):
            if json.loads(text)["kind"] == "hello":
                if failure_at == "hello":
                    raise error_type()
                srv.bus.publish(Event("system", "send"))
                return
            started.set()
            if failure_at == "send":
                raise error_type()
            await asyncio.Event().wait()

        async def receive(self):
            await started.wait()
            if failure_at == "receive":
                raise error_type()
            await asyncio.Event().wait()

    async with asyncio.timeout(2):
        await srv._websocket(Socket())
    assert srv.client_count == srv.bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_cancel_and_cleanup_failure_both_remain_visible():
    srv = StudioServer(EventBus())
    started = asyncio.Event()
    error = RuntimeError("worker cleanup failed")

    class Socket:
        query_params = {"token": srv.token}

        async def accept(self):
            pass

        async def send_text(self, text):
            if json.loads(text)["kind"] == "hello":
                srv.bus.publish(Event("system", "send"))
                return
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                raise error

        async def receive(self):
            await asyncio.Event().wait()

    handler = asyncio.create_task(srv._websocket(Socket()))
    async with asyncio.timeout(2):
        await started.wait()
        handler.cancel("original stop")
        with pytest.raises(BaseExceptionGroup) as caught:
            await handler
    cancel, failure = caught.value.exceptions
    assert isinstance(cancel, asyncio.CancelledError) and cancel.args == ("original stop",)
    assert failure is error
    assert handler.cancelling() == 1
    assert srv.client_count == srv.bus.subscriber_count == 0


@pytest.fixture
def page(tmp_path, monkeypatch):
    target = tmp_path / "page.html"
    target.write_text("<p>preview</p>")
    monkeypatch.setattr(studio_tools, "_load", AsyncMock(return_value=[]))
    emitted = []
    context.set_context(ToolContext(store=None, working=None, browser=None,
                                   session_id="test", workspace=tmp_path, emit=emitted.append))
    return target, emitted


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [studio_tools.show_to_user, studio_tools.done])
@pytest.mark.parametrize("clients", [0, 1])
async def test_delivery_uses_browser_connections_and_preserves_tool_results(page, tool, clients):
    target, emitted = page
    srv = StudioServer(EventBus())
    # Stub only server readiness; connect a real ASGI WebSocket for delivery.
    srv._ready = True
    srv._task = SimpleNamespace(done=lambda: False)
    context.set_studio(srv)
    with TestClient(srv.app) as client:
        if clients:
            with client.websocket_connect(f"/ws?token={srv.token}") as ws:
                ws.receive_json()
                result = await tool.handler({"path": target.name})
                assert "1 connected Studio browser" in _text(result)
                assert "rendering is not confirmed" in _text(result)
        else:
            with srv.bus.subscribe():
                result = await tool.handler({"path": target.name})
            assert "Preview queued" in _text(result)
            assert "no Studio browser is connected" in _text(result)
        assert not result.get("is_error")
        assert "opened in Studio" not in _text(result)
        assert len(emitted) == 1 and emitted[0].data["op"] == "show"
        # The captured emit hook need not itself be a real App for replay to work.
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            ws.receive_json()
            assert ws.receive_json()["data"]["content"] == "<p>preview</p>"


@pytest.mark.asyncio
@pytest.mark.parametrize("panel", [None, "stopped", "unknown"])
async def test_unavailable_delivery_never_claims_visibility(page, panel):
    target, emitted = page
    context.set_studio(StudioServer(EventBus()) if panel == "stopped" else object() if panel == "unknown" else None)
    result = await studio_tools.show_to_user.handler({"path": target.name})
    assert not result.get("is_error")  # hidden preview success retains the existing contract
    if panel == "unknown":
        assert "Preview queued" in _text(result)
        assert "no Studio browser is connected" in _text(result)
    else:
        assert "Studio is not open" in _text(result)
        assert not emitted


@pytest.mark.asyncio
async def test_done_reports_hidden_errors_even_when_delivery_is_queued(page, monkeypatch):
    target, emitted = page
    context.set_studio(SimpleNamespace(ready=True, client_count=0))
    monkeypatch.setattr(studio_tools, "_load", AsyncMock(return_value=["error: broken script"]))
    result = await studio_tools.done.handler({"path": target.name})
    assert result["is_error"]
    assert "Preview queued" in _text(result) and "NOT clean" in _text(result)
    assert "broken script" in _text(result) and len(emitted) == 1


@pytest.mark.asyncio
async def test_real_idle_disconnect_reconnect_and_connected_shutdown(page):
    import websockets

    target, _ = page
    srv = StudioServer(EventBus())
    context.ctx().emit = srv.bus.publish
    context.set_studio(srv)
    try:
        await srv.start()
        result = await studio_tools.show_to_user.handler({"path": target.name})
        assert "Preview queued" in _text(result)
        address = f"ws://127.0.0.1:{srv.port}/ws?token={srv.token}"
        async with websockets.connect(address, proxy=None) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 1))["kind"] == "hello"
            assert json.loads(await asyncio.wait_for(ws.recv(), 1))["data"]["path"] == target.name
            assert srv.client_count == 1
        async with asyncio.timeout(1):
            while srv.client_count or srv.bus.subscriber_count:
                await asyncio.sleep(0.01)
        # No event was needed to notice the close. The next socket gets the show.
        async with websockets.connect(address, proxy=None) as ws:
            assert json.loads(await asyncio.wait_for(ws.recv(), 1))["kind"] == "hello"
            assert json.loads(await asyncio.wait_for(ws.recv(), 1))["data"]["content"] == "<p>preview</p>"
            async with httpx.AsyncClient(trust_env=False) as client:
                state = await client.get(f"http://127.0.0.1:{srv.port}/api/desktop?token={srv.token}")
            assert state.json()["clients"] == 1
            await asyncio.wait_for(srv.stop(), 4)
            await asyncio.wait_for(ws.wait_closed(), 1)
        assert srv.client_count == srv.bus.subscriber_count == 0
    finally:
        await srv.stop()
