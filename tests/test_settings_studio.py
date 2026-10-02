"""DREAM-143 S2 (settings design P3, sections 3.2, 3.4, 3.5): the Settings tab of Studio's Controls dialog and its
two actions, `settings_get` and `settings_save`, on the authenticated /api/control route beside `profile`.

Every row the resolver (dream/core/settings.py effective()) reports appears once with a plain label, a one-line
explanation, its value and its source; only settings `set_value`/`unset_value` can change are editable, a row the
environment sets is read-only, a refused value names its key and writes nothing, and no secret reaches the page.
No Engine, provider request, model or GPU: an App without a session and a real StudioServer on a free port.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright, expect
from starlette.testclient import TestClient

from dream import config
from dream.core import moe, settings
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import PROVIDERS
from dream.environment import NUMERIC_SETTINGS
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.local import machx
from dream.tui.app import App

ORIGINS = {"session", "env", "global", "default", "not-applied"}
EDITABLE = {"output.max_tokens", "behaviour.vitals", "roles.subagents.default", "roles.verifier", "roles.filer",
            "roles.critic", "roles.evaluator", "roles.reviewer",      # the last three: S4 (DREAM-145)
            "engine.parallel", "engine.slot_ctx",                     # the engine lanes (DREAM-151)
            "behaviour.context_overflow", "behaviour.subagent_overflow",  # context management (DREAM-175)
            "behaviour.context_trigger", "behaviour.context_warning",
            "behaviour.response_style",                                   # response styles (DREAM-181)
            "nested.max_workers"}                                         # the worker cap (DREAM-197)


@pytest.fixture(autouse=True)
def _clean_env(tmp_path, monkeypatch):
    for name in ("DREAM_PROFILE", "DREAM_MAX_TOKENS", "DREAM_CONTEXT_WINDOW", "DREAM_MAX_PARALLEL",
                 "DREAM_SUBAGENT_TIMEOUT_S", "DREAM_IDLE_TIMEOUT_S", "DREAM_RUN_TOKEN_BUDGET", "DREAM_RUN_TOOL_BUDGET",
                 "DREAM_RUN_SECONDS", "DREAM_WALL_SECONDS", "DREAM_VISION", "DREAM_VISION_HELPER", "DREAM_BLENDER",
                 "DREAM_AUTO_VERIFY", "DREAM_SINGLE_SLOT_VERIFIER", "OPENAI_API_KEY", "XAI_API_KEY",
                 "DREAM_DESKTOP_SESSION_FILE", "DREAM_EVALUATOR_PROVIDER", "DREAM_EVALUATOR_MODEL", *NUMERIC_SETTINGS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", None, raising=False)
    # The tab reads the saved Council (DREAM-145): never the owner's var/moe.json from a test.
    monkeypatch.setattr(moe, "CONFIG_PATH", tmp_path / "var" / "moe.json")
    # The engine layout section reads the local endpoint (DREAM-144): never the owner's engine port from a test.
    monkeypatch.setattr(machx, "BASE_URL", "http://127.0.0.1:1/v1")


def _write(data):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _file_bytes():
    path = settings_path()
    return path.read_bytes() if path.exists() else None


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


# --- auth, shape ------------------------------------------------------------------------------------------

def test_settings_actions_need_the_studio_token_and_this_origin(studio):
    app, server, client, call = studio
    for action in ("settings_get", "settings_save"):
        assert client.post("/api/control", json={"action": action}).status_code == 401
        assert client.post("/api/control", headers={"x-dream-token": "wrong"}, json={"action": action}).status_code == 401
        crossed = client.post("/api/control", headers={"x-dream-token": server.token, "origin": "https://unrelated.example"},
                              json={"action": "settings_save", "key": "behaviour.vitals", "value": False})
        assert crossed.status_code == 403
    assert _file_bytes() is None
    view = call({"action": "settings_get"})
    assert view["ok"] is True and view["provider"] == "machx" and view["file"] == str(settings_path())


def test_every_effective_row_appears_once_with_label_help_value_and_source(studio):
    _, _, _, call = studio
    view = call({"action": "settings_get"})
    keys = [row["key"] for section in view["sections"] for row in section["rows"]]
    assert len(keys) == len(set(keys))
    session = {"roles.main": {"provider": "machx", "model": None}}
    for key in settings.effective(provider="machx", model=None, session=session):
        assert key in keys, key
    titles = [section["title"] for section in view["sections"]]
    for title in ("Models & providers", "Roles", "Local engine layout", "Permissions & sandbox", "Media",
                  "Output & reasoning", "Vitals", "Memory", "Privacy"):
        assert title in titles, title
    for section in view["sections"]:
        assert section["id"] and section["intro"]
        for row in section["rows"]:
            assert row["label"] and row["help"] and row["label"] != row["key"], row
            assert isinstance(row["display"], str) and row["display"], row
            assert row["origin"] in ORIGINS and row["source"], row
            assert isinstance(row["restart"], bool) and row["applies"], row
            assert (row["edit"] is None) == bool(row["locked"]), row
    rows = _rows(view)
    assert rows["output.max_tokens"]["origin"] == "default" and rows["output.max_tokens"]["value"] == 131072
    assert rows["behaviour.vitals"]["display"] == "On" and rows["behaviour.vitals"]["restart"] is False
    assert rows["output.max_tokens"]["restart"] is True
    # The layout section renders dream.core.engine_layout.layout_view() read-only (DREAM-144): here the endpoint
    # does not answer, and the view says so instead of offering a control that does nothing.
    engine = next(s for s in view["sections"] if s["title"] == "Local engine layout")
    assert engine["intro"].startswith("Which model runs on which card, read from the engine layout")
    engine_rows = {row["key"]: row for row in engine["rows"]}
    assert {"engine.endpoint", "engine.reachable", "engine.servers", "engine.main", "engine.notes"} <= set(engine_rows)
    assert engine_rows["engine.reachable"]["display"] == "Off" and engine_rows["engine.servers"]["display"] == "[]"
    assert all(row["edit"] is None and row["locked"] for row in engine["rows"])


def test_only_settings_the_writer_accepts_are_editable(studio):
    _, _, _, call = studio
    _write({"version": 1, "roles": {"subagents": {"researcher": {"model": "fixture-small"}}}})
    rows = _rows(call({"action": "settings_get"}))
    editable = {key for key, row in rows.items() if row["edit"] is not None}
    assert editable == EDITABLE | {"roles.subagents.researcher"}
    assert rows["behaviour.vitals"]["edit"] == {"kind": "switch"}
    assert rows["output.max_tokens"]["edit"]["kind"] == "number"
    assert rows["output.max_tokens"]["edit"]["min"] == settings.MIN_MAX_TOKENS
    role = rows["roles.verifier"]["edit"]
    assert role["kind"] == "role" and role["model"] == "" and role["provider"] == ""
    assert set(role["providers"]) == {key for key, p in PROVIDERS.items() if p.kind == "openai"}
    assert rows["roles.subagents.researcher"]["edit"]["model"] == "fixture-small"
    assert rows["roles.subagents.researcher"]["edit"]["reset"] == "Use sub-agents default"
    assert role["reset"] == "Use main model" and role["removable"] is False
    assert rows["roles.main"]["edit"] is None and rows["profile"]["edit"] is None
    view = call({"action": "settings_get"})
    assert "researcher" in view["agents"] and not {"verifier", "filer"} & set(view["agents"])


# --- round trip ---------------------------------------------------------------------------------------------

CHANGES = [
    ("output.max_tokens", 40000, lambda s: s["output"]["max_tokens"] == 40000, "40,000"),
    ("behaviour.vitals", False, lambda s: s["behaviour"]["vitals"] is False, "Off"),
    ("roles.subagents.default", {"model": "fixture-small"},
     lambda s: s["roles"]["subagents"]["default"] == {"model": "fixture-small"}, "fixture-small"),
    ("roles.subagents.researcher", {"model": "fixture-small", "provider": "machx"},
     lambda s: s["roles"]["subagents"]["researcher"] == {"model": "fixture-small", "provider": "machx"},
     "fixture-small"),
    ("roles.verifier", {"model": "fixture-check", "provider": ""},
     lambda s: s["roles"]["verifier"] == {"model": "fixture-check"}, "fixture-check"),
    ("roles.filer", {"model": "fixture-notes", "provider": "machx"},
     lambda s: s["roles"]["filer"] == {"model": "fixture-notes", "provider": "machx"}, "fixture-notes"),
]


def test_every_editable_control_round_trips_through_the_file(studio):
    _, _, _, call = studio
    _write({"version": 1, "profile": "lean", "keep": {"unknown": "section"}})
    for key, value, stored, shown in CHANGES:
        result = call({"action": "settings_save", "key": key, "value": value})
        assert result["key"] == key and result["applies"], result
        assert stored(read_settings()), (key, read_settings())
        row = _rows(result["settings"])[key]
        assert row["origin"] == "global" and shown in row["display"], row
        assert _rows(call({"action": "settings_get"}))[key]["display"] == row["display"]
    saved = read_settings()
    assert saved["profile"] == "lean" and saved["keep"] == {"unknown": "section"}
    # A role's provider can be cleared again; the model stays.
    call({"action": "settings_save", "key": "roles.filer", "value": {"model": "fixture-notes", "provider": ""}})
    assert read_settings()["roles"]["filer"] == {"model": "fixture-notes"}
    # Removing each one (value null) returns it to its default and empties its section.
    for key, *_ in CHANGES:
        result = call({"action": "settings_save", "key": key, "value": None})
        assert _rows(result["settings"])[key]["origin"] == "default" if key in EDITABLE else \
            key not in _rows(result["settings"])
    saved = read_settings()
    assert not {"output", "behaviour", "roles"} & set(saved) and saved["keep"] == {"unknown": "section"}


def test_a_vitals_change_reaches_the_running_backend(studio):
    app, _, _, call = studio
    backend = SimpleNamespace(vitals=True)
    app.engine = SimpleNamespace(provider=SimpleNamespace(key="machx"), model="fixture-model", backend=backend)
    call({"action": "settings_save", "key": "behaviour.vitals", "value": False})
    assert backend.vitals is False
    view = call({"action": "settings_get"})
    assert view["model"] == "fixture-model"
    assert _rows(view)["roles.main"]["origin"] == "session" and "fixture-model" in _rows(view)["roles.main"]["display"]
    call({"action": "settings_save", "key": "behaviour.vitals", "value": None})
    assert backend.vitals is True


def test_a_session_value_is_shown_as_the_session_s(studio, monkeypatch):
    _, _, _, call = studio
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", 20000)
    row = _rows(call({"action": "settings_get"}))["output.max_tokens"]
    assert row["origin"] == "session" and row["value"] == 20000
    assert row["edit"] is not None and "restart dream" in row["applies"].lower()


# --- read-only rows, validation, masking ---------------------------------------------------------------------

def test_environment_rows_are_read_only_and_a_save_is_refused(studio, monkeypatch):
    _, _, _, call = studio
    monkeypatch.setenv("DREAM_MAX_TOKENS", "9000")
    monkeypatch.setenv("DREAM_SUBAGENT_ROUNDS", "40")
    _write({"version": 1, "output": {"max_tokens": 50000}})
    before = _file_bytes()
    rows = _rows(call({"action": "settings_get"}))
    for key, name in (("output.max_tokens", "DREAM_MAX_TOKENS"), ("numeric.subagent_rounds", "DREAM_SUBAGENT_ROUNDS")):
        row = rows[key]
        assert row["origin"] == "env" and row["edit"] is None and name in row["locked"], row
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": 30000}, status=400)
    assert "output.max_tokens" in error and "DREAM_MAX_TOKENS" in error
    assert _file_bytes() == before


@pytest.mark.parametrize("key, value, named", [
    ("output.max_tokens", 12, "output.max_tokens"),
    ("output.max_tokens", "lots", "output.max_tokens"),
    ("output.max_tokens", 4096.5, "output.max_tokens"),
    ("output.max_tokens", True, "output.max_tokens"),
    ("behaviour.vitals", "maybe", "behaviour.vitals"),
    ("roles.verifier", {"model": ""}, "roles.verifier"),
    ("roles.verifier", {"model": " padded "}, "roles.verifier"),
    ("roles.verifier", {"model": "fixture-check", "provider": "anthropic"}, "roles.verifier.provider"),
    ("roles.filer", {"model": "fixture-notes", "provider": "nowhere"}, "roles.filer.provider"),
    ("roles.filer", {"model": "fixture-notes", "extra": 1}, "roles.filer"),
    ("roles.filer", "fixture-notes", "roles.filer"),
    ("roles.subagents.Bad Name", {"model": "fixture-small"}, "roles.subagents.Bad Name"),
    ("roles.critic", {"model": "fixture-small"}, "roles.critic"),
    ("profile", "lean", "profile"),
    ("numeric.subagent_rounds", 40, "numeric.subagent_rounds"),
    (None, 1, "key"),
])
def test_a_refused_value_names_its_key_and_writes_nothing(studio, key, value, named):
    _, _, _, call = studio
    _write({"version": 1, "roles": {"verifier": {"model": "fixture-check"}}, "output": {"max_tokens": 50000}})
    before = _file_bytes()
    error = call({"action": "settings_save", "key": key, "value": value}, status=400)
    assert named in error, error
    assert _file_bytes() == before


def test_an_unusable_file_is_reported_and_never_rewritten(studio):
    _, _, _, call = studio
    path = _write({"version": 1, "output": {"max_tokens": 3}})
    before = path.read_bytes()
    view = call({"action": "settings_get"})
    assert view["ok"] is False and "output.max_tokens" in view["error"] and view["sections"] == []
    error = call({"action": "settings_save", "key": "behaviour.vitals", "value": False}, status=400)
    assert "repair" in error
    assert path.read_bytes() == before


def test_secrets_never_reach_the_page(studio, monkeypatch):
    _, server, client, call = studio
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fixture-value-0001")
    monkeypatch.setattr(PROVIDERS["xai"], "base_url", "https://fixture-user:fixture-pass-0002@api.example/v1")
    _write({"version": 1, "roles": {"summarizer": {"model": "fixture-small", "api_key": "fixture-value-0003",
                                                     "token": "fixture-value-0004"}}})
    reply = client.post("/api/control", headers={"x-dream-token": server.token}, json={"action": "settings_get"})
    text = reply.text
    for secret in ("sk-fixture-value-0001", "fixture-pass-0002", "fixture-user", "fixture-value-0003",
                   "fixture-value-0004", server.token):
        assert secret not in text, secret
    providers = {row["id"]: row for row in reply.json()["result"]["providers"]}
    assert providers["openai"]["credential"] == "set in OPENAI_API_KEY (hidden)"
    assert providers["xai"]["credential"] == "not set (XAI_API_KEY)"
    assert providers["xai"]["endpoint"] == "https://api.example/v1"
    assert providers["machx"]["credential"] == "not needed"
    rows = _rows(reply.json()["result"])
    assert rows["roles.summarizer"]["origin"] == "not-applied" and rows["roles.summarizer"]["edit"] is None


def test_roles_the_session_does_not_apply_say_so(studio):
    app, _, _, call = studio
    app.provider = "openai"
    _write({"version": 1, "roles": {"verifier": {"model": "fixture-check"}}})
    view = call({"action": "settings_get"})
    row = _rows(view)["roles.verifier"]
    assert row["origin"] == "not-applied" and "machx only" in row["source"]
    assert row["edit"] is not None       # still editable: the file is what it changes
    assert any("roles.verifier" in warning for warning in view["warnings"])


# --- the tab in a browser -------------------------------------------------------------------------------------

@pytest.fixture
async def settings_page(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAM_SUBAGENT_ROUNDS", "40")
    _write({"version": 1, "output": {"max_tokens": 50000}})
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


async def _no_overflow(page):
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert await page.locator(".dc-scroll").evaluate("e => e.scrollWidth <= e.clientWidth")


async def test_settings_tab_renders_sources_edits_round_trip_and_errors_are_inline(settings_page, tmp_path):
    page, url, errors = settings_page
    await page.goto(url)
    await expect(page.locator("#stat")).to_have_text("Ready")
    await page.locator("#dream-controls-open").click()
    await page.get_by_role("tab", name="Settings", exact=True).click()
    panel = page.locator("#dc-settings")
    await expect(panel.get_by_role("heading", name="Output & reasoning")).to_be_visible()
    for title in ("Models & providers", "Roles", "Local engine layout", "Permissions & sandbox", "Vitals", "Privacy"):
        await expect(panel.get_by_role("heading", name=title, exact=True)).to_be_visible()

    # Saved value, its source badge, and the restart badge.
    row = panel.locator('[data-key="output.max_tokens"]')
    await expect(row.locator(".dc-source")).to_have_text("Saved")
    await expect(row.locator(".dc-restart")).to_be_visible()
    await expect(row.get_by_label("Max output tokens")).to_have_value("50000")
    # An environment row: read-only with the variable named.
    env_row = panel.locator('[data-key="numeric.subagent_rounds"]')
    await expect(env_row.locator(".dc-source")).to_have_text("Environment")
    await expect(env_row).to_contain_text("DREAM_SUBAGENT_ROUNDS")
    assert await env_row.locator("input,select,button").count() == 0

    # An edit round-trips through the file.
    await row.get_by_label("Max output tokens").fill("60000")
    await row.get_by_role("button", name="Save").click()
    await expect(page.locator("#dc-feedback")).to_contain_text("output.max_tokens")
    assert read_settings()["output"]["max_tokens"] == 60000
    row = panel.locator('[data-key="output.max_tokens"]')
    await expect(row.get_by_label("Max output tokens")).to_have_value("60000")

    # An invalid value: the error sits under the row, names the key, and nothing is written.
    await row.get_by_label("Max output tokens").fill("12")
    await row.get_by_role("button", name="Save").click()
    error = row.locator(".dc-row-error")
    await expect(error).to_contain_text("output.max_tokens")
    assert await error.get_attribute("role") == "alert"
    await expect(row.get_by_label("Max output tokens")).to_have_attribute("aria-invalid", "true")
    await expect(row.get_by_label("Max output tokens")).to_be_focused()
    assert read_settings()["output"]["max_tokens"] == 60000

    # The vitals switch and a role save through the same path.
    vitals = panel.locator('[data-key="behaviour.vitals"]').get_by_role("switch")
    await expect(vitals).to_have_attribute("aria-checked", "true")
    await vitals.click()
    await expect(panel.locator('[data-key="behaviour.vitals"]').get_by_role("switch")).to_have_attribute("aria-checked", "false")
    assert read_settings()["behaviour"]["vitals"] is False
    verifier = panel.locator('[data-key="roles.verifier"]')
    await verifier.get_by_label("Verifier model").fill("fixture-check")
    await verifier.get_by_role("button", name="Save").click()
    await expect(panel.locator('[data-key="roles.verifier"] .dc-source')).to_have_text("Saved")
    assert read_settings()["roles"]["verifier"] == {"model": "fixture-check"}
    await panel.locator('[data-key="roles.verifier"]').get_by_role("button", name="Use main model").click()
    await expect(panel.locator('[data-key="roles.verifier"] .dc-source')).to_have_text("Default")
    assert "roles" not in read_settings()
    # One sub-agent type gets its own row.
    await panel.get_by_label("Sub-agent type").select_option("researcher")
    await panel.get_by_label("Sub-agent model").fill("fixture-small")
    await panel.get_by_role("button", name="Add", exact=True).click()
    await expect(panel.locator('[data-key="roles.subagents.researcher"] .dc-source')).to_have_text("Saved")
    assert read_settings()["roles"]["subagents"]["researcher"] == {"model": "fixture-small"}

    await _no_overflow(page)
    await page.screenshot(path=str(tmp_path / "settings-tab-800.png"))
    await page.set_viewport_size({"width": 480, "height": 800})
    await _no_overflow(page)
    # Keyboard: the tab strip reaches Settings with End and leaves it with ArrowLeft.
    await page.get_by_role("tab", name="Runtime", exact=True).focus()
    await page.keyboard.press("End")
    await expect(page.get_by_role("tab", name="Settings", exact=True)).to_be_focused()
    await page.keyboard.press("ArrowLeft")
    await expect(page.get_by_role("tab", name="Learn", exact=True)).to_be_focused()
    assert errors == []


# --- gate round 1 findings ---------------------------------------------------------------------------------------

def test_secrets_are_masked_by_key_and_urls_lose_their_query(studio, monkeypatch):
    _, server, client, call = studio
    monkeypatch.setattr(PROVIDERS["openai"], "base_url", "https://api.example/v1?api_key=fixture-value-0011#frag")
    _write({"version": 1, "roles": {"api_key": "fixture-value-0012",
                                     "summarizer": {"model": "fixture-small", "client_secret": "fixture-value-0013",
                                                    "endpoint": "https://svc.example/v1?sig=fixture-value-0014"}}})
    reply = client.post("/api/control", headers={"x-dream-token": server.token}, json={"action": "settings_get"})
    for secret in ("fixture-value-0011", "fixture-value-0012", "fixture-value-0013", "fixture-value-0014", "frag"):
        assert secret not in reply.text, secret
    view = reply.json()["result"]
    rows = _rows(view)
    assert rows["roles.api_key"]["display"] == "hidden" and rows["roles.api_key"]["value"] == "hidden"
    assert {row["id"]: row for row in view["providers"]}["openai"]["endpoint"] == "https://api.example/v1"
    # A key that only looks like a secret word inside it is not masked.
    assert rows["output.max_tokens"]["display"] == "131,072"


def test_an_unusable_file_error_does_not_echo_its_values(studio):
    _, _, _, call = studio
    _write({"version": 1, "roles": {"verifier": {"model": "fixture-model", "provider": "fixture-value-0021"}}})
    view = call({"action": "settings_get"})
    assert view["ok"] is False and "roles.verifier" in view["error"]
    assert "fixture-value-0021" not in json.dumps(view)
    error = call({"action": "settings_save", "key": "behaviour.vitals", "value": False}, status=400)
    assert "fixture-value-0021" not in error and "repair" in error


@pytest.mark.parametrize("value", ["٤٠٠٠٠", "４００００", "40000١"])
def test_non_ascii_digits_are_refused(studio, value):
    _, _, _, call = studio
    error = call({"action": "settings_save", "key": "output.max_tokens", "value": value}, status=400)
    assert "output.max_tokens" in error and "'" not in error
    assert _file_bytes() is None


@pytest.mark.parametrize("key, value", [
    ("output.max_tokens", "lots"), ("roles.subagents.Bad Name", {"model": "x"}),
    ("roles.verifier", {"model": "fixture-check", "provider": "nowhere"}), ("roles.filer", {"model": 7}),
])
def test_inline_errors_are_plain_words(studio, key, value):
    _, _, _, call = studio
    error = call({"action": "settings_save", "key": key, "value": value}, status=400)
    assert key in error and "'" not in error and "Traceback" not in error, error


def test_saved_max_tokens_needs_a_restart_and_the_ceiling_shows_the_running_value(studio, monkeypatch):
    _, _, _, call = studio
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS", 30000)
    _write({"version": 1, "overrides": {"output_tokens": 200000}, "output": {"max_tokens": 50000}})
    rows = _rows(call({"action": "settings_get"}))
    assert rows["output.max_tokens"]["restart_label"] == "Restart Dream"
    assert rows["output.max_tokens"]["applies"].startswith("When Dream starts")
    assert rows["output.max_tokens"]["value"] == 50000                   # what is saved
    assert rows["output.ceiling"]["value"] == 30000                      # what this running Dream uses
    result = call({"action": "settings_save", "key": "output.max_tokens", "value": 60000})
    assert "restart" in result["applies"].lower()
    assert _rows(result["settings"])["output.ceiling"]["value"] == 30000
    # A /maxtokens value is what runs.
    monkeypatch.setattr(config, "MAX_OUTPUT_TOKENS_OVERRIDE", 20000)
    assert _rows(call({"action": "settings_get"}))["output.ceiling"]["value"] == 20000


def test_warnings_are_plain_words(studio):
    app, _, _, call = studio
    app.provider = "openai"
    _write({"version": 1, "roles": {"verifier": {"model": "fixture-check"}, "summarizer": {"model": "fixture-small"}}})
    warnings = call({"action": "settings_get"})["warnings"]
    assert warnings and not any(word in " ".join(warnings) for word in ("#71", "/v1/models", "backend", "machx only"))
    assert any("Verifier" in w and "MachX" in w for w in warnings)
    assert any("roles.summarizer" in w for w in warnings)


def test_the_main_model_row_matches_model_command(studio):
    _, _, _, call = studio
    row = _rows(call({"action": "settings_get"}))["roles.main"]
    assert row["restart"] is False and "/model" in row["applies"] and "/model" in row["locked"]
