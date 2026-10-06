"""A0: what the owner's pane did with a page is evidence next time -- each pane report the Studio page posts
(`/api/studio_report`, DREAM-111) is written to the session's runtime log, `var/logs/runtime/<session>.jsonl`,
as a `studio_report` event. The owner's nine shows left no record of the pane's side (SPEC 2.0)."""
from __future__ import annotations

import json

import httpx
import pytest

import dream.config as config
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


def _lines(session_id):
    path = config.LOG_DIR / "runtime" / f"{session_id}.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []


async def test_each_pane_report_is_a_studio_report_event_in_the_runtime_log(tmp_path):
    srv = StudioServer(EventBus(), session={"workspace": str(tmp_path), "session_id": "20261002-sess"})
    head = {"X-Dream-Token": srv.token, "Origin": "http://127.0.0.1"}
    clean = {"path": "site/index.html", "digest": "0123abcd", "status": "loaded", "phase": "load", "errors": [], "webgl_lost": 0}
    failing = {**clean, "phase": "update", "errors": ["failed to load <script post.js> (the Studio frame loads nothing by URL)"],
               "webgl_lost": 1}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url="http://127.0.0.1") as c:
        assert (await c.post("/api/studio_report", json=clean, headers=head)).status_code == 200
        assert (await c.post("/api/studio_report", json=failing, headers=head)).status_code == 200
        assert (await c.post("/api/studio_report", json=clean)).status_code == 401, "a refused post logs nothing"
    events = _lines("20261002-sess")
    assert [e["event"] for e in events] == ["studio_report", "studio_report"]
    first, second = events
    assert first["session"] == "20261002-sess" and first["seq"] == 1 and first["ts"] > 0
    assert {k: first[k] for k in ("path", "digest", "status", "phase", "errors", "webgl_lost")} == {
        "path": "site/index.html", "digest": "0123abcd", "status": "loaded", "phase": "load", "errors": [], "webgl_lost": 0}
    assert second["seq"] == 2 and second["phase"] == "update" and second["webgl_lost"] == 1
    assert second["errors"] == ["failed to load <script post.js> (the Studio frame loads nothing by URL)"]
    (log,) = (config.LOG_DIR / "runtime").iterdir()
    assert log.name == "20261002-sess.jsonl"


async def test_a_report_with_no_session_to_name_the_log_is_kept_but_not_logged(tmp_path):
    srv = StudioServer(EventBus(), session={"workspace": str(tmp_path)})
    srv.add_report({"path": "p.html", "digest": "0123abcd", "status": "code", "phase": "load", "errors": [], "webgl_lost": 0})
    assert srv.report_mark() == 1
    assert not (config.LOG_DIR / "runtime").exists()


async def test_the_session_is_read_at_each_report_so_a_new_chat_logs_under_its_own_id(tmp_path):
    """/new changes the session while Studio stays up: the session source is a callable."""
    current = {"workspace": str(tmp_path), "session_id": "one"}
    srv = StudioServer(EventBus(), session=lambda: current)
    report = {"path": "p.html", "digest": "0123abcd", "status": "loaded", "phase": "load", "errors": [], "webgl_lost": 0}
    srv.add_report(report)
    current["session_id"] = "two"
    srv.add_report(report)
    assert [e["seq"] for e in _lines("one")] == [1] and [e["seq"] for e in _lines("two")] == [2]
