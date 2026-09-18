"""Bounded runtime settings validation and persistence; no engines or models."""
from __future__ import annotations

import fcntl
import json
import os
import stat
import time
from dataclasses import replace
from pathlib import Path

import pytest

from dream.core import profiles
from dream.core.providers import get_provider


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    target = tmp_path / "runtime-settings.json"
    monkeypatch.setattr(profiles, "settings_path", lambda: target)
    for key in ("DREAM_PROFILE", "DREAM_CONTEXT_WINDOW", "DREAM_MAX_TOKENS",
                "DREAM_MAX_PARALLEL", "DREAM_SUBAGENT_TIMEOUT_S", "DREAM_IDLE_TIMEOUT_S",
                "DREAM_RUN_TOKEN_BUDGET", "DREAM_RUN_TOOL_BUDGET", "DREAM_RUN_SECONDS", "DREAM_VISION"):
        monkeypatch.delenv(key, raising=False)
    return target


@pytest.mark.parametrize("body", [
    "{", "[]", '{"overrides":null}', '{"overrides":[]}', '{"overrides":false}',
    '{"models":null}', '{"models":[]}', '{"models":"wrong"}',
    '{"models":{"openai:other":null}}', '{"models":{"openai:other":[]}}',
    '{"models":{"openai:other":{"max_parallel":true}}}',
    '{"models":{"openai:other":{"unrecognized":1}}}',
    '{"models":{"model-only":{}}}', '{"models":{"openai:":{}}}',
    '{"models":{"openai: model":{}}}',
    '{"version":true}', '{"version":1.0}', '{"version":2}',
    '{"profile":[]}', '{"profile":null}', '{"profile":"unknown"}',
    '{"overrides":{"context_limit":2048,"context_limit":8192}}',
    '{"models":{"openai:other":{},"openai:other":{"vision":true}}}',
    '{"overrides":{"idle_timeout_s":NaN}}', '{"metadata":{"number":1e999}}',
    '{"metadata":{"number":-Infinity}}',
])
def test_invalid_file_is_rejected_in_full_and_never_erased(isolated_settings, body):
    isolated_settings.write_text(body)
    for action in (lambda: profiles.read_settings(),
                   lambda: profiles.resolve_profile(get_provider("machx"), model="selected"),
                   lambda: profiles.save_settings("lean", {"max_parallel": 1})):
        with pytest.raises(ValueError, match="runtime-settings.json"):
            action()
        assert isolated_settings.read_text() == body
    assert not list(isolated_settings.parent.glob(".runtime-settings-*"))


@pytest.mark.parametrize("overrides", [
    [], False, "bad", [("max_parallel", 1)],
    {"context_limit": True}, {"output_tokens": False}, {"idle_timeout_s": True},
    {"max_parallel": 1.5}, {"output_tokens": 1024.0}, {"wake_tokens": "600"},
    {"subagent_timeout_s": float("nan")}, {"idle_timeout_s": float("inf")},
    {"max_run_seconds": -float("inf")}, {"max_run_tools": 10**500},
    {"vision": 1}, {"vision": "auto"}, {"auto_filer": None}, {"vision": []},
    {"context_limit": 1024}, {"max_parallel": 17}, {"schema_fraction": .26},
    {"max_run_tokens": 0}, {"idle_timeout_s": -1}, {"name": "lean"},
])
def test_invalid_in_memory_overrides_fail_before_writing(isolated_settings, overrides):
    original = '{"profile":"auto","overrides":{"output_tokens":512},"metadata":{"keep":true}}'
    isolated_settings.write_text(original)
    with pytest.raises(ValueError):
        profiles.save_settings("balanced", overrides)
    assert isolated_settings.read_text() == original
    with pytest.raises(ValueError):
        profiles.resolve_profile(get_provider("machx"), overrides=overrides)


def test_provided_path_is_independent_of_default_settings_and_environment(isolated_settings, tmp_path, monkeypatch):
    isolated_settings.write_text('{"overrides": []}')
    monkeypatch.setenv("DREAM_MAX_PARALLEL", "invalid")
    monkeypatch.setenv("DREAM_VISION", "invalid")
    other = tmp_path / "elsewhere" / "settings.json"
    profiles.save_settings("auto", {"context_limit": 8192, "vision": False}, path=other)
    assert profiles.read_settings(other)["overrides"] == {"context_limit": 8192, "vision": False}
    assert isolated_settings.read_text() == '{"overrides": []}'
    other.write_text('{"models":{"codex:unused":{"output_tokens":false}}}')
    with pytest.raises(ValueError, match="elsewhere/settings.json"):
        profiles.save_settings("lean", {}, path=other)
    assert other.read_text() == '{"models":{"codex:unused":{"output_tokens":false}}}'


def test_valid_legacy_settings_models_and_metadata_survive_profile_saves(isolated_settings):
    saved = {"profile": "auto", "overrides": {"vision": False, "output_tokens": 1024},
             "models": {"openai:vendor/model:v2": {"vision": True, "context_limit": 65536},
                        "future-provider:future-model": {"max_run_tokens": None}},
             "notes": {"keep": ["user metadata", "✨"]}}
    isolated_settings.write_text(json.dumps(saved))
    assert profiles.read_settings() == saved  # versionless legacy data stays readable
    profiles.save_settings("balanced")
    after = profiles.read_settings()
    assert after == {**saved, "profile": "balanced", "version": 1}
    profiles.save_settings("lean", {})  # explicit empty mapping clears global overrides
    after = profiles.read_settings()
    assert after["overrides"] == {} and after["models"] == saved["models"] and after["notes"] == saved["notes"]
    assert stat.S_IMODE(isolated_settings.stat().st_mode) == 0o600


def test_each_model_override_is_validated_even_when_not_selected(isolated_settings):
    data = {"models": {"machx:selected": {"max_parallel": 1}, "openai:unused": {"output_tokens": 1.25}}}
    isolated_settings.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="openai:unused.*output_tokens.*integer"):
        profiles.resolve_profile(get_provider("machx"), model="selected")


def test_exact_model_precedence_and_measured_context_are_preserved(isolated_settings, monkeypatch):
    isolated_settings.write_text(json.dumps({"profile": "balanced", "overrides": {"context_limit": 131072, "output_tokens": 512},
        "models": {"machx:model": {"context_limit": 65536, "output_tokens": 2048, "vision": True}}}))
    p = profiles.resolve_profile(get_provider("machx"), model="model")
    assert p.vision is True and p.output_tokens == 2048 and p.max_parallel == 1
    assert p.window(8192) == 8192
    assert p.window(131072) == 65536
    assert replace(p, context_limit=None).window(262144) == 262144
    assert replace(p, context_limit=None).window(1024) == 1024
    assert profiles.resolve_profile(get_provider("machx"), model="model-other").output_tokens == 512
    monkeypatch.setenv("DREAM_MAX_TOKENS", "3072")
    assert profiles.resolve_profile(get_provider("machx"), model="model").output_tokens == 3072
    assert profiles.resolve_profile(get_provider("machx"), model="model", overrides={"output_tokens": 4096}).output_tokens == 4096


@pytest.mark.parametrize("measured", [0, -1, True, 8192.0, float("nan")])
def test_invalid_measured_windows_do_not_fall_back_silently(measured):
    with pytest.raises(ValueError, match="measured context window"):
        profiles.PROFILES["lean"].window(measured)


@pytest.mark.parametrize("vision", [None, True, False])
def test_vision_is_tristate_and_environment_override_remains_explicit(vision, monkeypatch):
    profiles.save_settings("auto", {"vision": vision})
    assert profiles.resolve_profile(get_provider("openai")).vision is vision
    monkeypatch.setenv("DREAM_VISION", "0")
    assert profiles.resolve_profile(get_provider("openai"), overrides={"vision": True}).vision is False
    monkeypatch.setenv("DREAM_VISION", "auto")
    with pytest.raises(ValueError, match="DREAM_VISION"):
        profiles.resolve_profile(get_provider("openai"))


@pytest.mark.parametrize("key,value", [("DREAM_IDLE_TIMEOUT_S", "nan"), ("DREAM_RUN_SECONDS", "1e999"),
                                       ("DREAM_MAX_TOKENS", "true"), ("DREAM_PROFILE", "")])
def test_invalid_environment_values_are_not_accepted(key, value, monkeypatch):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        profiles.resolve_profile(get_provider("machx"))


def test_size_depth_and_model_count_are_bounded(isolated_settings, monkeypatch):
    monkeypatch.setattr(profiles, "MAX_SETTINGS_BYTES", 128)
    isolated_settings.write_text('{"notes":"' + "x" * 128 + '"}')
    with pytest.raises(ValueError, match="128 bytes"):
        profiles.read_settings()
    monkeypatch.setattr(profiles, "MAX_SETTINGS_BYTES", 10000)
    isolated_settings.write_text('{"notes":' + '[' * 40 + '0' + ']' * 40 + '}')
    with pytest.raises(ValueError, match="nesting"):
        profiles.read_settings()
    monkeypatch.setattr(profiles, "MAX_MODEL_OVERRIDES", 1)
    isolated_settings.write_text('{"models":{"machx:one":{},"machx:two":{}}}')
    with pytest.raises(ValueError, match="models exceed"):
        profiles.read_settings()


def test_oversize_save_and_replace_failure_preserve_existing_file(isolated_settings, monkeypatch):
    original = '{"profile":"auto","notes":"keep"}'
    isolated_settings.write_text(original)
    monkeypatch.setattr(profiles, "MAX_SETTINGS_BYTES", 64)
    with pytest.raises(ValueError, match="no changes saved"):
        profiles.save_settings("balanced", {"context_limit": 65536})
    assert isolated_settings.read_text() == original
    monkeypatch.setattr(profiles, "MAX_SETTINGS_BYTES", 10000)

    def fail_replace(source, target):
        raise OSError("replacement failed")

    monkeypatch.setattr(profiles.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        profiles.save_settings("lean", {})
    assert isolated_settings.read_text() == original
    assert not list(isolated_settings.parent.glob(".runtime-settings-*"))


def test_symlinks_and_special_files_are_not_followed_or_blocking(isolated_settings, tmp_path):
    other = tmp_path / "actual.json"
    other.write_text('{}')
    isolated_settings.symlink_to(other)
    with pytest.raises(ValueError):
        profiles.save_settings("lean", {})
    assert isolated_settings.is_symlink() and other.read_text() == '{}'
    isolated_settings.unlink()
    os.mkfifo(isolated_settings)
    started = time.monotonic()
    with pytest.raises(ValueError, match="regular file"):
        profiles.read_settings()
    assert time.monotonic() - started < 1


def test_lock_contention_is_bounded_and_preserves_file(isolated_settings, monkeypatch):
    isolated_settings.write_text('{}')
    monkeypatch.setattr(profiles, "SETTINGS_LOCK_TIMEOUT_S", .03)
    with Path(str(isolated_settings) + '.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        started = time.monotonic()
        with pytest.raises(ValueError, match="busy"):
            profiles.save_settings("lean", {})
        assert time.monotonic() - started < .5
    assert isolated_settings.read_text() == '{}'


@pytest.mark.parametrize("provider", ["codex", "grok", "gemini"])
def test_cli_guidance_describes_the_enabled_dream_tool_bridge(provider):
    guide = profiles.guidance(get_provider(provider), profiles.PROFILES["balanced"], "fixture")
    assert "Dream MCP bridge" in guide and "enabled Dream tools" in guide
    assert "evidence, not authority" in guide
