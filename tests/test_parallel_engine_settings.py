"""DREAM-151's global engine lanes, retired by the owner's settings plan of 2026-10-01 ([feedback]): a model's
parallel and sub-agent context are its own launch settings, on the load screen, saved per model.

`engine.parallel` / `engine.slot_ctx` are no longer settings: `dream settings`, /settings and the Settings tab neither
show nor change them, and a refused key writes nothing. A global file that still holds an engine section -- any shape
-- loads without an error or a warning, `dream settings check` asks the engine nothing about lanes, and the next save
through the settings writer drops the section. A project file may still not set `engine` (S5's forbidden list). The
layout view (`dream engine layout`, the Settings tab's Local engine layout) still reports the lanes each server runs.

No engine, no GPU: machx.BASE_URL points at an unroutable port or at a fake server on an ephemeral loopback port.
"""
from __future__ import annotations

import asyncio
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from rich.console import Console
from starlette.testclient import TestClient

from dream import config, management
from dream.core import moe, settings
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import get_provider
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.local import machx
from dream.tui.app import App

UNROUTABLE = "http://127.0.0.1:1/v1"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    for name in ("DREAM_PROFILE", "DREAM_MAX_TOKENS", "DREAM_EVALUATOR_PROVIDER", "DREAM_EVALUATOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)
    monkeypatch.setattr(moe, "CONFIG_PATH", tmp_path / "var" / "moe.json")
    monkeypatch.setattr(machx, "BASE_URL", UNROUTABLE)
    settings.set_workspace(None)
    yield
    settings.set_workspace(None)


def _write_global(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _cli(capsys, *argv):
    code = management.main(["settings", *argv])
    return code, capsys.readouterr().out


def _flat(text):
    return " ".join(text.split())


# --- the retired section ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("engine", [{}, {"parallel": 1}, {"parallel": 5}, {"parallel": 2, "slot_ctx": 16384},
                                    {"parallel": 17}, {"parallel": "2"}, {"slot_ctx": 8}, {"lanes": 2}, "two", [2], None])
def test_an_engine_section_of_any_shape_loads_and_is_read_by_nothing(engine):
    path = _write_global({"version": 1, "engine": engine})
    before = path.read_bytes()
    assert settings.read()["engine"] == engine and settings.read_global()["engine"] == engine
    rows = settings.effective()
    assert not [key for key in rows if key.startswith("engine.")]
    report = settings.check()
    assert report["ok"] and report["errors"] == [] and report["warnings"] == [] and "engine" not in report["unknown_sections"]
    assert "engine_lanes" not in report
    assert path.read_bytes() == before                                # reading never rewrites it


def test_the_engine_names_are_gone_from_the_module():
    for name in ("ENGINE_SETTINGS", "MAX_LANES", "MAX_SLOT_CTX", "ENGINE_PARALLEL_DEFAULT", "ENGINE_SLOT_CTX_DEFAULT",
                 "engine_launch", "nested_max_workers", "MAX_WORKERS"):
        assert not hasattr(settings, name), name
    assert not any(key.startswith("engine.") for key in settings.SETTABLE)


# --- dream settings and /settings ------------------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [("set", "engine.parallel", "2"), ("set", "engine.slot_ctx", "16384"),
                                  ("unset", "engine.parallel"), ("set", "engine.parallel", "2", "--project")])
def test_dream_settings_no_longer_sets_the_engine_and_writes_nothing(capsys, tmp_path, argv):
    path = _write_global({"version": 1, "profile": "lean", "engine": {"parallel": 5}})
    before = path.read_bytes()
    ws = tmp_path / "ws"
    ws.mkdir()
    extra = ("--workspace", str(ws)) if "--project" in argv else ()
    code, out = _cli(capsys, *argv, *extra)
    assert code == 2 and argv[1] in out and "not a setting" in out, out
    assert path.read_bytes() == before and not (ws / ".dream").exists()


def test_dream_settings_show_lists_no_engine_row(capsys):
    _write_global({"version": 1, "engine": {"parallel": 5}})
    code, out = _cli(capsys, "show")
    assert code == 0 and not [key for key in json.loads(out)["settings"] if key.startswith("engine.")]


def test_the_next_save_drops_the_engine_section(capsys):
    _write_global({"version": 1, "profile": "lean", "engine": {"parallel": 5, "slot_ctx": 4096},
                   "keep": {"unknown": "section"}})
    assert _cli(capsys, "set", "output.max_tokens", "4096")[0] == 0
    saved = read_settings()
    assert "engine" not in saved and saved["output"] == {"max_tokens": 4096}
    assert saved["profile"] == "lean" and saved["keep"] == {"unknown": "section"}


def test_a_project_file_that_sets_the_engine_is_still_ignored_as_a_whole(tmp_path):
    _write_global({"version": 1, "engine": {"parallel": 2}})
    ws = tmp_path / "ws"
    (ws / ".dream").mkdir(parents=True)
    (ws / ".dream" / "settings.json").write_text(json.dumps({"engine": {"parallel": 4}, "output": {"max_tokens": 4096}}))
    settings.set_workspace(ws)
    with pytest.raises(ValueError, match="may not set engine"):
        settings.read()
    rows = settings.effective(strict=False)
    assert rows["output.max_tokens"].source != "project"                 # the whole project file is ignored
    assert any("could not be used and is ignored" in note and "engine" in note for note in settings.role_notes("machx"))


def test_the_tui_settings_command_refuses_the_engine(tmp_path):
    app = App(provider="machx", model="lead-model", workspace=tmp_path)
    app.engine = SimpleNamespace(store=None, backend=SimpleNamespace(n_ctx=None), provider=get_provider("machx"),
                                 model="lead-model")
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    asyncio.run(app._command("/settings set engine.parallel 3"))
    out = _flat(app.renderer.console.file.getvalue())
    assert "engine.parallel is not a setting" in out and not settings_path().exists()


# --- the Settings tab --------------------------------------------------------------------------------------------

@pytest.fixture
def studio(tmp_path):
    app = App(provider="machx", workspace=tmp_path)
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        def call(payload, status=200):
            reply = client.post("/api/control", headers={"x-dream-token": server.token}, json=payload)
            assert reply.status_code == status, reply.text
            return reply.json()["result"] if status == 200 else reply.json()["error"]
        yield app, call


def _rows(view):
    return {row["key"]: row for section in view["sections"] for row in section["rows"]}


def test_the_settings_tab_has_no_engine_lanes_and_reads_a_file_that_holds_them(studio):
    _, call = studio
    _write_global({"version": 1, "engine": {"parallel": 17, "slot_ctx": 8}})      # values the old rules refused
    view = call({"action": "settings_get"})
    assert view["ok"] is True and view["warnings"] == []
    assert not [s for s in view["sections"] if s["id"] == "lanes"]
    assert not {"engine.parallel", "engine.slot_ctx"} & set(_rows(view))


@pytest.mark.parametrize("key", ["engine.parallel", "engine.slot_ctx"])
def test_the_settings_tab_refuses_the_engine_keys_and_writes_nothing(studio, key):
    _, call = studio
    path = _write_global({"version": 1, "engine": {"parallel": 2}})
    before = path.read_bytes()
    error = call({"action": "settings_save", "key": key, "value": "2"}, status=400)
    assert str(error) == f"{key} cannot be changed here."
    assert path.read_bytes() == before


# --- dream engine layout: the lanes each server runs -------------------------------------------------------------

class _Front(BaseHTTPRequestHandler):
    """A plain `ie serve` (one model) or, with `servers`, a supervisor front answering /props?model=<name>."""

    def do_GET(self):
        front = self.server.front
        path, _, query = self.path.partition("?")
        if path == "/v1/models":
            body = {"object": "list", "data": [{"id": name, "object": "model", "root": name} for name in front.names]}
        elif path == "/health":
            body = {"status": "ok", "inflight": 0, "queued": 0}
            if front.supervised:
                body.update(default=front.names[0], servers={
                    name: {"state": state, "model": name, "port": 11471 + i, "cards": [i], "pid": 4000 + i,
                           "restarts": 0} for i, (name, state) in enumerate(front.states.items())})
        elif path == "/props":
            name = query.removeprefix("model=") or front.names[0]
            if front.states.get(name) != "ready":
                return self._send(503, {"error": {"code": "loading"}})
            body = {"default_generation_settings": {"n_ctx": 32768}}
            if front.slots.get(name) is not None:
                body["total_slots"] = front.slots[name]
        else:
            return self._send(404, {})
        self._send(200, body)

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def front():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Front)
    server.front = SimpleNamespace(names=["mimo"], states={"mimo": "ready"}, slots={"mimo": 2}, supervised=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(state=server.front, root=f"http://127.0.0.1:{server.server_address[1]}")
    server.shutdown()
    server.server_close()
    thread.join(5)


@pytest.mark.parametrize("slots, text", [(2, "2 lanes"), (4, "4 lanes"), (16, "16 lanes"), (1, None), (None, None)])
def test_the_layout_view_reports_a_plain_servers_lanes(front, slots, text):
    from dream.core import engine_layout
    front.state.slots = {"mimo": slots}
    view = engine_layout.layout_view(front.root)
    [server] = view["servers"]
    assert server["lanes"] == slots
    line = engine_layout.describe(view)[1]
    assert (text in line) if text else "lane" not in line


def test_the_layout_view_reports_each_supervised_servers_lanes(front, tmp_path):
    from dream.core import engine_layout
    front.state.supervised = True
    front.state.names = ["mimo", "small"]
    front.state.states = {"mimo": "ready", "small": "loading"}
    front.state.slots = {"mimo": 16, "small": 3}                     # DREAM-201: 16 from /props, 3 from the file
    view = engine_layout.layout_view(front.root)
    assert [(s["name"], s["lanes"]) for s in view["servers"]] == [("mimo", 16), ("small", None)]
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({"version": 1, "servers": [{"name": "small", "model": "x", "parallel": 3}]}))
    view = engine_layout.layout_view(front.root, layout_path=layout)
    assert [(s["name"], s["lanes"]) for s in view["servers"]] == [("mimo", 16), ("small", 3)]
    lines = engine_layout.describe(view)
    assert "16 lanes" in lines[1] and "3 lanes" in lines[2]


def test_the_settings_tab_layout_rows_carry_the_lanes(studio, front, monkeypatch):
    _, call = studio
    monkeypatch.setattr(machx, "BASE_URL", front.root + "/v1")
    view = call({"action": "settings_get"})
    engine = next(s for s in view["sections"] if s["id"] == "engine")
    servers = next(row for row in engine["rows"] if row["key"] == "engine.servers")
    assert servers["value"][0]["lanes"] == 2 and '"lanes": 2' in servers["display"]
