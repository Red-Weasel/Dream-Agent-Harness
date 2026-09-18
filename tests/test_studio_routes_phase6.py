"""Phase 6 server routes: answers become prompts, tweaks persist, uploads land."""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from dream.gui.bus import EventBus
from dream.gui.server import StudioServer

PAGE = """<script>const T = /*EDITMODE-BEGIN*/{"fontSize": 16}/*EDITMODE-END*/;</script>"""


def _server(tmp_path, prompts):
    return StudioServer(EventBus(), on_prompt=prompts.append, session={"workspace": str(tmp_path)})


def test_answers_become_the_next_prompt(tmp_path):
    prompts = []
    srv = _server(tmp_path, prompts)
    with TestClient(srv.app) as c:
        h = {"X-Dream-Token": srv.token}
        assert c.post("/api/answer", json={"answers": {"a": 1}}).status_code == 401
        assert c.post("/api/answer", json={"answers": {}}, headers=h).status_code == 400
        r = c.post("/api/answer", json={"title": "Deck", "answers": {"audience": "Execs", "vibe": ["a", "b"]}}, headers=h)
        assert r.status_code == 200
    assert prompts == ["Answers to 'Deck':\n- audience: Execs\n- vibe: a, b"]


def test_tweaks_persist_into_the_file_and_refuse_what_they_should(tmp_path):
    (tmp_path / "page.html").write_text(PAGE)
    (tmp_path / "plain.html").write_text("<p>no block</p>")
    outside = tmp_path.parent / "outside.html"
    outside.write_text(PAGE)
    srv = _server(tmp_path, [])
    with TestClient(srv.app) as c:
        h = {"X-Dream-Token": srv.token}
        assert c.post("/api/tweak", json={"path": "page.html", "edits": {"fontSize": 18}}).status_code == 401
        r = c.post("/api/tweak", json={"path": "page.html", "edits": {"fontSize": 18, "dark": True}}, headers=h)
        assert r.status_code == 200 and r.json()["defaults"] == {"fontSize": 18, "dark": True}
        assert '"fontSize": 18' in (tmp_path / "page.html").read_text()
        assert c.post("/api/tweak", json={"path": "plain.html", "edits": {"a": 1}}, headers=h).status_code == 422
        assert c.post("/api/tweak", json={"path": "../outside.html", "edits": {"a": 1}}, headers=h).status_code == 400
        assert c.post("/api/tweak", json={"path": str(outside), "edits": {"a": 1}}, headers=h).status_code == 400
        assert c.post("/api/tweak", json={"path": "page.html", "edits": [1]}, headers=h).status_code == 400
        assert c.post("/api/tweak", json={"path": "missing.html", "edits": {}}, headers=h).status_code == 400
        # Gate 6 finding 2: an ABSOLUTE path inside the workspace is inside
        r = c.post("/api/tweak", json={"path": str(tmp_path / "page.html"), "edits": {"fontSize": 30}}, headers=h)
        assert r.status_code == 200 and r.json()["defaults"]["fontSize"] == 30
        assert c.post("/api/tweak", json=[1, 2], headers=h).status_code == 400
        assert c.post("/api/tweak", json={"path": "pa\u0000ge.html", "edits": {}}, headers=h).status_code == 400
        assert c.post("/api/answer", json="str", headers=h).status_code == 400
    assert outside.read_text() == PAGE


def test_uploads_land_in_the_workspace_with_safe_names(tmp_path):
    srv = _server(tmp_path, [])
    with TestClient(srv.app) as c:
        h = {"X-Dream-Token": srv.token}
        assert c.post("/api/upload", files={"file": ("x.png", b"\x89PNG", "image/png")}).status_code == 401
        r = c.post("/api/upload", files={"file": ("../../evil name?.png", b"\x89PNG", "image/png")}, headers=h)
        assert r.status_code == 200 and r.json()["path"] == "uploads/evil_name_.png"
        assert (tmp_path / "uploads" / "evil_name_.png").read_bytes() == b"\x89PNG"
        r2 = c.post("/api/upload", files={"file": ("evil_name_.png", b"2", "image/png")}, headers=h)
        second = r2.json()["path"]
        assert second.startswith("uploads/evil_name_-") and second.endswith(".png")
        assert second != r.json()["path"]
        assert (tmp_path / second).read_bytes() == b"2"
        assert (tmp_path / r.json()["path"]).read_bytes() == b"\x89PNG"
        assert c.post("/api/upload", files={"file": ("", b"", "text/plain")}, headers=h).status_code == 400
        r = c.post("/api/upload", files={"file": ("日本語 logo é.png", b"1", "image/png")}, headers=h)
        assert r.json()["path"] == "uploads/日本語_logo_é.png"
        r = c.post("/api/upload", files={"file": ("x" * 300 + ".png", b"1", "image/png")}, headers=h)
        assert r.status_code == 200 and len(Path(r.json()["path"]).name) <= 120


def test_questions_v2_is_registered_and_free():
    from dream.core import policy
    from dream.tools import registry

    assert "questions_v2" in {t.name for t in registry._BASE_TOOLS}
    assert policy.capability("questions_v2") == policy.READONLY
