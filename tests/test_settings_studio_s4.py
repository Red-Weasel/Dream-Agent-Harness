"""DREAM-145 S4 in Studio's Settings tab: rows for the critic, the loop evaluator and /review roles; the Council's
advisor models and efforts read from and written to var/moe.json through moe.save_config; the read-only "Local engine
layout" section behind one adapter (dream.core.engine_layout.layout_view when that module exists, "not available"
otherwise). Same fixtures as test_settings_studio: an App without a session and a real StudioServer on a free port;
the browser check runs headless Chromium. The Council file is always a temporary one here.
"""
from __future__ import annotations

import json
import sys
import types
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from playwright.async_api import expect

from dream.core import council_config, moe, settings
from dream.core.profiles import read_settings, settings_path
from dream.core.providers import PROVIDERS
from dream.gui import settings_panel
from dream.local import machx
from test_settings_studio import _clean_env, _no_overflow, _rows, _write, settings_page, studio  # noqa: F401

CRITICS = {"anthropic", "codex", "gemini", "grok"}


@pytest.fixture(autouse=True)
def council_file(tmp_path, monkeypatch):
    """The Council file lives in this test's directory only; nothing here may touch var/moe.json."""
    path = tmp_path / "var" / "moe.json"
    monkeypatch.setattr(moe, "CONFIG_PATH", path)
    # The engine layout section reads the local endpoint (DREAM-144): never the owner's engine port from a test.
    monkeypatch.setattr(machx, "BASE_URL", "http://127.0.0.1:1/v1")
    return path


def _council(**changes):
    cfg = moe.MoeConfig("machx", ["codex", "gemini"], advisor_models={"codex": "gpt-6-astra"},
                        advisor_efforts={"codex": "high"}, orchestrator_effort=None)
    cfg = moe.MoeConfig(**{**asdict(cfg), **changes})
    moe.save_config(cfg)
    return cfg


# --- roles ---------------------------------------------------------------------------------------------------------

def test_the_new_role_rows_are_editable_with_their_own_providers(studio):
    _, _, _, call = studio
    view = call({"action": "settings_get"})
    rows = _rows(view)
    for key in ("roles.critic", "roles.evaluator", "roles.reviewer"):
        row = rows[key]
        assert row["edit"] is not None and row["edit"]["kind"] == "role", row
        assert row["label"] != key and row["help"], row
        assert row["origin"] == "default"
    critic = rows["roles.critic"]["edit"]
    assert set(critic["providers"]) == CRITICS and critic["provider_unset"] == "First available (default)"
    assert "claude" in rows["roles.critic"]["display"] or "first" in rows["roles.critic"]["display"].lower()
    evaluator = rows["roles.evaluator"]["edit"]
    assert set(evaluator["providers"]) == set(PROVIDERS) and evaluator["provider_unset"]
    assert set(rows["roles.reviewer"]["edit"]["providers"]) == set(PROVIDERS)
    subagent = rows["roles.subagents.default"]["edit"]
    assert set(subagent["providers"]) == {k for k, p in PROVIDERS.items() if p.kind == "openai"} | {"anthropic"}
    assert set(rows["roles.verifier"]["edit"]["providers"]) == {k for k, p in PROVIDERS.items() if p.kind == "openai"}
    assert "roles" == next(s["id"] for s in view["sections"] if any(r["key"] == "roles.critic" for r in s["rows"]))


# (key, what the tab sends, what the file holds, text in the row, the row's origin on this machx session)
CHANGES = [
    ("roles.critic", {"model": "", "provider": "codex"}, {"critic": {"provider": "codex"}}, "codex", "global"),
    ("roles.critic", {"model": "critic-model", "provider": "gemini"},
     {"critic": {"provider": "gemini", "model": "critic-model"}}, "critic-model", "global"),
    ("roles.evaluator", {"model": "judge-model", "provider": ""}, {"evaluator": {"model": "judge-model"}}, "judge-model",
     "global"),
    ("roles.evaluator", {"model": "", "provider": "anthropic"}, {"evaluator": {"provider": "anthropic"}}, "anthropic",
     "global"),
    ("roles.reviewer", {"model": "review-model", "provider": "codex"},
     {"reviewer": {"provider": "codex", "model": "review-model"}}, "review-model", "global"),
    # A sub-agent role for the Claude SDK: saved, and on this MachX session marked not applied (the S1 rule).
    ("roles.subagents.default", {"model": "haiku", "provider": "anthropic"},
     {"subagents": {"default": {"model": "haiku", "provider": "anthropic"}}}, "haiku", "not-applied"),
]


def test_the_new_roles_round_trip_through_the_file(studio):
    _, _, _, call = studio
    _write({"version": 1, "keep": {"unknown": "section"}})
    for key, value, stored, shown, origin in CHANGES:
        result = call({"action": "settings_save", "key": key, "value": value})
        assert result["key"] == key and result["applies"], result
        saved = read_settings()
        for name, entry in stored.items():
            assert saved["roles"][name] == entry, (key, saved)
        row = _rows(result["settings"])[key]
        assert row["origin"] == origin and shown in row["display"], row
        assert row["edit"]["model"] == value["model"] and row["edit"]["provider"] == value["provider"]
    assert read_settings()["keep"] == {"unknown": "section"}
    for key in ("roles.critic", "roles.evaluator", "roles.reviewer", "roles.subagents.default"):
        result = call({"action": "settings_save", "key": key, "value": None})
        assert _rows(result["settings"])[key]["origin"] == "default"
    assert "roles" not in read_settings()


@pytest.mark.parametrize("key, value, named", [
    ("roles.critic", {"model": "critic-model", "provider": ""}, "roles.critic.provider"),
    ("roles.critic", {"model": "", "provider": "machx"}, "roles.critic.provider"),
    ("roles.critic", {"model": "", "provider": "openai"}, "roles.critic.provider"),
    ("roles.evaluator", {"model": "", "provider": ""}, "roles.evaluator"),
    ("roles.reviewer", {"model": " padded ", "provider": ""}, "roles.reviewer.model"),
    ("roles.reviewer", {"model": "", "provider": "nowhere"}, "roles.reviewer.provider"),
    ("roles.verifier", {"model": "checker", "provider": "anthropic"}, "roles.verifier.provider"),
    ("roles.filer", {"model": "notes", "provider": "codex"}, "roles.filer.provider"),
    ("roles.critic", {"provider": "codex", "effort": "high"}, "roles.critic"),
])
def test_a_refused_role_value_names_its_key_and_writes_nothing(studio, key, value, named):
    _, _, _, call = studio
    path = _write({"version": 1, "roles": {"critic": {"provider": "grok"}}})
    before = path.read_bytes()
    error = call({"action": "settings_save", "key": key, "value": value}, status=400)
    assert named in error and "'" not in error and "Traceback" not in error, error
    assert path.read_bytes() == before


def test_an_untouched_critic_save_writes_nothing(studio):
    """Gate finding 3: the critic's select offers the default itself, so Save with nothing chosen names the key
    and writes no provider (before, the first option, anthropic, would have been saved)."""
    _, _, _, call = studio
    row = _rows(call({"action": "settings_get"}))["roles.critic"]
    assert row["edit"]["provider"] == "" and row["edit"]["provider_unset"] == "First available (default)"
    error = call({"action": "settings_save", "key": "roles.critic", "value": {"model": "", "provider": ""}}, status=400)
    assert "roles.critic.provider" in error and "First available" in error and "'" not in error
    assert not settings_path().exists()
    assert _rows(call({"action": "settings_get"}))["roles.critic"]["origin"] == "default"


def test_the_evaluator_row_is_read_only_when_its_variables_are_set(studio, monkeypatch):
    _, _, _, call = studio
    _write({"version": 1, "roles": {"evaluator": {"model": "judge-model"}}})
    monkeypatch.setenv("DREAM_EVALUATOR_PROVIDER", "openai")
    row = _rows(call({"action": "settings_get"}))["roles.evaluator"]
    assert row["origin"] == "env" and row["edit"] is None and "DREAM_EVALUATOR_PROVIDER" in row["locked"]
    assert row["value"] == {"provider": "openai", "model": "judge-model"}
    error = call({"action": "settings_save", "key": "roles.evaluator", "value": {"model": "other", "provider": ""}}, status=400)
    assert "DREAM_EVALUATOR_PROVIDER" in error
    assert read_settings()["roles"]["evaluator"] == {"model": "judge-model"}


def test_a_critic_whose_program_is_missing_is_a_warning(studio, monkeypatch):
    _, _, _, call = studio
    state = {"anthropic": True, "codex": False, "gemini": True, "grok": True, "machx": True, "openai": False, "xai": False}
    monkeypatch.setattr(council_config, "provider_choices",
                        lambda **kw: [{"key": k, "label": k, "available": v, "models": [], "efforts": []}
                                      for k, v in state.items()])
    _write({"version": 1, "roles": {"critic": {"provider": "codex"}}})
    view = call({"action": "settings_get"})
    assert any("roles.critic" in w and "codex" in w for w in view["warnings"]), view["warnings"]
    _write({"version": 1, "roles": {"critic": {"provider": "gemini"}}})
    assert not any("roles.critic" in w for w in call({"action": "settings_get"})["warnings"])


# --- the Council in the tab -----------------------------------------------------------------------------------------

def test_without_a_saved_council_the_section_says_so(studio, council_file):
    _, _, _, call = studio
    view = call({"action": "settings_get"})
    section = next(s for s in view["sections"] if s["id"] == "council")
    assert section["title"] == "Council" and section["rows"] == []
    assert "Council panel" in section["intro"] and "no" in section["intro"].lower()
    error = call({"action": "settings_save", "key": "council.advisor.codex", "value": {"model": "m", "effort": ""}}, status=400)
    assert "council.advisor.codex" in error and "Council panel" in error
    assert not council_file.exists()


def test_the_council_section_reads_and_edits_moe_json_through_save_config(studio, council_file, monkeypatch):
    _, _, _, call = studio
    cfg = _council()
    written = []
    real = moe.save_config
    monkeypatch.setattr(moe, "save_config", lambda new: (written.append(new), real(new)))
    view = call({"action": "settings_get"})
    section = next(s for s in view["sections"] if s["id"] == "council")
    rows = {row["key"]: row for row in section["rows"]}
    assert list(rows) == ["council.main", "council.advisor.codex", "council.advisor.gemini"]
    main = rows["council.main"]
    assert main["edit"] is None and "Council panel" in main["locked"] and "machx" in main["display"]
    codex = rows["council.advisor.codex"]
    assert codex["edit"]["kind"] == "council" and codex["edit"]["model"] == "gpt-6-astra" and codex["edit"]["effort"] == "high"
    assert codex["origin"] == "global" and "gpt-6-astra" in codex["display"] and "high" in codex["display"]
    assert "moe.json" in codex["source_text"]
    assert any(m["id"] == "gpt-6-astra" for m in codex["edit"]["models"]) and "high" in codex["edit"]["efforts"]
    gemini = rows["council.advisor.gemini"]
    assert gemini["origin"] == "default" and gemini["edit"]["model"] == "" and gemini["edit"]["effort"] == ""
    assert gemini["edit"]["removable"] is False and codex["edit"]["removable"] is True
    assert "gemini" in gemini["label"].lower() or "Gemini" in gemini["label"]

    result = call({"action": "settings_save", "key": "council.advisor.gemini",
                   "value": {"model": "gemini-3.1-pro-preview", "effort": ""}})
    assert result["key"] == "council.advisor.gemini" and result["applies"]
    saved = moe.load_config()
    assert saved.advisor_models == {"codex": "gpt-6-astra", "gemini": "gemini-3.1-pro-preview"}
    assert saved.advisor_efforts == {"codex": "high"} and saved.advisors == cfg.advisors
    assert json.loads(council_file.read_text()) == asdict(saved)
    assert len(written) == 1
    row = _rows(result["settings"])["council.advisor.gemini"]
    assert row["origin"] == "global" and "gemini-3.1-pro-preview" in row["display"]

    call({"action": "settings_save", "key": "council.advisor.codex", "value": {"model": "gpt-6-astra", "effort": "xhigh"}})
    assert moe.load_config().advisor_efforts == {"codex": "xhigh"}
    call({"action": "settings_save", "key": "council.advisor.codex", "value": {"model": "", "effort": "low"}})
    assert moe.load_config().advisor_models == {"gemini": "gemini-3.1-pro-preview"}
    assert moe.load_config().advisor_efforts == {"codex": "low"}
    result = call({"action": "settings_save", "key": "council.advisor.codex", "value": None})
    saved = moe.load_config()
    assert "codex" not in saved.advisor_models and "codex" not in saved.advisor_efforts
    assert _rows(result["settings"])["council.advisor.codex"]["origin"] == "default"
    assert saved.orchestrator == "machx" and saved.advisors == ["codex", "gemini"] and saved.max_concurrency == 3
    assert len(written) == 4 and council_file.is_relative_to(council_file.parent.parent)


@pytest.mark.parametrize("key, value, named", [
    ("council.advisor.codex", {"model": "m", "effort": "invented"}, "council.advisor.codex"),
    ("council.advisor.codex", {"model": "bad\nmodel", "effort": ""}, "council.advisor.codex"),
    ("council.advisor.codex", {"model": "x" * 300, "effort": ""}, "council.advisor.codex"),
    ("council.advisor.codex", "m", "council.advisor.codex"),
    ("council.advisor.codex", {"model": "m", "timeout": 5}, "council.advisor.codex"),
    ("council.advisor.grok", {"model": "m", "effort": ""}, "council.advisor.grok"),        # not a member
    ("council.advisor.nowhere", {"model": "m", "effort": ""}, "council.advisor.nowhere"),
    ("council.main", {"provider": "codex"}, "council.main"),
    ("council.advisors", ["codex"], "council.advisors"),
])
def test_a_refused_council_value_names_its_key_and_writes_nothing(studio, council_file, key, value, named):
    _, _, _, call = studio
    _council()
    before = council_file.read_bytes()
    error = call({"action": "settings_save", "key": key, "value": value}, status=400)
    assert named in error and "'" not in error and "Traceback" not in error, error
    assert council_file.read_bytes() == before


def test_a_council_edit_reaches_the_running_session_when_its_council_is_the_saved_one(studio, council_file):
    app, _, _, call = studio
    saved = _council()
    context = SimpleNamespace(moe_config=saved)
    app.engine = SimpleNamespace(provider=SimpleNamespace(key="machx"), model="fixture-model",
                                 backend=SimpleNamespace(), _moe=saved, _tool_context=context)
    result = call({"action": "settings_save", "key": "council.advisor.gemini", "value": {"model": "g-model", "effort": ""}})
    assert app.engine._moe.advisor_models == {"codex": "gpt-6-astra", "gemini": "g-model"}
    assert app.engine._moe is context.moe_config and app.moe is app.engine._moe
    assert app.engine._moe.advisors == saved.advisors and app.engine._moe.orchestrator == "machx"
    assert "this session" in result["applies"]
    # A session whose Council differs from the saved one keeps it: the file changes, the session says so.
    live = moe.MoeConfig("codex", ["gemini"], advisor_models={"gemini": "live-model"})
    app.engine._moe = live
    result = call({"action": "settings_save", "key": "council.advisor.gemini", "value": {"model": "h-model", "effort": ""}})
    assert app.engine._moe is live and moe.load_config().advisor_models["gemini"] == "h-model"
    assert "Council panel" in result["applies"] and "this session" in result["applies"]


# --- the engine layout adapter ----------------------------------------------------------------------------------------

def test_the_layout_section_says_not_available_without_the_module(studio, monkeypatch):
    _, _, _, call = studio
    monkeypatch.setitem(sys.modules, "dream.core.engine_layout", None)       # an ImportError, whatever is on disk
    section = next(s for s in call({"action": "settings_get"})["sections"] if s["id"] == "engine")
    assert section["rows"] == [] and "not available" in section["intro"]


def test_the_layout_section_renders_layout_view_read_only_and_masked(studio, monkeypatch):
    _, _, _, call = studio
    fake = types.ModuleType("dream.core.engine_layout")
    fake.layout_view = lambda: {"cards": [{"card": 0, "model": "mimo-v2.6-flash", "port": 11440},
                                          {"card": 1, "model": "small-model", "port": 11441}],
                                "main": "mimo-v2.6-flash", "api_key": "fixture-value-0031",
                                "endpoint": "http://user:fixture-value-0032@localhost:11440/v1?k=fixture-value-0033"}
    monkeypatch.setitem(sys.modules, "dream.core.engine_layout", fake)
    _, server, client, _ = studio
    reply = client.post("/api/control", headers={"x-dream-token": server.token}, json={"action": "settings_get"})
    assert reply.status_code == 200
    for secret in ("fixture-value-0031", "fixture-value-0032", "fixture-value-0033"):
        assert secret not in reply.text
    section = next(s for s in reply.json()["result"]["sections"] if s["id"] == "engine")
    assert "not available" not in section["intro"] and "restart" in section["intro"].lower()
    rows = {row["key"]: row for row in section["rows"]}
    assert set(rows) == {"engine.cards", "engine.main", "engine.api_key", "engine.endpoint"}
    for row in rows.values():
        assert row["edit"] is None and row["locked"] and row["origin"] == "global" and row["display"]
    assert rows["engine.api_key"]["display"] == "hidden"
    assert rows["engine.endpoint"]["display"] == "http://localhost:11440/v1"
    assert "mimo-v2.6-flash" in rows["engine.cards"]["display"] and "small-model" in rows["engine.cards"]["display"]
    assert settings_panel.engine_layout()[0]


def test_a_layout_module_that_fails_to_import_is_a_note_not_a_broken_tab(studio, monkeypatch):
    """Gate finding 2: an error while the S3 module loads (not an ImportError) must not break the Settings view,
    and is named as a failure to load, not as "not available"."""
    real = settings_panel.importlib.import_module

    def failing(name, *args, **kwargs):
        if name == "dream.core.engine_layout":
            raise RuntimeError("fixture-value-0042 at import time")
        return real(name, *args, **kwargs)
    monkeypatch.setattr(settings_panel.importlib, "import_module", failing)
    _, _, _, call = studio
    view = call({"action": "settings_get"})
    assert view["ok"] is True
    section = next(s for s in view["sections"] if s["id"] == "engine")
    assert section["rows"] == [] and "failed to load" in section["intro"] and "RuntimeError" in section["intro"]
    assert "not available" not in section["intro"] and "fixture-value-0042" not in json.dumps(view)
    rows, note = settings_panel.engine_layout()
    assert rows == [] and note.startswith("The layout view failed to load")


def test_a_failing_layout_view_is_a_note_not_a_broken_tab(studio, monkeypatch):
    _, _, _, call = studio
    fake = types.ModuleType("dream.core.engine_layout")

    def boom():
        raise RuntimeError("fixture-value-0041 must not be shown")
    fake.layout_view = boom
    monkeypatch.setitem(sys.modules, "dream.core.engine_layout", fake)
    view = call({"action": "settings_get"})
    assert view["ok"] is True
    section = next(s for s in view["sections"] if s["id"] == "engine")
    assert section["rows"] == [] and "could not be read" in section["intro"] and "fixture-value-0041" not in json.dumps(view)


# --- secrets, with the new sections present ---------------------------------------------------------------------------

def test_secrets_still_never_reach_the_page(studio, monkeypatch):
    _, server, client, call = studio
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fixture-value-0051")
    _council(advisor_models={"codex": "gpt-6-astra", "gemini": "gemini-3.1-pro-preview"})
    _write({"version": 1, "roles": {"critic": {"provider": "codex"}, "reviewer": {"model": "review-model"},
                                     "summarizer": {"model": "x", "token": "fixture-value-0052"}}})
    reply = client.post("/api/control", headers={"x-dream-token": server.token}, json={"action": "settings_get"})
    assert reply.status_code == 200 and reply.json()["result"]["ok"] is True
    for secret in ("sk-fixture-value-0051", "fixture-value-0052", server.token):
        assert secret not in reply.text, secret
    rows = _rows(reply.json()["result"])
    assert rows["roles.summarizer"]["origin"] == "not-applied" and rows["roles.critic"]["origin"] == "global"


# --- the tab in a browser -------------------------------------------------------------------------------------------------

async def test_settings_tab_edits_a_critic_role_and_a_council_advisor(settings_page, council_file, tmp_path):
    _council()
    page, url, errors = settings_page
    await page.goto(url)
    await expect(page.locator("#stat")).to_have_text("Ready")
    await page.locator("#dream-controls-open").click()
    await page.get_by_role("tab", name="Settings", exact=True).click()
    panel = page.locator("#dc-settings")
    for title in ("Roles", "Council", "Local engine layout"):
        await expect(panel.get_by_role("heading", name=title, exact=True)).to_be_visible()
    layout = panel.locator("#dc-settings-engine").locator("..")
    # The real dream.core.engine_layout is present (S3, DREAM-144): the section renders its view read-only; here
    # the endpoint (an unroutable port, see council_file) does not answer, and the rows say so.
    await expect(layout).to_contain_text("read from the engine layout")
    await expect(layout).to_contain_text("did not answer /v1/models")

    # The critic: a provider is chosen from its own list, saved, and reset.
    critic = panel.locator('[data-key="roles.critic"]')
    await expect(critic.locator(".dc-source")).to_have_text("Default")
    options = await critic.get_by_label("Critic provider").locator("option").all_text_contents()
    assert set(options) == CRITICS | {"First available (default)"}
    await expect(critic.get_by_label("Critic provider")).to_have_value("")      # the default is what is selected
    # An untouched Save writes nothing: the error names the key and the row stays at its default.
    await critic.get_by_role("button", name="Save").click()
    await expect(critic.locator(".dc-row-error")).to_contain_text("roles.critic.provider")
    assert "roles" not in read_settings()
    await critic.get_by_label("Critic provider").select_option("gemini")
    await critic.get_by_role("button", name="Save").click()
    await expect(panel.locator('[data-key="roles.critic"] .dc-source')).to_have_text("Saved")
    assert read_settings()["roles"]["critic"] == {"provider": "gemini"}
    await panel.locator('[data-key="roles.critic"]').get_by_role("button", name="Use first available").click()
    await expect(panel.locator('[data-key="roles.critic"] .dc-source')).to_have_text("Default")
    assert "roles" not in read_settings()

    # A Council advisor's model and effort, written to the temporary moe.json.
    gemini = panel.locator('[data-key="council.advisor.gemini"]')
    await expect(gemini.locator(".dc-source")).to_have_text("Default")
    await gemini.get_by_label("Council advisor: Gemini · Google model").fill("gemini-3.1-pro-preview")
    await gemini.get_by_role("button", name="Save").click()
    await expect(panel.locator('[data-key="council.advisor.gemini"] .dc-source')).to_have_text("Saved")
    assert moe.load_config().advisor_models["gemini"] == "gemini-3.1-pro-preview"
    codex = panel.locator('[data-key="council.advisor.codex"]')
    await codex.get_by_label("Council advisor: ChatGPT · Codex effort").select_option("xhigh")
    await codex.get_by_role("button", name="Save").click()
    await expect(page.locator("#dc-feedback")).to_contain_text("council.advisor.codex")
    assert moe.load_config().advisor_efforts["codex"] == "xhigh"
    # A refused effort shows under the row and writes nothing.
    await page.evaluate("""() => { const s = document.querySelector('[data-key="council.advisor.codex"] [data-setting-effort]');
        const o = document.createElement('option'); o.value = 'invented'; o.textContent = 'invented'; s.appendChild(o); s.value = 'invented'; }""")
    await panel.locator('[data-key="council.advisor.codex"]').get_by_role("button", name="Save").click()
    error = panel.locator('[data-key="council.advisor.codex"] .dc-row-error')
    await expect(error).to_contain_text("council.advisor.codex")
    assert moe.load_config().advisor_efforts["codex"] == "xhigh"
    await panel.locator('[data-key="council.advisor.codex"]').get_by_role("button", name="Use provider defaults").click()
    await expect(panel.locator('[data-key="council.advisor.codex"] .dc-source')).to_have_text("Default")
    assert "codex" not in moe.load_config().advisor_models and "codex" not in moe.load_config().advisor_efforts
    assert council_file.is_relative_to(tmp_path)

    await _no_overflow(page)
    await page.set_viewport_size({"width": 480, "height": 800})
    await _no_overflow(page)
    assert errors == []
