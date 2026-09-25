"""Model-free repeat-guard regressions for captures after workspace mutations.

The execution boundary is fake; no shell command, browser, or renderer runs.
"""
from collections import Counter
from types import SimpleNamespace

import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend


@pytest.fixture
def guard():
    backend = OpenAICompatBackend(
        provider=SimpleNamespace(
            key="machx", label="MachX", base_url="http://unused/v1",
            multimodal=False, api_key=lambda: "unused",
        ),
        model="fixture", system_prompt="fixture", tools=[], permission_cb=None,
    )
    executions = Counter()
    outcomes = {}

    async def execute(name, args, allowed=None):
        executions[name] += 1
        return outcomes.get(name, ("Saved: preview.png", False))

    backend._exec_tool = execute
    return backend, executions, outcomes


CAPTURE = {"path": "preview.html", "steps": [{}], "save_path": "preview.png"}


@pytest.mark.parametrize("name,args", [
    ("write_file", {"path": "preview.html", "content": "new HTML"}),
    ("str_replace_edit", {"path": "preview.html", "old_str": "old", "new_str": "new"}),
    ("run_bash", {"command": "fake rerender"}),
    ("run_script", {"code": "fake rerender"}),
])
async def test_same_capture_after_successful_mutation_gets_fresh_attempt(guard, name, args):
    backend, executions, _ = guard
    history = {}
    await backend._guarded_exec("save_screenshot", CAPTURE, history)
    await backend._guarded_exec(name, args, history)
    text, error, stop = await backend._guarded_exec("save_screenshot", CAPTURE, history)
    assert executions["save_screenshot"] == 2
    assert "loop guard" not in text.lower()
    assert not error and not stop


async def test_capture_block_is_recoverable_after_successful_write(guard):
    backend, executions, _ = guard
    history = {}
    for _ in range(3):
        await backend._guarded_exec("save_screenshot", CAPTURE, history)
    assert executions["save_screenshot"] == 2
    await backend._guarded_exec("write_file", {"path": "preview.html"}, history)
    text, error, stop = await backend._guarded_exec("save_screenshot", CAPTURE, history)
    assert executions["save_screenshot"] == 3
    assert "loop guard" not in text.lower()
    assert not error and not stop


@pytest.mark.parametrize("name", ["save_screenshot", "read_file", "write_file", "run_bash"])
async def test_true_unchanged_spam_still_warns_blocks_and_stops(guard, name):
    backend, executions, _ = guard
    history = {}
    results = [await backend._guarded_exec(name, CAPTURE, history) for _ in range(4)]
    assert executions[name] == 2
    assert "loop guard" in results[1][0].lower()
    assert results[2][1:] == (True, False)
    assert results[3][1:] == (True, True)


@pytest.mark.parametrize("result", [
    "Error: render failed", "Permission denied", "tool unavailable to this subagent",
])
async def test_unexecuted_mutation_error_does_not_unlock_blocked_capture(guard, result):
    backend, executions, outcomes = guard
    outcomes["run_bash"] = (result, True)
    history = {}
    for _ in range(2):
        await backend._guarded_exec("save_screenshot", CAPTURE, history)
    await backend._guarded_exec("run_bash", {"command": "fake rerender"}, history)
    _, error, stop = await backend._guarded_exec("save_screenshot", CAPTURE, history)
    assert executions["save_screenshot"] == 2
    assert error and not stop


async def test_observations_and_memory_updates_do_not_reset_capture(guard):
    backend, executions, _ = guard
    history = {}
    for _ in range(2):
        await backend._guarded_exec("save_screenshot", CAPTURE, history)
    await backend._guarded_exec("read_file", {"path": "preview.html"}, history)
    await backend._guarded_exec("update_todos", {}, history)
    await backend._guarded_exec("save_screenshot", CAPTURE, history)
    assert executions["save_screenshot"] == 2


async def test_different_capture_does_not_reset_previous_capture(guard):
    backend, executions, _ = guard
    history = {}
    for _ in range(2):
        await backend._guarded_exec("save_screenshot", CAPTURE, history)
    await backend._guarded_exec("save_screenshot", {**CAPTURE, "save_path": "other.png"}, history)
    await backend._guarded_exec("save_screenshot", CAPTURE, history)
    assert executions["save_screenshot"] == 3


async def test_worker_mutation_does_not_clear_another_history(guard):
    backend, executions, _ = guard
    lead_history, worker_history = {}, {}
    for _ in range(2):
        await backend._guarded_exec("save_screenshot", CAPTURE, lead_history)
    await backend._guarded_exec("write_file", {"path": "preview.html"}, worker_history,
                                allowed={"write_file"})
    await backend._guarded_exec("save_screenshot", CAPTURE, lead_history)
    assert executions["save_screenshot"] == 2


async def test_alternating_unchanged_mutators_remain_bounded(guard):
    backend, executions, _ = guard
    history = {}
    for _ in range(6):
        for name in ("write_file", "run_bash"):
            await backend._guarded_exec(name, {"path": "preview.html"}, history)
    assert executions["write_file"] == 2
    assert executions["run_bash"] <= 4


@pytest.mark.parametrize("raises", [False, True])
async def test_executed_shell_failure_may_change_artifacts_and_allows_capture(guard, raises):
    backend, executions, _ = guard
    history = {}
    for _ in range(2):
        await backend._guarded_exec("save_screenshot", CAPTURE, history)
    fake_execute = backend._exec_tool

    async def partial_write(args):
        if raises:
            raise RuntimeError("failed after a partial output")
        return {"content": [{"type": "text", "text": "partial output, exit 1"}], "is_error": True}

    backend.tools_by_name["run_bash"] = SimpleNamespace(
        name="run_bash", description="fixture", input_schema={"type": "object"},
        handler=partial_write,
    )
    backend._exec_tool = OpenAICompatBackend._exec_tool.__get__(backend)
    _, error, _ = await backend._guarded_exec("run_bash", {}, history)
    assert error
    backend._exec_tool = fake_execute
    text, error, stop = await backend._guarded_exec("save_screenshot", CAPTURE, history)
    assert executions["save_screenshot"] == 3
    assert not error and not stop and "loop guard" not in text.lower()


async def test_real_permission_refusal_does_not_unlock_capture(guard):
    backend, executions, _ = guard
    history = {}
    for _ in range(2):
        await backend._guarded_exec("save_screenshot", CAPTURE, history)
    fake_execute = backend._exec_tool
    called = False

    async def should_not_execute(args):
        nonlocal called
        called = True
        return {"content": []}

    async def deny(name, args):
        return False

    backend.tools_by_name["run_bash"] = SimpleNamespace(
        name="run_bash", description="fixture", input_schema={"type": "object"},
        handler=should_not_execute,
    )
    backend.permission_cb = deny
    backend._exec_tool = OpenAICompatBackend._exec_tool.__get__(backend)
    _, error, _ = await backend._guarded_exec("run_bash", {}, history)
    assert error and not called
    backend._exec_tool = fake_execute
    await backend._guarded_exec("save_screenshot", CAPTURE, history)
    assert executions["save_screenshot"] == 2


async def test_write_allows_retry_after_file_was_missing(guard):
    backend, executions, outcomes = guard
    outcomes["read_file"] = ("Error: file not found", True)
    history = {}
    for _ in range(2):
        await backend._guarded_exec("read_file", {}, history)
    await backend._guarded_exec("write_file", {"path": "preview.html"}, history)
    outcomes["read_file"] = ("new contents", False)
    text, error, stop = await backend._guarded_exec("read_file", {}, history)
    assert executions["read_file"] == 3
    assert text == "new contents" and not error and not stop


async def test_unchanged_failed_reads_still_guard(guard):
    backend, executions, outcomes = guard
    outcomes["read_file"] = ("Error: file not found", True)
    history = {}
    results = [await backend._guarded_exec("read_file", {}, history) for _ in range(4)]
    assert executions["read_file"] == 2
    assert results[-1][1:] == (True, True)


async def test_script_edit_recovers_blocked_identical_rerender(guard):
    backend, executions, _ = guard
    history = {}
    render_args = {"command": "python render.py"}
    for _ in range(3):
        await backend._guarded_exec("run_bash", render_args, history)
    assert executions["run_bash"] == 2
    await backend._guarded_exec("str_replace_edit", {"path": "render.py", "old_str": "old", "new_str": "new"}, history)
    text, error, stop = await backend._guarded_exec("run_bash", render_args, history)
    assert executions["run_bash"] == 3
    assert "loop guard" not in text.lower() and not error and not stop


async def test_two_unchanged_file_mutators_cannot_unlock_each_other(guard):
    backend, executions, _ = guard
    history = {}
    for _ in range(5):
        for name in ("write_file", "str_replace_edit"):
            await backend._guarded_exec(name, {"path": "render.py"}, history)
    assert executions["write_file"] == executions["str_replace_edit"] == 2


async def test_shell_commands_cannot_unlock_each_other(guard):
    backend, executions, _ = guard
    history = {}
    for _ in range(5):
        for command in ("fake render A", "fake render B"):
            await backend._guarded_exec("run_bash", {"command": command}, history)
    assert executions["run_bash"] == 4


# DREAM-133: a call that changes a live app (live Blender's code and snapshot restore,
# a computer action, any unclassified tool) can change what the next look at that app
# sees, so the same screenshot after it is a fresh attempt.
SHOT = "blender__get_viewport_screenshot"
LIVE_CHANGES = [
    ("blender__execute_blender_code", {"code": "fake scene change"}),
    ("blender__restore_scene_snapshot", {"name": "fixture"}),
    ("computer_action", {"action": "click", "x": 1, "y": 1}),
    ("unclassified_fixture_tool", {}),
]


@pytest.mark.parametrize("name,args", LIVE_CHANGES)
async def test_screenshot_after_each_live_change_runs(guard, name, args):
    backend, executions, _ = guard
    history = {}
    results = []
    for _ in range(3):
        results.append(await backend._guarded_exec(SHOT, {}, history))
        if len(results) < 3:
            await backend._guarded_exec(name, args, history)
    assert executions[SHOT] == 3
    assert all("loop guard" not in text.lower() and not error and not stop
               for text, error, stop in results)


@pytest.mark.parametrize("name", [SHOT, "computer_observe"])
async def test_unchanged_live_screenshot_spam_still_warns_blocks_and_stops(guard, name):
    backend, executions, _ = guard
    history = {}
    results = [await backend._guarded_exec(name, {}, history) for _ in range(4)]
    assert executions[name] == 2
    assert "loop guard" in results[1][0].lower()
    assert "not executed" in results[2][0].lower() and results[2][1:] == (True, False)
    assert results[3][1:] == (True, True)


async def test_unexecuted_blender_code_does_not_unlock_screenshot(guard):
    backend, executions, outcomes = guard
    outcomes["blender__execute_blender_code"] = ("Permission denied", True)
    history = {}
    for _ in range(2):
        await backend._guarded_exec(SHOT, {}, history)
    await backend._guarded_exec("blender__execute_blender_code", {"code": "x"}, history)
    _, error, stop = await backend._guarded_exec(SHOT, {}, history)
    assert executions[SHOT] == 2
    assert error and not stop


async def test_unchanged_blender_code_repeats_still_guard(guard):
    backend, executions, _ = guard
    history = {}
    for _ in range(4):
        await backend._guarded_exec("blender__execute_blender_code", {"code": "x"}, history)
        await backend._guarded_exec(SHOT, {}, history)
    assert executions["blender__execute_blender_code"] == 2
