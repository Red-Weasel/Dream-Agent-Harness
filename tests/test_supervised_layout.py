"""DREAM-144 (settings design P3, phase S3): Dream in front of `ie supervise --config <layout>` (the engine's
docs/serve_config.md): several `ie serve` children, one per card set, behind one front that routes each request by
its "model" field and lists every server at /v1/models. `machx.served_model_id()` picks the main model when several
are listed (roles.main.model, else the supervisor's default, else the first listed, as before);
`core/engine_layout.layout_view` is the read-only table of which server runs where (cards, port, ctx, state) that
`dream engine layout` prints and the Settings tab renders; the launcher can start the supervisor and stops it through
its /admin/shutdown, never a kill.

The supervisor here is a real HTTP server on an ephemeral loopback port that answers the front's paths the way the
engine's supervisor does. Nothing talks to the owner's engine; no engine process is started; the lease directory is
never /tmp/dream-inference-<uid>.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from rich.console import Console

from dream import config, management
from dream.core import engine_layout, settings
from dream.core.profiles import settings_path
from dream.local import machx


def _write(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- the fake front ------------------------------------------------------------------------------------------

class FakeSupervisor:
    """`ie supervise`'s front: /v1/models (every server, id = name, root = the model file's id), /health (status,
    default, servers with state/model/port/cards/pid), /props?model=<name> (that child's /props) and
    POST /admin/shutdown. `supervised=False` makes it a plain `ie serve`: one model, no `servers` in /health."""

    def __init__(self, servers: dict[str, dict], default: str | None = None, *, supervised: bool = True):
        self.servers = servers
        self.default = default
        self.supervised = supervised
        self.requests: list[tuple[str, str]] = []
        self.models_data = None            # not None: what /v1/models puts under "data" instead of the list
        self.shutdowns = 0
        self.on_shutdown = None
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        self.server.fake = self
        self.port = self.server.server_address[1]
        self.root = f"http://127.0.0.1:{self.port}"
        self.url = self.root + "/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def record(self, method, path):
        with self.lock:
            self.requests.append((method, path))

    def paths(self, method="GET"):
        return [path for m, path in self.requests if m == method]

    def status(self):
        states = [s["state"] for s in self.servers.values()]
        if all(state == "ready" for state in states):
            return "ok"
        if "ready" in states:
            return "degraded"
        return "loading" if "loading" in states else "unavailable"

    def models(self):
        if self.models_data is not None:
            return self.models_data
        if not self.supervised:
            return [{"id": name, "object": "model", "owned_by": "local"} for name in self.servers]
        return [{"id": name, "object": "model", "owned_by": "local", "root": s["model"], "status": s["state"]}
                for name, s in self.servers.items()]

    def health(self):
        if not self.supervised:
            return 200, {"status": "ok", "inflight": 0, "queued": 0}
        status = self.status()
        servers = {}
        for pid, (name, s) in enumerate(self.servers.items(), start=4000):
            entry = {"state": s["state"], "model": s["model"], "port": s["port"], "cards": s["cards"],
                     "pid": pid, "restarts": 0}
            if s["state"] == "ready":
                entry["health"] = {"status": "ok", "inflight": 0, "queued": 0}
            if s["state"] == "exited":
                entry["exit_code"] = 1
            servers[name] = entry
        return (200 if status in ("ok", "degraded") else 503), {
            "status": status, "inflight": 0, "queued": 0, "default": self.default, "servers": servers}

    def props(self, name):
        if not self.supervised:
            [(only, s)] = self.servers.items()
            return 200, {"default_generation_settings": {"n_ctx": s["ctx"]}, "prompt_cache_slots": 1}
        if name is None:
            if self.default is None:
                return 400, {"error": {"message": "model required", "type": "invalid_request_error",
                                       "code": "model_required"}}
            name = self.default
        entry = self.servers.get(name)
        if entry is None:
            return 404, {"error": {"message": f'model "{name}" not found; available models: '
                                              + ", ".join(self.servers),
                                   "type": "invalid_request_error", "code": "model_not_found"}}
        if entry["state"] != "ready":
            return 503, {"error": {"message": f'server "{name}" is {entry["state"]}', "type": "server_error",
                                   "code": entry["state"]}}
        return 200, {"default_generation_settings": {"n_ctx": entry["ctx"]}, "prompt_cache_slots": 1}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        fake = self.server.fake
        fake.record("GET", self.path)
        parts = urlsplit(self.path)
        if parts.path == "/v1/models":
            self._json(200, {"object": "list", "data": fake.models()})
        elif parts.path == "/health":
            self._json(*fake.health())
        elif parts.path == "/props":
            model = parse_qs(parts.query).get("model", [None])[0]
            self._json(*fake.props(model))
        else:
            self._json(404, {"error": {"message": "not found", "type": "not_found"}})

    def do_POST(self):
        fake = self.server.fake
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        fake.record("POST", self.path)
        if self.path == "/admin/shutdown":
            with fake.lock:
                fake.shutdowns += 1
            self._json(200, {"status": "stopping"})
            if fake.on_shutdown:
                fake.on_shutdown()
        else:
            self._json(404, {"error": {"message": "not found", "type": "not_found"}})


TWO = {
    "distill-card0": {"model": "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M", "cards": [0], "port": 11471, "ctx": 4096,
                      "state": "ready"},
    "coder": {"model": "other-model", "cards": [1], "port": 11472, "ctx": 16384, "state": "ready"},
}


@pytest.fixture
def front(monkeypatch):
    """A two-server supervisor, with Dream's machx module pointed at it."""
    fake = FakeSupervisor({name: dict(s) for name, s in TWO.items()}, default="coder")
    monkeypatch.setattr(machx, "BASE_URL", fake.url)
    try:
        yield fake
    finally:
        fake.close()


@pytest.fixture
def plain(monkeypatch):
    fake = FakeSupervisor({"mimo": {"model": "MiMo-V2.6-Flash-RL", "cards": [0, 1], "port": 0, "ctx": 131072,
                                    "state": "ready"}}, supervised=False)
    monkeypatch.setattr(machx, "BASE_URL", fake.url)
    try:
        yield fake
    finally:
        fake.close()


# --- machx.served_model_id: which listed model is the main one ---------------------------------------------

def test_one_listed_model_is_the_main_one_with_no_other_request(plain):
    assert machx.served_model_id() == "mimo"
    assert plain.paths() == ["/v1/models"]                       # exactly today's one request


def test_several_models_without_a_main_role_follow_the_supervisors_default(front):
    assert machx.served_model_id() == "coder"
    assert front.paths() == ["/v1/models", "/health"]


def test_roles_main_picks_the_listed_model_it_names(front):
    _write({"roles": {"main": {"model": "distill-card0"}}})
    assert machx.served_model_id() == "distill-card0"
    assert "/health" not in front.paths()                        # the role decided; no default needed


def test_roles_main_may_name_the_model_file_id_a_single_server_has(front):
    _write({"roles": {"main": {"provider": "machx", "model": "other-model"}}})
    assert machx.served_model_id() == "coder"


def test_roles_main_that_no_server_serves_falls_back_to_the_default(front):
    _write({"roles": {"main": {"model": "not-served"}}})
    assert machx.served_model_id() == "coder"


def test_roles_main_on_another_provider_is_ignored_for_the_local_engine(front):
    _write({"roles": {"main": {"provider": "openai", "model": "distill-card0"}}})
    assert machx.served_model_id() == "coder"


def test_no_default_and_no_role_is_the_first_listed_as_before(front):
    front.default = None
    assert machx.served_model_id() == "distill-card0"


def test_an_unusable_settings_file_never_stops_the_attach(front):
    _write({"roles": "broken"})
    assert machx.served_model_id() == "coder"


def test_an_unreachable_endpoint_is_none(monkeypatch):
    fake = FakeSupervisor(dict(TWO), default="coder")
    fake.close()
    monkeypatch.setattr(machx, "BASE_URL", fake.url)
    assert machx.served_model_id() is None


def test_choose_main_is_the_rule_itself():
    listed = [{"id": "a", "root": "file-a"}, {"id": "b", "root": "file-b"}, {"id": "c", "root": "file-b"}]
    assert machx.choose_main([{"id": "only"}]) == "only"
    assert machx.choose_main(listed, default="b") == "b"
    assert machx.choose_main(listed, default="b", preferred="a") == "a"
    assert machx.choose_main(listed, default="b", preferred="file-a") == "a"          # a unique file id
    assert machx.choose_main(listed, default="b", preferred="file-b") == "b"          # shared file id: not it
    assert machx.choose_main(listed, default="zzz", preferred="nope") == "a"          # neither listed: the first
    assert machx.choose_main([]) is None


# --- the settings side: roles.main is a role this version applies ---------------------------------------------

def test_roles_main_is_settable_and_shown_from_the_file(capsys):
    assert management.main(["settings", "set", "roles.main.model", "coder"]) == 0
    assert "attaches" in capsys.readouterr().out
    assert settings.main_model() == "coder"
    rows = settings.effective()
    assert rows["roles.main"] == ({"model": "coder"}, "global")
    assert "roles.main (in file)" not in rows
    session = settings.effective(session={"roles.main": {"provider": "machx", "model": "coder"}})
    assert session["roles.main"].source == "session"                  # the running session's choice still wins
    assert management.main(["settings", "unset", "roles.main"]) == 0
    assert settings.main_model() is None


def test_roles_main_admits_the_local_provider_only(capsys):
    """The main role names the served model on the local MachX endpoint, so its rule (settings._RULES, the S4
    table) admits provider machx only: another provider is refused by `set`, and a file holding one is unusable."""
    _write({"roles": {"main": {"model": "coder"}}})
    assert settings.main_model() == "coder"                           # provider-less: the local engine's
    assert management.main(["settings", "set", "roles.main.provider", "machx"]) == 0
    assert settings.main_model() == "coder"
    assert management.main(["settings", "set", "roles.main.provider", "openai"]) == 2
    assert "roles.main.provider" in capsys.readouterr().out
    _write({"roles": {"main": {"provider": "openai", "model": "gpt"}}})
    with pytest.raises(ValueError, match="roles.main.provider"):
        settings.main_model()
    assert machx.main_role() is None                                  # the attach never raises for the file
    code = management.main(["settings", "check", "--provider", "machx"])
    report = json.loads(capsys.readouterr().out)
    assert code == 2 and report["ok"] is False and "roles.main.provider" in " ".join(report["errors"])


# --- engine_layout.layout_view: who runs where ------------------------------------------------------------------

def test_the_layout_view_of_a_supervised_endpoint(front):
    _write({"roles": {"main": {"model": "distill-card0"}}})
    view = engine_layout.layout_view(front.root)
    assert view["reachable"] and view["supervised"] and view["status"] == "ok"
    assert view["endpoint"] == front.root and view["default"] == "coder"
    assert view["main"] == "distill-card0"
    assert view["servers"] == [
        {"name": "distill-card0", "model": "DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M", "cards": [0], "port": 11471,
         "ctx": 4096, "state": "ready", "pid": 4000, "restarts": 0, "lanes": None},     # DREAM-151: no total_slots here
        {"name": "coder", "model": "other-model", "cards": [1], "port": 11472, "ctx": 16384, "state": "ready",
         "pid": 4001, "restarts": 0, "lanes": None},
    ]
    assert view["layout_file"] is None and view["notes"] == []
    assert sorted(front.paths()) == sorted(["/v1/models", "/health", "/props?model=distill-card0",
                                            "/props?model=coder"])


def test_a_server_that_is_not_ready_has_no_context_unless_the_layout_file_says(front, tmp_path):
    front.servers["coder"]["state"] = "loading"
    view = engine_layout.layout_view(front.root)
    coder = next(s for s in view["servers"] if s["name"] == "coder")
    assert coder["state"] == "loading" and coder["ctx"] is None
    assert view["status"] == "degraded" and "/props?model=coder" not in front.paths()
    layout = tmp_path / "two.json"
    layout.write_text(json.dumps({"version": 1, "default": "coder", "servers": [
        {"name": "distill-card0", "model": "~/models/d.gguf", "cards": [0], "port": 11471, "ctx": 4096},
        {"name": "coder", "model": "~/models/other-model.gguf", "cards": [1], "port": 11472, "ctx": 16384}]}))
    view = engine_layout.layout_view(front.root, layout_path=layout)
    coder = next(s for s in view["servers"] if s["name"] == "coder")
    assert coder["ctx"] == 16384 and coder["cards"] == [1] and view["layout_file"] == str(layout)


def test_an_exited_server_is_shown_down_while_the_others_serve(front):
    front.servers["distill-card0"]["state"] = "exited"
    view = engine_layout.layout_view(front.root)
    assert view["status"] == "degraded"
    assert [(s["name"], s["state"]) for s in view["servers"]] == [("distill-card0", "exited"), ("coder", "ready")]
    assert view["main"] == "coder"


def test_the_layout_view_of_a_plain_server(plain):
    view = engine_layout.layout_view(plain.root)
    assert view["reachable"] and not view["supervised"] and view["status"] == "ok"
    assert view["default"] is None and view["main"] == "mimo"
    assert view["servers"] == [{"name": "mimo", "model": "mimo", "cards": None, "port": plain.port, "ctx": 131072,
                                "state": "ready", "pid": None, "restarts": None, "lanes": None}]


def test_an_unreachable_endpoint_is_reported_not_raised():
    fake = FakeSupervisor(dict(TWO), default="coder")
    fake.close()
    view = engine_layout.layout_view(fake.root)
    assert view["reachable"] is False and view["servers"] == [] and view["main"] is None
    assert view["notes"] and fake.root in view["notes"][0]


@pytest.mark.parametrize("data", [5, "nope", {"id": "x"}])
def test_a_malformed_model_list_is_a_note_not_an_exception(front, data):
    """Gate finding 3: {"data": 5} raised TypeError out of layout_view."""
    front.models_data = data
    view = engine_layout.layout_view(front.root)
    assert view["reachable"] and view["supervised"]
    assert [s["name"] for s in view["servers"]] == ["distill-card0", "coder"]      # /health still describes them
    assert any("without a model list" in note for note in view["notes"])
    assert view["main"] is None


def test_a_layout_file_that_cannot_be_read_is_a_note_not_a_failure(front, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    view = engine_layout.layout_view(front.root, layout_path=bad)
    assert view["reachable"] and len(view["servers"]) == 2
    assert any(str(bad) in note for note in view["notes"])


def test_describe_is_one_line_per_server(front):
    lines = engine_layout.describe(engine_layout.layout_view(front.root))
    assert len(lines) == 3 and lines[0].startswith("supervised")
    assert "distill-card0" in lines[1] and "card 0" in lines[1] and "port 11471" in lines[1] \
        and "ctx 4096" in lines[1] and "ready" in lines[1]
    assert "coder" in lines[2] and "main" in lines[2] and "main" not in lines[1]


def test_dream_engine_layout_prints_the_view(front, capsys):
    assert management.main(["engine", "layout", "--url", front.root]) == 0
    view = json.loads(capsys.readouterr().out)
    assert [s["name"] for s in view["servers"]] == ["distill-card0", "coder"] and view["main"] == "coder"
    assert management.main(["engine", "layout", "--url", front.root, "--text"]) == 0
    out = capsys.readouterr().out
    assert "distill-card0" in out and "coder" in out and "port 11472" in out


def test_dream_engine_layout_defaults_to_dreams_endpoint(front, capsys):
    assert management.main(["engine", "layout"]) == 0
    assert json.loads(capsys.readouterr().out)["endpoint"] == front.root


def test_the_entrypoint_dispatches_engine_to_management(monkeypatch, front, capsys):
    import dream.__main__ as entry
    monkeypatch.setattr("sys.argv", ["dream", "engine", "layout", "--url", front.root])
    with pytest.raises(SystemExit) as stop:
        entry.main()
    assert stop.value.code == 0 and json.loads(capsys.readouterr().out)["supervised"] is True


# --- the launcher: start `ie supervise`, wait for the layout, stop it through the front ----------------------

class _Proc:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


def test_supervisor_ready_means_every_server_finished_loading_and_one_serves(front):
    assert machx.supervisor_ready() is True
    front.servers["coder"]["state"] = "loading"
    assert machx.supervisor_ready() is False
    front.servers["coder"]["state"] = "exited"
    assert machx.supervisor_ready() is True                          # degraded, but done loading
    front.servers["distill-card0"]["state"] = "exited"
    assert machx.supervisor_ready() is False


def test_supervisor_ready_is_false_for_a_plain_server_or_nothing(plain, monkeypatch):
    assert machx.supervisor_ready() is False
    monkeypatch.setattr(machx, "BASE_URL", "http://127.0.0.1:1/v1")
    assert machx.supervisor_ready() is False


def test_wait_ready_takes_the_readiness_it_is_given(front, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    front.servers["coder"]["state"] = "loading"
    asked = []

    def serving():
        asked.append(1)
        return True                                                  # the front is up: today's test is met
    monkeypatch.setattr(machx, "is_serving", serving)
    assert machx.wait_ready(_Proc(), ready=machx.supervisor_ready, stall_s=0.3, poll_s=0.05) is False
    assert asked == []                                               # the given predicate decided, not is_serving
    front.servers["coder"]["state"] = "ready"
    assert machx.wait_ready(_Proc(), ready=machx.supervisor_ready, stall_s=1, poll_s=0.05) is True


def test_supervise_builds_the_ie_supervise_command_on_dreams_front(monkeypatch, tmp_path):
    import contextlib

    import dream.local.engine_guard as guard
    import dream.local.load_lock as load_lock
    layout = tmp_path / "two.json"
    layout.write_text("{}")
    (tmp_path / "var").mkdir()
    monkeypatch.setattr(config, "VAR_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(machx, "HOST", "127.0.0.1")
    monkeypatch.setattr(machx, "PORT", 11470)
    spawned = []

    def spawn(command, *, keep_hot, log_path, **kwargs):
        spawned.append((command, keep_hot, kwargs))
        return SimpleNamespace(pid=4242)
    monkeypatch.setattr(guard, "spawn", spawn)

    @contextlib.contextmanager
    def lock(port):
        yield os.open(tmp_path, os.O_RDONLY)
    monkeypatch.setattr(load_lock, "load_lock", lock)
    proc = machx.supervise(layout, keep_hot=True)
    assert proc.pid == 4242
    [(command, keep_hot, kwargs)] = spawned
    assert keep_hot is True and kwargs["cwd"] == machx.MACHX_DIR and kwargs["start_new_session"] is True
    assert kwargs["grace_s"] == machx.SUPERVISOR_STOP_S == 600.0       # the guard's SIGKILL waits for its shutdown
    assert command[:2] == ["bash", "-lc"]
    assert command[-1].endswith(f"source scripts/env.sh && exec ./build/src/ie supervise --config {layout} "
                                "--host 127.0.0.1 --port 11470")
    assert (tmp_path / "var" / "machx.pid").read_text() == "4242"
    with pytest.raises(ValueError, match="not found"):
        machx.supervise(tmp_path / "missing.json")
    assert len(spawned) == 1                                         # nothing spawned for a missing layout


def test_the_engine_guard_gives_a_supervisor_its_own_shutdown_time(tmp_path, monkeypatch):
    """Gate finding 4: if Dream dies, the guard's SIGKILL 10 s after SIGTERM would orphan children still loading.
    A supervisor's watchdog is started with SUPERVISOR_STOP_S; a plain engine keeps the default grace."""
    import dream.local.engine_guard as guard
    monkeypatch.delenv(guard.GRACE_ENV, raising=False)
    log = tmp_path / "machx.log"
    procs = []
    try:
        for grace, expected in ((None, "120.0"), (machx.SUPERVISOR_STOP_S, "600.0")):
            proc = guard.spawn(["sleep", "30"], log_path=log, grace_s=grace, start_new_session=True)
            procs.append(proc)
            assert proc.dream_watchdog is not None
            # Right after Popen the watchdog's exec may not have finished: its /proc cmdline can still be empty (an
            # IndexError in the DREAM-150 gate's run) or the parent's, as for _hold below (0f98441). Wait until it
            # names the guard's own script, the path the watchdog is started with.
            cmdline, script = Path(f"/proc/{proc.dream_watchdog.pid}/cmdline"), str(Path(guard.__file__).resolve())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and script.encode() not in cmdline.read_bytes():
                time.sleep(0.01)
            argv = cmdline.read_bytes().split(b"\0")
            assert argv[5].decode() == expected                       # owner_fd engine_fd GRACE term owner engine
    finally:
        for proc in procs:
            proc.kill()
            proc.wait(timeout=5)
            if proc.dream_watchdog is not None:
                proc.dream_watchdog.wait(timeout=10)                  # leaves with the engine


def test_the_stop_message_never_says_down_for_a_process_still_there(monkeypatch, tmp_path):
    from dream.local import launcher
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    assert launcher._stop_message(True) == "GPU memory freed"
    assert launcher._stop_message(False) == "MachX was already down"  # nothing recorded
    proc = subprocess.Popen(["sleep", "300"])
    (tmp_path / "machx.pid").write_text(str(proc.pid), encoding="utf-8")
    try:
        assert machx.left_running() == proc.pid
        text = launcher._stop_message(False)
        assert "still stopping" in text and str(proc.pid) in text and "/admin/shutdown" in text
        assert not text.startswith("MachX was already down")
    finally:
        proc.kill()
        proc.wait(timeout=5)
    assert machx.left_running() is None and launcher._stop_message(False) == "MachX was already down"


def _hold(*argv):
    """A process whose /proc cmdline carries `argv` after the shell's own: `sh -c 'sleep 300' <argv...>`.
    Right after Popen returns, /proc/<pid>/cmdline can still be empty (the exec has not finished), so wait for it:
    otherwise stop() may read an empty command line and take the plain-engine path."""
    proc = subprocess.Popen(["sh", "-c", "sleep 300", *argv], start_new_session=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and argv[0].encode() not in Path(f"/proc/{proc.pid}/cmdline").read_bytes():
        time.sleep(0.01)
    return proc


def test_stop_asks_a_supervisor_to_shut_down_and_never_kills_it(front, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    proc = _hold("supervise", "--config", "two.json")
    (tmp_path / "machx.pid").write_text(str(proc.pid), encoding="utf-8")
    front.on_shutdown = lambda: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)   # the supervisor stops itself
    signalled = []
    monkeypatch.setattr(machx, "_signal_group", lambda pid, sig: signalled.append(sig))
    try:
        assert machx.stop(timeout=3, supervisor_timeout=10) is True
        assert front.shutdowns == 1 and signalled == []
        assert proc.wait(timeout=5) != 0
        assert not (tmp_path / "machx.pid").exists()
    finally:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def test_stop_falls_back_to_sigterm_when_the_front_does_not_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    monkeypatch.setattr(machx, "BASE_URL", "http://127.0.0.1:1/v1")
    proc = _hold("supervise", "--config", "two.json")
    (tmp_path / "machx.pid").write_text(str(proc.pid), encoding="utf-8")
    signalled = []
    real = machx._signal_group

    def record(pid, sig):
        signalled.append(sig)
        real(pid, sig)
    monkeypatch.setattr(machx, "_signal_group", record)
    try:
        assert machx.stop(timeout=3, supervisor_timeout=10) is True
        assert signalled == [signal.SIGTERM]                         # its orderly stop, and never SIGKILL
        assert proc.wait(timeout=5) != 0
    finally:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def test_a_supervisor_still_stopping_after_the_wait_is_left_running(front, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    proc = _hold("supervise", "--config", "two.json")
    (tmp_path / "machx.pid").write_text(str(proc.pid), encoding="utf-8")
    signalled = []
    monkeypatch.setattr(machx, "_signal_group", lambda pid, sig: signalled.append(sig))
    try:
        assert machx.stop(timeout=1, supervisor_timeout=0.5) is False
        assert front.shutdowns == 1 and signalled == [] and proc.poll() is None
        assert (tmp_path / "machx.pid").exists()                     # a later stop can try again
    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def test_a_plain_engine_is_stopped_as_before(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    proc = subprocess.Popen(["sleep", "300"], start_new_session=True)
    (tmp_path / "machx.pid").write_text(str(proc.pid), encoding="utf-8")
    posted = []
    monkeypatch.setattr(machx.httpx, "post", lambda *a, **k: posted.append(a))
    assert machx.stop(timeout=3) is True
    assert posted == [] and proc.wait(timeout=5) != 0


async def test_dream_local_with_a_layout_starts_the_supervisor_not_the_picker(monkeypatch, tmp_path):
    from dream.local import launcher
    layout = tmp_path / "two.json"
    layout.write_text("{}")
    monkeypatch.setenv("DREAM_MACHX_LAYOUT", str(layout))
    monkeypatch.setattr(launcher.machx, "available", lambda: True)
    monkeypatch.setattr(launcher.machx, "is_serving", lambda *a, **k: False)
    monkeypatch.setattr(launcher.machx, "list_models_detailed", lambda: pytest.fail("the picker ran"))
    monkeypatch.setattr(launcher.Renderer, "show_logo", lambda self: None)
    ran = []

    async def serve_layout(r, path, **kw):
        ran.append((path, kw))
    monkeypatch.setattr(launcher, "serve_layout_and_run", serve_layout)
    await launcher.run_local(consolidate_on_exit=False, keep_hot=True)
    assert ran == [(layout, {"consolidate_on_exit": False, "keep_hot": True})]


async def test_serve_layout_and_run_waits_for_the_layout_and_runs_on_its_main_model(monkeypatch, tmp_path):
    from dream.local import launcher
    from dream.tui.render import Renderer
    layout = tmp_path / "two.json"
    layout.write_text("{}")
    console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    r = Renderer(console)
    calls = []
    monkeypatch.setattr(launcher.machx, "supervise", lambda path, *, keep_hot: calls.append(("supervise", path, keep_hot)) or _Proc())
    monkeypatch.setattr(launcher.machx, "wait_ready", lambda proc, **kw: calls.append(("wait", kw["ready"])) or True)
    monkeypatch.setattr(launcher.machx, "stop", lambda: calls.append(("stop",)))
    view = {"reachable": True, "supervised": True, "status": "ok", "default": "coder", "main": "coder",
            "endpoint": "http://127.0.0.1:11470", "layout_file": None, "notes": [], "servers": [
                {"name": "coder", "model": "other-model", "cards": [1], "port": 11472, "ctx": 16384,
                 "state": "ready", "pid": 1, "restarts": 0}]}
    monkeypatch.setattr(launcher.engine_layout, "layout_view", lambda *a, **k: view)

    async def harness(r, **kw):
        calls.append(("harness", kw))
    monkeypatch.setattr(launcher, "_run_harness", harness)
    monkeypatch.setattr(launcher.atexit, "register", lambda fn: calls.append(("atexit", fn)))
    await launcher.serve_layout_and_run(r, layout, consolidate_on_exit=True, keep_hot=False)
    assert calls[0] == ("supervise", layout, False) and calls[1] == ("wait", launcher.machx.supervisor_ready)
    assert ("atexit", launcher.machx.stop) in calls
    assert calls[-1] == ("harness", {"provider": "machx", "model": "coder", "consolidate_on_exit": True,
                                     "keep_hot": False})
    out = console.file.getvalue()
    assert "coder" in out and "card 1" in out and "port 11472" in out


async def test_serve_layout_and_run_stops_a_layout_that_never_finishes_loading(monkeypatch, tmp_path):
    from dream.local import launcher
    from dream.tui.render import Renderer
    layout = tmp_path / "two.json"
    layout.write_text("{}")
    console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    calls = []
    monkeypatch.setattr(launcher.machx, "supervise", lambda path, *, keep_hot: _Proc())
    monkeypatch.setattr(launcher.machx, "wait_ready", lambda proc, **kw: False)
    monkeypatch.setattr(launcher.machx, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(launcher, "_run_harness", lambda *a, **k: pytest.fail("ran without a ready layout"))
    await launcher.serve_layout_and_run(Renderer(console), layout, consolidate_on_exit=True, keep_hot=False)
    assert calls == ["stop"] and "didn't come up" in console.file.getvalue()
