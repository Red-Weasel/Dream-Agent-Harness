"""Offline protocol contracts based on antigravity.google/docs/cli/headless/.

Synthetic examples follow the documented envelopes; no vendor binary or account
is invoked. Root owns runtime integration and subprocess lifecycle tests.
"""
import json

import pytest

from dream.core.backends.antigravity_adapter import AntigravityAdapter


def step(index, kind, state="ACTIVE", **fields):
    return {"event": "step_update", "step_update": {
        "conversation_id": "conversation-a", "step_index": index,
        "step_type": kind, "state": state, **fields}}


def terminal(**fields):
    return {"event": "result", "result": {"status": "SUCCESS", **fields}}


@pytest.mark.parametrize("sandbox,mode", [("read-only", "plan"), ("workspace-write", "accept-edits")])
def test_invocation_uses_stdin_and_exact_conversation(sandbox, mode):
    adapter = AntigravityAdapter()
    argv = adapter.argv("secret prompt", cwd="/tmp/workspace", resume_id="conversation-a", sandbox=sandbox)
    assert argv[0] == "agy"
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--print-timeout") + 1] == "15m"
    assert argv[argv.index("--mode") + 1] == mode
    assert argv[argv.index("--conversation") + 1] == "conversation-a"
    assert "--sandbox" in argv
    assert "secret prompt" not in argv
    assert not {"-p", "--continue", "--resume"}.intersection(argv)


def test_stdin_prompt_is_one_lossless_ndjson_message():
    payload = AntigravityAdapter().stdin_payload('line one\n"line two"')
    if isinstance(payload, bytes):
        payload = payload.decode()
    assert payload.endswith("\n")
    assert len(payload.splitlines()) == 1
    assert json.loads(payload) == {"event": "user", "message": {"content": 'line one\n"line two"'}}


@pytest.mark.parametrize("obj,expected", [
    ({"event": "init", "conversation_id": "a"}, "a"),
    ({"event": "init", "init": {"conversation_id": "b"}}, "b"),
    ({"event": "init", "conversation_id": ""}, None),
    ({"event": "init", "conversation_id": 42}, None),
    ({"event": "result", "conversation_id": "a"}, None),
])
def test_capture_requires_init_and_string_identity(obj, expected):
    assert AntigravityAdapter().capture_session(obj) == expected


def test_equal_deltas_are_real_text_and_terminal_response_does_not_duplicate():
    adapter, state = AntigravityAdapter(), {}
    events = []
    for obj in [step(1, "agent_response", text_delta="ha"),
                step(1, "agent_response", "DONE", text_delta="ha"),
                terminal(response="haha")]:
        events.extend(adapter.translate(obj, state))
    assert [e.data for e in events if e.kind == "text_delta"] == ["ha", "ha"]
    assert [e.data for e in events if e.kind == "assistant_done"] == ["haha"]
    assert events[-1].kind == "result"
    assert events[-1].data["is_error"] is False


def test_terminal_response_fallback_without_streamed_text():
    events = AntigravityAdapter().translate(terminal(response="Finished"), {})
    assert [e.data for e in events if e.kind == "assistant_done"] == ["Finished"]
    assert events[-1].kind == "result"


@pytest.mark.parametrize("status", [None, "success", "UNKNOWN", "FAILED", 1])
def test_missing_or_unknown_terminal_status_fails_closed(status):
    events = AntigravityAdapter().translate(terminal(status=status), {})
    assert events[-1].kind == "result"
    assert events[-1].data["is_error"] is True


def test_denied_actions_override_success():
    events = AntigravityAdapter().translate(terminal(denied_actions=[{"tool": "write_to_file"}]), {})
    assert events[-1].data["is_error"] is True


@pytest.mark.parametrize("active_first", [False, True])
def test_tool_correlates_index_and_duplicate_done_emits_no_second_result(active_first):
    adapter, state = AntigravityAdapter(), {}
    info = {"name": "read_file", "parameters": {"path": "example.txt"}}
    events = adapter.translate(step(3, "tool", tool_info=info), state) if active_first else []
    done = step(3, "tool", "DONE", tool_info={**info, "output": "example content"})
    events.extend(adapter.translate(done, state))
    events.extend(adapter.translate(done, state))
    calls = [e.data for e in events if e.kind == "tool_use"]
    results = [e.data for e in events if e.kind == "tool_result"]
    assert len(calls) == len(results) == 1
    assert calls[0]["name"] == results[0]["name"] == "read_file"
    assert calls[0]["input"] == {"path": "example.txt"}
    assert calls[0]["id"] == results[0]["id"]
    assert results[0]["content"] == "example content"
    assert results[0]["is_error"] is False


def test_usage_preserves_input_output_and_cache_accounting():
    usage = {"input_tokens": 100, "output_tokens": 12, "cache_read_tokens": 70}
    events = AntigravityAdapter().translate(terminal(usage=usage), {})
    result = events[-1].data
    assert result["usage"]["prompt_tokens"] == 170
    assert result["usage"]["completion_tokens"] == 12
    assert result["usage"]["cache_read_tokens"] == 70
    assert result["total_cost_usd"] is None
    assert result["usage_scope"] == "turn"
    assert result["context_tokens"] is None


def test_documented_cumulative_resume_counts_only_new_input_and_cache():
    adapter = AntigravityAdapter()
    first = {"input_tokens": 30384, "output_tokens": 4, "cache_read_tokens": 0,
             "thinking_tokens": 0, "total_tokens": 30388}
    second = {"input_tokens": 30662, "output_tokens": 8, "cache_read_tokens": 30214,
              "thinking_tokens": 0, "total_tokens": 30670}
    results = []
    for usage in (first, second):
        state = {}  # CliAgentBackend creates a fresh stream state on resume.
        adapter.translate({"event": "init", "conversation_id": "a"}, state)
        event = adapter.translate(terminal(conversation_id="a", usage=usage), state)[-1]
        adapter.commit_result(event)
        results.append(event.data)
    assert results[0]["usage"]["prompt_tokens"] == 30384
    assert results[1]["usage"]["prompt_tokens"] == 30492
    assert results[1]["usage"]["completion_tokens"] == 4
    assert results[1]["usage"]["prompt_tokens_details"]["cached_tokens"] == 30214
    assert results[1]["provider_usage"] == second
    assert results[1]["provider_usage_scope"] == "session"
    assert results[1]["usage_counter_reset"] is False


def test_usage_baseline_resets_on_new_conversation_or_counter_reset():
    adapter = AntigravityAdapter()
    def result(sid, incoming):
        event = adapter.translate(terminal(conversation_id=sid,
            usage={"input_tokens": incoming, "cache_read_tokens": 5}), {})[-1]
        adapter.commit_result(event)
        return event.data
    assert result("a", 100)["usage"]["prompt_tokens"] == 105
    assert result("b", 200)["usage"]["prompt_tokens"] == 205
    reset = result("b", 10)
    assert reset["usage"]["prompt_tokens"] == 15
    assert reset["usage_counter_reset"] is True


def test_malformed_or_missing_usage_does_not_destroy_baseline():
    adapter = AntigravityAdapter()
    event = adapter.translate(terminal(usage={"input_tokens": 100, "output_tokens": 10}), {})[-1]
    adapter.commit_result(event)
    assert adapter.translate(terminal(usage=None), {})[-1].data["usage"] == {}
    adapter.translate(terminal(usage={"input_tokens": True, "output_tokens": -1}), {})
    result = adapter.translate(terminal(usage={"input_tokens": 125, "output_tokens": 12}), {})[-1].data
    assert result["usage"]["prompt_tokens"] == 25
    assert result["usage"]["completion_tokens"] == 2


def test_unpublished_terminal_does_not_advance_usage_baseline():
    adapter = AntigravityAdapter()
    first = adapter.translate(terminal(usage={"input_tokens": 100}), {})[-1]
    adapter.commit_result(first)
    # Simulate a translated result dropped by interruption / cleanup failure.
    adapter.translate(terminal(usage={"input_tokens": 140}), {})
    resumed = adapter.translate(terminal(usage={"input_tokens": 160}), {})[-1]
    assert resumed.data["usage"]["prompt_tokens"] == 60
    adapter.commit_result(resumed)
    following = adapter.translate(terminal(usage={"input_tokens": 175}), {})[-1]
    assert following.data["usage"]["prompt_tokens"] == 15


@pytest.mark.parametrize("obj", [{}, {"event": "heartbeat"}, {"event": "step_update"},
    step(0, "user_input", "DONE"), {"event": "step_update", "step_update": None}])
def test_unknown_or_malformed_nonterminal_events_render_nothing(obj):
    assert AntigravityAdapter().translate(obj, {}) == []


def test_mcp_option_boundaries_and_exact_removal_name():
    adapter = AntigravityAdapter()
    argv = adapter.mcp_register_argv("dream-test", {"PYTHONPATH": "/tmp/module", "DREAM_REQUIRE_PARENT_BRIDGE": "1"},
                                     ["python3", "-m", "example_proxy"])
    name_index = argv.index("dream-test")
    assert argv.index("--type") < name_index
    assert argv[argv.index("--type") + 1] == "stdio"
    assert argv.index("--env") < name_index
    assert argv[name_index + 1:] == ["--", "python3", "-m", "example_proxy"]
    assert adapter.mcp_deregister_argv("dream-test") == ["agy", "mcp", "remove", "dream-test"]
