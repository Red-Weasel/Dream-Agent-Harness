"""Owner 2026-10-13: Dream's settings must be editable for real. Every DREAM_* number is saved from the Settings tab
(runtime-settings.json "numeric", applied when Dream starts, a set variable still wins); the profile's limits that
pointed at Runtime -> Next session (which has three of them) are saved from the tab too, and the Runtime form keeps a
limit the tab saved meanwhile."""
import json

import pytest

import dream.config as config
from dream.core import profiles, settings
from dream.gui import settings_panel


def _saved():
    return json.loads((config.DATA_DIR / "runtime-settings.json").read_text())


def test_a_number_is_saved_as_text_and_shown_as_saved(monkeypatch):
    monkeypatch.delenv("DREAM_FREQUENCY_PENALTY", raising=False)
    settings.set_value("numeric.frequency_penalty", "0")
    settings.set_value("numeric.tool_budget", "0")          # an unlimited alias
    assert _saved()["numeric"] == {"frequency_penalty": "0.0", "tool_budget": "none"}
    rows = settings.effective(provider="machx")
    assert rows["numeric.frequency_penalty"] == settings.Effective(0.0, "global")
    assert rows["numeric.tool_budget"] == settings.Effective(None, "global")
    settings.unset_value("numeric.frequency_penalty")
    assert "frequency_penalty" not in _saved()["numeric"]


@pytest.mark.parametrize("key,value", [("numeric.max_tool_rounds", "abc"), ("numeric.frequency_penalty", "nan"),
                                       ("numeric.subagent_rounds", "1.5")])
def test_a_value_dream_cannot_use_is_refused_and_nothing_is_written(key, value):
    with pytest.raises(ValueError, match=key):
        settings.set_value(key, value)
    assert not (config.DATA_DIR / "runtime-settings.json").exists()


def test_the_numbers_are_saved_on_this_computer_only():
    with pytest.raises(ValueError, match="this computer only"):
        settings.set_value("numeric.frequency_penalty", "0", scope="project")


def test_a_set_variable_still_wins(monkeypatch):
    settings.set_value("numeric.max_tool_rounds", "250")
    monkeypatch.setenv("DREAM_MAX_TOOL_ROUNDS", "90")
    assert settings.effective(provider="machx")["numeric.max_tool_rounds"] == settings.Effective(
        90, "env DREAM_MAX_TOOL_ROUNDS")
    monkeypatch.setattr(config, "MAX_TOOL_ROUNDS", config.MAX_TOOL_ROUNDS)
    assert "DREAM_MAX_TOOL_ROUNDS" not in config.apply_saved_numeric()
    assert config.MAX_TOOL_ROUNDS != 250


def test_dream_applies_the_saved_numbers_when_it_starts(monkeypatch):
    for attr in (*config.NUMERIC_ATTRS, "SEARXNG_URL"):
        monkeypatch.setattr(config, attr, getattr(config, attr))     # put every value back after the test
    for name in config.NUMERIC_ATTRS.values():
        monkeypatch.delenv(name, raising=False)
    (config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "runtime-settings.json").write_text(json.dumps(
        {"version": 1, "numeric": {"frequency_penalty": "0", "searxng_port": "9999", "max_tool_rounds": "oops"}}))
    applied = config.apply_saved_numeric()
    assert set(applied) == {"DREAM_FREQUENCY_PENALTY", "DREAM_SEARXNG_PORT"}       # the unusable one is skipped
    assert config.FREQUENCY_PENALTY == 0.0 and config.SEARXNG_URL.endswith(":9999")
    assert config.MAX_TOOL_ROUNDS == 100


def test_the_settings_tab_edits_numbers_and_the_profile_limits():
    view = settings_panel.view(provider="machx", model=None)
    rows = {row["key"]: row for section in view["sections"] for row in section["rows"]}
    assert rows["numeric.frequency_penalty"]["edit"]["step"] == "any"
    assert rows["numeric.tool_budget"]["edit"]["placeholder"] == "0 = no limit"
    assert rows["overrides.max_run_tools"]["edit"]["kind"] == "number"
    assert rows["overrides.vision_helper"]["edit"]["choices"][0] == "off"
    assert "API providers" in rows["numeric.frequency_penalty"]["help"]          # local models use the load screen
    settings_panel.save("numeric.frequency_penalty", "0.2")
    settings_panel.save("overrides.max_run_tools", "250")
    settings_panel.save("overrides.auto_filer", True)
    with pytest.raises(ValueError, match="overrides.schema_fraction"):
        settings_panel.save("overrides.schema_fraction", "0.5")
    with pytest.raises(ValueError, match="this computer only"):
        settings_panel.save("overrides.max_run_tools", "10", "project")
    saved = _saved()
    assert saved["numeric"] == {"frequency_penalty": "0.2"}
    assert saved["overrides"] == {"max_run_tools": 250, "auto_filer": True}
    settings_panel.save("overrides.max_run_tools", None)                             # Use default
    assert _saved()["overrides"] == {"auto_filer": True}


def test_the_runtime_form_keeps_a_limit_the_tab_saved_meanwhile():
    settings_panel.save("overrides.max_run_tools", "250")
    settings_panel.save("overrides.context_limit", "50000")
    # the form's own fields: context_limit left blank (removed), idle timeout set; max_run_tools is not the form's
    profiles.update_profile_fields("balanced", {"idle_timeout_s": 1600}, ["context_limit", "output_tokens",
                                                                          "idle_timeout_s", "vision"])
    assert _saved()["overrides"] == {"max_run_tools": 250, "idle_timeout_s": 1600}
    with pytest.raises(ValueError, match="only set the fields"):
        profiles.update_profile_fields("balanced", {"max_run_tools": 1}, ["context_limit"])
