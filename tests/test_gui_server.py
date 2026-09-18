"""Dream Studio's local server.

This process can drive an agent that runs shell commands, so the server is a
security boundary before it is a feature: loopback only, and a token any other
local process must present. The rest pins the contract the browser UI depends
on — events stream out, prompts come back in, and a disconnecting browser is a
non-event for the session it was watching.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


def _server(**kw) -> StudioServer:
    return StudioServer(EventBus(), **kw)


# --- the security boundary ----------------------------------------------------


def test_binds_loopback_only():
    """An agent harness with shell access must not be reachable off-box. This is
    not configurable on purpose."""
    assert _server().host == "127.0.0.1"


def test_a_token_is_generated_and_is_not_guessable():
    a, b = _server().token, _server().token
    assert a != b and len(a) >= 32


def test_websocket_without_the_token_is_rejected():
    srv = _server()
    with TestClient(srv.app) as client:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws"):
                pass


def test_prompt_endpoint_without_the_token_is_rejected():
    srv = _server()
    with TestClient(srv.app) as client:
        assert client.post("/api/prompt", json={"prompt": "hi"}).status_code == 401


# --- streaming ---------------------------------------------------------------


def test_published_events_reach_a_connected_browser():
    srv = _server()
    with TestClient(srv.app) as client:
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            assert ws.receive_json()["kind"] == "hello"  # handshake first
            srv.bus.publish(Event("text_delta", "streamed"))
            msg = ws.receive_json()
            assert msg["kind"] == "text_delta" and msg["data"] == "streamed"


def test_tool_events_serialize_their_dict_payload():
    srv = _server()
    with TestClient(srv.app) as client:
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            ws.receive_json()
            srv.bus.publish(Event("tool_use", {"name": "read_file", "input": {"path": "x.py"}}))
            msg = ws.receive_json()
            assert msg["data"]["name"] == "read_file"
            assert msg["data"]["input"]["path"] == "x.py"


def test_an_unserializable_payload_does_not_kill_the_socket():
    """Tool results carry arbitrary handler output. One awkward object must not
    take down the GUI stream mid-turn."""
    srv = _server()

    class Weird:
        def __repr__(self): return "<weird>"

    with TestClient(srv.app) as client:
        with client.websocket_connect(f"/ws?token={srv.token}") as ws:
            ws.receive_json()
            srv.bus.publish(Event("tool_result", {"name": "t", "content": Weird()}))
            assert "weird" in str(ws.receive_json()["data"])
            srv.bus.publish(Event("text_delta", "still alive"))
            assert ws.receive_json()["data"] == "still alive"


def test_a_browser_that_disconnects_unsubscribes_cleanly():
    srv = _server()
    with TestClient(srv.app) as client:
        with client.websocket_connect(f"/ws?token={srv.token}"):
            pass
    # The session outlives the browser watching it.
    assert srv.bus.subscriber_count == 0
    srv.bus.publish(Event("system", "session continues"))


# --- input: the GUI is not read-only -----------------------------------------


def test_a_prompt_from_the_browser_reaches_the_session():
    got: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=got.append)
    with TestClient(srv.app) as client:
        r = client.post("/api/prompt", json={"prompt": "build me a site"},
                        headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200
    assert got == ["build me a site"]


def test_an_empty_prompt_is_refused():
    got: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=got.append)
    with TestClient(srv.app) as client:
        r = client.post("/api/prompt", json={"prompt": "   "},
                        headers={"X-Dream-Token": srv.token})
        assert r.status_code == 400
    assert got == []


def test_the_ui_is_served():
    srv = _server()
    with TestClient(srv.app) as client:
        r = client.get(f"/?token={srv.token}")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]


# --- telemetry: Dream's differentiator, surfaced ------------------------------


def test_telemetry_is_served_when_a_sampler_is_wired():
    srv = StudioServer(EventBus(), telemetry=lambda: {
        "available": True,
        "gpu": {"ok": True, "devices": [{"index": 0, "name": "Arc B70", "vram_used_mib": 32000}]},
        "inference": {"live_tps": 14.2, "state": "decoding"},
    })
    with TestClient(srv.app) as client:
        r = client.get("/api/telemetry", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200
        assert r.json()["inference"]["live_tps"] == 14.2
        assert r.json()["gpu"]["devices"][0]["name"] == "Arc B70"


def test_telemetry_requires_the_token():
    srv = StudioServer(EventBus(), telemetry=lambda: {"available": True})
    with TestClient(srv.app) as client:
        assert client.get("/api/telemetry").status_code == 401


def test_a_throwing_sampler_is_a_blank_panel_not_a_500():
    """Reading sysfs/xpu-smi can fail transiently. The pane goes quiet; the
    session — and the rest of the GUI — must not notice."""
    def boom():
        raise OSError("xpu-smi vanished")

    srv = StudioServer(EventBus(), telemetry=boom)
    with TestClient(srv.app) as client:
        r = client.get("/api/telemetry", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200 and r.json()["available"] is False
        assert "xpu-smi vanished" in r.json()["error"]


def test_no_sampler_reports_unavailable_rather_than_erroring():
    srv = StudioServer(EventBus())
    with TestClient(srv.app) as client:
        r = client.get("/api/telemetry", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200 and r.json() == {"available": False}


# --- checkpoints: undo, exposed without inventing capability it lacks ---------


class _FakeStore:
    """Stands in for CheckpointStore with the same surface the GUI uses."""
    def __init__(self):
        self.restored: list[tuple[str, bool]] = []

    def list(self, limit=None):
        return [{"id": "0002", "label": "add the header", "session": "s1",
                 "created_at": "2026-08-07T10:00:00+00:00", "files": 2,
                 "paths": ["/w/a.py", "/w/b.py"], "sealed": True},
                {"id": "0001", "label": "first turn", "session": "s1",
                 "created_at": "2026-08-07T09:00:00+00:00", "files": 1,
                 "paths": ["/w/a.py"], "sealed": False}]

    def diff(self, cid):
        if cid != "0002":
            raise KeyError(f"no checkpoint '{cid}'")
        return "--- a.py\n+++ a.py\n-old\n+new"

    def restore(self, cid, force=False):
        self.restored.append((cid, force))
        from dream.core.checkpoints import RestoreReport
        return RestoreReport(checkpoint_id=cid, label="add the header",
                             restored=["/w/a.py"], skipped=[("/w/b.py", "changed since")])


def _cp_server():
    store = _FakeStore()
    return StudioServer(EventBus(), checkpoints=lambda: store), store


def test_checkpoints_are_listed_newest_first():
    srv, _ = _cp_server()
    with TestClient(srv.app) as client:
        r = client.get("/api/checkpoints", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200
        rows = r.json()["checkpoints"]
        assert [c["id"] for c in rows] == ["0002", "0001"]
        assert rows[0]["label"] == "add the header" and rows[0]["files"] == 2
        assert rows[1]["sealed"] is False  # an unfinished turn is marked as such


def test_a_checkpoint_diff_is_served():
    srv, _ = _cp_server()
    with TestClient(srv.app) as client:
        r = client.get("/api/checkpoints/0002/diff", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200 and "+new" in r.json()["diff"]


def test_an_unknown_checkpoint_diff_is_a_404_not_a_crash():
    srv, _ = _cp_server()
    with TestClient(srv.app) as client:
        r = client.get("/api/checkpoints/9999/diff", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 404


def test_restore_writes_files_and_reports_exactly_what_it_did():
    srv, store = _cp_server()
    with TestClient(srv.app) as client:
        r = client.post("/api/checkpoints/0002/restore", json={},
                        headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200
        body = r.json()
        assert body["restored"] == ["/w/a.py"]
        # What it did NOT touch matters as much as what it did.
        assert body["skipped"][0][1] == "changed since"
    assert store.restored == [("0002", False)]


def test_restore_force_is_opt_in_and_never_the_default():
    srv, store = _cp_server()
    with TestClient(srv.app) as client:
        client.post("/api/checkpoints/0002/restore", json={},
                    headers={"X-Dream-Token": srv.token})
        client.post("/api/checkpoints/0002/restore", json={"force": True},
                    headers={"X-Dream-Token": srv.token})
    assert store.restored == [("0002", False), ("0002", True)]


def test_restore_requires_the_token():
    """This one WRITES to the filesystem — it is the most dangerous endpoint
    here and must never be reachable without the token."""
    srv, store = _cp_server()
    with TestClient(srv.app) as client:
        assert client.post("/api/checkpoints/0002/restore", json={}).status_code == 401
    assert store.restored == []


def test_checkpoint_routes_report_unavailable_when_no_store_is_wired():
    srv = StudioServer(EventBus())
    with TestClient(srv.app) as client:
        r = client.get("/api/checkpoints", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200 and r.json()["checkpoints"] == []
