"""DREAM-111 (#67): "Open in browser" -- the Studio's page, one click away in the owner's own browser.

The owner runs the model's three.js page fine in a normal browser. A file:// URL cannot load
ES modules in Chrome, so the page is served over http -- from a SEPARATE origin, never Dream's:
its own 127.0.0.1 port, an unguessable per-session token in the path, read-only, confined to
the workspace (realpath check, no symlink out, no listing, 404 outside), `nosniff` and
`no-store`. Dream's API authenticates every call with its own per-session token -- the
X-Dream-Token header or a ?token= query, and ?token= on the websocket; no cookie -- which that
origin never has, so a workspace page cannot call Dream with the owner's credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import httpx
import pytest

from dream.gui import page_server
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

pytestmark = pytest.mark.asyncio
REPO = Path(__file__).resolve().parents[1]
SECRET = "top-secret-outside-the-workspace"


def _workspace(tmp_path: Path) -> Path:
    """A module page, a secret outside the workspace, and every kind of way out."""
    ws = tmp_path / "work"
    (ws / "falcon9").mkdir(parents=True)
    (ws / "falcon9" / "index.html").write_text(
        "<!doctype html><title>booting</title><h1 id='h'>booting...</h1>"
        "<script type='module' src='./main.js'></script>")
    (ws / "falcon9" / "main.js").write_text(
        "import {msg} from './lib.js'; document.title = msg; document.getElementById('h').textContent = msg;")
    (ws / "falcon9" / "lib.js").write_text("export const msg = 'modules ran';")
    (ws / ".env").write_text("OPENAI_KEY=do-not-serve")
    (ws / "assets").mkdir()
    (ws / "assets" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (ws / "falcon9" / "same.js").symlink_to(ws / "falcon9" / "lib.js")          # a link that stays inside
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(SECRET)
    (ws / "leak.txt").symlink_to(outside / "secret.txt")                         # a file link out
    (ws / "linked").symlink_to(outside, target_is_directory=True)               # a folder link out
    return ws


@pytest.fixture
async def served(tmp_path, monkeypatch):
    """A running Studio, and so its page server, over that workspace."""
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    ws = _workspace(tmp_path)
    prompts: list = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, session={"workspace": str(ws)})
    await srv.start()
    try:
        yield srv, ws, prompts
    finally:
        await srv.stop()


async def _raw_get(port: int, target: str, host: str | None = None) -> tuple[int, dict, bytes]:
    """One GET exactly as written: no client normalises the path on the way."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {target} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{port}'}\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    data = await reader.read()
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
    return int(lines[0].split()[1]), headers, body


async def test_pages_come_from_a_separate_loopback_origin_with_their_own_token(served):
    srv, ws, _ = served
    pages = srv.pages
    assert pages.ready and pages.host == "127.0.0.1"
    base = pages.base_url
    assert base.startswith(f"http://127.0.0.1:{pages.port}/") and pages.port != srv.port
    assert len(pages.token) >= 40 and pages.token != srv.token
    assert srv.token not in base, "the page origin never carries Dream's credential"
    async with httpx.AsyncClient(trust_env=False) as c:
        r = await c.get(base + "falcon9/index.html")
        assert r.status_code == 200 and "booting" in r.text
        assert r.headers["content-type"] == "text/html; charset=utf-8"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["referrer-policy"] == "no-referrer"
        r = await c.get(base + "falcon9/main.js")
        assert r.status_code == 200 and r.headers["content-type"] == "text/javascript; charset=utf-8"
        assert (await c.get(base + "assets/logo.svg")).headers["content-type"] == "image/svg+xml"
        assert (await c.get(base + "falcon9/same.js")).text == "export const msg = 'modules ran';", \
            "a link that stays inside the workspace is followed"
        r = await c.post(base + "falcon9/index.html", content=b"x")
        assert r.status_code == 405 and r.headers["x-content-type-options"] == "nosniff", "read-only"
        # the desktop learns where pages live (and so what to hand to the default browser)
        desk = await c.get(f"http://127.0.0.1:{srv.port}/api/desktop", headers={"X-Dream-Token": srv.token})
        assert desk.json()["page_base"] == base


@pytest.mark.parametrize("target", [
    "/{t}/../outside/secret.txt", "/{t}/%2e%2e/outside/secret.txt", "/{t}/..%2foutside%2fsecret.txt",
    "/{t}/%2E%2E%2Foutside%2Fsecret.txt", "/{t}/falcon9/../../outside/secret.txt", "/{t}/falcon9/%2e%2e/%2e%2e/outside/secret.txt",
    "/{t}/leak.txt", "/{t}/linked/secret.txt", "/{t}//etc/passwd", "/{t}/%2Fetc%2Fpasswd", "/{t}/{abs}",
    "/{t}/", "/{t}/falcon9", "/{t}/falcon9/", "/{t}/.env", "/{t}/%2eenv", "/{t}/missing.html",
    "/{t}/falcon9/index.html%00.txt", "/wrong-token/falcon9/index.html", "/falcon9/index.html", "/{t}",
])
async def test_confinement_refuses_every_way_out(served, target, tmp_path):
    srv, ws, _ = served
    path = target.replace("{t}", srv.pages.token).replace("{abs}", str(tmp_path / "outside" / "secret.txt").lstrip("/"))
    status, headers, body = await _raw_get(srv.pages.port, path)
    assert status == 404, (target, status)
    assert headers.get("x-content-type-options") == "nosniff" and headers.get("cache-control") == "no-store"
    assert SECRET.encode() not in body and b"do-not-serve" not in body and b"<a href" not in body


async def test_another_host_name_is_refused(served):
    """A page reached under another name (DNS rebinding) is not served, token or not."""
    srv, ws, _ = served
    status, _, body = await _raw_get(srv.pages.port, f"/{srv.pages.token}/falcon9/index.html",
                                     host=f"attacker.example:{srv.pages.port}")
    assert status == 404 and b"booting" not in body
    status, _, _ = await _raw_get(srv.pages.port, f"/{srv.pages.token}/falcon9/index.html",
                                  host=f"localhost:{srv.pages.port}")
    assert status == 200


async def test_the_page_link_route(served):
    srv, ws, _ = served
    base = f"http://127.0.0.1:{srv.port}"
    async with httpx.AsyncClient(trust_env=False, base_url=base) as c:
        assert (await c.get("/api/page_link", params={"path": "falcon9/index.html"})).status_code == 401
        head = {"X-Dream-Token": srv.token}
        r = await c.get("/api/page_link", params={"path": "falcon9/index.html"}, headers=head)
        assert r.status_code == 200 and r.json() == {"url": srv.pages.base_url + "falcon9/index.html"}
        r = await c.get("/api/page_link", params={"path": str(ws / "falcon9" / "index.html")}, headers=head)
        assert r.json()["url"].endswith("/falcon9/index.html"), "the absolute spelling the SDK's Write uses"
        for bad in ("../outside/secret.txt", "leak.txt", "linked/secret.txt", ".env", "falcon9", "missing.html", "",
                    "~nosuchuser_zz/index.html"):   # gate 1: expanduser raised RuntimeError, a 500
            assert (await c.get("/api/page_link", params={"path": bad}, headers=head)).status_code == 400, bad
    await srv.pages.stop()
    async with httpx.AsyncClient(trust_env=False, base_url=base) as c:
        r = await c.get("/api/page_link", params={"path": "falcon9/index.html"}, headers={"X-Dream-Token": srv.token})
        assert r.status_code == 503 and "not running" in r.json()["error"]
        assert (await c.get("/api/desktop", headers={"X-Dream-Token": srv.token})).json()["page_base"] is None


async def test_es_modules_load_from_the_page_origin_and_not_from_file(served):
    from playwright.async_api import async_playwright

    srv, ws, _ = served
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
        page = await browser.new_page()
        await page.goto(srv.pages.base_url + "falcon9/index.html", wait_until="load")
        await page.wait_for_function("document.title === 'modules ran'", timeout=5000)
        # the same page as a file:// URL -- what a plain "open the file" would give the owner
        await page.goto((ws / "falcon9" / "index.html").as_uri(), wait_until="load")
        await page.wait_for_timeout(300)
        assert await page.title() == "booting", "Chrome refuses module scripts from file://"
        await browser.close()


async def test_a_served_page_cannot_call_dreams_api(tmp_path, monkeypatch):
    """Every call a workspace page could make at Dream's own origin, from the owner's browser:
    each reaches the server without the per-session token and is refused, and nothing runs."""
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    ws = _workspace(tmp_path)
    prompts: list = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, session={"workspace": str(ws)})
    seen: list = []
    inner = srv.app

    async def recording(scope, receive, send):   # what reaches Dream's server, and its answer
        async def sent(message):
            if message["type"] == "http.response.start":
                seen.append((scope["path"], message["status"]))
            elif message["type"] == "websocket.close":
                seen.append((scope["path"], "ws close %s" % message.get("code")))
            await send(message)
        await inner(scope, receive, sent)

    srv.app = recording
    await srv.start()
    try:
        await _attack(srv, ws, prompts, seen)
    finally:
        await srv.stop()


async def _attack(srv, ws, prompts, seen):
    from playwright.async_api import async_playwright

    dream = f"http://127.0.0.1:{srv.port}"
    (ws / "attack.html").write_text(f"""<!doctype html><title>attack</title><script>
    window.results = (async () => {{
      const out = {{}};
      const tryit = async (name, fn) => {{ try {{ out[name] = await fn(); }} catch (e) {{ out[name] = 'refused: ' + e.name; }} }};
      await tryit('session', () => fetch('{dream}/api/session').then(r => r.status));
      await tryit('session_query', () => fetch('{dream}/api/session?token=').then(r => r.status));
      await tryit('prompt', () => fetch('{dream}/api/prompt', {{method: 'POST', mode: 'no-cors',
        headers: {{'Content-Type': 'text/plain'}}, body: JSON.stringify({{prompt: 'rm -rf ~'}})}}).then(r => r.type));
      await tryit('prompt_header', () => fetch('{dream}/api/prompt', {{method: 'POST',
        headers: {{'Content-Type': 'application/json', 'X-Dream-Token': 'guess'}}, body: '{{"prompt":"x"}}'}}).then(r => r.status));
      out.ws = await new Promise(done => {{ const s = new WebSocket('ws://127.0.0.1:{srv.port}/ws');
        s.onopen = () => done('open'); s.onclose = e => done('closed ' + e.code); s.onerror = () => {{}}; }});
      out.cookie = document.cookie;
      out.href = location.href;
      return out;
    }})();
    </script>""")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
        page = await browser.new_page()
        await page.goto(srv.pages.base_url + "attack.html", wait_until="load")
        results = await page.evaluate("window.results")
        await browser.close()
    assert results["session"].startswith("refused") and results["session_query"].startswith("refused"), results
    assert results["prompt"] == "opaque", "the browser sent it; the server answered 401 (below)"
    assert results["prompt_header"].startswith("refused"), "a custom header needs a CORS preflight Dream never grants"
    assert results["ws"] != "open", results
    assert results["cookie"] == "" and srv.token not in results["href"]
    assert prompts == [], "nothing reached the session"
    statuses = {path: status for path, status in seen if path != "/ws"}
    assert statuses.get("/api/session") == 401 and statuses.get("/api/prompt") in (401, 405), seen
    assert all(status in (401, 403, 405) for path, status in seen if path.startswith("/api/")), seen
    assert ("/ws", "ws close 4401") in seen, seen


# --- gate 1 (2026-09-24): an over-long path, and a path swapped for a link after the check ------

HEADERS3 = {"x-content-type-options": "nosniff", "cache-control": "no-store", "referrer-policy": "no-referrer"}


@pytest.mark.parametrize("name", ["x" * 300, "/".join(["d" * 200] * 25), "d/" * 2100 + "a.html"],
                         ids=["a 300-byte component", "5,024 bytes of 200-byte folders", "4,206 bytes of 1-byte folders"])
async def test_an_over_long_path_is_not_found_and_never_a_crash(served, name, caplog):
    """B1: a component over 255 bytes or a path over PATH_MAX made stat() raise ENAMETOOLONG:
    a bare 500 without the headers, and a traceback in the log for every request."""
    srv, ws, _ = served
    status, headers, body = await _raw_get(srv.pages.port, f"/{srv.pages.token}/{name}")
    assert status == 404, (status, body[:200])
    assert {k: headers.get(k) for k in HEADERS3} == HEADERS3
    async with httpx.AsyncClient(trust_env=False, base_url=f"http://127.0.0.1:{srv.port}") as c:
        r = await c.get("/api/page_link", params={"path": name}, headers={"X-Dream-Token": srv.token})
        assert r.status_code == 400 and "inside the workspace" in r.json()["error"], (r.status_code, r.text[:200])
    assert not [r for r in caplog.records if "Exception in ASGI application" in r.getMessage()]


@pytest.mark.parametrize("swap", ["file", "folder"])
async def test_a_path_swapped_for_a_link_after_the_check_is_refused(served, swap, monkeypatch, tmp_path):
    """B2: the realpath check passes on an innocent workspace file, then the file (or its folder)
    becomes a symlink out before the open. The component-by-component O_NOFOLLOW open refuses it."""
    srv, ws, _ = served
    outside = tmp_path / "outside"
    (ws / "page.txt").write_text("innocent page")
    (ws / "d").mkdir()
    (ws / "d" / "note.txt").write_text("innocent note")
    (outside / "page.txt").write_text(SECRET)
    (outside / "note.txt").write_text(SECRET)
    target = "page.txt" if swap == "file" else "d/note.txt"
    status, _, body = await _raw_get(srv.pages.port, f"/{srv.pages.token}/{target}")
    assert status == 200 and b"innocent" in body, "control: served before any swap"
    checked = page_server.confined

    def racing(workspace, raw):
        result = checked(workspace, raw)          # the check sees the innocent file ...
        if swap == "file":                        # ... then the file becomes a link out
            (ws / "page.txt").unlink()
            (ws / "page.txt").symlink_to(outside / "page.txt")
        else:                                     # ... or its folder does
            shutil.rmtree(ws / "d")
            (ws / "d").symlink_to(outside, target_is_directory=True)
        return result
    monkeypatch.setattr(page_server, "confined", racing)
    status, headers, body = await _raw_get(srv.pages.port, f"/{srv.pages.token}/{target}")
    assert status == 404 and SECRET.encode() not in body, (status, body[:80])
    assert {k: headers.get(k) for k in HEADERS3} == HEADERS3


# --- the desktop hands the click to the default browser ----------------------------------------

_DESKTOP_PROBE = textwrap.dedent("""
    import json, sys
    from unittest.mock import Mock
    sys.path.insert(0, sys.argv[1])
    from dream.desktop import window as win
    from gi.repository import GLib, WebKit2

    launched = []
    class FakeAppInfo:
        fail = False
        @staticmethod
        def launch_default_for_uri(uri, ctx):
            if FakeAppInfo.fail:
                raise GLib.Error('no default browser')
            launched.append(uri)
    win.Gio = type('FakeGio', (), {'AppInfo': FakeAppInfo})   # never the owner's real browser

    PAGE = 'http://127.0.0.1:4321/' + 'T' * 43 + '/'
    out = {}

    def decide(uri, gesture, kind=WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
        window = Mock(address_info=('http://127.0.0.1:3210', 'secret'), page_base=PAGE)
        decision = Mock()
        action = decision.get_navigation_action.return_value
        action.get_request.return_value.get_uri.return_value = uri
        action.is_user_gesture.return_value = gesture
        handled = win.DreamWindow._studio_policy(window, None, decision, kind)
        return {'handled': handled, 'ignored': decision.ignore.called,
                'opened': [c.args[0] for c in window._open_in_default_browser.call_args_list],
                'browser': window.browser.navigate.called}

    out['page_click'] = decide(PAGE + 'falcon9/index.html', True)
    out['page_no_gesture'] = decide(PAGE + 'falcon9/index.html', False)
    out['page_navigation'] = decide(PAGE + 'falcon9/index.html', True, WebKit2.PolicyDecisionType.NAVIGATION_ACTION)
    out['other_site'] = decide('https://example.com/', True)
    out['studio_origin'] = decide('http://127.0.0.1:3210/api/download?token=secret&path=a', True)
    out['page_port_lookalike'] = decide('http://127.0.0.1:43210/' + 'T' * 43 + '/x.html', True)

    window = Mock()
    win.DreamWindow._open_in_default_browser(window, PAGE + 'falcon9/index.html')
    out['launched'] = list(launched)
    out['launch_status'] = window.status.call_args.args[0]
    FakeAppInfo.fail = True
    window = Mock()
    win.DreamWindow._open_in_default_browser(window, PAGE + 'falcon9/index.html')
    out['fallback'] = window.browser.navigate.call_args.args[0]
    out['fallback_status'] = window.status.call_args.args[0]

    bases = {}
    for base in (PAGE, 'http://127.0.0.1:4321/short/', 'http://evil.example:4321/' + 'T' * 43 + '/',
                 'https://127.0.0.1:4321/' + 'T' * 43 + '/', PAGE + '?x=1', None, 7):
        bases[str(base)] = win.page_base({'page_base': base})
    out['bases'] = bases
    print(json.dumps(out))
""")


def _system_python_with_gtk() -> str | None:
    python = shutil.which("python3", path="/usr/bin")
    if not python:
        return None
    probe = subprocess.run([python, "-c", "import gi; gi.require_version('WebKit2', '4.1'); "
                            "gi.require_version('Gtk', '3.0'); gi.require_version('Vte', '2.91')"],
                           capture_output=True, env=_no_display_env(), timeout=60)
    return python if probe.returncode == 0 else None


def _no_display_env() -> dict:
    """No display, no session bus: nothing the probe does can reach the owner's screen or browser."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")}
    env["BROWSER"] = "/bin/false"
    return env


async def test_the_desktop_hands_a_page_click_to_the_default_browser_and_nothing_else():
    python = _system_python_with_gtk()
    if python is None:
        pytest.skip("System GTK/VTE/WebKit bindings required (as tests/desktop_native_regressions.py)")
    run = subprocess.run([python, "-c", _DESKTOP_PROBE, str(REPO)], capture_output=True, text=True,
                         env=_no_display_env(), timeout=120)
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    page = "http://127.0.0.1:4321/" + "T" * 43 + "/"
    assert out["page_click"] == {"handled": True, "ignored": True, "opened": [page + "falcon9/index.html"],
                                 "browser": False}
    assert out["page_no_gesture"] == {"handled": True, "ignored": True, "opened": [], "browser": False}
    assert out["page_navigation"]["opened"] == [page + "falcon9/index.html"] and out["page_navigation"]["ignored"]
    assert out["other_site"] == {"handled": True, "ignored": True, "opened": [], "browser": True}, "unchanged"
    assert out["studio_origin"]["handled"] is False and out["studio_origin"]["opened"] == [], "unchanged"
    assert out["page_port_lookalike"]["opened"] == [] and out["page_port_lookalike"]["browser"] is True
    assert out["launched"] == [page + "falcon9/index.html"] and "default browser" in out["launch_status"]
    assert out["fallback"] == page + "falcon9/index.html" and "Browser tab" in out["fallback_status"]
    assert out["bases"] == {page: page, "http://127.0.0.1:4321/short/": None,
                            "http://evil.example:4321/" + "T" * 43 + "/": None,
                            "https://127.0.0.1:4321/" + "T" * 43 + "/": None, page + "?x=1": None,
                            "None": None, "7": None}


async def test_the_pane_offers_open_in_browser_without_dreams_token(served):
    """Static half; tests/test_studio_stills.py clicks it in a real pane."""
    ui = (REPO / "dream" / "gui" / "static" / "index.html").read_text()
    assert 'id="artext"' in ui and 'target="_blank"' in ui and 'rel="noopener noreferrer"' in ui
    assert "'/api/page_link?path='" in ui
