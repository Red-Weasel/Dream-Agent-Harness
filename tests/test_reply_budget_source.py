"""The main agent's reply budget says where it comes from (the owner's run of 2026-10-02).

The owner's saved launch preset for the 35B carries max_tokens 16,384 (the load screen's max tokens field, which the
Recommended settings card calls Max output). With a launch max_tokens Dream asks for min(that, the room left in the
window) on every request of the main agent (openai_compat._base_max_tokens), so the profile's output_tokens -- the
owner had saved 60,000 -- is never consulted; the settings view still showed 60,000 on that row, and Studio's "Output a
turn may use now" showed Dream's own 131,072. Nothing is different now, only said: the row shows what is in effect
and why, in `dream settings`' resolver, /settings and Studio's Settings tab, and the load screen's field says what it
caps. No engine, model or GPU.
"""
from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console
from starlette.testclient import TestClient

from dream import config
from dream.core import settings
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.profiles import PROFILES, resolve_profile, settings_path
from dream.core.providers import get_provider
from dream.environment import NUMERIC_SETTINGS
from dream.gui import settings_panel
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.local import machx, settings as local_settings
from dream.tui.app import App

LAUNCH, SAVED, DREAMS_OWN = 16_384, 60_000, 131_072
LOAD_SCREEN = "from this model's load screen (max tokens; Max output on its card)"
NO_LAUNCH = " applies only where no launch sets it (another provider, or an engine that was already running)"
FROM_LAUNCH = LOAD_SCREEN + "; the saved 60,000" + NO_LAUNCH


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("DREAM_PROFILE", "DREAM_MAX_TOKENS", "DREAM_CONTEXT_WINDOW", *NUMERIC_SETTINGS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", DREAMS_OWN)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)
    monkeypatch.setattr(machx, "BASE_URL", "http://127.0.0.1:1/v1")       # the Settings tab's layout section


def _save(output_tokens=SAVED):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "overrides": {"output_tokens": output_tokens}}), encoding="utf-8")


def _lead(launch):
    """The main agent's backend as a session builds it: the saved profile, a 262,144 window, the launch's options."""
    provider = get_provider("machx")
    b = OpenAICompatBackend(provider=provider, model="lead-model", system_prompt="s", tools=[], permission_cb=None,
                            subagents=None, profile=resolve_profile(provider, model="lead-model"))
    b.n_ctx, b._local_options = 262_144, dict(launch)
    return b


def _app(launch):
    app = App(provider="machx", model="lead-model")
    app.engine = SimpleNamespace(store=None, backend=_lead(launch), provider=get_provider("machx"), model="lead-model")
    app.renderer.console = Console(file=io.StringIO(), width=400, force_terminal=False, color_system=None)
    return app


def _rows(view):
    return {row["key"]: row for section in view["sections"] for row in section["rows"]}


# --- the fact the rows describe ---------------------------------------------------------------------------------

def test_a_launch_max_tokens_is_what_the_main_agent_asks_for_and_the_saved_reserve_is_not_consulted():
    _save()
    assert _lead({"max_tokens": LAUNCH})._max_tokens(0) == LAUNCH
    assert _lead({})._max_tokens(0) == SAVED                             # no launch value: the saved reserve


# --- the resolver: `dream settings`, /settings and Studio read these rows ------------------------------------------

def test_the_output_row_shows_the_launch_value_and_says_the_saved_one_is_not_used():
    _save()
    rows = settings.effective(provider="machx", model="lead-model", session={"output.preset_max_tokens": LAUNCH})
    assert rows["overrides.output_tokens"] == (LAUNCH, FROM_LAUNCH)
    assert rows["output.ceiling"] == (LAUNCH, LOAD_SCREEN)


def test_without_a_launch_max_tokens_the_output_row_is_the_saved_value_as_before():
    _save()
    rows = settings.effective(provider="machx", model="lead-model", session={})
    assert rows["overrides.output_tokens"] == (SAVED, "global")
    assert rows["output.ceiling"] == (SAVED, "global (profile output_tokens)")
    assert settings.effective(provider="machx", model="lead-model")["overrides.output_tokens"] == (SAVED, "global")


def test_the_row_names_what_the_unused_value_is(monkeypatch):
    session = {"output.preset_max_tokens": LAUNCH}
    row = settings.effective(provider="machx", session=session)["overrides.output_tokens"]       # nothing saved
    assert row == (LAUNCH, f"{LOAD_SCREEN}; the profile's {PROFILES['lean'].output_tokens:,}{NO_LAUNCH}")
    monkeypatch.setenv("DREAM_MAX_TOKENS", "70000")
    row = settings.effective(provider="machx", session=session)["overrides.output_tokens"]
    assert row == (LAUNCH, f"{LOAD_SCREEN}; DREAM_MAX_TOKENS's 70,000{NO_LAUNCH}")


def test_a_session_maxtokens_replaces_the_launch_value_and_the_row_says_so(monkeypatch):
    _save()
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 40_000)              # what /maxtokens 40000 records
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", 40_000)
    assert _lead({"max_tokens": LAUNCH})._max_tokens(0) == 40_000
    rows = settings.effective(provider="machx", model="lead-model",
                              session={"output.preset_max_tokens": LAUNCH, "output.max_tokens": 40_000})
    assert rows["overrides.output_tokens"] == (
        40_000, "session (/maxtokens, in place of the load screen's 16,384); the saved 60,000" + NO_LAUNCH)
    assert rows["output.ceiling"] == (40_000, "session")


# --- /settings in the terminal ----------------------------------------------------------------------------------

@pytest.mark.parametrize("launch, value, source", [({"max_tokens": LAUNCH}, "16384", FROM_LAUNCH), ({}, "60000", "global")],
                         ids=["a launch with max_tokens", "a launch without"])
def test_slash_settings_prints_the_row_for_the_running_session(launch, value, source):
    _save()
    app = _app(launch)
    asyncio.run(app._command("/settings get overrides.output_tokens"))
    line = app.renderer.console.file.getvalue().splitlines()[-1]
    assert line.split() == ["overrides.output_tokens", value, *source.split()]


# --- Studio: Controls -> Settings ---------------------------------------------------------------------------------

def _studio_rows(app):
    server = StudioServer(EventBus(), on_control=app._runtime_control)
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        reply = client.post("/api/control", headers={"x-dream-token": server.token}, json={"action": "settings_get"})
        assert reply.status_code == 200, reply.text
        return _rows(reply.json()["result"])


def test_the_settings_tab_shows_the_launch_value_its_source_and_where_to_change_it():
    _save()
    rows = _studio_rows(_app({"max_tokens": LAUNCH}))
    row = rows["overrides.output_tokens"]
    assert row["display"] == "16,384" and row["value"] == LAUNCH and row["origin"] == "session"
    assert row["source_text"] == ("From this model's load screen (max tokens; Max output on its card); the saved 60,000 "
                                  "applies only where no launch sets it (another provider, or an engine that was "
                                  "already running)")
    assert row["edit"] is None and row["locked"] == (
        "Set by this model's launch: change max tokens on its load screen and load it again, or use /maxtokens in "
        "this session. Runtime → Next session changes only the saved value.")
    ceiling = rows["output.ceiling"]                # it showed Dream's own 131,072, "in effect since Dream started"
    assert ceiling["value"] == LAUNCH and ceiling["display"] == "16,384" and ceiling["origin"] == "session"
    assert ceiling["source_text"] == "From this model's load screen (max tokens; Max output on its card)"


def test_the_settings_tab_without_a_launch_max_tokens_is_as_before():
    _save()
    rows = _studio_rows(_app({}))
    row = rows["overrides.output_tokens"]
    assert row["display"] == "60,000" and row["origin"] == "global"
    assert row["source_text"] == "Saved in runtime-settings.json"
    assert row["locked"] == "Change it in Runtime → Next session."
    ceiling = rows["output.ceiling"]
    assert ceiling["value"] == SAVED and ceiling["source_text"] == "Saved in runtime-settings.json (profile output_tokens)"


def test_the_settings_tab_with_maxtokens_over_a_launch_value(monkeypatch):
    _save()
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 40_000)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", 40_000)
    rows = _studio_rows(_app({"max_tokens": LAUNCH}))
    row = rows["overrides.output_tokens"]
    assert row["display"] == "40,000" and row["origin"] == "session"
    assert row["source_text"].startswith("Set in this session (/maxtokens, in place of the load screen's 16,384); the saved")
    assert row["locked"].startswith("Set by this model's launch: ")
    assert rows["output.ceiling"]["value"] == 40_000


# --- the load screen's field ---------------------------------------------------------------------------------

def test_the_load_screens_max_tokens_field_says_what_it_caps():
    hint = local_settings.BY_NAME["max_tokens"].hint
    assert hint == ("the most tokens one reply of the main agent may use, thinking included; bounded by context. "
                    "Sub-agents on engine lanes use their lane's room instead")
    [shown] = [c for c in local_settings.available_controls({"sampling": ["max_tokens"]}) if c.name == "max_tokens"]
    assert shown.hint == hint                                             # the screen's tooltip, the picker's "meaning"
    with pytest.raises(ValueError, match="^maximum output per generation; bounded by context$"):
        local_settings.parse_value(shown, "0")                            # a refused value is told what it was told
