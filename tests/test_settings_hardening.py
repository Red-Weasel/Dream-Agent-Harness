"""DREAM-147 -- settings hardening from the DREAM-146 gate's non-blocking findings.

1. Terminal escape injection: every key or role name echoed from a settings file (project or global) goes through
   one helper, `profiles.shown` -- repr() for anything not plainly printable (ESC, BEL, CR and other control or
   format characters become visible escapes), cut to 80 characters plus an ellipsis -- in notes, errors, the CLI,
   the TUI, the effective() row keys and the Studio view.
2. The "ignored project file" note is shown at session start on every backend, not only the OpenAI-compatible one.
3. The Studio project-scope refusal names the actual offending key of a refused project file.
4. A raw OSError from the project writer (a linked lock file, a .dream deleted mid-write) is a ValueError naming
   the project file and saying that nothing was written.

No engine, no GPU, no socket to the owner's ports: machx.BASE_URL points at an unroutable port.
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from starlette.testclient import TestClient

from dream import config, management
from dream.core import council_config, moe, profiles, settings
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import get_provider
from dream.gui import settings_panel
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.local import machx
from dream.tui.app import App

LEAD = "lead-model"
# An attacker's name: sets the terminal title, rings the bell, clears the screen, returns the carriage.
HOSTILE = "x\x1b]0;PWNED\x07\x1b[2J\r"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    for name in ("DREAM_PROFILE", "DREAM_MAX_TOKENS", "DREAM_EVALUATOR_PROVIDER", "DREAM_EVALUATOR_MODEL",
                 "DREAM_AUTO_VERIFY", "DREAM_SINGLE_SLOT_VERIFIER", "DREAM_VISION", "DREAM_VISION_HELPER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 131072)
    monkeypatch.setattr(moe, "CONFIG_PATH", tmp_path / "var" / "moe.json")
    monkeypatch.setattr(machx, "BASE_URL", "http://127.0.0.1:1/v1")
    settings.set_workspace(None)
    yield
    settings.set_workspace(None)


def _write_global(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_project(workspace: Path, data):
    path = workspace / ".dream" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


def _raw(text: str) -> set[str]:
    """The control characters a terminal would act on: C0 except newline and tab, DEL, and the C1 range."""
    return {c for c in text if (ord(c) < 32 and c not in "\n\t") or 0x7F <= ord(c) <= 0x9F}


def _cli(capsys, *argv):
    code = management.main(["settings", *argv])
    return code, capsys.readouterr().out


def _app(workspace, provider="machx"):
    app = App(provider=provider, model=LEAD, workspace=workspace)
    app.engine = SimpleNamespace(store=None, backend=SimpleNamespace(n_ctx=None), provider=get_provider(provider),
                                 model=LEAD)
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    return app


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "project"
    ws.mkdir()
    settings.set_workspace(ws)
    return ws


@pytest.fixture
def studio(tmp_path):
    app = App(provider="machx", workspace=tmp_path)
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        headers = {"x-dream-token": server.token}

        def call(payload, status=200):
            # The body is sent as a browser would send it -- JSON with non-ASCII escaped (a lone surrogate travels as
            # the six characters \udfff); httpx's own json= cannot UTF-8-encode such a value and would fail client-side.
            reply = client.post("/api/control", headers={**headers, "content-type": "application/json"},
                                content=json.dumps(payload).encode("ascii"))
            assert reply.status_code == status, reply.text
            return reply.json()["result"] if status == 200 else reply.json()["error"]
        yield app, server, client, call


def _rows(view):
    return {row["key"]: row for section in view["sections"] for row in section["rows"]}


def _system_texts(app) -> list[str]:
    """The system notes the App published to Studio's bus (kept in its Conversation for a pane that opens later)."""
    texts = []
    for event in list(app.bus.conversation.events):
        kind = getattr(event, "kind", None) or (event.get("kind") if isinstance(event, dict) else None)
        data = getattr(event, "data", None) or (event.get("data") if isinstance(event, dict) else None)
        if kind == "system":
            texts.append(str(data))
    return texts


# --- 1. escapes and size -------------------------------------------------------------------------------------

def test_shown_keeps_a_tame_name_and_makes_the_rest_safe_and_bounded():
    assert profiles.shown("summarizer") == "summarizer"
    assert profiles.shown("roles.subagents.default") == "roles.subagents.default"
    text = profiles.shown(HOSTILE)
    assert not _raw(text) and "PWNED" in text and text.startswith("'") and "\\x1b" in text
    assert "\\u200b" in profiles.shown("zero​width")                # a format character is shown, not hidden
    assert "\\u202e" in profiles.shown("right‮left")                # the bidi override too
    long = profiles.shown("a" * 1_000_000)
    assert len(long) <= 81 and long.endswith("…")
    assert profiles.shown(7) == "7"


def test_an_unknown_role_name_with_escapes_never_reaches_a_terminal_raw(workspace, capsys):
    _write_project(workspace, {"roles": {HOSTILE: {"model": "m"}}})    # valid: an unknown role is kept and noted
    notes = settings.role_notes("machx")
    assert notes and any("PWNED" in n for n in notes) and not any(_raw(n) for n in notes)
    rows = settings.effective()
    keys = [k for k in rows if "PWNED" in k]
    assert keys and not any(_raw(k) for k in keys)
    assert all(not _raw(k) for k in settings.check()["roles"])
    code, out = _cli(capsys, "show", "--workspace", str(workspace))
    assert code == 0 and "PWNED" in out and not _raw(out)
    app = _app(workspace)
    asyncio.run(app._command("/settings show"))
    out = app.renderer.console.file.getvalue()
    assert "PWNED" in out and not _raw(out)
    view = settings_panel.view(provider="machx", model=None)
    row = next(r for r in _rows(view).values() if "PWNED" in r["key"])
    assert not _raw(row["label"]) and not _raw(json.dumps(row, ensure_ascii=False))
    assert not any(_raw(w) for w in view["warnings"])


def test_the_same_holds_for_the_global_file(workspace):
    _write_global({"roles": {HOSTILE: {"model": "m"}}})
    assert not any(_raw(n) for n in settings.role_notes("machx"))
    assert all(not _raw(k) for k in settings.effective())
    for data, marker in [({"overrides": {"ctx" + HOSTILE: 1}}, "Unknown profile override"),
                         ({"models": {"m" + HOSTILE + ":x": {}}}, "invalid model key"),
                         ({"roles": {"subagents": {"bad" + HOSTILE: {"model": "m"}}}}, "is not an agent name"),
                         ({"roles": {"verifier": {"model": "m", "url" + HOSTILE: 1}}}, "unknown keys"),
                         ({"overrides": {"vision_helper": "cloud" + HOSTILE}}, "must name a multimodal")]:
        _write_global(data)
        with pytest.raises(ValueError) as caught:
            settings.read()
        message = str(caught.value)
        assert marker in message and "PWNED" in message and not _raw(message), message
        assert not any(_raw(n) for n in settings.role_notes("machx"))


def test_a_forbidden_key_with_escapes_is_named_safely_everywhere(workspace, capsys):
    path = _write_project(workspace, {"perm" + HOSTILE: {"mode": "auto"}})
    with pytest.raises(ValueError) as caught:
        settings.read()
    message = str(caught.value)
    assert "PWNED" in message and str(path) in message and not _raw(message)
    code, out = _cli(capsys, "show", "--workspace", str(workspace))
    assert code == 2 and "PWNED" in out and not _raw(out)
    notes = settings.role_notes("machx")
    assert len(notes) == 1 and "PWNED" in notes[0] and not _raw(notes[0])
    app = _app(workspace)
    asyncio.run(app._command("/settings set output.max_tokens 9000 --project"))
    out = app.renderer.console.file.getvalue()
    assert "PWNED" in out and not _raw(out)
    view = settings_panel.view(provider="machx", model=None)
    assert "PWNED" in view["project_error"] and not _raw(view["project_error"])
    with pytest.raises(ValueError) as caught:
        settings_panel.save("output.max_tokens", "9000", scope="project")
    assert "PWNED" in str(caught.value) and not _raw(str(caught.value))


def test_a_one_megabyte_key_gives_a_bounded_message(workspace, capsys):
    big = "k" * 1_000_000
    _write_project(workspace, {"roles": {big: {"model": "m"}}})          # valid but unknown: a note and a row
    notes = settings.role_notes("machx")
    assert len(notes) == 1 and len(notes[0]) < 400 and "…" in notes[0]
    rows = settings.effective()
    assert all(len(k) < 120 for k in rows) and any("kkkk" in k for k in rows)
    code, out = _cli(capsys, "show", "--workspace", str(workspace))
    assert code == 0 and len(out) < 100_000
    view = settings_panel.view(provider="machx", model=None)
    assert all(len(r["label"]) < 200 for r in _rows(view).values())
    _write_project(workspace, {big: {}})                                  # forbidden and huge
    with pytest.raises(ValueError) as caught:
        settings.read()
    assert len(str(caught.value)) < 600 and "…" in str(caught.value)
    assert len(settings.role_notes("machx")[0]) < 600
    code, out = _cli(capsys, "check", "--workspace", str(workspace))
    assert code == 2 and len(out) < 2000
    _write_global({"overrides": {big: 1}})
    with pytest.raises(ValueError) as caught:
        read_settings()
    assert len(str(caught.value)) < 600
    _write_global({"roles": {"verifier": {"model": "m", big: 1}}})
    with pytest.raises(ValueError) as caught:
        settings.read()
    assert len(str(caught.value)) < 600


# --- 2. the note at session start on every backend ------------------------------------------------------------

def _started_app(monkeypatch, tmp_path, provider, backend):
    app = App(provider=provider, model=LEAD, workspace=tmp_path)
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)

    class Engine:
        store = SimpleNamespace(top_memories=lambda limit: [])

        def __init__(self):
            self.provider, self.model, self.provider_label, self.backend = get_provider(provider), LEAD, provider, backend

        async def start(self):
            return None

        def stats(self):
            return {}

    monkeypatch.setattr(app, "_boot_engine", lambda: Engine())

    async def skip(*args, **kwargs):
        return None
    for name in ("_associate_project_on_start", "_start_monitor", "_start_studio"):
        monkeypatch.setattr(app, name, skip)
    monkeypatch.setattr(app, "_welcome", lambda: None)
    return app


@pytest.mark.parametrize("provider", ["anthropic", "codex", "gemini", "grok"])
async def test_the_ignored_project_note_shows_at_start_on_the_sdk_and_cli_backends(tmp_path, monkeypatch, provider):
    _write_global({"output": {"max_tokens": 40000}})
    path = _write_project(tmp_path, {"permissions": {"mode": "auto"}})
    app = _started_app(monkeypatch, tmp_path, provider, SimpleNamespace())
    await app.start()
    out = app.renderer.console.file.getvalue()
    assert str(path) in out and "permissions" in out and "ignored" in out
    assert any(str(path) in text and "permissions" in text for text in _system_texts(app))
    assert config.MAX_OUTPUT_TOKENS == 40000


async def test_the_start_note_is_not_doubled_on_the_openai_compatible_backend(tmp_path, monkeypatch):
    from test_cache_friendly_head import FakeEngine, backend
    path = _write_project(tmp_path, {"permissions": {"mode": "auto"}})
    app = _started_app(monkeypatch, tmp_path, "machx", backend(FakeEngine(lead=[{"text": "ok"}]), model=LEAD))
    await app.start()
    assert str(path) not in app.renderer.console.file.getvalue()        # that backend says it at its first request
    assert not any(str(path) in text for text in _system_texts(app))


async def test_a_usable_project_file_makes_no_start_note(tmp_path, monkeypatch):
    _write_project(tmp_path, {"output": {"max_tokens": 9000}})
    app = _started_app(monkeypatch, tmp_path, "anthropic", SimpleNamespace())
    await app.start()
    assert ".dream" not in app.renderer.console.file.getvalue()
    assert config.MAX_OUTPUT_TOKENS == 9000


async def test_the_start_note_is_safe_and_bounded_too(tmp_path, monkeypatch):
    _write_project(tmp_path, {"perm" + HOSTILE: {}, "k" * 1_000_000: {}})
    app = _started_app(monkeypatch, tmp_path, "anthropic", SimpleNamespace())
    await app.start()
    out = app.renderer.console.file.getvalue()
    assert "PWNED" in out and not _raw(out) and len(out) < 2000


# --- 3. the Studio refusal names the offending key ------------------------------------------------------------

def test_studio_names_the_offending_key_of_a_refused_project_file(studio, tmp_path):
    _write_project(tmp_path, {"permissions": {"mode": "auto"}})
    _, _, _, call = studio
    view = call({"action": "settings_get"})
    assert "permissions" in view["project_error"]
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000", "scope": "project"},
                 status=400)
    assert "permissions" in error and "roles" not in error, error
    _write_project(tmp_path, {"privacy": {"a": 1}, "engine": {"b": 2}})
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000", "scope": "project"},
                 status=400)
    assert "engine" in error and "privacy" in error and "roles" not in error, error
    _write_project(tmp_path, {"roles": {"verifier": {"model": "m", "provider": "openai"}}})
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000", "scope": "project"},
                 status=400)
    assert "roles.verifier.provider" in error, error
    _write_project(tmp_path, {"roles": {"critic": {"model": "m"}}})     # the cloud-role rule names its key
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000", "scope": "project"},
                 status=400)
    assert "roles.critic.provider" in error, error
    _write_project(tmp_path, {"output": {"max_tokens": 12}})
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000", "scope": "project"},
                 status=400)
    assert "output.max_tokens" in error, error


# --- 4. a raw OSError from the project writer is a plain refusal ----------------------------------------------

def test_a_linked_lock_file_is_a_plain_refusal_naming_the_project_file(workspace, tmp_path, capsys):
    folder = workspace / ".dream"
    folder.mkdir()
    target = tmp_path / "outside-lock"
    (folder / "settings.json.lock").symlink_to(target)
    path = folder / "settings.json"
    with pytest.raises(ValueError) as caught:
        settings.set_value("output.max_tokens", "9000", scope="project")
    message = str(caught.value)
    assert str(path) in message and "nothing was written" in message, message
    assert not target.exists() and not path.exists()
    code, out = _cli(capsys, "set", "output.max_tokens", "9000", "--project", "--workspace", str(workspace))
    assert code == 2 and str(path) in out and "nothing was written" in out
    with pytest.raises(ValueError) as caught:
        settings_panel.save("output.max_tokens", "9000", scope="project")
    assert str(path) in str(caught.value) and "nothing was written" in str(caught.value)
    assert not target.exists() and not path.exists()
    # Reads do not use the lock: the (absent) file is simply not there.
    assert settings.read() == {}


def test_a_dream_folder_deleted_mid_write_is_a_plain_refusal(workspace, monkeypatch):
    settings.set_value("output.max_tokens", "1000", scope="project")
    path = workspace / ".dream" / "settings.json"
    real = settings.read_settings

    def vanish(target=None, *, dir_fd=None):
        if dir_fd is not None:                       # the folder goes while the writer holds its descriptor
            shutil.rmtree(workspace / ".dream")
        return real(target, dir_fd=dir_fd)
    monkeypatch.setattr(settings, "read_settings", vanish)
    with pytest.raises(ValueError) as caught:
        settings.set_value("output.max_tokens", "2000", scope="project")
    message = str(caught.value)
    assert str(path) in message and "nothing was written" in message, message
    assert not (workspace / ".dream").exists()
    assert not any(p.name.startswith(".settings.json") for p in workspace.iterdir())     # no temp file anywhere


def test_studio_reports_a_writer_error_plainly(studio, tmp_path):
    folder = tmp_path / ".dream"
    folder.mkdir()
    (folder / "settings.json.lock").symlink_to(tmp_path / "outside-lock")
    _, _, _, call = studio
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000", "scope": "project"},
                 status=400)
    assert str(folder / "settings.json") in error and "nothing was written" in error, error
    assert not (tmp_path / "outside-lock").exists()


# --- gate round 1: model values, workspace paths, long names ---------------------------------------------------

# Model values a terminal or a page could act on or fail to encode: a lone surrogate (unencodable), a C1 control
# (CSI), a bidi override, a zero-width space, DEL, a line separator. One rule refuses them all: str.isprintable().
BAD_MODELS = {"lone-surrogate": "v\udfffw", "c1-csi": "v\u009bw", "bidi-rlo": "v‮w", "zero-width": "v​w",
              "del": "v\x7fw", "line-separator": "v w"}


def _strict_console():
    """A console on a strict UTF-8 stream, like a real terminal: a lone surrogate in printed text would raise and
    wedge every later print."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict", write_through=True)
    return Console(file=stream, width=200, force_terminal=False, color_system=None), stream


def _strict_text(stream) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("utf-8")


@pytest.mark.parametrize("model", list(BAD_MODELS.values()), ids=list(BAD_MODELS))
def test_a_model_value_that_is_not_printable_is_refused_in_the_project_file(workspace, model, capsys):
    path = _write_project(workspace, {"roles": {"verifier": {"model": model}}})
    with pytest.raises(ValueError) as caught:
        settings.read()
    message = str(caught.value)
    assert "roles.verifier.model" in message and str(path) in message and not _raw(message), message
    message.encode("utf-8")                                            # encodable: no lone surrogate
    notes = settings.role_notes("machx")                                # the runtime ignores the file, naming the key
    assert len(notes) == 1 and "roles.verifier.model" in notes[0] and not _raw(notes[0])
    notes[0].encode("utf-8")
    assert settings.role_model("verifier", "machx") is None
    for argv in (["show"], ["get", "output.max_tokens"], ["check"]):
        code, out = _cli(capsys, *argv, "--workspace", str(workspace))
        assert code == 2 and str(path) in out and "roles.verifier.model" in out and not _raw(out), (argv, out)
        out.encode("utf-8")
    console, stream = _strict_console()
    app = _app(workspace)
    app.renderer.console = console
    asyncio.run(app._command("/settings check"))                        # wedged a strict stream before
    asyncio.run(app._command("/settings show"))
    asyncio.run(app._command("/settings path"))                          # later prints still work
    out = _strict_text(stream)
    assert "roles.verifier.model" in out and str(path) in out and not _raw(out)
    with pytest.raises(ValueError) as caught:
        settings.set_value("roles.verifier.model", model, scope="project")
    assert "roles.verifier.model" in str(caught.value) and not _raw(str(caught.value))


@pytest.mark.parametrize("model", list(BAD_MODELS.values()), ids=list(BAD_MODELS))
def test_the_same_model_values_in_the_global_file_are_refused_at_read_naming_the_file(model, capsys):
    _write_global({"roles": {"filer": {"model": model}}})
    for call in (settings.read, settings.read_runtime, settings.effective):    # the global file is never ignored
        with pytest.raises(ValueError) as caught:
            call()
        message = str(caught.value)
        assert "roles.filer.model" in message and str(settings_path()) in message and not _raw(message), message
        message.encode("utf-8")
    notes = settings.role_notes("machx")
    assert len(notes) == 1 and "roles.filer.model" in notes[0] and not _raw(notes[0])
    code, out = _cli(capsys, "check")
    assert code == 2 and str(settings_path()) in out and "roles.filer.model" in out and not _raw(out)
    out.encode("utf-8")
    view = settings_panel.view(provider="machx", model=None)
    assert view["ok"] is False and str(settings_path()) in view["error"] and not _raw(view["error"])
    with pytest.raises(ValueError, match="roles.filer.model"):
        settings.set_value("output.max_tokens", "9000")                 # the global writer refuses a broken file


def test_a_non_printable_model_value_is_refused_by_every_writer_naming_the_key(workspace):
    for model in BAD_MODELS.values():
        with pytest.raises(ValueError) as caught:
            settings.set_value("roles.verifier.model", model)                     # the global file
        assert "roles.verifier.model" in str(caught.value) and not _raw(str(caught.value))
        with pytest.raises(ValueError) as caught:
            settings.set_role("roles.filer", {"model": model}, scope="project")    # the project file
        assert "roles.filer.model" in str(caught.value) and not _raw(str(caught.value))
        with pytest.raises(ValueError) as caught:
            settings_panel._role_value("roles.verifier", {"model": model})          # the tab's own rule
        assert "roles.verifier.model" in str(caught.value) and not _raw(str(caught.value))
        with pytest.raises(ValueError):
            council_config.validate_model(model)                                    # a Council advisor's model
    assert not settings_path().exists() and not settings.project_path().exists()
    assert settings_panel._role_value("roles.verifier", {"model": "Qwen3.8-27B (q8) é"}) == {"model": "Qwen3.8-27B (q8) é"}
    assert council_config.validate_model("Qwen3.8-27B (q8) é") == "Qwen3.8-27B (q8) é"


def test_a_vision_helper_value_that_is_no_provider_is_shown_safely():
    _write_global({"overrides": {"vision_helper": "openai‮\udfff"}})
    with pytest.raises(ValueError) as caught:
        settings.read()
    message = str(caught.value)
    assert "vision_helper" in message and not _raw(message)
    message.encode("utf-8")


@pytest.mark.parametrize("model", list(BAD_MODELS.values()), ids=list(BAD_MODELS))
def test_studio_still_loads_and_saves_globally_after_a_refused_model_value(studio, tmp_path, model):
    path = _write_project(tmp_path, {"roles": {"verifier": {"model": model}}})
    _, _, _, call = studio
    view = call({"action": "settings_get"})                             # 200, never a raw UnicodeEncodeError
    assert view["ok"] is True
    assert str(path) in view["project_error"] and "roles.verifier.model" in view["project_error"]
    assert not _raw(json.dumps(view, ensure_ascii=False))
    result = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000"})
    assert result["scope"] == "global" and read_settings()["output"] == {"max_tokens": 9000}
    error = call({"action": "settings_save", "key": "roles.verifier", "value": {"model": model}, "scope": "project"},
                 status=400)
    assert "roles.verifier.model" in error and not _raw(error)
    error = call({"action": "settings_save", "key": "roles.filer", "value": {"model": model}}, status=400)
    assert "roles.filer.model" in error and not _raw(error)


@pytest.mark.parametrize("name", ["notes [draft]", "x[link=https:evil.example]y", "ws\x1b]0;PWNED\x07"],
                         ids=["markup", "hyperlink", "escape"])
async def test_workspace_paths_are_shown_literally_and_safely(tmp_path, monkeypatch, name, capsys):
    ws = tmp_path / name
    ws.mkdir()
    literal = str(ws / ".dream" / "settings.json")
    hostile = "\x1b" in name

    def right(text):
        return "\x1b]8" not in text and not _raw(text) and ("PWNED" in text if hostile else literal in text)

    app = _app(ws)
    await app._command("/settings path")
    assert right(app.renderer.console.file.getvalue())
    _write_project(ws, {"permissions": {"mode": "auto"}})
    app = _app(ws)
    await app._command("/settings show")                                 # the error path
    assert right(app.renderer.console.file.getvalue())
    assert right(settings.role_notes("machx")[0])                        # the connect note
    code, out = _cli(capsys, "show", "--workspace", str(ws))              # the CLI error
    assert code == 2 and right(out)
    app = _started_app(monkeypatch, ws, "anthropic", SimpleNamespace())
    await app.start()                                                    # the start note
    assert right(app.renderer.console.file.getvalue())
    assert any(right(text) for text in _system_texts(app))


def test_long_unknown_role_names_sharing_a_prefix_stay_distinct_rows(workspace):
    prefix = "p" * 100
    _write_project(workspace, {"roles": {prefix + "a": {"model": "m"}, prefix + "b": {"model": "m"}}})
    keys = [k for k in settings.effective() if k.startswith("roles." + "p" * 20)]
    assert len(keys) == 2 and len(set(keys)) == 2 and all(len(k) < 120 for k in keys)
    notes = [n for n in settings.role_notes("machx") if "ppp" in n]
    assert len(notes) == 2 and all(len(n) < 400 for n in notes)
