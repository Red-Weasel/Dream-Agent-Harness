"""Exercise user-facing launch flags without booting any provider."""
import os
import sys
import pytest
from dream import config
from dream.__main__ import main


@pytest.mark.parametrize("flags", [["--profile", "lean"], ["--profile=lean"]])
def test_profile_flag_reaches_explicit_provider_launch(flags, monkeypatch, tmp_path):
    from dream.tui import app
    observed = []
    class FakeApp:
        def __init__(self, **kwargs):
            observed.append(kwargs)
        async def run(self):
            observed.append(os.environ["DREAM_PROFILE"])
    monkeypatch.setattr(app, "App", FakeApp)
    monkeypatch.setenv("DREAM_PROFILE", "frontier")
    monkeypatch.setattr(sys, "argv", ["dream", "--provider", "codex", "--workspace", str(tmp_path), *flags])
    main()
    assert observed[0]["provider"] == "codex" and observed[1] == "lean"


def test_resume_uses_durable_goal_and_workspace(monkeypatch, tmp_path):
    from dream.core.run_state import RunState
    from dream.tui import app
    monkeypatch.setattr(config, "LOOP_DIR", tmp_path / "runs")
    journal = RunState(config.LOOP_DIR)
    journal.record("fixture", goal="Preserve this goal", worker_workspace=str(tmp_path), status="budget")
    run_id = journal.run_id
    journal.close()
    observed = {}
    class FakeApp:
        def __init__(self, **kwargs):
            observed.update(kwargs)
        async def run_autonomous(self, goal, iterations, **kwargs):
            observed.update(goal=goal, **kwargs)
    monkeypatch.setattr(app, "App", FakeApp)
    monkeypatch.setattr(sys, "argv", ["dream", "--provider", "codex", "--resume-run", run_id,
                                     "--resume-note", "Verified the artifact already exists"])
    main()
    assert observed["goal"] == "Preserve this goal" and observed["workspace"] == str(tmp_path)
    assert observed["resume_run_id"] == run_id and "already exists" in observed["resume_note"]


def test_sdk_delegation_uses_enforced_shell_and_readers_have_no_shell():
    from dream.core.subagents import subagents, local_subagents
    from dream.core.backends.anthropic import AnthropicBackend, SAFE_BUILTINS
    assert "Task" not in SAFE_BUILTINS
    sdk, local = subagents(), local_subagents()
    assert "Bash" not in sdk["coder"].tools and config.tool_id("run_bash") in sdk["coder"].tools
    for name in ("explorer", "reviewer"):
        assert "Bash" not in sdk[name].tools and "run_bash" not in local[name].tool_names
    backend = AnthropicBackend(system_prompt="fixture", mcp_server={}, preapproved_tool_ids=[],
                               agents=None, permission_cb=None, model=None)
    options = backend._build_options()
    assert options.permission_mode == "default" and options.setting_sources == []
    assert options.settings == '{"disableAllHooks":true}'
    assert options.strict_mcp_config is True
