"""present_fs_item_for_download: the route serves a workspace file or a zipped
folder behind the token; the tool shows a card, or names the path without Studio."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tools import context as tool_context
from dream.tools.context import ToolContext, set_context, set_studio
from dream.tools.studio import present_fs_item_for_download

UI = Path(__file__).parent.parent / "dream" / "gui" / "static" / "index.html"


def test_the_route_serves_files_and_zips_folders_inside_the_workspace(tmp_path):
    (tmp_path / "report.pdf").write_bytes(b"%PDF-fake")
    (tmp_path / "site" / "img").mkdir(parents=True)
    (tmp_path / "site" / "index.html").write_text("<p>hi</p>")
    (tmp_path / "site" / "img" / "a.png").write_bytes(b"\x89PNG")
    (tmp_path / "site" / "node_modules").mkdir()
    (tmp_path / "site" / "node_modules" / "big.js").write_text("nope")
    (tmp_path.parent / "secret.txt").write_text("no")
    srv = StudioServer(EventBus(), session={"workspace": str(tmp_path)})
    with TestClient(srv.app) as c:
        assert c.get("/api/download?path=report.pdf").status_code == 401
        r = c.get(f"/api/download?path=report.pdf&token={srv.token}")
        assert r.status_code == 200 and r.content == b"%PDF-fake"
        assert "attachment" in r.headers.get("content-disposition", "") and "report.pdf" in r.headers["content-disposition"]
        r = c.get(f"/api/download?path=site&token={srv.token}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/zip")
        names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
        assert names == {"index.html", "img/a.png"}
        r = c.get(f"/api/download?token={srv.token}")
        assert r.status_code == 200 and "workspace.zip" in r.headers["content-disposition"] or "site" in r.headers["content-disposition"] or r.status_code == 200
        for bad in ("../secret.txt", str(tmp_path.parent / "secret.txt"), "missing.txt"):
            assert c.get(f"/api/download?path={bad}&token={srv.token}").status_code == 400, bad


def test_a_name_too_long_for_the_filesystem_is_a_400_like_any_bad_path(tmp_path):
    """Fix list #75: a 300-byte file name made target.exists() raise ENAMETOOLONG, and the route answered
    500. A 300-byte name and a 5,000-byte path are refused like any path that names no workspace file:
    400, with the same body and headers; a normal download still works."""
    (tmp_path / "report.pdf").write_bytes(b"%PDF-fake")
    srv = StudioServer(EventBus(), session={"workspace": str(tmp_path)})
    long_name, long_path = "a" * 296 + ".pdf", "/".join(["d" * 249] * 20) + "x"
    assert len(long_name.encode()) == 300 and len(long_path.encode()) == 5000
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        usual = c.get("/api/download", params={"path": "missing.txt", "token": srv.token})
        assert usual.status_code == 400
        for bad in (long_name, long_path):
            r = c.get("/api/download", params={"path": bad, "token": srv.token})
            assert r.status_code == 400, (len(bad), r.status_code, r.text[:200])
            assert r.json() == usual.json()
            assert dict(r.headers) == dict(usual.headers)
        r = c.get("/api/download", params={"path": "report.pdf", "token": srv.token})
        assert r.status_code == 200 and r.content == b"%PDF-fake"


@pytest.fixture
def ws(tmp_path):
    emitted: list = []
    set_context(ToolContext(store=None, working=None, browser=None,  # type: ignore[arg-type]
                            session_id="t", workspace=tmp_path, emit=emitted.append))
    yield tmp_path, emitted
    set_studio(None)
    tool_context._CTX = None


def _text(res):
    return res["content"][0]["text"]


@pytest.mark.asyncio
async def test_the_tool_shows_a_card_with_studio_and_names_the_path_without(ws):
    root, emitted = ws
    (root / "deck.pdf").write_bytes(b"%PDF")
    (root / "out").mkdir()
    set_studio(None)
    res = await present_fs_item_for_download.handler({"path": "deck.pdf"})
    assert not res.get("is_error") and "Studio is not open" in _text(res) and str(root / "deck.pdf") in _text(res)
    assert emitted == []
    set_studio(object())
    res = await present_fs_item_for_download.handler({"path": "deck.pdf", "label": "The deck"})
    assert "is showing in Studio" in _text(res)
    (ev,) = emitted
    assert ev.kind == "studio" and ev.data == {"op": "download", "path": "deck.pdf", "label": "The deck", "kind": "file (4 bytes)"}
    res = await present_fs_item_for_download.handler({"path": "out"})
    assert emitted[-1].data["kind"] == "folder (zipped)"
    res = await present_fs_item_for_download.handler({})
    assert emitted[-1].data["path"] == "" and emitted[-1].data["label"] == "Project"
    assert present_fs_item_for_download.handler and (await present_fs_item_for_download.handler({"path": "../x"})).get("is_error")
    assert (await present_fs_item_for_download.handler({"path": "nope"})).get("is_error")


def test_the_panel_renders_a_download_card_with_the_token_in_the_link():
    ui = UI.read_text()
    assert "function renderDownload" in ui and "'/api/download?path='" in ui
    fn = ui[ui.index("function renderDownload"):ui.index("/* ---------- question forms")]
    assert "esc(d.label" in fn and "esc(d.kind" in fn and "encodeURIComponent(TOKEN)" in fn
    assert "d.op === 'download'" in ui
