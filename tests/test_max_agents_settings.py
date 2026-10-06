"""The owner's settings plan of 2026-10-01: "Max agents" (`nested.max_agents`) replaces "Agents per reply"
(`nested.max_workers`, DREAM-197) and "Parallel workers" (`overrides.max_parallel`); the global engine lanes
(`engine.parallel` / `engine.slot_ctx`, DREAM-151) leave Settings for the load screen, per model.

- `nested.max_agents`: how many sub-agents run at the same time, for every provider; empty or absent = no limit (the
  default). The global file only. The settings route takes an integer of 1 or more (or its digits), clears it on an
  empty value or null, and refuses anything else with a sentence that names the key.
- The owner's file of today (`nested.max_workers: 3`, `overrides.max_parallel: 1`, `engine.parallel: 5`) loads without
  an error or a warning, and none of the three caps anything; the next save through the settings writer drops the
  retired nested and engine keys.
- Settings shows one short Agents section and no lanes, workers or Parallel workers rows.
No engine, no model, no GPU: the isolated settings file and a StudioServer on the test client.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from dream import config
from dream.core import moe, settings
from dream.core.profiles import resolve_profile, read_settings, settings_path
from dream.core.providers import get_provider
from dream.gui import settings_panel
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.local import machx
from dream.tui.app import App

KEY = "nested.max_agents"
OWNERS_FILE = {"version": 1, "profile": "auto", "nested": {"max_workers": 3}, "overrides": {"max_parallel": 1},
               "engine": {"parallel": 5}, "behaviour": {"vitals": True}}
REFUSED = "nested.max_agents: enter a whole number of 1 or more, or leave it empty for no limit."


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    for name in ("DREAM_PROFILE", "DREAM_MAX_PARALLEL", "DREAM_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(moe, "CONFIG_PATH", tmp_path / "var" / "moe.json")
    monkeypatch.setattr(machx, "BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)
    settings.set_workspace(None)
    yield
    settings.set_workspace(None)


def _write(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


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
        yield call


def _rows(view):
    return {row["key"]: row for section in view["sections"] for row in section["rows"]}


# --- the setting ------------------------------------------------------------------------------------------------

def test_unset_means_no_limit():
    assert settings.max_agents() is None
    assert settings.effective()[KEY] == settings.Effective(None, "default")
    assert KEY in settings.SETTABLE


@pytest.mark.parametrize("text, value", [("1", 1), ("3", 3), ("16", 16), (" 250 ", 250)])
def test_a_whole_number_of_one_or_more_is_saved(text, value):
    note = settings.set_value(KEY, text)
    assert "next reply" in note
    assert read_settings()["nested"] == {"max_agents": value}
    assert settings.max_agents() == value


@pytest.mark.parametrize("text", ["0", "-1", "two", "1.5", "", "3 agents"])
def test_anything_else_is_refused_and_writes_nothing(text):
    path = _write({"version": 1, "nested": {"max_agents": 4}})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="nested.max_agents"):
        settings.set_value(KEY, text)
    assert path.read_bytes() == before


def test_a_project_file_may_not_set_it(tmp_path):
    settings.set_workspace(tmp_path)
    with pytest.raises(ValueError, match="set on this computer"):
        settings.set_value(KEY, "3", scope="project")
    assert not (tmp_path / ".dream").exists()


def test_unset_clears_it():
    _write({"version": 1, "nested": {"max_agents": 4}})
    assert "removed" in settings.unset_value(KEY)
    assert "nested" not in read_settings() and settings.max_agents() is None


# --- the owner's file of today ------------------------------------------------------------------------------------

def test_the_owners_file_loads_without_errors_or_warnings_and_caps_nothing():
    _write(OWNERS_FILE)
    assert settings.read()["nested"] == {"max_workers": 3}                 # accepted as it is
    assert settings.max_agents() is None                                   # max_workers 3 caps nothing
    report = settings.check()
    assert report["ok"] and report["errors"] == [] and report["warnings"] == [] and report["unknown_sections"] == []
    rows = settings.effective()
    assert "nested.max_workers" not in rows and "engine.parallel" not in rows and "engine.slot_ctx" not in rows
    assert "overrides.max_parallel" not in rows
    assert rows[KEY] == settings.Effective(None, "default")


@pytest.mark.parametrize("key", ["openai", "xai", "machx", "anthropic", "codex"])
def test_a_saved_max_parallel_no_longer_reaches_the_profile(key):
    _write(OWNERS_FILE)
    saved = resolve_profile(get_provider(key))
    _write({"version": 1})
    assert saved.max_parallel == resolve_profile(get_provider(key)).max_parallel
    _write({"version": 1, "models": {f"{key}:m": {"max_parallel": 1, "output_tokens": 2048}}})
    profile = resolve_profile(get_provider(key), model="m")
    assert profile.output_tokens == 2048                                   # the model's other overrides still apply
    _write({"version": 1})
    assert profile.max_parallel == resolve_profile(get_provider(key), model="m").max_parallel


@pytest.mark.parametrize("retired", [{"nested": {"max_workers": 99}}, {"engine": {"parallel": 40, "lanes": 2}},
                                     {"engine": "five"}])
def test_retired_keys_are_accepted_whatever_they_hold(retired):
    _write({"version": 1, **retired})
    settings.read()
    assert settings.max_agents() is None


def test_the_next_save_drops_the_retired_nested_and_engine_keys():
    _write(OWNERS_FILE)
    settings.set_value("behaviour.vitals", "off")
    saved = read_settings()
    assert "engine" not in saved and "nested" not in saved
    assert saved["overrides"] == {"max_parallel": 1}                       # the profile's writer drops that one
    assert saved["behaviour"] == {"vitals": False} and saved["profile"] == "auto"
    _write(OWNERS_FILE)
    settings.set_value(KEY, "4")
    assert read_settings()["nested"] == {"max_agents": 4} and "engine" not in read_settings()


def test_the_retired_keys_are_not_settable():
    for key in ("nested.max_workers", "engine.parallel", "engine.slot_ctx"):
        with pytest.raises(ValueError):
            settings.set_value(key, "3")
    assert not settings_path().exists()


# --- the Settings tab and the route the Nested view uses -------------------------------------------------------------

def test_the_tab_has_one_short_agents_section_and_no_lanes_workers_or_parallel_workers(studio):
    _write(OWNERS_FILE)
    view = studio({"action": "settings_get"})
    ids = [section["id"] for section in view["sections"]]
    assert "agents" in ids and "lanes" not in ids and "workers" not in ids
    [agents] = [section for section in view["sections"] if section["id"] == "agents"]
    assert [row["key"] for row in agents["rows"]] == [KEY]
    row = agents["rows"][0]
    assert row["label"] == "Max agents" and row["value"] is None and row["display"] == "No limit"
    assert row["edit"] == {"kind": "number", "min": 1, "max": settings.MAX_AGENTS, "value": None}
    # Item 6(c): two short lines at most -- the section's line and the row's
    for text in (agents["intro"], row["help"]):
        assert text.count(".") <= 2 and len(text) <= 120, text
    rows = _rows(view)
    assert not {"engine.parallel", "engine.slot_ctx", "nested.max_workers", "overrides.max_parallel"} & set(rows)
    assert view["warnings"] == []
    text = json.dumps(view)
    assert "Parallel workers" not in text and "Workers per reply" not in text and "Engine lanes" not in text


@pytest.mark.parametrize("value, saved", [(3, 3), ("12", 12), (" 7 ", 7), (1, 1)])
def test_the_route_saves_a_number(studio, value, saved):
    result = studio({"action": "settings_save", "key": KEY, "value": value})
    assert read_settings()["nested"] == {"max_agents": saved}
    row = _rows(result["settings"])[KEY]
    assert row["value"] == saved and row["edit"]["value"] == saved and row["origin"] == "global"
    assert "next reply" in result["applies"]


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_the_route_clears_it_on_an_empty_value(studio, empty):
    _write({"version": 1, "nested": {"max_agents": 5}})
    result = studio({"action": "settings_save", "key": KEY, "value": empty})
    assert "nested" not in read_settings()
    assert _rows(result["settings"])[KEY]["display"] == "No limit"


@pytest.mark.parametrize("bad", [0, -2, "0", "two", 2.5, "2.5", True, [3], {"n": 3}, "1e3"])
def test_the_route_refuses_a_bad_value_with_a_clear_message(studio, bad):
    path = _write({"version": 1, "nested": {"max_agents": 5}})
    before = path.read_bytes()
    assert studio({"action": "settings_save", "key": KEY, "value": bad}, status=400) == REFUSED
    assert path.read_bytes() == before


def test_the_route_refuses_the_project_scope(studio, tmp_path):
    error = studio({"action": "settings_save", "key": KEY, "value": 3, "scope": "project"}, status=400)
    assert error.startswith(KEY) and "this computer" in error
    assert not settings_path().exists()


async def test_the_running_session_takes_a_saved_limit_from_its_next_reply(tmp_path):
    taken = []
    backend = SimpleNamespace(apply_worker_cap=lambda: taken.append(settings.max_agents()))
    app = App(provider="machx", workspace=tmp_path)
    app.engine = SimpleNamespace(provider=get_provider("machx"), model="fixture-model", backend=backend)
    await app._runtime_control({"action": "settings_save", "key": KEY, "value": 2})
    await app._runtime_control({"action": "settings_save", "key": KEY, "value": ""})
    assert taken == [2, None]
