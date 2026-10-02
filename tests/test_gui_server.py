"""Dream Studio's local server.

This process can drive an agent that runs shell commands, so the server is a
security boundary before it is a feature: loopback only, and a token any other
local process must present. The rest pins the contract the browser UI depends
on — events stream out, prompts come back in, and a disconnecting browser is a
non-event for the session it was watching.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lease_isolation import isolated_lease_dir  # noqa: F401  (DREAM-205: leases under tmp_path, never the live folder)
from starlette.testclient import TestClient

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

STATIC = Path(__file__).parent.parent / "dream" / "gui" / "static"


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
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        with pytest.raises(Exception):
            with client.websocket_connect("ws://127.0.0.1/ws"):
                pass


def test_prompt_endpoint_without_the_token_is_rejected():
    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/prompt", json={"prompt": "hi"}).status_code == 401


# --- the browser-side boundary (DREAM-187) --------------------------------------
#
# The token is the lock. These checks keep a leaked token unusable from a web page:
# a request must name this server itself (Host), a write or a WebSocket must come
# from this page's origin when the browser says where it came from, and a write
# never takes the token from the URL. TestClient's base_url is the loopback
# address the middleware expects; the default "testserver" is another name.


def test_another_host_name_is_refused_everywhere():
    """A DNS-rebinding page arrives under its own name, token or not."""
    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        auth = {"X-Dream-Token": srv.token}
        for host in ("attacker.example", "attacker.example:80", "127.0.0.1:9", "evil.localhost"):
            assert client.get("/api/session", headers={**auth, "host": host}).status_code == 403, host
            assert client.get("/", headers={"host": host}).status_code == 403, host
            assert client.get("/assets/theme.css", headers={"host": host}).status_code == 403, host
        for host in ("127.0.0.1", "localhost", "[::1]"):
            assert client.get("/api/session", headers={**auth, "host": host}).status_code == 200, host


def test_a_write_from_another_origin_is_refused_even_with_the_token():
    got: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=got.append)
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        auth = {"X-Dream-Token": srv.token}
        for headers in ({"origin": "http://attacker.example"}, {"origin": "https://127.0.0.1"},
                        {"origin": "http://127.0.0.1:9"}, {"origin": "null"},
                        {"origin": "http://127.0.0.1", "sec-fetch-site": "cross-site"},
                        {"sec-fetch-site": "same-site"}):
            r = client.post("/api/prompt", json={"prompt": "rm -rf ~"}, headers={**auth, **headers})
            assert r.status_code == 403, headers
        assert got == []
        for headers in ({"origin": "http://127.0.0.1"}, {"origin": "http://localhost", "sec-fetch-site": "same-origin"},
                        {"sec-fetch-site": "none"}, {}):   # {}: a non-browser client with the header token
            r = client.post("/api/prompt", json={"prompt": "hi"}, headers={**auth, **headers})
            assert r.status_code == 200, headers
        assert got == ["hi"] * 4


def test_the_websocket_handshake_checks_the_origin():
    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        with pytest.raises(Exception):
            with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}", headers={"origin": "http://attacker.example"}):
                pass
        with pytest.raises(Exception):
            with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}", headers={"sec-fetch-site": "cross-site"}):
                pass
        with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}", headers={"origin": "http://127.0.0.1"}) as ws:
            assert ws.receive_json()["kind"] == "hello"
    assert srv.client_count == 0


def test_restore_from_another_origin_never_touches_the_filesystem():
    srv, store = _cp_server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.post("/api/checkpoints/0002/restore", json={"force": True},
                        headers={"X-Dream-Token": srv.token, "origin": "http://attacker.example"})
        assert r.status_code == 403
    assert store.restored == []


def test_a_write_never_takes_the_token_from_the_url():
    """A URL sits in history and referrers; a write needs the header."""
    got: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=got.append)
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        assert client.post(f"/api/prompt?token={srv.token}", json={"prompt": "hi"}).status_code == 401
        assert client.post(f"/api/permission?token={srv.token}", json={"id": "x", "choice": "y"}).status_code == 401
    assert got == []


def test_plain_api_reads_take_the_header_only(tmp_path):
    """Only a link or a frame, which cannot carry a header, may put the token in the URL:
    downloads and the websocket. The JSON routes the page fetches never do."""
    (tmp_path / "a.txt").write_text("a")
    srv = StudioServer(EventBus(), session={"workspace": str(tmp_path)})
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        assert client.get(f"/api/session?token={srv.token}").status_code == 401
        assert client.get(f"/api/desktop?token={srv.token}").status_code == 401
        assert client.get(f"/api/telemetry?token={srv.token}").status_code == 401
        assert client.get(f"/api/download?path=a.txt&token={srv.token}").status_code == 200
        with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}") as ws:
            assert ws.receive_json()["kind"] == "hello"


def test_the_page_carries_no_referrer_and_cannot_be_framed():
    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.get("/")
        assert r.status_code == 200
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_every_html_document_carries_the_page_headers_wherever_it_is_served():
    """The same page is also served from the static mount, at /assets/index.html, and it
    works there: a page on another origin holding a leaked token framed it and a click
    inside the frame reached the session (the frame's own requests are same-origin, so
    the Origin check cannot stop them). The headers belong to every HTML document this
    origin serves, and the Understand dashboard may be framed by this page only."""
    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        for path in ("/", "/assets/index.html"):
            r = client.get(path)
            assert r.status_code == 200 and "text/html" in r.headers["content-type"], path
            assert r.headers["referrer-policy"] == "no-referrer", path
            assert "frame-ancestors 'none'" in r.headers["content-security-policy"], path
        r = client.get("/ua/")   # the dashboard, or the "not built" page: HTML either way
        assert "text/html" in r.headers["content-type"]
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
        r = client.get("/api/session", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200 and "content-security-policy" not in r.headers


def test_a_link_token_counts_only_from_this_page(tmp_path):
    """A page on another origin holding a leaked token could load a workspace file as
    <script src="/api/download?path=...&token=..."> and read what it defines (an attachment
    disposition does not stop a script from running). The browser says where a request
    came from (Sec-Fetch-Site, Origin): a URL token is honoured from this page or from no
    page -- a non-browser client, the desktop's own download -- never from another site."""
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / ".ua").mkdir()
    (tmp_path / ".ua" / "knowledge-graph.json").write_text('{"nodes": [], "edges": []}')
    srv = StudioServer(EventBus(), session={"workspace": str(tmp_path)})
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        for path in (f"/api/download?path=a.txt&token={srv.token}", f"/knowledge-graph.json?token={srv.token}"):
            for headers in ({"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"},
                            {"origin": "http://attacker.example"}):
                assert client.get(path, headers=headers).status_code == 401, (path, headers)
            for headers in ({"sec-fetch-site": "same-origin"}, {"sec-fetch-site": "none"},
                            {"origin": "http://127.0.0.1"}, {}):
                assert client.get(path, headers=headers).status_code == 200, (path, headers)


def test_the_cli_never_hands_the_studio_url_to_a_browser():
    """The URL carries the token, which a browser's history and sync would keep: the TUI
    prints it once (the run itself is in test_desktop_bridge). No path to webbrowser
    remains, and no setting to turn one on."""
    tui = (Path(__file__).parent.parent / "dream" / "tui" / "app.py").read_text()
    assert "webbrowser" not in tui
    from dream import config
    assert not hasattr(config, "GUI_OPEN")


def test_the_studio_url_keeps_the_token_in_the_fragment():
    """A fragment never reaches a server, a Referer or a proxy log."""
    srv = _server()
    srv.port = 4321
    assert srv.url == f"http://127.0.0.1:4321/#token={srv.token}"
    assert "?token=" not in srv.url


def test_every_module_reads_the_token_through_the_shared_getter():
    page = (STATIC / "index.html").read_text()
    assert "window.dreamToken = " in page and "const TOKEN = dreamToken();" in page
    assert 'target="_blank" rel="noopener noreferrer">$1</a>' in page, "model links need noreferrer"
    for script in STATIC.glob("*.js"):
        text = script.read_text()
        assert "get('token')" not in text, f"{script.name} parses the URL itself instead of calling dreamToken()"


@pytest.mark.skipif(shutil.which("node") is None, reason="the getter's behaviour runs under node")
def test_the_getter_reads_the_fragment_and_clears_the_address_bar_once_the_tab_holds_the_token():
    """The page-load script, run in node against a fake browser: the token comes from
    #token= (nothing loads the older ?token= form any more), then leaves the address bar
    once the tab's sessionStorage holds it for a reload -- except in the desktop pane
    (companion=1), which has no address bar and is recognised by that URL, and when
    storage is refused, where the fragment is the only thing a reload can read. A new
    session on the same port differs only in the fragment, which is a move within the
    document, not a load: the page reloads on a new #token= (and on nothing else), so it
    connects to the new session."""
    page = (STATIC / "index.html").read_text()
    script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    assert "dreamToken" in script, "the getter must be the first script, ahead of every module"
    cases = [{"search": "", "hash": "#token=abc"}, {"search": "?token=abc", "hash": ""},
             {"search": "?companion=1", "hash": "#token=abc"}, {"search": "", "hash": "#token=abc&companion=1"},
             {"search": "?other=1", "hash": "#token=abc&keep=2"}, {"search": "", "hash": ""},
             {"search": "", "hash": "", "stored": "abc"},   # a reload: the tab kept the token
             {"search": "", "hash": "#token=abc", "denied": True}]   # storage refused: the fragment stays
    harness = """
      const [script, cases] = [process.argv[1], JSON.parse(process.argv[2])];
      const denied = () => { throw new Error('storage denied'); };
      for (const c of cases) {
        const replaced = [], reloads = [], classes = new Set(), listeners = {};
        global.window = global;
        global.location = {search: c.search, hash: c.hash, pathname: '/', reload: () => reloads.push(location.hash)};
        global.history = {state: null, replaceState: (_s, _t, url) => replaced.push(url)};
        global.document = {documentElement: {classList: {toggle: (n, on) => on ? classes.add(n) : classes.delete(n)}}};
        global.sessionStorage = c.denied ? {store: {}, setItem: denied, getItem: denied}
                              : {store: c.stored ? {'dream-token': c.stored} : {},
                                 setItem(k, v) { this.store[k] = v; }, getItem(k) { return this.store[k] ?? null; }};
        global.addEventListener = (name, fn) => { listeners[name] = fn; };
        new Function(script)();
        for (const hash of ['#token=' + dreamToken(), '', '#other=1', '#token=zzz']) { location.hash = hash; listeners.hashchange(); }
        console.log(JSON.stringify({token: dreamToken(), replaced, companion: classes.has('companion'),
                                    stored: sessionStorage.store['dream-token'] ?? null, reloads}));
      }"""
    out = subprocess.run(["node", "-e", harness, script, json.dumps(cases)], capture_output=True, text=True, check=True)
    results = [json.loads(line) for line in out.stdout.splitlines()]
    new_token = {"reloads": ["#token=zzz"]}   # the same token, no token, another fragment: no reload
    assert results == [
        {"token": "abc", "replaced": ["/"], "companion": False, "stored": "abc", **new_token},
        {"token": "", "replaced": [], "companion": False, "stored": None, **new_token},
        {"token": "abc", "replaced": [], "companion": True, "stored": "abc", **new_token},
        {"token": "abc", "replaced": ["/#companion=1"], "companion": False, "stored": "abc", **new_token},
        {"token": "abc", "replaced": ["/?other=1#keep=2"], "companion": False, "stored": "abc", **new_token},
        {"token": "", "replaced": [], "companion": False, "stored": None, **new_token},
        {"token": "abc", "replaced": [], "companion": False, "stored": "abc", **new_token},
        {"token": "abc", "replaced": [], "companion": False, "stored": None, **new_token},
    ]


# --- streaming ---------------------------------------------------------------


def test_published_events_reach_a_connected_browser():
    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}") as ws:
            assert ws.receive_json()["kind"] == "hello"  # handshake first
            srv.bus.publish(Event("text_delta", "streamed"))
            msg = ws.receive_json()
            assert msg["kind"] == "text_delta" and msg["data"] == "streamed"


def test_tool_events_serialize_their_dict_payload():
    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}") as ws:
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

    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}") as ws:
            ws.receive_json()
            srv.bus.publish(Event("tool_result", {"name": "t", "content": Weird()}))
            assert "weird" in str(ws.receive_json()["data"])
            srv.bus.publish(Event("text_delta", "still alive"))
            assert ws.receive_json()["data"] == "still alive"


def test_a_browser_that_disconnects_unsubscribes_cleanly():
    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        with client.websocket_connect(f"ws://127.0.0.1/ws?token={srv.token}"):
            pass
    # The session outlives the browser watching it.
    assert srv.bus.subscriber_count == 0
    srv.bus.publish(Event("system", "session continues"))


# --- input: the GUI is not read-only -----------------------------------------


def test_a_prompt_from_the_browser_reaches_the_session():
    got: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=got.append)
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.post("/api/prompt", json={"prompt": "build me a site"},
                        headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200
    assert got == ["build me a site"]


def test_an_empty_prompt_is_refused():
    got: list[str] = []
    srv = StudioServer(EventBus(), on_prompt=got.append)
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.post("/api/prompt", json={"prompt": "   "},
                        headers={"X-Dream-Token": srv.token})
        assert r.status_code == 400
    assert got == []


def test_the_ui_is_served():
    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.get(f"/?token={srv.token}")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]


def test_the_page_title_is_the_one_desktop_control_refuses():
    """`dream --gui` shows this page, permission prompts included, as a tab in the owner's browser.
    Desktop control (DREAM-187) refuses any window whose title carries the page's <title>, so the
    page must keep it fixed and the two modules must agree on the string."""
    from dream import computer

    srv = _server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.get("/")
    assert r.status_code == 200
    assert f"<title>{computer._STUDIO_TITLE}</title>" in r.text
    assert computer._own_window({"title": computer._STUDIO_TITLE + " — Mozilla Firefox", "pid": None, "wm_class": ["Navigator", "firefox"]})


# --- telemetry: Dream's differentiator, surfaced ------------------------------


def test_telemetry_is_served_when_a_sampler_is_wired():
    srv = StudioServer(EventBus(), telemetry=lambda: {
        "available": True,
        "gpu": {"ok": True, "devices": [{"index": 0, "name": "Arc B70", "vram_used_mib": 32000}]},
        "inference": {"live_tps": 14.2, "state": "decoding"},
    })
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.get("/api/telemetry", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200
        assert r.json()["inference"]["live_tps"] == 14.2
        assert r.json()["gpu"]["devices"][0]["name"] == "Arc B70"


def test_telemetry_requires_the_token():
    srv = StudioServer(EventBus(), telemetry=lambda: {"available": True})
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/telemetry").status_code == 401


def test_a_throwing_sampler_is_a_blank_panel_not_a_500():
    """Reading sysfs/xpu-smi can fail transiently. The pane goes quiet; the
    session — and the rest of the GUI — must not notice."""
    def boom():
        raise OSError("xpu-smi vanished")

    srv = StudioServer(EventBus(), telemetry=boom)
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.get("/api/telemetry", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200 and r.json()["available"] is False
        assert "xpu-smi vanished" in r.json()["error"]


def test_no_sampler_reports_unavailable_rather_than_erroring():
    srv = StudioServer(EventBus())
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
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
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.get("/api/checkpoints", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200
        rows = r.json()["checkpoints"]
        assert [c["id"] for c in rows] == ["0002", "0001"]
        assert rows[0]["label"] == "add the header" and rows[0]["files"] == 2
        assert rows[1]["sealed"] is False  # an unfinished turn is marked as such


def test_a_checkpoint_diff_is_served():
    srv, _ = _cp_server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.get("/api/checkpoints/0002/diff", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200 and "+new" in r.json()["diff"]


def test_an_unknown_checkpoint_diff_is_a_404_not_a_crash():
    srv, _ = _cp_server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.get("/api/checkpoints/9999/diff", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 404


def test_restore_writes_files_and_reports_exactly_what_it_did():
    srv, store = _cp_server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
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
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        client.post("/api/checkpoints/0002/restore", json={},
                    headers={"X-Dream-Token": srv.token})
        client.post("/api/checkpoints/0002/restore", json={"force": True},
                    headers={"X-Dream-Token": srv.token})
    assert store.restored == [("0002", False), ("0002", True)]


def test_restore_requires_the_token():
    """This one WRITES to the filesystem — it is the most dangerous endpoint
    here and must never be reachable without the token."""
    srv, store = _cp_server()
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/checkpoints/0002/restore", json={}).status_code == 401
    assert store.restored == []


def test_checkpoint_routes_report_unavailable_when_no_store_is_wired():
    srv = StudioServer(EventBus())
    with TestClient(srv.app, base_url="http://127.0.0.1") as client:
        r = client.get("/api/checkpoints", headers={"X-Dream-Token": srv.token})
        assert r.status_code == 200 and r.json()["checkpoints"] == []
