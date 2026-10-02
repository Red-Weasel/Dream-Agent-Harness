"""DREAM-148 -- follow-ups the settings gates flagged as non-blocking.

1. An unusable Council file (var/moe.json) was silent: moe.load_config() returns None for it, so the saved Council
   was simply off. It stays off and the file is never rewritten, but Dream now says so, naming the file and the
   offending entry: a note at session start (the terminal and Studio's bus, on every backend), a warning in
   `dream settings check` and `/settings check`, and the Settings tab's Council section.
4. `dream settings check` and `/settings check` ask the local engine's /v1/models (settings design 3.5) whether the
   model each role runs on the engine is served -- a listed id, or the `root` (the model file's id) of exactly one
   listed server. An engine that does not answer is one line, never an error; a model it does not serve is a
   warning naming the role and the model.

No engine, no GPU: the Council file is a temporary one, machx.BASE_URL points at an unroutable port or at a fake
/v1/models server on an ephemeral loopback port, and nothing talks to the owner's engine ports.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream import config, management
from dream.core import moe, settings
from dream.core.profiles import settings_path
from dream.core.providers import get_provider
from dream.local import machx
from dream.tui.app import App
from test_settings_hardening import HOSTILE, _raw, _started_app, _system_texts, studio  # noqa: F401

LEAD = "lead-model"
UNROUTABLE = "http://127.0.0.1:1/v1"
GOOD_COUNCIL = {"orchestrator": "machx", "advisors": ["codex"], "advisor_models": {"codex": "gpt-6-astra"}}


@pytest.fixture(autouse=True)
def council_file(tmp_path, monkeypatch):
    """The Council file of this test only; the owner's var/moe.json is never read or written here."""
    for name in ("DREAM_PROFILE", "DREAM_MAX_TOKENS", "DREAM_EVALUATOR_PROVIDER", "DREAM_EVALUATOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 131072)
    path = tmp_path / "var" / "moe.json"
    monkeypatch.setattr(moe, "CONFIG_PATH", path)
    monkeypatch.setattr(machx, "BASE_URL", UNROUTABLE)
    settings.set_workspace(None)
    yield path
    settings.set_workspace(None)


def _council(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path.read_bytes(), os.stat(path).st_mtime_ns


def _untouched(path, before):
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before


def _write_global(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _cli(capsys, *argv):
    code = management.main(["settings", *argv])
    return code, capsys.readouterr().out


def _flat(text: str) -> str:
    """Terminal output with Rich's line wrapping undone (a long note wraps at the console's width)."""
    return " ".join(text.split())


def _app(provider="machx"):
    app = App(provider=provider, model=LEAD)
    app.engine = SimpleNamespace(store=None, backend=SimpleNamespace(n_ctx=None), provider=get_provider(provider),
                                 model=LEAD)
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    return app


# --- 1. an unusable Council file is said, not silent ----------------------------------------------------------

# (what the file holds, the entry the report must name)
BAD_COUNCILS = [
    ({"orchestrator": "nowhere", "advisors": []}, "orchestrator"),
    ({"orchestrator": "machx", "advisors": "codex"}, "advisors"),
    ({"orchestrator": "machx", "advisors": ["codex", "codex"]}, "advisors"),
    ({"orchestrator": "machx", "advisors": ["invented"]}, "advisors"),
    ({"orchestrator": "machx", "advisors": [], "max_concurrency": 0}, "max_concurrency"),
    ({"orchestrator": "machx", "advisors": [], "timeout_seconds": "90"}, "timeout_seconds"),
    ({"orchestrator": "machx", "advisors": ["codex"], "legal_review": "false"}, "legal_review"),
    ({"orchestrator": "machx", "advisors": ["codex"], "advisor_models": {"gemini": "m"}}, "advisor_models"),
    ({"orchestrator": "machx", "advisors": ["codex"], "advisor_models": {"codex": "bad\nmodel"}},
     "advisor_models.codex"),
    ({"orchestrator": "machx", "advisors": ["codex"], "advisor_efforts": {"grok": "high"}}, "advisor_efforts"),
    ({"orchestrator": "anthropic", "advisors": ["codex"], "advisor_efforts": {"codex": "invented"}},
     "advisor_efforts.codex"),
    ({"orchestrator": "anthropic", "advisors": [], "orchestrator_effort": "ultra"}, "orchestrator_effort"),
    ({"orchestrator": "machx", "advisors": [], "surprise": 1}, "surprise"),
]


@pytest.mark.parametrize("data, key", BAD_COUNCILS)
def test_the_problem_names_the_offending_entry_and_the_council_stays_off(council_file, data, key):
    before = _council(council_file, data)
    assert moe.load_config() is None                       # unchanged: an unusable file is no saved Council
    entry, reason = moe.config_problem()
    assert entry == key and reason, (entry, reason)
    note = moe.config_note()
    assert str(council_file) in note and f"-- {key}: {reason}." in note and "never rewrites" in note
    assert f"-- the {key} entry is not valid." in moe.config_note(entry_only=True)     # the tab's wording
    _untouched(council_file, before)


@pytest.mark.parametrize("text, needle", [
    ("{not json", "not valid JSON"),
    ("[]", "object"),
    ('"just a string"', "object"),
    (b"\xff\xfe\x00junk", "UTF-8"),
])
def test_a_file_that_is_no_council_object_is_named_too(council_file, text, needle):
    council_file.parent.mkdir(parents=True, exist_ok=True)
    council_file.write_bytes(text if isinstance(text, bytes) else text.encode())
    before = (council_file.read_bytes(), os.stat(council_file).st_mtime_ns)
    assert moe.load_config() is None
    entry, reason = moe.config_problem()
    assert entry is None and needle in reason
    assert str(council_file) in moe.config_note() and moe.config_note() == moe.config_note(entry_only=True)
    _untouched(council_file, before)


def test_an_absent_or_usable_file_has_nothing_to_say(council_file):
    assert moe.config_problem() is None and moe.config_note() is None
    _council(council_file, GOOD_COUNCIL)
    assert moe.load_config() is not None
    assert moe.config_problem() is None and moe.config_note() is None


def test_a_hostile_unknown_key_is_shown_safely_and_bounded(council_file):
    _council(council_file, {"orchestrator": "machx", "advisors": [], "k" + HOSTILE: 1, "x" * 1_000_000: 2})
    note = moe.config_note()
    assert not _raw(note) and "PWNED" in note and len(note) < 1200


@pytest.mark.parametrize("provider", ["anthropic", "codex", "gemini", "grok"])
async def test_the_start_note_names_the_file_and_the_entry_on_every_backend(tmp_path, monkeypatch, council_file,
                                                                           provider):
    before = _council(council_file, {"orchestrator": "machx", "advisors": ["codex", "codex"]})
    app = _started_app(monkeypatch, tmp_path, provider, SimpleNamespace())
    await app.start()
    out = _flat(app.renderer.console.file.getvalue())
    assert str(council_file) in out and "advisors" in out and "Council is off" in out
    assert any(str(council_file) in text and "advisors" in text for text in _system_texts(app))
    _untouched(council_file, before)


async def test_the_openai_compatible_backend_gets_the_council_note_at_start_too(tmp_path, monkeypatch, council_file):
    from test_cache_friendly_head import FakeEngine, backend
    before = _council(council_file, {"orchestrator": "machx", "advisors": [], "legal_review": "no"})
    app = _started_app(monkeypatch, tmp_path, "machx", backend(FakeEngine(lead=[{"text": "ok"}]), model=LEAD))
    await app.start()
    out = app.renderer.console.file.getvalue()
    assert out.count(str(council_file)) == 1 and "legal_review" in out
    assert sum(str(council_file) in text for text in _system_texts(app)) == 1
    _untouched(council_file, before)


async def test_no_start_note_for_an_absent_or_usable_council_file(tmp_path, monkeypatch, council_file):
    app = _started_app(monkeypatch, tmp_path, "anthropic", SimpleNamespace())
    await app.start()
    assert "moe.json" not in app.renderer.console.file.getvalue()
    _council(council_file, GOOD_COUNCIL)
    app = _started_app(monkeypatch, tmp_path, "codex", SimpleNamespace())
    await app.start()
    assert "moe.json" not in app.renderer.console.file.getvalue()
    assert not any("Council" in text for text in _system_texts(app))


async def test_a_hostile_council_file_cannot_reach_the_console_raw(tmp_path, monkeypatch, council_file):
    _council(council_file, {"orchestrator": "machx", "advisors": [], "k" + HOSTILE: 1})
    app = _started_app(monkeypatch, tmp_path, "anthropic", SimpleNamespace())
    await app.start()
    out = app.renderer.console.file.getvalue()
    assert "PWNED" in out and not _raw(out)
    assert all(not _raw(text) for text in _system_texts(app))


def test_dream_settings_check_warns_naming_the_file_and_the_entry(capsys, council_file):
    before = _council(council_file, {"orchestrator": "machx", "advisors": ["codex"],
                                     "advisor_models": {"codex": "bad‮model"}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True and report["errors"] == []       # a warning, not an error
    [warning] = [w for w in report["warnings"] if "moe.json" in w]
    assert str(council_file) in warning and "advisor_models.codex" in warning and "Council is off" in warning
    assert "‮" not in out
    _untouched(council_file, before)
    council_file.unlink()                                                      # absent: nothing to say
    assert not any("moe.json" in w for w in json.loads(_cli(capsys, "check")[1])["warnings"])


def test_check_still_names_the_council_file_when_the_settings_file_is_unusable(capsys, council_file):
    _council(council_file, "{not json")
    _write_global({"roles": {"verifier": {"model": ""}}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 2 and report["ok"] is False and "roles.verifier" in " ".join(report["errors"])
    assert any(str(council_file) in w and "not valid JSON" in w for w in report["warnings"])


def test_the_tui_check_says_it_too(council_file):
    before = _council(council_file, {"orchestrator": "machx", "advisors": [], "max_concurrency": 99})
    app = _app()
    asyncio.run(app._command("/settings check"))
    out = _flat(app.renderer.console.file.getvalue())
    assert str(council_file) in out and "max_concurrency" in out
    _untouched(council_file, before)


def test_the_settings_tab_council_section_names_the_file_and_the_entry(studio, council_file):
    _, _, _, call = studio
    before = _council(council_file, {"orchestrator": "machx", "advisors": ["codex"],
                                     "advisor_efforts": {"codex": "invented"}})
    view = call({"action": "settings_get"})
    section = next(s for s in view["sections"] if s["id"] == "council")
    assert section["rows"] == []
    assert str(council_file) in section["intro"] and "advisor_efforts.codex" in section["intro"]
    assert "Council is off" in section["intro"] and "never rewrites" in section["intro"]
    assert any(str(council_file) in w and "advisor_efforts.codex" in w for w in view["warnings"])
    # The tab names the entry, never a value from the file (its rule since DREAM-143).
    assert "invented" not in section["intro"] and not any("invented" in w for w in view["warnings"])
    # A save aimed at the Council (a page loaded before the file broke) names the problem and writes nothing.
    error = call({"action": "settings_save", "key": "council.advisor.codex", "value": {"model": "m", "effort": ""}},
                 status=400)
    assert "council.advisor.codex" in error and "advisor_efforts.codex" in error and "nothing was saved" in error
    assert "invented" not in error
    _untouched(council_file, before)


def test_the_settings_tab_without_a_council_file_is_unchanged(studio, council_file):
    _, _, _, call = studio
    view = call({"action": "settings_get"})
    section = next(s for s in view["sections"] if s["id"] == "council")
    assert "no Council is saved yet" in section["intro"] and "Council panel" in section["intro"]
    assert not any("moe.json" in w for w in view["warnings"])
    assert not council_file.exists()


# --- 4. role model names against the engine's /v1/models ----------------------------------------------------

class _Models(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        status, body = self.server.answer
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def engine(monkeypatch):
    """A fake engine front: GET /v1/models answers what `serve` set; every request path is kept."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Models)
    server.requests = []
    server.answer = (200, {"object": "list", "data": []})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    monkeypatch.setattr(machx, "BASE_URL", base)

    def serve(*entries, status=200, body=None):
        server.answer = (status, body if body is not None else {"object": "list", "data": [
            {"object": "model", "owned_by": "local", **entry} for entry in entries]})
    yield SimpleNamespace(serve=serve, requests=server.requests, base=base)
    server.shutdown()
    server.server_close()
    thread.join(5)


def _model_warnings(report):
    return [w for w in report["warnings"] if " is not served" in w or "ambiguous" in w]


def test_role_models_the_engine_serves_by_id_or_root_pass(capsys, engine):
    engine.serve({"id": "coder", "root": "Qwen3-Coder-30B-A3B-Q4_K_M"}, {"id": "checker", "root": "Distill-1.5B"})
    _write_global({"roles": {"main": {"model": "coder"}, "verifier": {"model": "checker"},
                             "subagents": {"default": {"model": "Qwen3-Coder-30B-A3B-Q4_K_M", "provider": "machx"}},
                             "evaluator": {"model": "Distill-1.5B"}}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True and _model_warnings(report) == []
    assert report["model_names"].startswith("checked against " + engine.base + "/models")
    assert engine.requests == ["/v1/models"]


def test_a_role_model_the_engine_does_not_serve_is_a_warning_naming_the_role_and_model(capsys, engine):
    engine.serve({"id": "lead-model"})
    _write_global({"roles": {"verifier": {"model": "ghost-model"}, "filer": {"model": "lead-model"},
                             "subagents": {"researcher": {"model": "phantom"}}}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True and report["errors"] == []
    warnings = _model_warnings(report)
    assert len(warnings) == 2
    assert any(w.startswith("roles.verifier:") and "ghost-model" in w and "lead-model" in w for w in warnings)
    assert any(w.startswith("roles.subagents.researcher:") and "phantom" in w for w in warnings)


def test_an_engine_that_does_not_answer_is_a_line_never_an_error(capsys, monkeypatch):
    monkeypatch.setattr(machx, "BASE_URL", UNROUTABLE)
    _write_global({"roles": {"verifier": {"model": "checker"}, "main": {"model": "coder"}}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True and report["errors"] == []
    assert "engine not running; model names not checked" in report["model_names"]
    assert _model_warnings(report) == []


@pytest.mark.parametrize("status, body", [
    (503, {"error": "loading"}),
    (200, b"not json"),
    (200, {"object": "list"}),
    (200, ["not", "an", "object"]),
    (404, {"error": "no such path"}),
])
def test_an_answer_without_a_model_list_is_not_checked_either(capsys, engine, status, body):
    engine.serve(status=status, body=body)
    _write_global({"roles": {"verifier": {"model": "checker"}}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True
    assert "model names not checked" in report["model_names"] and _model_warnings(report) == []


def test_a_model_file_two_servers_share_is_ambiguous(capsys, engine):
    engine.serve({"id": "distill-card0", "root": "Distill-1.5B"}, {"id": "distill-card1", "root": "Distill-1.5B"})
    _write_global({"roles": {"verifier": {"model": "Distill-1.5B"}, "filer": {"model": "distill-card1"}}})
    report = json.loads(_cli(capsys, "check")[1])
    [warning] = _model_warnings(report)
    assert warning.startswith("roles.verifier:") and "ambiguous" in warning
    assert "distill-card0" in warning and "distill-card1" in warning


def test_only_models_the_local_engine_runs_are_checked(capsys, engine):
    engine.serve({"id": "lead-model"})
    _write_global({"roles": {"critic": {"provider": "codex", "model": "gpt-6"},
                             "reviewer": {"provider": "anthropic", "model": "opus"},
                             "subagents": {"default": {"provider": "anthropic", "model": "haiku"}}}})
    report = json.loads(_cli(capsys, "check")[1])
    assert engine.requests == [] and "nothing to check" in report["model_names"]      # no probe at all
    # A provider-less evaluator runs on the session's provider: MachX's model on machx, not on anthropic.
    _write_global({"roles": {"evaluator": {"model": "judge"}, "verifier": {"model": "checker"}}})
    report = json.loads(_cli(capsys, "check", "--provider", "anthropic")[1])
    assert engine.requests == [] and _model_warnings(report) == []        # the verifier is not applied there
    report = json.loads(_cli(capsys, "check")[1])
    assert engine.requests == ["/v1/models"]
    assert {w.split(":")[0] for w in _model_warnings(report)} == {"roles.evaluator", "roles.verifier"}


def test_the_tui_check_asks_the_engine_too(engine):
    engine.serve({"id": "lead-model"})
    _write_global({"roles": {"verifier": {"model": "ghost-model"}}})
    app = _app()
    asyncio.run(app._command("/settings check"))
    out = _flat(app.renderer.console.file.getvalue())
    assert "roles.verifier" in out and "ghost-model" in out and " is not served" in out
    assert engine.requests == ["/v1/models"]


def test_the_library_check_asks_no_engine_unless_told(engine):
    engine.serve({"id": "lead-model"})
    _write_global({"roles": {"verifier": {"model": "ghost-model"}}})
    report = settings.check()
    assert engine.requests == [] and _model_warnings(report) == []
    assert "not checked" in report["model_names"]
    report = settings.check(ask_engine=True)
    assert engine.requests == ["/v1/models"] and len(_model_warnings(report)) == 1


def test_names_the_engine_lists_are_shown_safely(capsys, engine):
    engine.serve({"id": "evil" + HOSTILE}, {"id": "x" * 5000})
    _write_global({"roles": {"verifier": {"model": "ghost-model"}}})
    code, out = _cli(capsys, "check")
    assert code == 0 and not _raw(out) and "PWNED" in out and len(out) < 6000


# --- round 2: the gate's findings -------------------------------------------------------------------------------

def _in_thread(fn, timeout):
    """fn() in a daemon thread, waited for `timeout` seconds: (finished, {"result"} or {"error"}). A call that would
    hang is reported instead of hanging the run."""
    box = {}

    def run():
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 -- reported to the test
            box["error"] = exc
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    return not thread.is_alive(), box


def _release(fifo):
    """Open the FIFO for writing and close it at once: a reader blocked on it gets end-of-file."""
    try:
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
    except OSError:
        pass


def _special(path, kind):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path) if kind == "fifo" else path.mkdir()


@pytest.mark.parametrize("kind, what", [("fifo", "a FIFO"), ("directory", "a directory")])
def test_a_special_council_file_is_a_note_and_never_hangs_the_start(tmp_path, monkeypatch, council_file, kind, what):
    """Gate round 1, finding 1: a FIFO at var/moe.json hung every session start (the note's read blocked)."""
    _special(council_file, kind)
    app = _started_app(monkeypatch, tmp_path, "anthropic", SimpleNamespace())
    try:
        finished, box = _in_thread(lambda: asyncio.run(app.start()), 10)
    finally:
        _release(council_file)
    assert finished and "error" not in box, box
    out = _flat(app.renderer.console.file.getvalue())
    assert str(council_file) in out and f"it is not a regular file ({what})" in out and "Council is off" in out


def test_dream_settings_check_names_a_fifo_council_file_without_hanging(capsys, council_file):
    _special(council_file, "fifo")
    try:
        finished, box = _in_thread(lambda: management.main(["settings", "check"]), 10)
    finally:
        _release(council_file)
    assert finished and box.get("result") == 0, box
    report = json.loads(capsys.readouterr().out)
    assert any(str(council_file) in w and "not a regular file (a FIFO)" in w for w in report["warnings"])


def test_load_config_and_the_note_read_the_file_the_same_bounded_way(council_file):
    """The note and load_config() share one reader (the way profiles.read_settings reads): without blocking, a
    regular file only, at most MAX_SETTINGS_BYTES, so they can never disagree about whether the Council is off."""
    from dream.core.profiles import MAX_SETTINGS_BYTES
    _special(council_file, "fifo")
    try:
        finished, box = _in_thread(moe.load_config, 5)
    finally:
        _release(council_file)
    assert finished and box == {"result": None}, box
    council_file.unlink()
    council_file.write_text(json.dumps(GOOD_COUNCIL) + " " * MAX_SETTINGS_BYTES, encoding="utf-8")   # usable, padded
    assert moe.load_config() is None and f"larger than {MAX_SETTINGS_BYTES:,} bytes" in moe.config_problem()[1]
    council_file.write_text("[" * 20000 + "]" * 20000, encoding="utf-8")
    assert moe.load_config() is None and "nested too deeply" in moe.config_problem()[1]
    real = council_file.parent / "saved.json"
    real.write_text(json.dumps(GOOD_COUNCIL), encoding="utf-8")
    council_file.unlink()
    council_file.symlink_to(real)                  # a link to a usable file stays usable, as before
    assert moe.load_config() is not None and moe.config_problem() is None


def test_the_settings_tab_survives_a_deeply_nested_council_file(studio, council_file):
    """Gate round 1, note 5: a moe.json nested 20k deep made settings_get and the Council save return 500."""
    _, _, _, call = studio
    before = _council(council_file, "[" * 20000 + "]" * 20000)
    view = call({"action": "settings_get"})
    section = next(s for s in view["sections"] if s["id"] == "council")
    assert "nested too deeply" in section["intro"] and str(council_file) in section["intro"]
    error = call({"action": "settings_save", "key": "council.advisor.codex", "value": {"model": "m", "effort": ""}},
                 status=400)
    assert "council.advisor.codex" in error and "nothing was saved" in error
    _untouched(council_file, before)


def test_the_settings_tab_names_the_council_file_when_the_settings_file_is_unusable_too(studio, council_file):
    """Gate round 1, note 5: with runtime-settings.json unusable the tab shows only its error; the Council note is
    part of it now."""
    _, _, _, call = studio
    _write_global({"roles": {"verifier": {"model": ""}}})
    _council(council_file, {"orchestrator": "machx", "advisors": ["codex", "codex"]})
    view = call({"action": "settings_get"})
    assert view["ok"] is False and "roles.verifier" in view["error"]
    assert str(council_file) in view["error"] and "the advisors entry is not valid" in view["error"]


class _RawEngine:
    """A loopback front for what a well-behaved server never does: "drip" sends the headers, then the first `drip`
    bytes of the body one every `interval` seconds, then the rest; "hang" reads the request and never answers;
    "body" answers `body` at once. Never one of the owner's ports: the kernel picks a free one."""

    def __init__(self, mode, body=b"", *, interval=2.5, drip=8):
        self.mode, self.body, self.interval, self.drip = mode, body, interval, drip
        self.stop = threading.Event()
        self.conns = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.base = f"http://127.0.0.1:{self.sock.getsockname()[1]}/v1"
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            self.conns.append(conn)
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    def _answer(self, conn):
        try:
            conn.settimeout(5)
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                request += chunk
            if self.mode == "hang":
                self.stop.wait(60)
                return
            conn.sendall((f"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {len(self.body)}"
                          "\r\nconnection: close\r\n\r\n").encode())
            if self.mode == "drip":
                for i in range(min(self.drip, len(self.body))):
                    if self.stop.wait(self.interval):
                        return
                    conn.sendall(self.body[i:i + 1])
                conn.sendall(self.body[self.drip:])
            else:
                conn.sendall(self.body)
        except OSError:
            pass

    def close(self):
        self.stop.set()
        for conn in self.conns:
            try:
                conn.close()
            except OSError:
                pass
        self.sock.close()
        self.thread.join(2)


@pytest.fixture
def raw_engine(monkeypatch):
    made = []

    def make(mode, body=b"", **kw):
        front = _RawEngine(mode, body, **kw)
        made.append(front)
        monkeypatch.setattr(machx, "BASE_URL", front.base)
        return front
    yield make
    for front in made:
        front.close()


LISTED = json.dumps({"object": "list", "data": [{"id": "lead-model"}]}).encode()
MARGIN = 1.5      # seconds of slack over the deadline for the rest of `check` on a loaded machine


@pytest.mark.parametrize("mode", ["drip", "hang"])
def test_the_engine_question_has_one_overall_deadline(capsys, council_file, raw_engine, mode):
    """Gate round 1, finding 2: httpx timeouts are per operation, so a front that drips a byte every 2.5 s kept
    `check` waiting for 20 s. One deadline covers the whole question now; the rest of the report is complete."""
    _council(council_file, {"orchestrator": "machx", "advisors": ["codex", "codex"]})
    raw_engine(mode, LISTED)
    _write_global({"roles": {"verifier": {"model": "ghost-model"}}})
    start = time.monotonic()
    code, out = _cli(capsys, "check")
    elapsed = time.monotonic() - start
    report = json.loads(out)
    assert code == 0 and report["ok"] is True
    assert elapsed <= settings.ENGINE_DEADLINE_S + MARGIN, elapsed
    assert "model names not checked" in report["model_names"] and "within" in report["model_names"]
    assert any(str(council_file) in w for w in report["warnings"]) and _model_warnings(report) == []


async def test_the_tui_check_asks_the_engine_off_the_event_loop(raw_engine):
    """Gate round 1, finding 2: the question ran on the session's event loop, freezing the TUI and Studio with it."""
    raw_engine("drip", LISTED)
    _write_global({"roles": {"verifier": {"model": "ghost-model"}}})
    app = _app()
    gaps, done = [], asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
    ticking = asyncio.create_task(ticker())
    start = time.monotonic()
    await app._command("/settings check")
    elapsed = time.monotonic() - start
    done.set()
    await ticking
    assert elapsed <= settings.ENGINE_DEADLINE_S + MARGIN, elapsed
    assert len(gaps) > 10 and max(gaps) < 0.5, max(gaps)          # the loop kept running the whole time
    assert "model names not checked" in _flat(app.renderer.console.file.getvalue())


def test_an_oversized_model_list_is_not_read_past_the_cap(capsys, raw_engine):
    body = json.dumps({"data": [{"id": "lead-model", "pad": "x" * (settings.MAX_MODELS_BYTES + 10)}]}).encode()
    raw_engine("body", body)
    _write_global({"roles": {"verifier": {"model": "ghost-model"}}})
    start = time.monotonic()
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True and time.monotonic() - start <= settings.ENGINE_DEADLINE_S + MARGIN
    assert "model names not checked" in report["model_names"] and f"{settings.MAX_MODELS_BYTES:,}" in report["model_names"]


@pytest.mark.parametrize("body", [
    b"[" * 20000 + b"]" * 20000,                                   # too deep for the JSON reader
    b'{"data":' + b"[" * 20000 + b"]" * 20000 + b"}",
    b'{"data":[{"id":"lead-model","n":' + b"9" * 5000 + b"}]}",       # past Python's integer-digit limit
    b'{"data":[{"id":"\xff\xfe"}]}',                                # not UTF-8
], ids=["deep", "deep-data", "digits", "not-utf8"])
def test_a_model_list_dream_cannot_read_is_not_a_crash(capsys, council_file, raw_engine, body):
    """Gate round 1, finding 3: a body nested ~20k deep raised RecursionError; `check` exited 1 with a traceback and
    the Council warning was lost."""
    _council(council_file, {"orchestrator": "machx", "advisors": ["codex", "codex"]})
    raw_engine("body", body)
    _write_global({"roles": {"verifier": {"model": "ghost-model"}}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and report["ok"] is True
    assert report["model_names"].startswith("the engine did not list its models; model names not checked")
    assert any(str(council_file) in w for w in report["warnings"]) and _model_warnings(report) == []


def test_many_served_models_are_capped_in_every_line(capsys, raw_engine):
    """Gate round 1, finding 4: with 20k served ids one "not served" warning was 1.5 MB."""
    body = json.dumps({"data": [{"id": f"m{i:05d}", "root": "Shared"} for i in range(20000)]}).encode()
    raw_engine("body", body)
    _write_global({"roles": {"verifier": {"model": "ghost-model"}, "filer": {"model": "Shared"}}})
    code, out = _cli(capsys, "check")
    report = json.loads(out)
    assert code == 0 and len(out) < 8000
    [missing] = [w for w in report["warnings"] if " is not served" in w]
    [shared] = [w for w in report["warnings"] if "ambiguous" in w]
    for line in (missing, shared, report["model_names"]):
        assert "m00009" in line and "m00010" not in line and "and 19990 more" in line and len(line) < 1000, line
