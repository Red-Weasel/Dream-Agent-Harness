"""The artifact content route and the follow-up note (DREAM-233, batch A1): `GET /api/artifact/{id}?v=N` serves
one version of a registered artifact to Dream's own page (token header, this session's ids only, workspace files
only), and `/api/prompt` with `{artifact: id}` appends the open-artifact note to the model's text while the owner's
bubble shows only their words. The `artifact` event is replayable. No browser, no model."""
from __future__ import annotations

import httpx

from dream.core.backends.base import Event
from dream.gui.artifacts import ArtifactRegistry
from dream.gui.bus import EventBus
from dream.gui.conversation import Conversation
from dream.gui.server import StudioServer

PAGE = "<!doctype html><title>Coffee Shop</title><h1>Beans</h1>"
VIDEO_PAGE = "<!doctype html><title>Clip</title><video src='clip.mp4'></video>"


def make(tmp_path, session="s1"):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    bus = EventBus()
    registry = ArtifactRegistry(ws, session, bus.publish, store=tmp_path / "store")
    srv = StudioServer(bus, on_prompt=lambda p: None, session={"workspace": str(ws), "session_id": session},
                       artifacts=registry)
    return ws, bus, registry, srv


def written(registry, ws, rel, text, tid="t1"):
    registry.observe(Event("turn_start", {}))
    registry.observe(Event("tool_use", {"id": tid, "name": "write_file", "input": {"path": rel, "content": text}}))
    (ws / rel).parent.mkdir(parents=True, exist_ok=True)
    (ws / rel).write_text(text)
    registry.observe(Event("tool_result", {"id": tid, "name": "write_file", "content": "ok", "is_error": False}))


async def test_the_route_needs_the_token_and_serves_the_asked_version(tmp_path):
    ws, bus, registry, srv = make(tmp_path)
    written(registry, ws, "coffee-shop/index.html", PAGE)
    written(registry, ws, "coffee-shop/index.html", PAGE.replace("Beans", "Roast"), tid="t2")
    aid = registry.snapshot()[0]["id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1") as c:
        assert (await c.get(f"/api/artifact/{aid}")).status_code == 401
        assert (await c.get(f"/api/artifact/{aid}?token={srv.token}")).status_code == 401, "a header, never the URL"
        head = {"X-Dream-Token": srv.token}
        r = await c.get(f"/api/artifact/{aid}", headers=head)
        assert r.status_code == 200
        body = r.json()
        assert body["content"] == PAGE.replace("Beans", "Roast") and body["version"] == 2 and body["versions"] == 2
        assert body["title"] == "Coffee Shop" and body["type"] == "page" and body["path"] == "coffee-shop/index.html"
        assert body["id"] == aid and len(body["digest"]) == 8
        r = await c.get(f"/api/artifact/{aid}?v=1", headers=head)
        assert r.status_code == 200 and r.json()["content"] == PAGE and r.json()["version"] == 1
        assert (await c.get(f"/api/artifact/{aid}?v=3", headers=head)).status_code == 404
        assert (await c.get(f"/api/artifact/{aid}?v=0", headers=head)).status_code == 400
        assert (await c.get(f"/api/artifact/{aid}?v=x", headers=head)).status_code == 400
        assert (await c.get("/api/artifact/a99", headers=head)).status_code == 404
        assert (await c.get("/api/artifact/../etc", headers=head)).status_code in (404, 400)


async def test_another_sessions_id_is_unknown_here(tmp_path):
    ws, bus, registry, srv = make(tmp_path)
    written(registry, ws, "p.html", PAGE)
    aid = registry.snapshot()[0]["id"]
    other_ws, _, other_registry, other = make(tmp_path / "other", session="s2")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other.app), base_url="http://127.0.0.1") as c:
        assert (await c.get(f"/api/artifact/{aid}", headers={"X-Dream-Token": other.token})).status_code == 404
    bare = StudioServer(EventBus(), session={"workspace": str(ws)})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bare.app), base_url="http://127.0.0.1") as c:
        assert (await c.get(f"/api/artifact/{aid}", headers={"X-Dream-Token": bare.token})).status_code == 404


async def test_the_route_prepares_local_media_like_a_show_and_says_when_it_cannot(tmp_path):
    ws, bus, registry, srv = make(tmp_path)
    written(registry, ws, "clip/index.html", VIDEO_PAGE)
    aid = registry.snapshot()[0]["id"]
    head = {"X-Dream-Token": srv.token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1") as c:
        r = await c.get(f"/api/artifact/{aid}", headers=head)
        assert r.status_code == 200 and r.json()["content"] == VIDEO_PAGE, "the raw page when the media is missing"
        assert "clip.mp4" in r.json()["note"]
        (ws / "clip" / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        r = await c.get(f"/api/artifact/{aid}", headers=head)
        assert r.status_code == 200 and "data:video/mp4;base64," in r.json()["content"] and "note" not in r.json()


async def test_a_follow_up_carries_the_open_artifact_note_to_the_model_only(tmp_path):
    ws, bus, registry, srv = make(tmp_path)
    sent = []
    srv._on_prompt = sent.append
    written(registry, ws, "coffee-shop/index.html", PAGE)
    (ws / "coffee-shop" / "style.css").write_text("body{}")
    aid = registry.snapshot()[0]["id"]
    shown = []
    with bus.subscribe() as sub:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1") as c:
            r = await c.post("/api/prompt", json={"prompt": "make the header dark", "artifact": aid},
                             headers={"X-Dream-Token": srv.token})
            assert r.status_code == 200, r.text
            r = await c.post("/api/prompt", json={"prompt": "and the footer", "artifact": "a99"},
                             headers={"X-Dream-Token": srv.token})
            assert r.status_code == 200
            r = await c.post("/api/prompt", json={"prompt": "doubled", "artifact": aid, "read_twice": True},
                             headers={"X-Dream-Token": srv.token})
            assert r.status_code == 200
        while sub.qsize():
            ev = await sub.get()
            if ev.kind == "user":
                shown.append(ev.data)
    assert sent[0].startswith("make the header dark\n\n[Dream, not from the user: the user has \"Coffee Shop\" open in the "
                              "preview panel: coffee-shop/index.html (v1, 1 lines). \"It\", \"the page\" and \"the site\" "
                              "mean this file; change it with str_replace_edit. Files beside it: style.css.]"), sent[0]
    assert sent[1] == "and the footer", "an unknown id adds nothing"
    assert sent[2].count("[Dream, not from the user") == 1 and sent[2].count("doubled") == 2, "the note is not doubled"
    assert shown == ["make the header dark", "and the footer", "doubled"], "the owner's bubble: their words only"


def test_the_artifact_event_is_replayed():
    c = Conversation()
    c.append(Event("artifact", {"op": "upsert", "id": "a1", "path": "p.html", "open": True}))
    c.append(Event("tool_preview", "x"))
    assert [e["kind"] for e in c.snapshot()["events"]] == ["artifact"]


# --- the Artifacts tab's list and the kinds beyond pages (DREAM-235, DREAM-236) --------------------------------


async def test_a_reports_source_is_served_as_written_and_a_binary_kind_has_no_content(tmp_path):
    ws, bus, registry, srv = make(tmp_path)
    written(registry, ws, "notes.md", "# Notes\n\n![chart](chart.png)\n")
    registry.observe(Event("tool_use", {"id": "b1", "name": "run_bash", "input": {"command": "plot"}}))
    (ws / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 24)
    registry.observe(Event("tool_result", {"id": "b1", "name": "run_bash", "content": "", "is_error": False}))
    by = {a["path"]: a["id"] for a in registry.snapshot()}
    head = {"X-Dream-Token": srv.token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1") as c:
        r = await c.get(f"/api/artifact/{by['notes.md']}", headers=head)
        assert r.status_code == 200 and r.json()["type"] == "report" and r.json()["title"] == "Notes"
        assert r.json()["content"] == "# Notes\n\n![chart](chart.png)\n", "the source, nothing embedded"
        r = await c.get(f"/api/artifact/{by['chart.png']}", headers=head)
        assert r.status_code == 200 and r.json()["type"] == "image" and r.json()["content"] is None
        assert r.json()["bytes"] == 32 and r.json()["version"] == 1


async def test_the_library_route_lists_this_session_and_earlier_ones_and_serves_their_versions(tmp_path):
    ws, _, old_registry, _ = make(tmp_path, session="s-old")
    written(old_registry, ws, "old/index.html", PAGE.replace("Coffee Shop", "Old Site"))
    ws, bus, registry, srv = make(tmp_path, session="s-now")
    written(registry, ws, "coffee-shop/index.html", PAGE)
    head = {"X-Dream-Token": srv.token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1") as c:
        assert (await c.get("/api/artifacts")).status_code == 401
        assert (await c.get(f"/api/artifacts?token={srv.token}")).status_code == 401, "a header, never the URL"
        r = await c.get("/api/artifacts", headers=head)
        assert r.status_code == 200
        body = r.json()
        assert body["session_id"] == "s-now"
        assert [(a["title"], a["type"], a["versions"], a["exists"]) for a in body["session"]] == [("Coffee Shop", "page", 1, True)]
        assert [(a["title"], a["session"]) for a in body["earlier"]] == [("Old Site", "s-old")]
        old = body["earlier"][0]
        r = await c.get(f"/api/artifact/{old['id']}?v=1&session=s-old", headers=head)
        assert r.status_code == 200 and "Old Site" in r.json()["content"]
        assert (await c.get(f"/api/artifact/{old['id']}?v=1&session=nope", headers=head)).status_code == 404
        assert (await c.get(f"/api/artifact/{old['id']}?v=1&session=..", headers=head)).status_code == 404
    bare = StudioServer(EventBus(), session={"workspace": str(ws)})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bare.app), base_url="http://127.0.0.1") as c:
        r = await c.get("/api/artifacts", headers={"X-Dream-Token": bare.token})
        assert r.status_code == 200 and r.json() == {"session_id": "", "session": [], "earlier": []}


async def test_open_in_app_takes_only_a_workspace_file_of_a_kind_the_panel_does_not_run(tmp_path, monkeypatch):
    from dream.gui import server as server_mod

    ws, bus, registry, srv = make(tmp_path)
    opened = []
    monkeypatch.setattr(server_mod, "open_in_app", opened.append)
    (ws / "deck.pptx").write_bytes(b"PK")
    (ws / "notes.md").write_text("# n")
    (ws / "page.html").write_text(PAGE)
    (ws / "run.sh").write_text("echo hi")
    (tmp_path / "outside.pdf").write_bytes(b"%PDF")
    head = {"X-Dream-Token": srv.token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1") as c:
        assert (await c.post("/api/open_file", json={"path": "deck.pptx"})).status_code == 401
        assert (await c.post(f"/api/open_file?token={srv.token}", json={"path": "deck.pptx"})).status_code == 401
        assert opened == []
        r = await c.post("/api/open_file", json={"path": "deck.pptx"}, headers=head)
        assert r.status_code == 200 and r.json() == {"ok": True, "name": "deck.pptx"}
        assert (await c.post("/api/open_file", json={"path": "notes.md"}, headers=head)).status_code == 200
        assert opened == [ws.resolve() / "deck.pptx", ws.resolve() / "notes.md"]
        for bad in ("page.html", "run.sh", "missing.pdf", str(tmp_path / "outside.pdf"), "../outside.pdf", ""):
            r = await c.post("/api/open_file", json={"path": bad}, headers=head)
            assert r.status_code == 400, (bad, r.text)
        assert (await c.post("/api/open_file", content=b"not json", headers=head)).status_code == 400
        assert len(opened) == 2, "nothing else reached the opener"

        def refuse(path):
            raise OSError("this machine has no xdg-open")

        monkeypatch.setattr(server_mod, "open_in_app", refuse)
        r = await c.post("/api/open_file", json={"path": "deck.pptx"}, headers=head)
        assert r.status_code == 503 and "Download it instead" in r.json()["error"]


def test_the_opener_is_xdg_open_on_the_file_and_says_when_there_is_none(tmp_path, monkeypatch):
    import shutil
    import subprocess

    from dream.gui import server as server_mod

    calls = []

    class Started:
        def wait(self):
            return 0

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return Started()

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None)
    monkeypatch.setattr(subprocess, "Popen", popen)
    server_mod.open_in_app(tmp_path / "deck.pptx")
    (argv, kwargs), = calls
    assert argv == ["/usr/bin/xdg-open", str(tmp_path / "deck.pptx")] and kwargs["start_new_session"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL, "no shell, no inherited terminal"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    try:
        server_mod.open_in_app(tmp_path / "deck.pptx")
    except OSError as exc:
        assert "xdg-open" in str(exc)
    else:
        raise AssertionError("no opener must be an error, not a silent success")
    assert len(calls) == 1
