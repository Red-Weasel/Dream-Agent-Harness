"""DREAM-142 S0 (settings design P3, sections 1 and 3): one resolver, `dream/core/settings.py effective()`, says
for every setting the value in effect AND where it came from -- flag > env > session > project > global > default,
except that a session command made after start is what runs (see the module docstring). S0 implements global, env
and default (plus the session value /maxtokens records); flag and project are stubs that never produce a row yet.
`dream settings show|get|path` and the TUI's read-only `/settings` print it without loading a model.
"""
from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream import config, management
from dream.core import settings
from dream.core.profiles import PROFILES, read_settings, save_settings, settings_path
from dream.core.providers import get_provider
from dream.environment import NUMERIC_SETTINGS


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("DREAM_PROFILE", "DREAM_MAX_TOKENS", "DREAM_CONTEXT_WINDOW", "DREAM_MAX_PARALLEL",
                 "DREAM_SUBAGENT_TIMEOUT_S", "DREAM_IDLE_TIMEOUT_S", "DREAM_RUN_TOKEN_BUDGET", "DREAM_RUN_TOOL_BUDGET",
                 "DREAM_RUN_SECONDS", "DREAM_WALL_SECONDS", "DREAM_VISION", "DREAM_VISION_HELPER",
                 "DREAM_AUTO_VERIFY", "DREAM_SINGLE_SLOT_VERIFIER", *NUMERIC_SETTINGS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)


def _write(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- every row: a value and a source ----------------------------------------------------------------------

def test_every_row_has_a_value_and_a_source():
    rows = settings.effective()
    assert rows, "no settings resolved"
    for key, row in rows.items():
        value, source = row
        assert isinstance(key, str) and key
        assert isinstance(source, str) and source, key
        assert source.split(" ", 1)[0] in settings.SOURCES or source.startswith(settings.NOT_APPLIED), (key, source)
    # The families the design names are all there.
    for key in ("profile", "output.max_tokens", "behaviour.vitals", "roles.main", "roles.subagents.default",
                "roles.verifier", "roles.filer", "overrides.context_limit", "overrides.max_parallel",
                "numeric.subagent_rounds", "behaviour.auto_verify", "output.ceiling"):
        assert key in rows, key


def test_defaults_with_no_file_and_no_environment():
    rows = settings.effective(provider="machx")
    assert rows["profile"] == ("auto", "default")
    assert rows["output.max_tokens"] == (131072, "default")
    assert rows["behaviour.vitals"] == (True, "default")
    assert rows["roles.subagents.default"] == ("same as main", "default")
    assert rows["roles.verifier"] == ("same as main", "default")
    assert rows["numeric.subagent_rounds"] == (60, "default")
    # A local provider resolves auto to lean and runs one request at a time.
    assert rows["overrides.context_limit"].source == "default"
    assert rows["overrides.max_parallel"] == (1, "default")
    assert rows["overrides.output_tokens"] == (PROFILES["lean"].output_tokens, "default")


# --- the precedence matrix, per key ---------------------------------------------------------------------

@pytest.mark.parametrize("key,saved,env,env_value,default,saved_value,env_expected", [
    ("profile", {"profile": "balanced"}, "DREAM_PROFILE", "frontier", "auto", "balanced", "frontier"),
    ("output.max_tokens", {"output": {"max_tokens": 50000}}, "DREAM_MAX_TOKENS", "70000", 131072, 50000, 70000),
    ("overrides.context_limit", {"overrides": {"context_limit": 90000}}, "DREAM_CONTEXT_WINDOW", "120000",
     None, 90000, 120000),
    ("overrides.idle_timeout_s", {"overrides": {"idle_timeout_s": 1600}}, "DREAM_IDLE_TIMEOUT_S", "99",
     PROFILES["lean"].idle_timeout_s, 1600, 99.0),
])
def test_default_then_global_then_env(monkeypatch, key, saved, env, env_value, default, saved_value, env_expected):
    assert settings.effective(provider="machx")[key] == (default, "default")
    _write({"version": 1, **saved})
    assert settings.effective(provider="machx")[key] == (saved_value, "global")
    monkeypatch.setenv(env, env_value)
    assert settings.effective(provider="machx")[key] == (env_expected, f"env {env}")


def test_the_session_value_replaces_the_configured_ceiling(monkeypatch):
    """/maxtokens replaces config.MAX_OUTPUT_TOKENS (DREAM_MAX_TOKENS's value) in the running session."""
    _write({"output": {"max_tokens": 50000}})
    monkeypatch.setenv("DREAM_MAX_TOKENS", "70000")
    rows = settings.effective(session={"output.max_tokens": 40000})
    assert rows["output.max_tokens"] == (40000, "session")


async def test_the_ceiling_row_is_what_a_request_actually_asks_for(monkeypatch):
    """Gate round 1 finding 4: DREAM_MAX_TOKENS=8000 also sets the profile's output_tokens, which still caps a
    session /maxtokens 40000 -- the request asks for 8000, and output.ceiling must say so, with its source."""
    from dream.core.profiles import resolve_profile
    from test_cache_friendly_head import FakeEngine, backend, turn
    monkeypatch.setenv("DREAM_MAX_TOKENS", "8000")
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 40000)              # what /maxtokens 40000 records
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", 40000)
    seen = []
    b = backend(FakeEngine(lead=[lambda payload: seen.append(payload["max_tokens"]) or {"text": "ok"}]),
                model="lead-model", profile=resolve_profile(get_provider("machx"), model="lead-model"))
    await turn(b, "hello")
    row = settings.effective(provider="machx", model="lead-model", session={"output.max_tokens": 40000})
    assert seen == [8000]
    assert row["output.ceiling"] == (8000, "env DREAM_MAX_TOKENS (profile output_tokens)")
    assert row["output.max_tokens"] == (40000, "session")


def test_the_ceiling_row_follows_a_preset_and_the_performance_mode():
    rows = settings.effective(session={"output.preset_max_tokens": 16384})
    assert rows["output.ceiling"] == (16384, "global (local model preset)")
    rows = settings.effective(session={"output.preset_max_tokens": 16384, "output.max_tokens": 40000})
    assert rows["output.ceiling"] == (40000, "session")                        # /maxtokens wins over a preset
    rows = settings.effective(session={"output.preset_max_tokens": 16384, "output.performance": 4096})
    assert rows["output.ceiling"] == (4096, "session (performance mode)")


def test_roles_main_is_the_sessions_choice_when_one_is_given():
    assert settings.effective()["roles.main"].source == "default"
    row = settings.effective(session={"roles.main": {"provider": "machx", "model": "lead-model"}})["roles.main"]
    assert row == ({"provider": "machx", "model": "lead-model"}, "session")


def test_numeric_rows_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("DREAM_SUBAGENT_ROUNDS", "12")
    monkeypatch.setenv("DREAM_TOOL_BUDGET", "0")
    rows = settings.effective()
    assert rows["numeric.subagent_rounds"] == (12, "env DREAM_SUBAGENT_ROUNDS")
    assert rows["numeric.tool_budget"] == (None, "env DREAM_TOOL_BUDGET")      # the documented unlimited alias


def test_the_profile_row_values_match_resolve_profile(monkeypatch):
    """Values are the resolved profile's, so the table cannot drift from what a session gets."""
    from dream.core.profiles import resolve_profile
    _write({"profile": "balanced", "overrides": {"max_parallel": 2}})
    monkeypatch.setenv("DREAM_CONTEXT_WINDOW", "65536")
    resolved = resolve_profile(get_provider("openai"))
    rows = settings.effective(provider="openai")
    for field in ("context_limit", "output_tokens", "max_parallel", "idle_timeout_s", "auto_filer"):
        assert rows[f"overrides.{field}"].value == getattr(resolved, field), field
    assert rows["overrides.max_parallel"].source == "global"


def test_flag_and_project_are_stubs_in_s0():
    assert "flag" in settings.SOURCES and "project" in settings.SOURCES
    assert not any(row.source.startswith(("flag", "project")) for row in settings.effective().values())


# --- the file: unknown sections kept, corrupt file raises and is left alone ------------------------------

def test_unknown_sections_survive_a_profile_save():
    path = _write({"version": 1, "profile": "lean", "blender": {"binary": "/opt/tool"}, "future": {"x": [1, 2]},
                   "roles": {"verifier": {"model": "checker-model"}}})
    save_settings("balanced")
    saved = read_settings(path)
    assert saved["future"] == {"x": [1, 2]} and saved["blender"] == {"binary": "/opt/tool"}
    assert saved["roles"] == {"verifier": {"model": "checker-model"}}
    assert saved["profile"] == "balanced"


@pytest.mark.parametrize("text", ["{not json", '{"profile": "nope"}', "[]", '{"output": {"max_tokens": "big"}}',
                                  '{"behaviour": {"vitals": "yes"}}', '{"roles": {"verifier": {"model": ""}}}'])
def test_a_corrupt_file_raises_and_is_left_untouched(text):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        settings.effective()
    assert path.read_text(encoding="utf-8") == text


# --- the CLI: show, get, path (no model loaded) ----------------------------------------------------------

def _cli(capsys, *argv):
    code = management.main(["settings", *argv])
    return code, capsys.readouterr().out


def test_cli_show_prints_every_row_with_its_source(capsys):
    _write({"output": {"max_tokens": 50000}})
    code, out = _cli(capsys, "show")
    assert code == 0
    data = json.loads(out)
    assert data["provider"] == "machx" and data["model"] is None               # what it resolved for
    assert data["settings"]["output.max_tokens"] == {"value": 50000, "source": "global"}
    assert all(set(row) == {"value", "source"} for row in data["settings"].values())


def test_cli_show_resolves_for_a_given_provider_and_model(capsys):
    _write({"models": {"openai:remote-model": {"output_tokens": 2048}},
            "roles": {"verifier": {"model": "checker-model"}}})
    code, out = _cli(capsys, "show", "--provider", "openai", "--model", "remote-model")
    data = json.loads(out)
    assert code == 0 and (data["provider"], data["model"]) == ("openai", "remote-model")
    assert data["settings"]["overrides.output_tokens"] == {"value": 2048,
                                                           "source": "global models[openai:remote-model]"}
    assert data["settings"]["roles.verifier"]["source"].startswith("not applied on this backend (openai)")


def test_cli_bare_settings_is_show(capsys):
    code, out = _cli(capsys)
    assert code == 0 and "output.max_tokens" in json.loads(out)["settings"]


def test_cli_get_one_key_and_an_unknown_key(capsys):
    code, out = _cli(capsys, "get", "behaviour.vitals")
    assert code == 0 and json.loads(out)["settings"] == {"behaviour.vitals": {"value": True, "source": "default"}}
    code, out = _cli(capsys, "get", "no.such.key")
    assert code == 2 and "no.such.key" in out


def test_cli_path_lists_the_files_it_reads(capsys):
    code, out = _cli(capsys, "path")
    data = json.loads(out)
    assert code == 0
    assert data["global"] == str(settings_path())
    assert {"model_presets", "council", "extensions", "mcp", "instructions", "project"} <= set(data)


def test_cli_corrupt_file_is_an_error_not_a_reset(capsys):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    code, out = _cli(capsys, "show")
    assert code == 2 and "repair" in out
    assert path.read_text(encoding="utf-8") == "{broken"


def test_the_entrypoint_routes_settings_to_management(monkeypatch):
    from dream import __main__ as entry
    seen = []
    monkeypatch.setattr(management, "main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr("sys.argv", ["dream", "settings", "path"])
    with pytest.raises(SystemExit) as stop:
        entry._main()
    assert stop.value.code == 0 and seen == [["settings", "path"]]


# --- the TUI: /settings, read-only view ------------------------------------------------------------------

def _app():
    from dream.tui.app import App
    app = App(provider="machx", model="lead-model")
    app.engine = SimpleNamespace(store=None, backend=SimpleNamespace(n_ctx=None),
                                 provider=get_provider("machx"), model="lead-model")
    app.renderer.console = Console(file=io.StringIO(), width=200, force_terminal=False, color_system=None)
    return app


def test_tui_settings_shows_values_and_sources():
    _write({"output": {"max_tokens": 50000}})
    app = _app()
    asyncio.run(app._command("/settings"))
    out = app.renderer.console.file.getvalue()
    assert "output.max_tokens" in out and "50000" in out.replace(",", "") and "global" in out
    assert "roles.main" in out and "lead-model" in out and "session" in out
    assert "resolved for provider machx, model lead-model" in out


def test_tui_settings_get_and_path():
    app = _app()
    asyncio.run(app._command("/settings get behaviour.vitals"))
    asyncio.run(app._command("/settings path"))
    out = app.renderer.console.file.getvalue()
    assert "behaviour.vitals" in out and "default" in out
    assert str(settings_path()) in out.replace("\n", "")


def test_tui_help_lists_settings():
    from dream.tui.app import HELP
    assert "/settings" in HELP
