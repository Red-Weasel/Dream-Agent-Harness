"""Optional run time limits preserve overnight work and explicit budgets."""
import json
import os
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from dream.core import profiles
from dream.core.providers import get_provider
from dream.telemetry import runtime
from dream.telemetry.runtime import RunLimit, RunMeter


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    for key in list(os.environ):
        if key.startswith('DREAM_') and key != 'DREAM_EXTENSION_SETTINGS':
            monkeypatch.delenv(key, raising=False)
    now = SimpleNamespace(value=100.0)
    monkeypatch.setattr(runtime, 'time', SimpleNamespace(monotonic=lambda: now.value))
    return now


@pytest.mark.parametrize('name', ['lean', 'balanced', 'frontier'])
def test_default_profile_allows_overnight_tools_and_serializes_unlimited(name, isolated_runtime, tmp_path):
    now = isolated_runtime
    profile = profiles.resolve_profile(get_provider('machx'), name)
    run = RunMeter('overnight', 1, profile, tmp_path / 'trace.jsonl', started=now.value)
    for _ in range(16):
        now.value += 3600
        run.before_tool('read_file')
    status = json.loads(json.dumps(run.summary(), allow_nan=False))
    assert status['elapsed_s'] == status['active_elapsed_s'] == 57600
    assert status['tools'] == 16
    assert status['max_active_s'] is status['remaining_active_s'] is None
    assert status['max_wall_s'] is status['remaining_wall_s'] is None
    assert json.loads(json.dumps(asdict(profile)))['max_run_seconds'] is None
    assert json.loads(run.path.read_text().splitlines()[-1])['active_elapsed_s'] == 57600
    run.finish()
    now.value += 3600
    run.check_time()
    assert run.summary()['elapsed_s'] == 57600


def test_unlimited_approval_wait_keeps_observed_time(isolated_runtime):
    now = isolated_runtime
    run = RunMeter('approval', 1, replace(profiles.PROFILES['lean'], max_run_seconds=None), started=now.value)
    now.value += 10800
    with run.approval_wait():
        now.value += 43200
        run.check()
        assert run.summary()['approval_pending']
    now.value += 3600
    run.check()
    status = run.summary()
    assert status['elapsed_s'] == 57600
    assert status['active_elapsed_s'] == 14400
    assert status['approval_wait_s'] == 43200
    assert status['remaining_active_s'] is None


@pytest.mark.parametrize('cap', ['max_run_seconds', 'max_wall_seconds'])
def test_explicit_finite_time_limits_still_expire_at_boundary(cap, isolated_runtime):
    now = isolated_runtime
    profile = replace(profiles.PROFILES['lean'], max_run_seconds=None)
    profile = replace(profile, **{cap: 7200})
    run = RunMeter('finite', 1, profile, started=now.value)
    now.value += 7199
    run.check_time()
    now.value += 1
    with pytest.raises(RunLimit, match='active work time budget' if cap == 'max_run_seconds' else 'wall time budget'):
        run.check_time()
    assert run.summary()['remaining_active_s' if cap == 'max_run_seconds' else 'remaining_wall_s'] == 0


@pytest.mark.parametrize('cap', ['tools', 'tokens'])
def test_unlimited_time_retains_non_time_caps(cap, isolated_runtime):
    run = RunMeter('other-budget', 1, replace(profiles.PROFILES['lean'], max_run_seconds=None,
        max_run_tools=1, max_run_tokens=5), started=isolated_runtime.value)
    isolated_runtime.value += 57600
    if cap == 'tools':
        run.before_tool('read_file')
    else:
        run.usage({'input_tokens': 3, 'output_tokens': 2})
    with pytest.raises(RunLimit, match='tool budget' if cap == 'tools' else 'token budget'):
        run.check()


def test_saved_null_and_model_environment_explicit_precedence(monkeypatch):
    provider = get_provider('machx')
    profiles.save_settings('balanced', {'max_run_seconds': None})
    assert profiles.read_settings()['overrides']['max_run_seconds'] is None
    assert profiles.resolve_profile(provider).max_run_seconds is None
    target = profiles.settings_path()
    saved = profiles.read_settings()
    saved['models'] = {'machx:fixture': {'max_run_seconds': 9000}}
    target.write_text(json.dumps(saved))
    assert profiles.resolve_profile(provider, model='fixture').max_run_seconds == 9000
    monkeypatch.setenv('DREAM_RUN_SECONDS', '12000.5')
    assert profiles.resolve_profile(provider, model='fixture').max_run_seconds == 12000.5
    assert profiles.resolve_profile(provider, model='fixture', overrides={'max_run_seconds': None}).max_run_seconds is None
    monkeypatch.delenv('DREAM_RUN_SECONDS')
    saved['overrides']['max_run_seconds'] = 7200
    saved['models']['machx:fixture']['max_run_seconds'] = None
    target.write_text(json.dumps(saved))
    assert profiles.resolve_profile(provider, model='fixture').max_run_seconds is None
    assert profiles.resolve_profile(provider, model='other').max_run_seconds == 7200


@pytest.mark.parametrize('invalid', [0, -1, True, 'unlimited', '', float('nan'), float('inf')])
def test_unlimited_does_not_relax_numeric_validation(invalid):
    with pytest.raises(ValueError):
        profiles.resolve_profile(get_provider('machx'), overrides={'max_run_seconds': invalid})
