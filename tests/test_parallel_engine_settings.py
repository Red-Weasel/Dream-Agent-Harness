"""DREAM-151: the engine's parallel lanes are a Dream setting.

`engine.parallel` (1-16, the engine's --parallel range; DREAM-201 raised it from 4, the range of the first lanes engine)
and `engine.slot_ctx` (0 = the engine's own size, else at least 9 tokens) live in the global runtime settings file only; a project file may not set them (S5's forbidden list covers
`engine`). `dream settings`, /settings and the Settings tab show and change them; they apply the next time Dream starts
the local engine. `dream settings check` compares them with what the running engine serves (its /props total_slots)
when they are set, and asks nothing when they are not (DREAM-148's rule).

No engine, no GPU: machx.BASE_URL points at an unroutable port or at a fake /props server on an ephemeral loopback port.
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


# --- the section -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("engine", [{}, {"parallel": 1}, {"parallel": 4}, {"parallel": 2, "slot_ctx": 16384},
                                    {"parallel": 5}, {"parallel": 16}, {"parallel": 16, "slot_ctx": 4096},
                                    {"slot_ctx": 0}, {"slot_ctx": 9}, {"slot_ctx": 2**31 - 1}])
def test_a_usable_engine_section(engine):
    _write_global({"version": 1, "engine": engine})
    assert settings.read()["engine"] == engine
    assert settings.engine_launch() == engine


BAD = [({"parallel": 0}, "engine.parallel"), ({"parallel": 17}, "engine.parallel"), ({"parallel": "2"}, "engine.parallel"),
       ({"parallel": True}, "engine.parallel"), ({"parallel": 2.0}, "engine.parallel"), ({"parallel": None}, "engine.parallel"),
       ({"slot_ctx": 8}, "engine.slot_ctx"), ({"slot_ctx": -1}, "engine.slot_ctx"), ({"slot_ctx": 2**31}, "engine.slot_ctx"),
       ({"slot_ctx": "16k"}, "engine.slot_ctx"), ({"lanes": 2}, "engine"), ("two", "engine"), ([2], "engine")]


@pytest.mark.parametrize("engine, named", BAD)
def test_an_unusable_engine_section_is_refused_naming_it_and_never_rewritten(engine, named):
    path = _write_global({"version": 1, "engine": engine})
    before = path.read_bytes()
    for read in (settings.read, settings.read_global, settings.engine_launch):
        with pytest.raises(ValueError) as info:
            read()
        assert named in str(info.value) and str(path) in str(info.value), str(info.value)
    assert path.read_bytes() == before


def test_unset_rows_say_who_decides_and_nothing_is_launched_differently():
    rows = settings.effective()
    assert rows["engine.parallel"] == settings.Effective(settings.ENGINE_PARALLEL_DEFAULT, "default")
    assert rows["engine.slot_ctx"] == settings.Effective(settings.ENGINE_SLOT_CTX_DEFAULT, "default")
    assert "1 unless changed" in settings.ENGINE_PARALLEL_DEFAULT
    assert settings.engine_launch() == {}
    assert "engine.parallel" in settings.SETTABLE and "engine.slot_ctx" in settings.SETTABLE


def test_the_default_rows_name_the_engines_lane_sizes():
    """DREAM-154: the engine's own size is 32,768 tokens per extra lane for MiMo-V2.6 and DeepSeek-V4.1; DREAM-207: so
    for Qwen3.8-Flash and the 35B-A3B class, and 65,536 for the 27B (q27_lane_ctx, its joint-step banks' default)."""
    assert settings.ENGINE_SLOT_CTX_DEFAULT == "the engine's own (32,768 tokens per extra lane; Qwen3.8-27B: 65,536)"


# --- dream settings ---------------------------------------------------------------------------------------------

def test_set_get_show_unset_through_dream_settings(capsys):
    _write_global({"version": 1, "profile": "lean", "keep": {"unknown": "section"}})
    code, out = _cli(capsys, "set", "engine.parallel", "2")
    assert code == 0, out
    note = json.loads(out)["note"]
    assert "next time Dream starts the local engine" in note and "keeps its lanes" in note
    saved = read_settings()
    assert saved["engine"] == {"parallel": 2} and saved["profile"] == "lean" and saved["keep"] == {"unknown": "section"}
    code, out = _cli(capsys, "get", "engine.parallel")
    assert code == 0 and json.loads(out)["settings"] == {"engine.parallel": {"value": 2, "source": "global"}}
    assert _cli(capsys, "set", "engine.slot_ctx", "16,384")[0] == 0
    assert read_settings()["engine"] == {"parallel": 2, "slot_ctx": 16384}
    shown = json.loads(_cli(capsys, "show")[1])["settings"]
    assert shown["engine.slot_ctx"] == {"value": 16384, "source": "global"}
    assert _cli(capsys, "unset", "engine.parallel")[0] == 0
    assert read_settings()["engine"] == {"slot_ctx": 16384}
    assert _cli(capsys, "unset", "engine.slot_ctx")[0] == 0
    assert "engine" not in read_settings()                           # an emptied section goes too
    code, out = _cli(capsys, "unset", "engine.parallel")
    assert code == 0 and "was not set" in out


@pytest.mark.parametrize("key, text", [("engine.parallel", "0"), ("engine.parallel", "17"), ("engine.parallel", "two"),
                                       ("engine.parallel", "1.5"), ("engine.parallel", "-2"), ("engine.slot_ctx", "8"),
                                       ("engine.slot_ctx", "-1"), ("engine.slot_ctx", "2147483648"),
                                       ("engine.slot_ctx", "lots"), ("engine.lanes", "2")])
def test_a_refused_value_names_the_key_and_writes_nothing(capsys, key, text):
    path = _write_global({"version": 1, "engine": {"parallel": 2}})
    before = path.read_bytes()
    code, out = _cli(capsys, "set", key, text)
    assert code == 2 and key in out, out
    assert path.read_bytes() == before


def test_sixteen_lanes_are_accepted_and_seventeen_and_zero_refused_naming_the_range(capsys):
    """DREAM-201: the engine serves up to 16 requests at once (`ie serve --parallel N`, N = 1..16, engine v0.2.0);
    engine.parallel takes the whole range, a refusal says what the range is, and the default stays unset (each
    model's own launch setting, 1): Dream never picks 16 on its own."""
    assert settings.engine_launch() == {} and "1 unless changed" in settings.ENGINE_PARALLEL_DEFAULT
    path = _write_global({"version": 1, "profile": "lean"})
    code, out = _cli(capsys, "set", "engine.parallel", "16")
    assert code == 0, out
    assert read_settings()["engine"] == {"parallel": 16}
    code, out = _cli(capsys, "get", "engine.parallel")
    assert code == 0 and json.loads(out)["settings"] == {"engine.parallel": {"value": 16, "source": "global"}}
    assert settings.engine_launch() == {"parallel": 16} and settings.MAX_LANES == 16
    before = path.read_bytes()
    for text in ("17", "0"):
        code, out = _cli(capsys, "set", "engine.parallel", text)
        assert code == 2 and "engine.parallel" in out and "from 1 to 16" in out, out
        assert path.read_bytes() == before


def test_the_project_scope_is_refused_and_writes_nothing(capsys, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    for argv in (("set", "engine.parallel", "2"), ("unset", "engine.parallel"), ("set", "engine.slot_ctx", "9")):
        code, out = _cli(capsys, *argv, "--project", "--workspace", str(ws))
        assert code == 2 and argv[1] in out and "global" in out and "project file may not set" in out, out
    assert not (ws / ".dream").exists()
    assert not settings_path().exists()


def test_a_project_file_that_sets_the_engine_is_ignored_as_a_whole(tmp_path):
    _write_global({"version": 1, "engine": {"parallel": 2}})
    ws = tmp_path / "ws"
    (ws / ".dream").mkdir(parents=True)
    (ws / ".dream" / "settings.json").write_text(json.dumps({"engine": {"parallel": 4}, "output": {"max_tokens": 4096}}))
    settings.set_workspace(ws)
    with pytest.raises(ValueError, match="may not set engine"):
        settings.read()
    assert settings.engine_launch() == {"parallel": 2}                  # the global file only, always
    rows = settings.effective(strict=False)
    assert rows["engine.parallel"] == settings.Effective(2, "global")
    assert rows["output.max_tokens"].source != "project"                 # the whole project file is ignored
    assert any("could not be used and is ignored" in note and "engine" in note for note in settings.role_notes("machx"))


def test_the_tui_settings_command_sets_and_shows_the_lanes(tmp_path):
    app = App(provider="machx", model="lead-model", workspace=tmp_path)
    app.engine = SimpleNamespace(store=None, backend=SimpleNamespace(n_ctx=None), provider=get_provider("machx"),
                                 model="lead-model")
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    asyncio.run(app._command("/settings set engine.parallel 3"))
    asyncio.run(app._command("/settings get engine.parallel"))
    asyncio.run(app._command("/settings set engine.parallel 3 --project"))
    out = _flat(app.renderer.console.file.getvalue())
    assert "engine.parallel: applies to the next time Dream starts the local engine" in out
    assert "engine.parallel 3 global" in out
    assert read_settings()["engine"] == {"parallel": 3}
    assert "project file may not set" in out


# --- dream settings check against the running engine -------------------------------------------------------------

class _Props(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        status, body = self.server.answer.get(self.path, (404, {"error": "no such path"}))
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def engine(monkeypatch):
    """A fake engine front on an ephemeral loopback port: /props with `total_slots`, /v1/models; every path kept."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Props)
    server.requests = []
    server.answer = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    monkeypatch.setattr(machx, "BASE_URL", base)

    def serve(slots):
        props = {"default_generation_settings": {"n_ctx": 32768}}
        if slots is not None:
            props["total_slots"] = slots
        server.answer = {"/props": (200, props), "/v1/models": (200, {"object": "list", "data": [{"id": "lead-model"}]})}
    yield SimpleNamespace(serve=serve, requests=server.requests, base=base, root=base.removesuffix("/v1"))
    server.shutdown()
    server.server_close()
    thread.join(5)


def _lanes_warnings(report):
    return [w for w in report["warnings"] if w.startswith("engine.")]


@pytest.mark.parametrize("lanes", [2, 16])
def test_check_reports_the_lanes_the_running_engine_serves(capsys, engine, lanes):
    engine.serve(lanes)
    _write_global({"version": 1, "engine": {"parallel": lanes}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True and _lanes_warnings(report) == []
    assert report["engine_lanes"].startswith(f"the running engine at {engine.root} serves {lanes} lanes")
    assert engine.requests == ["/props"]


@pytest.mark.parametrize("slots, served", [(1, "1 lane"), (4, "4 lanes"), (16, "16 lanes"), (None, None)])
def test_check_warns_when_the_running_engine_serves_other_lanes(capsys, engine, slots, served):
    engine.serve(slots)
    _write_global({"version": 1, "engine": {"parallel": 2}})
    report = json.loads(_cli(capsys, "check")[1])
    [warning] = _lanes_warnings(report)
    assert warning.startswith("engine.parallel: 2 lanes are set, but the running engine")
    assert "the next time Dream starts the engine" in warning
    assert warning.endswith("only to a model that serves lanes (MiMo-V2.6, DeepSeek-V4.1, Qwen3.8-Flash, the 35B-A3B "
                            "class and Qwen3.8-27B)")                                            # DREAM-154, DREAM-207
    if served:
        assert f"serves {served}" in warning and f"serves {served}" in report["engine_lanes"]
    else:
        assert "does not report" in report["engine_lanes"] and "does not report" in warning


def test_check_asks_nothing_when_the_lanes_are_not_set(capsys, engine):
    engine.serve(2)
    _write_global({"version": 1})
    report = json.loads(_cli(capsys, "check")[1])
    assert engine.requests == [] and "not set" in report["engine_lanes"]
    report = settings.check(provider="machx")
    assert "not checked here" in report["engine_lanes"]


def test_check_with_no_engine_running_is_a_line_never_an_error(capsys):
    _write_global({"version": 1, "engine": {"parallel": 2}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True and _lanes_warnings(report) == []
    assert report["engine_lanes"].startswith("engine not running; lanes not checked")


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


def test_the_settings_tab_shows_the_lanes_in_their_own_section(studio):
    _, call = studio
    view = call({"action": "settings_get"})
    lanes = next(s for s in view["sections"] if s["id"] == "lanes")
    assert lanes["title"] == "Engine lanes" and "next" in lanes["intro"]
    rows = {row["key"]: row for row in lanes["rows"]}
    assert set(rows) == {"engine.parallel", "engine.slot_ctx"}
    parallel = rows["engine.parallel"]
    assert parallel["edit"] == {"kind": "number", "min": 1, "max": 16, "value": None}
    assert parallel["origin"] == "default" and "1 unless changed" in parallel["display"]
    assert parallel["restart"] is True and parallel["restart_label"] == "Engine restart"
    assert "next time Dream starts the local engine" in parallel["applies"]
    assert "images" in parallel["help"].lower() and "1" in parallel["help"]
    # DREAM-201: the range is the engine's 1 to 16; the help says every extra lane reserves VRAM at load (out of the
    # expert cache, so more lanes can slow each one), to set the agents really run at once with a realistic context
    # per extra lane, that the engine refuses a count that does not fit, and that a change needs an engine restart.
    help_text = parallel["help"]
    assert "1 to 16" in help_text and "1 to 4" not in help_text
    for words in ("VRAM", "expert cache", "really run at once", "16,384", "refuses", "smaller context", "restart"):
        assert words in help_text, words
    # DREAM-154: both lanes models are named; images need 1 lane on MiMo only, and V4.1 lanes need two cards.
    assert "MiMo-V2.6" in parallel["help"] and "DeepSeek-V4.1" in parallel["help"]
    assert "MiMo-V2.6 takes images at 1 only" in parallel["help"] and "two cards" in parallel["help"]
    assert "32,768" in rows["engine.slot_ctx"]["help"] and "65,536 on Qwen3.8-27B" in rows["engine.slot_ctx"]["help"]
    assert rows["engine.slot_ctx"]["edit"]["min"] == 0 and rows["engine.slot_ctx"]["edit"]["max"] == 2**31 - 1
    engine = next(s for s in view["sections"] if s["id"] == "engine")
    assert not any(row["key"] in rows for row in engine["rows"])        # the layout section stays read-only


def test_the_settings_tab_saves_and_removes_the_lanes(studio):
    _, call = studio
    result = call({"action": "settings_save", "key": "engine.parallel", "value": "2"})
    assert result["key"] == "engine.parallel" and "next time Dream starts the local engine" in result["applies"]
    row = _rows(result["settings"])["engine.parallel"]
    assert row["origin"] == "global" and row["display"] == "2" and row["edit"]["value"] == 2
    call({"action": "settings_save", "key": "engine.slot_ctx", "value": 16384})
    assert read_settings()["engine"] == {"parallel": 2, "slot_ctx": 16384}
    result = call({"action": "settings_save", "key": "engine.parallel", "value": None})
    assert _rows(result["settings"])["engine.parallel"]["origin"] == "default"
    call({"action": "settings_save", "key": "engine.slot_ctx", "value": None})
    assert "engine" not in read_settings()


@pytest.mark.parametrize("key, value", [("engine.parallel", "17"), ("engine.parallel", "0"), ("engine.parallel", "x"),
                                        ("engine.parallel", True), ("engine.parallel", 2.5), ("engine.slot_ctx", "8"),
                                        ("engine.slot_ctx", {"x": 1})])
def test_the_settings_tab_refuses_a_bad_value_naming_the_key(studio, key, value):
    _, call = studio
    path = _write_global({"version": 1, "engine": {"parallel": 2}})
    before = path.read_bytes()
    error = call({"action": "settings_save", "key": key, "value": value}, status=400)
    assert key in str(error)
    assert path.read_bytes() == before


def test_the_settings_tab_takes_sixteen_lanes_and_names_the_range_when_it_refuses(studio):
    """DREAM-201: the tab's number field takes the engine's whole range, and 17 or 0 is refused with the range."""
    _, call = studio
    result = call({"action": "settings_save", "key": "engine.parallel", "value": "16"})
    row = _rows(result["settings"])["engine.parallel"]
    assert row["origin"] == "global" and row["display"] == "16" and row["edit"]["value"] == 16
    assert read_settings()["engine"] == {"parallel": 16}
    for value in ("17", 17, "0"):
        error = str(call({"action": "settings_save", "key": "engine.parallel", "value": value}, status=400))
        assert "engine.parallel" in error and "1 to 16" in error, error
    assert read_settings()["engine"] == {"parallel": 16}


def test_the_settings_tab_refuses_the_project_scope_plainly(studio, tmp_path):
    _, call = studio
    error = str(call({"action": "settings_save", "key": "engine.parallel", "value": "2", "scope": "project"},
                     status=400))
    assert "engine.parallel" in error and "this computer" in error and "cannot be used" not in error
    assert not (tmp_path / ".dream").exists() and not settings_path().exists()


def test_an_unusable_engine_entry_is_named_in_the_tab(studio):
    _, call = studio
    _write_global({"version": 1, "engine": {"parallel": 17}})
    view = call({"action": "settings_get"})
    assert view["ok"] is False and "the engine.parallel entry is not valid" in view["error"]


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
