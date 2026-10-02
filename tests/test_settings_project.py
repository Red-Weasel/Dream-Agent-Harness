"""DREAM-146 S5 (settings design P3, 3.1 and row S5): the optional project file `<workspace>/.dream/settings.json`.

Same shape as the global file (data/runtime-settings.json); it may hold `roles` (model names only), `output` and
`behaviour`, and nothing else: a provider, `overrides`, `models`, `profile`, `engine`, permissions or privacy are
refused with the key named. Precedence per key: flag > env > session > project > global > default; `effective()`
reports source "project" for a project-set value. The workspace is the session's (App.workspace: --workspace, the
picker, the desktop's choice; else Dream's own root); the CLI takes --workspace (default: the current directory).
`set`/`unset` with --project (CLI, /settings) and the Settings tab's scope go through the same locked, atomic writer
as the global file. A corrupt project file raises an error naming it and is never rewritten.

No engine, no socket, no GPU: an App without a session; the Studio tests use a real StudioServer on a free port
and point machx.BASE_URL at an unroutable port so nothing probes the owner's engine.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright, expect
from rich.console import Console
from starlette.testclient import TestClient

from dream import config, management
from dream.core import moe, settings
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import get_provider
from dream.gui import settings_panel
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.local import machx
from dream.tui.app import App

LEAD = "lead-model"


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


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "project"
    ws.mkdir()
    settings.set_workspace(ws)
    return ws


# --- the workspace and the path --------------------------------------------------------------------------

def test_no_workspace_means_no_project_file_and_nothing_changes(tmp_path):
    assert settings.workspace() is None and settings.project_path() is None
    before = settings.effective()
    assert "<workspace>/.dream/settings.json" in settings.paths()["project"]
    ws = tmp_path / "project"
    ws.mkdir()
    settings.set_workspace(ws)
    assert settings.project_path() == ws / ".dream" / "settings.json"
    assert settings.paths()["project"] == str(ws / ".dream" / "settings.json")
    # A workspace without a project file: every row exactly as without a workspace.
    assert settings.effective() == before
    assert settings.read() == {}


def test_the_app_registers_its_workspace(tmp_path):
    App(provider="machx", model=LEAD, workspace=tmp_path)
    assert settings.workspace() == tmp_path.resolve()
    assert settings.project_path() == tmp_path.resolve() / ".dream" / "settings.json"


# --- precedence: project beats global, env and session beat project -----------------------------------------

def test_project_beats_global_in_the_view_and_in_every_reader(workspace):
    _write_global({"version": 1, "output": {"max_tokens": 40000}, "behaviour": {"vitals": True},
                   "roles": {"verifier": {"model": "global-checker", "provider": "openai"},
                             "subagents": {"default": {"model": "global-small"},
                                           "researcher": {"model": "global-researcher", "provider": "anthropic"}},
                             "main": {"model": "global-main"},
                             "evaluator": {"provider": "openai", "model": "global-eval"}}})
    _write_project(workspace, {"version": 1, "output": {"max_tokens": 50000}, "behaviour": {"vitals": False},
                               "roles": {"verifier": {"model": "project-checker"},
                                         "subagents": {"researcher": {"model": "project-researcher"}},
                                         "main": {"model": "project-main"},
                                         "evaluator": {"model": "project-eval"}}})
    rows = settings.effective(provider="openai")
    assert rows["output.max_tokens"] == (50000, "project")
    assert rows["behaviour.vitals"] == (False, "project")
    # The project names the model; the provider stays the global file's.
    assert rows["roles.verifier"] == ({"model": "project-checker", "provider": "openai"}, "project")
    assert rows["roles.subagents.default"].value == {"model": "global-small"}
    assert rows["roles.subagents.default"].source.startswith(settings.NOT_APPLIED)     # provider-less: machx only
    assert rows["roles.subagents.researcher"].value == {"model": "project-researcher", "provider": "anthropic"}
    assert rows["roles.subagents.researcher"].source.startswith(settings.NOT_APPLIED)     # openai session
    assert rows["roles.evaluator"] == ({"provider": "openai", "model": "project-eval"}, "project")
    assert rows["roles.main"].value == {"model": "project-main"} and rows["roles.main"].source.startswith(
        settings.NOT_APPLIED)        # roles.main is the local endpoint's; resolved for openai here
    assert settings.effective(provider="machx")["roles.main"] == ({"model": "project-main"}, "project")
    # The readers the runtime calls see the same merge.
    assert settings.role_model("verifier", "openai") == "project-checker"
    assert settings.role_model("researcher", "anthropic") == "project-researcher"
    assert settings.role_model("coder", "machx") == "global-small"
    assert settings.role("evaluator") == {"provider": "openai", "model": "project-eval"}
    assert settings.main_model() == "project-main"
    assert settings.vitals_enabled() is False
    assert settings.sdk_model("researcher") == "project-researcher"
    assert settings.sdk_model("coder") == "inherit"        # the global default names no provider: MachX only
    settings.apply_saved_output()
    assert config.MAX_OUTPUT_TOKENS == 50000
    # The global file is untouched by any of it.
    assert read_settings()["output"] == {"max_tokens": 40000}


def test_a_project_value_needs_no_global_one(workspace):
    _write_project(workspace, {"roles": {"filer": {"model": "project-filer"}}, "output": {"max_tokens": 9000}})
    rows = settings.effective()
    assert rows["roles.filer"] == ({"model": "project-filer"}, "project")
    assert rows["output.max_tokens"] == (9000, "project")
    assert settings.role_model("filer", "machx") == "project-filer"
    assert not settings_path().exists()


def test_the_environment_beats_the_project_file(workspace, monkeypatch):
    _write_global({"roles": {"evaluator": {"provider": "openai"}}})     # a project evaluator model needs it
    _write_project(workspace, {"output": {"max_tokens": 50000}, "roles": {"evaluator": {"model": "project-eval"}}})
    monkeypatch.setenv("DREAM_MAX_TOKENS", "7000")
    monkeypatch.setenv("DREAM_EVALUATOR_MODEL", "env-eval")
    rows = settings.effective()
    assert rows["output.max_tokens"] == (7000, "env DREAM_MAX_TOKENS")
    assert rows["roles.evaluator"] == ({"provider": "openai", "model": "env-eval"}, "env DREAM_EVALUATOR_MODEL")
    settings.apply_saved_output()
    assert config.MAX_OUTPUT_TOKENS == 131072        # the environment's value is applied elsewhere, untouched here


def test_a_session_command_beats_the_project_file(workspace):
    _write_project(workspace, {"output": {"max_tokens": 50000}})
    rows = settings.effective(session={"output.max_tokens": 12000})
    assert rows["output.max_tokens"] == (12000, "session")


# --- what a project file may not hold ----------------------------------------------------------------------

@pytest.mark.parametrize("data, named", [
    ({"overrides": {"context_limit": 4096}}, "overrides"),
    ({"models": {"machx:x": {"output_tokens": 100}}}, "models"),
    ({"profile": "lean"}, "profile"),
    ({"engine": {"layout": "x"}}, "engine"),
    ({"privacy": {"vision_helper": "openai"}}, "privacy"),
    ({"permissions": {"mode": "auto"}}, "permissions"),
    ({"sandbox": {"network": True}}, "sandbox"),
    ({"providers": {"machx": {"url": "http://x"}}}, "providers"),
    ({"blender": {"binary": "/bin/blender"}}, "blender"),
    ({"studio": {"follow_model_view": True}}, "studio"),
    ({"roles": {"verifier": {"model": "x", "provider": "openai"}}}, "roles.verifier.provider"),
    ({"roles": {"subagents": {"researcher": {"provider": "anthropic", "model": "x"}}}},
     "roles.subagents.researcher.provider"),
    ({"roles": {"critic": {"provider": "codex"}}}, "roles.critic.provider"),
    ({"roles": {"verifier": {"model": "x", "url": "http://x"}}}, "roles.verifier.url"),
])
def test_forbidden_sections_and_keys_are_refused_with_the_key_named(workspace, data, named):
    path = _write_project(workspace, data)
    before = path.read_bytes()
    for call in (settings.read, settings.effective, settings.read_project):
        with pytest.raises(ValueError) as caught:
            call()
        message = str(caught.value)
        assert named in message and str(path) in message, message
        assert "project" in message.lower()
    assert path.read_bytes() == before
    # The runtime's forgiving readers report, they do not raise or fall back to another choice silently.
    notes = settings.role_notes("machx")
    assert len(notes) == 1 and named in notes[0] and str(path) in notes[0]
    assert settings.sdk_model("researcher") == "inherit"
    report = settings.check()
    assert report["ok"] is False and named in report["errors"][0] and report["project"] == str(path)


def test_a_project_role_model_is_not_a_model_name(workspace):
    path = _write_project(workspace, {"roles": {"verifier": {"model": " padded "}}})
    with pytest.raises(ValueError, match="roles.verifier.model"):
        settings.read()
    assert path.read_text(encoding="utf-8") == json.dumps({"roles": {"verifier": {"model": " padded "}}})


def test_a_project_critic_model_needs_the_global_provider(workspace):
    path = _write_project(workspace, {"roles": {"critic": {"model": "project-critic"}}})
    with pytest.raises(ValueError) as caught:
        settings.read()
    assert "roles.critic" in str(caught.value) and str(path) in str(caught.value)
    _write_global({"roles": {"critic": {"provider": "codex"}}})
    assert settings.read()["roles"]["critic"] == {"provider": "codex", "model": "project-critic"}
    assert settings.effective()["roles.critic"] == ({"provider": "codex", "model": "project-critic"}, "project")


def test_unknown_role_names_in_the_project_file_are_kept_and_noted_like_the_global_file_s(workspace):
    _write_project(workspace, {"roles": {"summarizer": {"model": "x"}}})
    assert settings.unknown_roles(settings.read()) == ["roles.summarizer"]
    assert any("roles.summarizer" in note for note in settings.role_notes("machx"))
    assert settings.effective()["roles.summarizer"].source.startswith(settings.NOT_APPLIED)


# --- a corrupt project file: an error naming it, never a reset -------------------------------------------------

def test_a_corrupt_project_file_raises_naming_it_and_is_never_rewritten(workspace):
    _write_global({"output": {"max_tokens": 40000}})
    path = _write_project(workspace, "{not json")
    before, global_before = path.read_bytes(), settings_path().read_bytes()
    for call in (settings.read, settings.effective, settings.read_project):      # the explicit commands refuse
        with pytest.raises(ValueError) as caught:
            call()
        assert str(path) in str(caught.value), str(caught.value)
    # The runtime starts on the global values and says why the project file is ignored.
    assert settings.vitals_enabled() is True
    settings.apply_saved_output()
    assert config.MAX_OUTPUT_TOKENS == 40000
    assert settings.main_model() is None and settings.role("critic") is None
    assert settings.role_model("verifier", "machx") is None
    notes = settings.role_notes("machx")
    assert len(notes) == 1 and str(path) in notes[0] and "ignored" in notes[0]
    assert settings.sdk_model("researcher") == "inherit"
    report = settings.check()
    assert report["ok"] is False and str(path) in report["errors"][0]
    # A project-scope write is refused before anything is written; a global write does not read the project file.
    with pytest.raises(ValueError) as caught:
        settings.set_value("output.max_tokens", "9000", scope="project")
    assert str(path) in str(caught.value)
    assert path.read_bytes() == before and settings_path().read_bytes() == global_before
    assert {p.name for p in path.parent.iterdir()} <= {"settings.json", "settings.json.lock"}   # no temp file left
    settings.set_value("output.max_tokens", "9000")
    assert read_settings()["output"] == {"max_tokens": 9000} and path.read_bytes() == before


def test_a_project_file_that_is_not_a_regular_file_is_refused(workspace):
    folder = workspace / ".dream" / "settings.json"
    folder.mkdir(parents=True)
    with pytest.raises(ValueError, match="project"):
        settings.read()


# --- set / unset with the project scope --------------------------------------------------------------------------

def test_set_and_unset_round_trip_in_the_project_scope(workspace):
    path = workspace / ".dream" / "settings.json"
    note = settings.set_value("output.max_tokens", "9000", scope="project")
    assert str(path) in note
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1, "output": {"max_tokens": 9000}}
    assert not settings_path().exists()
    settings.set_value("behaviour.vitals", "off", scope="project")
    settings.set_value("roles.verifier.model", "project-checker", scope="project")
    settings.set_value("roles.subagents.researcher.model", "project-researcher", scope="project")
    assert settings.read_project() == {"version": 1, "output": {"max_tokens": 9000}, "behaviour": {"vitals": False},
                                       "roles": {"verifier": {"model": "project-checker"},
                                                 "subagents": {"researcher": {"model": "project-researcher"}}}}
    rows = settings.effective()
    assert rows["output.max_tokens"] == (9000, "project") and rows["behaviour.vitals"] == (False, "project")
    assert rows["roles.verifier"] == ({"model": "project-checker"}, "project")
    assert settings.unset_value("output.max_tokens", scope="project").startswith("removed")
    assert settings.unset_value("roles.verifier.model", scope="project").startswith("removed")
    assert settings.unset_value("roles.subagents.researcher", scope="project").startswith("removed")
    assert settings.unset_value("behaviour.vitals", scope="project").startswith("removed")
    assert settings.read_project() == {"version": 1}
    assert settings.unset_value("output.max_tokens", scope="project") == "output.max_tokens was not set"
    assert not settings_path().exists()


def test_a_project_scope_write_leaves_the_global_file_alone_and_the_global_scope_the_project(workspace):
    _write_global({"version": 1, "output": {"max_tokens": 40000}})
    global_before = settings_path().read_bytes()
    settings.set_value("output.max_tokens", "9000", scope="project")
    assert settings_path().read_bytes() == global_before
    project_before = settings.project_path().read_bytes()
    settings.set_value("output.max_tokens", "41000")
    assert settings.project_path().read_bytes() == project_before
    assert read_settings()["output"] == {"max_tokens": 41000}
    assert settings.effective()["output.max_tokens"] == (9000, "project")
    settings.unset_value("output.max_tokens")                    # the global one; the project's stays
    assert settings.effective()["output.max_tokens"] == (9000, "project")


def test_a_provider_cannot_be_set_in_the_project_scope(workspace):
    for key in ("roles.verifier.provider", "roles.subagents.default.provider", "roles.critic.provider",
                "roles.evaluator.provider"):
        with pytest.raises(ValueError) as caught:
            settings.set_value(key, "openai", scope="project")
        assert key in str(caught.value) and "global" in str(caught.value)
    with pytest.raises(ValueError, match="roles.filer.provider"):
        settings.set_role("roles.filer", {"model": "x", "provider": "openai"}, scope="project")
    assert settings.project_path() is not None and not settings.project_path().exists()
    assert settings.set_role("roles.filer", {"model": "project-filer"}, scope="project")
    assert settings.read_project()["roles"] == {"filer": {"model": "project-filer"}}
    assert settings.set_role("roles.filer", {}, scope="project").startswith("removed")
    assert "roles" not in settings.read_project()


def test_a_project_set_that_makes_the_merge_invalid_is_refused(workspace):
    with pytest.raises(ValueError) as caught:
        settings.set_value("roles.critic.model", "project-critic", scope="project")
    assert "roles.critic" in str(caught.value)
    assert not settings.project_path().exists()
    _write_global({"roles": {"critic": {"provider": "codex"}}})
    settings.set_value("roles.critic.model", "project-critic", scope="project")
    assert settings.read()["roles"]["critic"] == {"provider": "codex", "model": "project-critic"}


def test_the_project_scope_needs_a_workspace():
    with pytest.raises(ValueError, match="workspace"):
        settings.set_value("output.max_tokens", "9000", scope="project")
    with pytest.raises(ValueError, match="scope"):
        settings.set_value("output.max_tokens", "9000", scope="elsewhere")


# --- the CLI ---------------------------------------------------------------------------------------------------

def _cli(capsys, *argv):
    code = management.main(["settings", *argv])
    return code, capsys.readouterr().out


def test_cli_project_flag_round_trips_and_show_reports_the_project_source(capsys, tmp_path):
    ws = tmp_path / "project"
    ws.mkdir()
    _write_global({"output": {"max_tokens": 40000}})
    code, out = _cli(capsys, "set", "output.max_tokens", "9000", "--project", "--workspace", str(ws))
    assert code == 0 and str(ws / ".dream" / "settings.json") in out
    assert json.loads((ws / ".dream" / "settings.json").read_text(encoding="utf-8"))["output"] == {"max_tokens": 9000}
    assert read_settings()["output"] == {"max_tokens": 40000}
    code, out = _cli(capsys, "show", "--workspace", str(ws))
    assert code == 0 and json.loads(out)["settings"]["output.max_tokens"] == {"value": 9000, "source": "project"}
    code, out = _cli(capsys, "get", "output.max_tokens")           # another directory: the global value
    assert json.loads(out)["settings"]["output.max_tokens"] == {"value": 40000, "source": "global"}
    code, out = _cli(capsys, "path", "--workspace", str(ws))
    assert json.loads(out)["project"] == str(ws / ".dream" / "settings.json")
    code, out = _cli(capsys, "check", "--workspace", str(ws))
    assert code == 0 and json.loads(out)["project"] == str(ws / ".dream" / "settings.json")
    code, out = _cli(capsys, "unset", "output.max_tokens", "--project", "--workspace", str(ws))
    assert code == 0 and "removed" in out
    assert "output" not in json.loads((ws / ".dream" / "settings.json").read_text(encoding="utf-8"))
    assert read_settings()["output"] == {"max_tokens": 40000}


def test_cli_default_workspace_is_the_current_directory(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = _cli(capsys, "set", "roles.verifier.model", "project-checker", "--project")
    assert code == 0
    assert json.loads((tmp_path / ".dream" / "settings.json").read_text(encoding="utf-8"))["roles"] == {
        "verifier": {"model": "project-checker"}}
    code, out = _cli(capsys, "set", "roles.verifier.provider", "openai", "--project")
    assert code == 2 and "roles.verifier.provider" in out


def test_cli_check_fails_on_a_forbidden_project_key(capsys, tmp_path):
    path = _write_project(tmp_path, {"overrides": {"context_limit": 4096}})
    code, out = _cli(capsys, "check", "--workspace", str(tmp_path))
    assert code == 2
    report = json.loads(out)
    assert report["ok"] is False and "overrides" in report["errors"][0] and str(path) in report["errors"][0]


# --- the TUI ---------------------------------------------------------------------------------------------------

def _app(tmp_path, backend_obj=None):
    app = App(provider="machx", model=LEAD, workspace=tmp_path)
    app.engine = SimpleNamespace(store=None, backend=backend_obj or SimpleNamespace(n_ctx=None),
                                 provider=get_provider("machx"), model=LEAD)
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    return app


def test_tui_settings_set_and_unset_with_project(tmp_path):
    live = SimpleNamespace(n_ctx=None, vitals=True)
    app = _app(tmp_path, live)
    path = tmp_path / ".dream" / "settings.json"
    asyncio.run(app._command("/settings set output.max_tokens 9000 --project"))
    assert json.loads(path.read_text(encoding="utf-8"))["output"] == {"max_tokens": 9000}
    assert not settings_path().exists()
    asyncio.run(app._command("/settings set behaviour.vitals off --project"))
    assert live.vitals is False                     # the switch applies to this session from either scope
    asyncio.run(app._command("/settings path"))
    asyncio.run(app._command("/settings show"))
    out = app.renderer.console.file.getvalue()
    assert str(path) in out and "project" in out
    asyncio.run(app._command("/settings unset behaviour.vitals --project"))
    assert live.vitals is True
    asyncio.run(app._command("/settings unset output.max_tokens --project"))
    assert "output" not in json.loads(path.read_text(encoding="utf-8"))
    asyncio.run(app._command("/settings set roles.verifier.provider openai --project"))
    assert "roles.verifier.provider" in app.renderer.console.file.getvalue()
    assert "roles" not in json.loads(path.read_text(encoding="utf-8"))


# --- Studio: the Settings tab saves to a chosen scope ---------------------------------------------------------

@pytest.fixture
def studio(tmp_path):
    app = App(provider="machx", workspace=tmp_path)
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        headers = {"x-dream-token": server.token}

        def call(payload, status=200):
            reply = client.post("/api/control", headers=headers, json=payload)
            assert reply.status_code == status, reply.text
            return reply.json()["result"] if status == 200 else reply.json()["error"]
        yield app, server, client, call


def _rows(view):
    return {row["key"]: row for section in view["sections"] for row in section["rows"]}


def test_studio_view_names_the_project_file_and_marks_project_rows(studio, tmp_path):
    _write_global({"output": {"max_tokens": 40000}})
    _write_project(tmp_path, {"output": {"max_tokens": 9000}, "roles": {"verifier": {"model": "project-checker"}}})
    _, _, _, call = studio
    view = call({"action": "settings_get"})
    assert view["ok"] is True
    assert view["project_file"] == str(tmp_path.resolve() / ".dream" / "settings.json")
    rows = _rows(view)
    assert rows["output.max_tokens"]["origin"] == "project" and rows["output.max_tokens"]["value"] == 9000
    assert "project" in rows["output.max_tokens"]["source_text"].lower()
    assert rows["output.max_tokens"]["edit"]["value"] == 9000
    assert rows["roles.verifier"]["origin"] == "project"
    assert rows["roles.verifier"]["edit"]["model"] == "project-checker" and rows["roles.verifier"]["edit"]["removable"]
    assert rows["behaviour.vitals"]["origin"] == "default"


def test_studio_save_with_scope_project_writes_the_project_file_only(studio, tmp_path):
    _write_global({"version": 1, "output": {"max_tokens": 40000}})
    global_before = settings_path().read_bytes()
    _, _, _, call = studio
    path = tmp_path.resolve() / ".dream" / "settings.json"
    result = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000", "scope": "project"})
    assert result["key"] == "output.max_tokens" and result["scope"] == "project"
    assert "project" in result["applies"].lower()
    assert json.loads(path.read_text(encoding="utf-8"))["output"] == {"max_tokens": 9000}
    assert settings_path().read_bytes() == global_before
    assert _rows(result["settings"])["output.max_tokens"]["origin"] == "project"
    # A role in the project scope: the model only; the provider is refused with the key named.
    result = call({"action": "settings_save", "key": "roles.verifier", "value": {"model": "project-checker"},
                   "scope": "project"})
    assert json.loads(path.read_text(encoding="utf-8"))["roles"] == {"verifier": {"model": "project-checker"}}
    error = call({"action": "settings_save", "key": "roles.filer", "value": {"model": "x", "provider": "openai"},
                  "scope": "project"}, status=400)
    assert "roles.filer.provider" in error and "global" in error
    assert "filer" not in json.loads(path.read_text(encoding="utf-8"))["roles"]
    # The Council is one file for every workspace.
    error = call({"action": "settings_save", "key": "council.advisor.codex", "value": {"model": "x", "effort": ""},
                  "scope": "project"}, status=400)
    assert "council.advisor.codex" in error
    # Removing in the project scope removes the project's value; the global one shows through.
    result = call({"action": "settings_save", "key": "output.max_tokens", "value": None, "scope": "project"})
    assert "output" not in json.loads(path.read_text(encoding="utf-8"))
    assert _rows(result["settings"])["output.max_tokens"]["origin"] == "global"
    # An unknown scope is refused and writes nothing.
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000", "scope": "elsewhere"},
                 status=400)
    assert "scope" in error
    assert settings_path().read_bytes() == global_before


def test_studio_save_without_a_scope_is_the_global_save_as_before(studio, tmp_path):
    _, _, _, call = studio
    result = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000"})
    assert result["scope"] == "global"
    assert read_settings()["output"] == {"max_tokens": 9000}
    assert not (tmp_path / ".dream").exists()


def test_studio_reports_a_corrupt_project_file_and_refuses_project_saves_only(studio, tmp_path):
    """A bad project file is reported and ignored: the tab shows the global values and saves to this computer
    still work; the workspace scope is refused, naming the file (gate round 2, decision 3)."""
    _write_global({"output": {"max_tokens": 40000}})
    path = _write_project(tmp_path, "{not json")
    before = path.read_bytes()
    _, _, _, call = studio
    view = call({"action": "settings_get"})
    assert view["ok"] is True and view["error"] is None
    assert str(path) in view["project_error"] and "ignored" in view["project_error"] and "never rewrites" in view["project_error"]
    assert view["warnings"][0] == view["project_error"]
    assert _rows(view)["output.max_tokens"]["origin"] == "global" and _rows(view)["output.max_tokens"]["value"] == 40000
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000", "scope": "project"},
                 status=400)
    assert str(path) in error and "saves to this computer still work" in error
    result = call({"action": "settings_save", "key": "output.max_tokens", "value": "9000"})       # global works
    assert result["scope"] == "global" and read_settings()["output"] == {"max_tokens": 9000}
    assert _rows(result["settings"])["output.max_tokens"]["value"] == 9000
    assert path.read_bytes() == before


def test_settings_panel_save_signature_defaults_to_global(tmp_path):
    settings.set_workspace(tmp_path)
    result = settings_panel.save("behaviour.vitals", False)
    assert result["scope"] == "global" and read_settings()["behaviour"] == {"vitals": False}
    result = settings_panel.save("behaviour.vitals", True, scope="project")
    assert result["scope"] == "project" and settings.read_project()["behaviour"] == {"vitals": True}
    assert settings.effective()["behaviour.vitals"] == (True, "project")


# --- headless Chromium: the scope choice in the Settings tab ---------------------------------------------------

@pytest.fixture
async def settings_page(tmp_path):
    _write_global({"version": 1, "output": {"max_tokens": 50000}})
    app = App(provider="machx", workspace=tmp_path)
    server = StudioServer(EventBus(), session={"workspace": str(tmp_path), "provider": "machx", "model": "fixture"},
                          on_control=app._runtime_control)
    url = await server.start()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 800, "height": 900}, reduced_motion="reduce")
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            yield page, url.replace("/#", "/?companion=1#"), errors
            await browser.close()
    finally:
        await server.stop()


async def test_settings_tab_saves_to_the_chosen_scope(settings_page, tmp_path):
    page, url, errors = settings_page
    project = tmp_path.resolve() / ".dream" / "settings.json"
    await page.goto(url)
    await expect(page.locator("#stat")).to_have_text("Ready")
    await page.locator("#dream-controls-open").click()
    await page.get_by_role("tab", name="Settings", exact=True).click()
    panel = page.locator("#dc-settings")
    scope = panel.locator(".dc-settings-scope")
    await expect(scope).to_be_visible()
    await expect(scope).to_contain_text(str(project))
    await expect(scope.get_by_label("This computer", exact=False)).to_be_checked()
    # Global scope, as before: the provider select is live and a save lands in the global file.
    verifier = panel.locator('[data-key="roles.verifier"]')
    await expect(verifier.get_by_label("Verifier provider")).to_be_enabled()
    # Project scope: the provider select is read-only, a save lands in the project file, the row says Project.
    await scope.get_by_label("This workspace", exact=False).check()
    await expect(panel.locator('[data-key="roles.verifier"]').get_by_label("Verifier provider")).to_be_disabled()
    row = panel.locator('[data-key="output.max_tokens"]')
    await expect(row.locator(".dc-source")).to_have_text("Saved")
    await row.get_by_label("Max output tokens").fill("9000")
    await row.get_by_role("button", name="Save").click()
    await expect(page.locator("#dc-feedback")).to_contain_text("project file")
    assert json.loads(project.read_text(encoding="utf-8"))["output"] == {"max_tokens": 9000}
    assert read_settings()["output"] == {"max_tokens": 50000}
    row = panel.locator('[data-key="output.max_tokens"]')
    await expect(row.locator(".dc-source")).to_have_text("Project")
    await expect(row.get_by_label("Max output tokens")).to_have_value("9000")
    # The scope choice survives the re-render; a role saved here carries its model only.
    await expect(panel.locator(".dc-settings-scope").get_by_label("This workspace", exact=False)).to_be_checked()
    verifier = panel.locator('[data-key="roles.verifier"]')
    await verifier.get_by_label("Verifier model").fill("project-check")
    await verifier.get_by_role("button", name="Save").click()
    await expect(panel.locator('[data-key="roles.verifier"] .dc-source')).to_have_text("Project")
    assert json.loads(project.read_text(encoding="utf-8"))["roles"] == {"verifier": {"model": "project-check"}}
    assert "roles" not in read_settings()
    # A reset removes the value where it is saved: the project file's, and the global value shows through.
    await panel.locator('[data-key="output.max_tokens"]').get_by_role("button", name="Use default").click()
    await expect(panel.locator('[data-key="output.max_tokens"] .dc-source')).to_have_text("Saved")
    assert "output" not in json.loads(project.read_text(encoding="utf-8"))
    assert read_settings()["output"] == {"max_tokens": 50000}
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    await panel.locator(".dc-settings-scope").scroll_into_view_if_needed()
    await page.screenshot(path=str(tmp_path / "settings-scope-800.png"))
    await page.set_viewport_size({"width": 480, "height": 800})
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert await page.locator(".dc-scroll").evaluate("e => e.scrollWidth <= e.clientWidth")
    await page.screenshot(path=str(tmp_path / "settings-scope-480.png"))
    assert errors == []


# --- gate round 1 findings -------------------------------------------------------------------------------------

@pytest.mark.parametrize("absolute", [False, True], ids=["relative-link", "absolute-link"])
def test_a_linked_dream_folder_is_refused_on_read_and_write_and_nothing_lands_outside(tmp_path, absolute, capsys):
    """Finding 1: O_NOFOLLOW covers the file only; a cloned repository can ship `.dream -> ../anywhere`."""
    ws = tmp_path / "project"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "settings.json").write_text(json.dumps({"output": {"max_tokens": 9000}}), encoding="utf-8")
    link = ws / ".dream"
    link.symlink_to(outside if absolute else Path("..") / "outside", target_is_directory=True)
    settings.set_workspace(ws)
    before = sorted(p.name for p in outside.iterdir())
    for call in (settings.read, settings.effective, settings.read_project):      # the explicit commands refuse
        with pytest.raises(ValueError) as caught:
            call()
        assert str(link) in str(caught.value) and "symbolic link" in str(caught.value), str(caught.value)
    # The runtime ignores the linked folder as a whole: nothing of the file behind it is read.
    assert settings.vitals_enabled() is True
    settings.apply_saved_output()
    assert config.MAX_OUTPUT_TOKENS == 131072                    # the linked file's 9000 was never applied
    assert settings.main_model() is None and settings.role_model("verifier", "machx") is None
    notes = settings.role_notes("machx")
    assert len(notes) == 1 and str(link) in notes[0] and "symbolic link" in notes[0] and "ignored" in notes[0]
    assert settings.sdk_model("researcher") == "inherit"
    report = settings.check()
    assert report["ok"] is False and str(link) in report["errors"][0]
    with pytest.raises(ValueError) as caught:
        settings.set_value("output.max_tokens", "5000", scope="project")
    assert str(link) in str(caught.value)
    with pytest.raises(ValueError):
        settings.set_role("roles.filer", {"model": "x"}, scope="project")
    code, out = _cli(capsys, "set", "output.max_tokens", "5000", "--project", "--workspace", str(ws))
    assert code == 2 and str(link) in out
    code, out = _cli(capsys, "show", "--workspace", str(ws))
    assert code == 2 and str(link) in out
    assert sorted(p.name for p in outside.iterdir()) == before          # no settings.json change, no .lock
    assert (outside / "settings.json").read_text(encoding="utf-8") == json.dumps({"output": {"max_tokens": 9000}})
    assert link.is_symlink()                                            # the link itself is left alone


def test_a_dangling_dream_link_is_not_followed_on_the_first_write(tmp_path, capsys):
    ws = tmp_path / "project"
    ws.mkdir()
    (ws / ".dream").symlink_to(tmp_path / "nowhere", target_is_directory=True)
    settings.set_workspace(ws)
    with pytest.raises(ValueError) as caught:
        settings.set_value("output.max_tokens", "5000", scope="project")
    assert str(ws / ".dream") in str(caught.value)
    assert not (tmp_path / "nowhere").exists() and (ws / ".dream").is_symlink()
    with pytest.raises(ValueError, match="symbolic link"):      # reads refuse the link too; nothing was created
        settings.read()
    # A file where the folder should be is refused as well.
    (ws / ".dream").unlink()
    (ws / ".dream").write_text("not a folder", encoding="utf-8")
    with pytest.raises(ValueError, match="not a real directory"):
        settings.set_value("output.max_tokens", "5000", scope="project")
    assert (ws / ".dream").read_text(encoding="utf-8") == "not a folder"


def test_the_first_project_write_creates_a_real_dream_folder(workspace):
    settings.set_value("output.max_tokens", "5000", scope="project")
    folder = workspace / ".dream"
    assert folder.is_dir() and not folder.is_symlink()
    assert json.loads((folder / "settings.json").read_text(encoding="utf-8"))["output"] == {"max_tokens": 5000}


def test_studio_refuses_a_linked_dream_folder(studio, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".dream").symlink_to(outside, target_is_directory=True)
    _, _, _, call = studio
    view = call({"action": "settings_get"})
    assert view["ok"] is True and ".dream" in view["project_error"] and "symbolic link" in view["project_error"]
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": "5000", "scope": "project"},
                 status=400)
    assert ".dream" in error and "symbolic link" in error
    result = call({"action": "settings_save", "key": "output.max_tokens", "value": "5000"})   # this computer: fine
    assert result["scope"] == "global" and read_settings()["output"] == {"max_tokens": 5000}
    assert list(outside.iterdir()) == []


def test_a_refused_project_file_contributes_nothing(workspace):
    """Finding 2: a project file with a forbidden section lent its output and behaviour sections anyway."""
    _write_global({"output": {"max_tokens": 40000}, "behaviour": {"vitals": True}})
    path = _write_project(workspace, {"permissions": {"mode": "auto"}, "output": {"max_tokens": 9000},
                                      "behaviour": {"vitals": False}})
    # The runtime ignores the refused file as a whole and runs on the global values (gate round 2, decision 2)...
    assert settings.vitals_enabled() is True
    settings.apply_saved_output()
    assert config.MAX_OUTPUT_TOKENS == 40000
    assert settings.read_runtime() == read_settings()
    notes = settings.role_notes("machx")
    assert len(notes) == 1
    assert str(path) in notes[0] and "permissions" in notes[0] and "project settings file" in notes[0]
    assert "ignored" in notes[0] and "roles in runtime settings" not in notes[0]
    # ...while the explicit commands refuse it, naming the file and the key.
    for call in (settings.read, settings.effective):
        with pytest.raises(ValueError) as caught:
            call()
        assert "permissions" in str(caught.value) and str(path) in str(caught.value), str(caught.value)
    assert settings.project_problem().startswith("a project file may not set permissions")
    # The global file's own rule is unchanged: a bad roles section there does not stop vitals or max tokens.
    path.unlink()
    _write_global({"output": {"max_tokens": 40000}, "behaviour": {"vitals": False}, "roles": {"verifier": 3}})
    assert settings.vitals_enabled() is False
    settings.apply_saved_output()
    assert config.MAX_OUTPUT_TOKENS == 40000


@pytest.mark.parametrize("name", ["evaluator", "reviewer", "critic"])
def test_a_project_model_for_a_cloud_role_needs_the_global_provider(workspace, name):
    """Finding 3 (owner default): a cloned repository must not choose the model a job runs on the owner's account."""
    path = _write_project(workspace, {"roles": {name: {"model": "pricey-model"}}})
    globals_without_provider = [{}] + ([{"roles": {name: {"model": "global-model"}}}] if name != "critic" else [])
    for data in globals_without_provider:
        if data:
            _write_global(data)
        elif settings_path().exists():
            settings_path().unlink()
        for call in (settings.read, settings.effective):
            with pytest.raises(ValueError) as caught:
                call()
            message = str(caught.value)
            assert f"roles.{name}.provider" in message and str(path) in message and "global file" in message, message
        # The runtime ignores the project file as a whole: the job keeps the global entry, or its own default.
        assert settings.role(name) == data.get("roles", {}).get(name)
        notes = settings.role_notes("machx")
        assert len(notes) == 1 and f"roles.{name}" in notes[0] and str(path) in notes[0] and "ignored" in notes[0]
    path.unlink()
    with pytest.raises(ValueError) as caught:
        settings.set_value(f"roles.{name}.model", "pricey-model", scope="project")
    assert f"roles.{name}.provider" in str(caught.value) and not path.exists()
    with pytest.raises(ValueError, match=f"roles.{name}.provider"):
        settings.set_role(f"roles.{name}", {"model": "pricey-model"}, scope="project")
    assert not path.exists()
    # With the provider set globally, the project may choose the model.
    _write_global({"roles": {name: {"provider": "codex" if name == "critic" else "openai"}}})
    settings.set_value(f"roles.{name}.model", "project-model", scope="project")
    assert settings.read()["roles"][name] == {"provider": "codex" if name == "critic" else "openai",
                                              "model": "project-model"}
    assert settings.effective()[f"roles.{name}"].source == "project"
    assert settings.role(name) == settings.read()["roles"][name]


def test_sub_agent_verifier_and_filer_project_models_need_no_global_role(workspace):
    _write_project(workspace, {"roles": {"verifier": {"model": "a"}, "filer": {"model": "b"},
                                         "subagents": {"default": {"model": "c"}}}})
    assert settings.role_model("verifier", "machx") == "a"
    assert settings.role_model("filer", "machx") == "b"
    assert settings.role_model("coder", "machx") == "c"


def test_set_project_on_a_file_with_a_forbidden_section_names_the_file(workspace, capsys):
    """Finding 4."""
    path = _write_project(workspace, {"permissions": {"mode": "auto"}})
    before = path.read_bytes()
    with pytest.raises(ValueError) as caught:
        settings.set_value("output.max_tokens", "9000", scope="project")
    assert str(path) in str(caught.value) and "permissions" in str(caught.value)
    with pytest.raises(ValueError) as caught:
        settings.unset_value("output.max_tokens", scope="project")
    assert str(path) in str(caught.value) and "permissions" in str(caught.value)
    code, out = _cli(capsys, "set", "output.max_tokens", "9000", "--project", "--workspace", str(workspace))
    assert code == 2 and str(path) in out and "permissions" in out
    assert path.read_bytes() == before


# --- gate round 2 findings -------------------------------------------------------------------------------------

def test_project_writes_and_reads_never_leave_the_workspace_while_dream_is_swapped_for_a_link(tmp_path):
    """The round-2 blocking finding: a race between the .dream check and the write (the gate's swapper got
    3,780 writes through and overwrote a foreign file). The writer and the reader now work on the checked .dream
    descriptor, so a link swapped in meanwhile is never followed. A swapper thread flips .dream between the real
    folder and a link to an outside folder for a few seconds while project writes and reads loop."""
    import shutil
    import threading
    import time

    ws = tmp_path / "project"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    foreign = outside / "settings.json"
    foreign_text = json.dumps({"output": {"max_tokens": 777}})     # valid project content: a redirected
    foreign.write_text(foreign_text, encoding="utf-8")             # read-modify-write WOULD change it
    outside_before = sorted(p.name for p in outside.iterdir())
    cwd_before = sorted(os.listdir(Path.cwd()))      # a descriptor-less fallback would resolve names against the cwd
    settings.set_workspace(ws)
    settings.set_value("output.max_tokens", "1000", scope="project")      # a real .dream to swap
    dream, held = ws / ".dream", ws / ".dream.held"
    stop, swaps = threading.Event(), [0]

    def swapper():
        while not stop.is_set():
            try:
                if held.exists():            # the writer re-made .dream while ours was held: drop it, restore ours
                    if dream.is_symlink():
                        dream.unlink()
                    else:
                        shutil.rmtree(dream, ignore_errors=True)
                    os.rename(held, dream)
                    continue
                os.rename(dream, held)
                os.symlink(outside, dream, target_is_directory=True)
                os.remove(dream)
                os.rename(held, dream)
                swaps[0] += 1
            except OSError:
                pass

    thread = threading.Thread(target=swapper, daemon=True)
    thread.start()
    written = refused = seen_foreign = reads_refused = reads = 0
    value, deadline = 1000, time.monotonic() + 3.0
    try:
        while time.monotonic() < deadline:
            value += 1
            try:
                settings.set_value("output.max_tokens", str(value), scope="project")
                written += 1
            except (ValueError, OSError):
                refused += 1
            try:
                if settings.read().get("output", {}).get("max_tokens") == 777:
                    seen_foreign += 1
                reads += 1
            except (ValueError, OSError):
                reads_refused += 1
    finally:
        stop.set()
        thread.join(5)
    counts = dict(written=written, refused=refused, reads=reads, reads_refused=reads_refused, swaps=swaps[0])
    assert sorted(p.name for p in outside.iterdir()) == outside_before, counts       # nothing appeared outside
    assert sorted(os.listdir(Path.cwd())) == cwd_before, counts                      # nor in the process cwd
    assert foreign.read_text(encoding="utf-8") == foreign_text, counts               # the foreign file is intact
    assert seen_foreign == 0, counts                                                 # no read went through a link
    assert written > 0 and swaps[0] > 0, counts                                      # both sides really ran
    print("race counts:", counts)


async def test_app_start_proceeds_with_a_refused_project_file(tmp_path, monkeypatch):
    """Decision 2: a refused or broken project file must not stop Dream from starting."""
    _write_global({"output": {"max_tokens": 40000}})
    path = _write_project(tmp_path, {"permissions": {"mode": "auto"}, "output": {"max_tokens": 9000}})
    app = App(provider="machx", model=LEAD, workspace=tmp_path)
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    started = []

    class Engine:
        provider, model, provider_label = get_provider("machx"), LEAD, "MachX"
        store = SimpleNamespace(top_memories=lambda limit: [])

        async def start(self):
            started.append(True)

        def stats(self):
            return {}

    monkeypatch.setattr(app, "_boot_engine", lambda: Engine())

    async def skip(*args, **kwargs):
        return None
    for name in ("_associate_project_on_start", "_start_monitor", "_start_studio"):
        monkeypatch.setattr(app, name, skip)
    monkeypatch.setattr(app, "_welcome", lambda: None)
    await app.start()
    assert started and config.MAX_OUTPUT_TOKENS == 40000        # the global value; the refused file's 9000 is ignored
    notes = settings.role_notes("machx")
    assert len(notes) == 1 and str(path) in notes[0] and "permissions" in notes[0] and "ignored" in notes[0]
    # The explicit command in that session still refuses, naming the file.
    app.engine = SimpleNamespace(store=None, backend=SimpleNamespace(n_ctx=None), provider=get_provider("machx"),
                                 model=LEAD)
    await app._command("/settings set output.max_tokens 7000 --project")
    out = app.renderer.console.file.getvalue()
    assert str(path) in out and "permissions" in out
    await app._command("/settings set output.max_tokens 7000")          # this computer: still works
    assert read_settings()["output"] == {"max_tokens": 7000}


async def test_the_connect_note_names_the_ignored_project_file(tmp_path, monkeypatch):
    from test_cache_friendly_head import FakeEngine, backend
    _write_global({"behaviour": {"vitals": False}})
    path = _write_project(tmp_path, {"permissions": {"mode": "auto"}, "behaviour": {"vitals": True}})
    settings.set_workspace(tmp_path)
    b = backend(FakeEngine(lead=[{"text": "ok"}]), model=LEAD)

    async def probe():
        return 65536
    monkeypatch.setattr(b, "_probe_n_ctx", probe)
    fake = b._client
    await b.connect()
    await b._client.aclose()
    b._client = fake
    assert b.vitals is False                    # the global value; the refused file's True is ignored
    assert any(str(path) in n and "permissions" in n and "ignored" in n for n in b._settings_notices)
