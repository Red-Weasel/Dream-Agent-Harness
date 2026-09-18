"""Model-free regression of local launch controls and actual HTTP payloads."""
import copy
import shlex
from types import SimpleNamespace

import pytest
from rich.console import Console

from dream.local import machx, launcher
from dream.local.settings import (
    BY_NAME, available_controls, parse_value, read_session_options,
    server_args, session_options, validate_options,
)
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.context_budget import ContextOverflow
from test_compaction import _FakeClient, _text_round


def backend(provider="machx", *, provider_metadata=None):
    return OpenAICompatBackend(
        provider=SimpleNamespace(key=provider, label=provider, base_url="http://x/v1",
                                 multimodal=False, api_key=lambda: "n"),
        model="fixture", system_prompt="system", tools=[], permission_cb=None,
        provider_metadata=provider_metadata,
    )


@pytest.mark.parametrize("key,value", [
    ("temperature", "nan"), ("temperature", "inf"), ("temperature", "-1"),
    ("top_k", "1.5"), ("top_k", "1025"), ("top_p", "0"), ("min_p", "1.01"),
    ("repeat_penalty", "0"), ("repeat_last_n", "513"), ("presence_penalty", "3"),
    ("threads", "0"), ("parallel", "5"), ("max_tokens", "0"), ("slot_ctx", "8"),
    ("context_overflow", "shift"), ("stop", '"END"'), ("stop", '[""]'),
    ("stop", '[1]'), ("stop", '["a","b","c","d","e"]'),
])
def test_invalid_value(key, value):
    with pytest.raises(ValueError):
        parse_value(BY_NAME[key], value)


def test_values_and_literal_stop_strings():
    stops = ['$(touch /tmp/should-not-exist)', 'END "quoted"', '\nUSER:']
    options = validate_options({"stop": stops, "temperature": 0, "prompt_cache": False})
    args = server_args(options)
    command = machx._command(["ie", "serve", "/tmp/a`b$.gguf", *args])
    assert shlex.split(command[-1].split(" && exec ", 1)[1]) == [
        "ie", "serve", "/tmp/a`b$.gguf", "--stop", stops[0], "--stop", stops[1],
        "--stop", stops[2], "--temp", "0.0", "--no-prompt-cache",
    ]
    with pytest.raises(ValueError):
        validate_options({"temperature": True})
    with pytest.raises(ValueError):
        validate_options({"slot_ctx": 9000}, ctx=8192)


def test_capabilities_remove_unsupported_controls():
    caps = {"sampling": ["temperature", "repeat_window"],
            "load": ["prompt_cache", "threads"], "features": {"prompt_cache": False}}
    assert {c.name for c in available_controls(caps)} == {
        "temperature", "repeat_last_n", "context_overflow", "threads"}


def test_session_is_model_and_provider_scoped(monkeypatch):
    monkeypatch.delenv("DREAM_MACHX_SESSION_OPTIONS", raising=False)
    with session_options({"temperature": .15, "max_tokens": 2345}, "fixture"):
        assert backend().temperature == .15
        assert backend("openai").temperature == .7
        assert read_session_options("different") == {}
        assert backend()._max_tokens(0) == 2345
    assert read_session_options("fixture") == {}


@pytest.mark.asyncio
async def test_selected_settings_reach_actual_stream_payload():
    options = {"temperature": .12, "top_k": 7, "top_p": .6, "min_p": .1,
               "repeat_penalty": 1.1, "repeat_last_n": 20,
               "presence_penalty": 0, "frequency_penalty": -.1,
               "seed": 123, "max_tokens": 1234, "stop": ["END"], "thinking": False}
    with session_options(options, "fixture"):
        b = backend()
    b.n_ctx = 16384
    b._client = _FakeClient([_text_round()])
    events = [event async for event in b.ask("hi")]
    payload = b._client.payloads[0]
    for key, value in options.items():
        if key != "thinking":
            assert payload[key] == value
    assert payload["enable_thinking"] is False
    assert "repetition_penalty" not in payload
    assert any(e.kind == "assistant_done" for e in events)


def test_overflow_error_preserves_history():
    with session_options({"context_overflow": "error", "max_tokens": 128}, "fixture"):
        b = backend()
    b.n_ctx = 2048
    b.messages += [{"role": "user", "content": "a" * 30000},
                   {"role": "assistant", "content": "b" * 30000},
                   {"role": "user", "content": "latest"}]
    before = copy.deepcopy(b.messages)
    assert b._maybe_compact() == []
    with pytest.raises(ContextOverflow):
        b._admit_request(b.messages, [])
    assert b.messages == before


@pytest.mark.asyncio
async def test_editor_validation_and_cancel(monkeypatch):
    answers = iter(["temperature", "nan", "temperature", "0.2", "stop", '["END"]', ""])
    async def ask(_, **kwargs):
        return next(answers)
    monkeypatch.setattr(launcher, "_ask", ask)
    errors = []
    renderer = SimpleNamespace(system=lambda _: None, error=errors.append)
    caps = {"sampling": ["temperature", "stop"]}
    options = await launcher._edit_options(Console(file=__import__('io').StringIO()), renderer, caps, 8192)
    assert options["temperature"] == .2
    assert options["stop"] == ["END"]
    assert errors
    answers = iter(["cancel"])
    assert await launcher._edit_options(Console(file=__import__('io').StringIO()), renderer, caps, 8192) is None


def test_serve_safe_arguments_and_context_reset(monkeypatch, tmp_path):
    monkeypatch.setattr(machx.config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(machx.config, "VAR_DIR", tmp_path)
    monkeypatch.setenv("DREAM_MACHX_CTX", "200000")
    calls = []
    monkeypatch.setattr(machx.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(pid=42))
    machx.serve(tmp_path / 'model $(bad).gguf', gpus=2, options={"stop": ['$(bad)'], "threads": 8})
    assert __import__('os').environ["DREAM_MACHX_CTX"] == "8192"
    args, kw = calls[0]
    assert kw["cwd"] == machx.MACHX_DIR
    tokens = shlex.split(args[-1].split(" && exec ", 1)[1])
    assert tokens[-4:] == ["--stop", "$(bad)", "--threads", "8"]


def test_capability_probe_rejects_old_binary(monkeypatch, tmp_path):
    monkeypatch.setattr(machx.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=1, stderr="unknown subcommand", stdout=""))
    with pytest.raises(ValueError, match="Rebuild"):
        machx.capabilities(tmp_path / "m.gguf")


def test_feature_combinations():
    with pytest.raises(ValueError, match="Speculation"):
        validate_options({"speculative": True, "temperature": .7})
    with pytest.raises(ValueError, match="Speculation"):
        validate_options({"speculative": True, "temperature": 0, "presence_penalty": .1})
    assert "--spec" in server_args({"speculative": True, "temperature": 0})
    assert "--spec" in server_args({"speculative": True, "temperature": 0,
                                   "presence_penalty": .1, "repeat_last_n": 0})
    for gpus in (None, 2):
        with pytest.raises(ValueError, match="one-GPU"):
            server_args({"int8_kv": True}, gpus=gpus)
    assert server_args({"int8_kv": True}, gpus=1) == ["--int8-kv"]
    assert {c.name for c in available_controls({"features": {"speculative": True}})} == {
        "context_overflow", "speculative", "spec_k"}


@pytest.mark.asyncio
async def test_launch_order_and_session_scope(monkeypatch, tmp_path):
    answers = iter(["advanced", "2", "16,384", "temperature", "0.25", ""])
    prompts = []
    async def ask(prompt, **kwargs):
        prompts.append(prompt)
        return next(answers)
    monkeypatch.setattr(launcher, "_ask", ask)
    monkeypatch.setattr(machx, "capabilities", lambda _: {
        "supported": True, "max_gpus": 2, "sampling": ["temperature", "max_tokens"]})
    from dream.local import model_defaults, preflight
    hardware_calls = 0
    def fixture_hardware():
        nonlocal hardware_calls
        hardware_calls += 1
        return {"devices": [{"integrated": False, "vram_total_mib": 32768}] * 2,
                "ram_total_gb": 64}
    monkeypatch.setattr(model_defaults, "inspect_hardware", fixture_hardware)
    monkeypatch.setattr(preflight, "check_live", lambda *args: SimpleNamespace(
        should_load=True, reason="fixture"))
    calls = []
    monkeypatch.setattr(machx, "serve", lambda path, **kw: calls.append(kw) or object())
    monkeypatch.setattr(machx, "wait_ready", lambda _: True)
    monkeypatch.setattr(machx, "served_model_id", lambda: "fixture")
    async def harness(*args, **kwargs):
        assert read_session_options("fixture")["temperature"] == .25
    monkeypatch.setattr(launcher, "_run_harness", harness)
    renderer = SimpleNamespace(system=lambda _: None, error=lambda msg: pytest.fail(msg))
    model = tmp_path / "tiny.gguf"
    model.write_bytes(b"test")
    await launcher.serve_and_run(Console(file=__import__('io').StringIO()), renderer,
                                 "fixture", model, keep_hot=True)
    assert hardware_calls == 1
    assert prompts[0].startswith("Enter = load")
    assert prompts[1].startswith("GPUs")
    assert prompts[2].startswith("context length")
    assert prompts[3].startswith("setting #")
    assert calls[0]["gpus"] == 2 and calls[0]["ctx"] == 16384
    assert calls[0]["options"]["temperature"] == .25
    assert read_session_options("fixture") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("error_type,error_code", [("server_error", None),
                                                   ("invalid_request_error", "context_length_exceeded")])
async def test_sse_error_is_failed_turn_without_committing_partial_reply(partial, error_type, error_code):
    from test_backend_resilience import _ScriptedClient, _sse
    b = backend()
    lines = []
    if partial:
        lines.append(_sse({"choices": [{"delta": {"content": "partial answer"}}]}))
        # A tool call delivered before the failure must never execute either.
        lines.append(_sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "bad", "arguments": "{}"}}
        ]}}]}))
    lines += [_sse({"error": {"type": error_type, "code": error_code, "message": "GLM fixture failure"}}),
              "data: [DONE]"]
    b._client = _ScriptedClient([lines])
    events = [event async for event in b.ask("hi")]
    assert any(e.kind == "error" and "GLM fixture failure" in e.data for e in events)
    assert not any(e.kind in ("assistant_done", "tool_result") for e in events)
    result = next(e.data for e in events if e.kind == "result")
    assert result["is_error"] is True
    assert result["subtype"] == ("context_overflow" if error_code else "server_error")
    assert [m["role"] for m in b.messages] == ["system", "user"]
    assert b._client.attempts == 1
    assert bool([e for e in events if e.kind == "text_delta"]) == partial


@pytest.mark.asyncio
async def test_nonstream_error_is_not_subagent_or_recovery_success():
    from test_backend_resilience import _PostClient, _backend, _researcher
    # Error must win even when a noncompliant response also includes choices.
    error = {"error": {"type": "runtime_error", "message": "GLM fixture failure"},
             "choices": [{"message": {"content": "must not be accepted"}}]}
    b = _backend(subs=_researcher())
    b._client = _PostClient([error])
    output, failed = await b._run_subagent("researcher", "test")
    assert failed and "GLM fixture failure" in output
    assert b._client.attempts == 1
    assert await b._salvage("failed round") == ""
    assert b._client.attempts == 2


@pytest.mark.parametrize("ctx", [0, 1, 8, 2**31])
def test_invalid_context_rejected_before_spawn(ctx, tmp_path):
    with pytest.raises(ValueError, match="Context"):
        machx.serve(tmp_path / "fixture.gguf", ctx=ctx)


@pytest.mark.parametrize("levels,default,thinking", [
    (["low", "high", "max"], "max", True),
    (["low", "medium", "high"], "medium", None),
    (["low", "high", "max"], "low", False),
    (["low", "medium", "high", "xhigh"], "xhigh", True),
])
def test_reasoning_controls_follow_capabilities(levels, default, thinking):
    caps = {"load": ["reasoning_effort"] + (["thinking"] if thinking is not None else []),
            "defaults": {"reasoning_effort": default, "thinking": thinking},
            "reasoning": {"effort_levels": levels, "default_effort": default,
                          "thinking_description": "Model thinking template"}}
    controls = {c.name: c for c in available_controls(caps)}
    effort = controls["reasoning_effort"]
    assert effort.choices == tuple(levels)
    assert effort.default == default
    assert all(level in effort.hint for level in levels)
    assert parse_value(effort, "default") == default
    with pytest.raises(ValueError, match="Choose"):
        parse_value(effort, "ultra")
    if thinking is None:
        assert "thinking" not in controls
    else:
        assert controls["thinking"].default is thinking
        assert "Model thinking template" in controls["thinking"].hint
    caps["reasoning"]["effort_levels"] = []
    assert "reasoning_effort" not in {c.name for c in available_controls(caps)}


@pytest.mark.asyncio
async def test_editor_shows_model_defaults_and_rejects_other_models_effort(monkeypatch):
    from io import StringIO
    answers = iter(["reasoning_effort", "medium", "reasoning_effort", "low", ""])
    async def ask(_, **kwargs):
        return next(answers)
    monkeypatch.setattr(launcher, "_ask", ask)
    output = StringIO()
    errors = []
    caps = {"load": ["thinking", "reasoning_effort"],
            "defaults": {"thinking": True, "reasoning_effort": "max"},
            "reasoning": {"effort_levels": ["low", "high", "max"], "default_effort": "max"}}
    result = await launcher._edit_options(Console(file=output, width=140),
        SimpleNamespace(system=lambda _: None, error=errors.append), caps, 8192)
    assert result["thinking"] is True
    assert result["reasoning_effort"] == "low"
    assert errors == ["Choose low, high, max"]
    assert "thinking" in output.getvalue() and "on" in output.getvalue()
    assert "reasoning_effort" in output.getvalue() and "max" in output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["low", "medium", "high", "max", "xhigh"])
async def test_local_reasoning_effort_reaches_launch_and_http_unchanged(effort):
    assert server_args({"reasoning_effort": effort}) == ["--reasoning-effort", effort]
    with session_options({"reasoning_effort": effort}, "fixture"):
        b = backend()
        assert backend("openai")._effort_params() == {}
        assert read_session_options("another-model") == {}
    b.set_effort(None)  # Initializing the global default must preserve the picker value.
    b.n_ctx = 16384
    b._client = _FakeClient([_text_round()])
    events = [event async for event in b.ask("hi")]
    assert b._client.payloads[0]["reasoning_effort"] == effort
    assert any(e.kind == "assistant_done" for e in events)
    assert read_session_options("fixture") == {}


def test_machx_effort_override_remains_raw_and_cloud_mapping_is_preserved():
    with session_options({"reasoning_effort": "max"}, "fixture"):
        b = backend()
    b.set_effort("low")
    assert b._effort_params() == {"reasoning_effort": "low"}
    b.set_effort(None)
    assert b._effort_params() == {"reasoning_effort": "max"}
    with pytest.raises(ValueError, match="not supported"):
        b.set_effort("ultra")
    assert b._effort_params() == {"reasoning_effort": "max"}
    cloud = backend("openai")
    cloud.set_effort("max")
    assert cloud._effort_params() == {"reasoning_effort": "xhigh"}
    cloud.set_effort(None)
    assert cloud._effort_params() == {}


@pytest.mark.parametrize("provider", ["machx", "openai"])
def test_explicit_reported_effort_uses_native_vocabulary(provider):
    b = backend(provider, provider_metadata={"reasoning_levels": ["low", "ultra"]})
    b.set_effort("ultra")
    assert b._effort_params() == {"reasoning_effort": "ultra"}
    with pytest.raises(ValueError, match="not supported"):
        b.set_effort("max")
    assert b._effort_params() == {"reasoning_effort": "ultra"}


@pytest.mark.asyncio
async def test_launch_effort_does_not_follow_a_model_switch():
    with session_options({"reasoning_effort": "max"}, "fixture"):
        b = backend()
    await b.set_model("another-model")
    assert b._effort_params() == {}


@pytest.mark.asyncio
async def test_global_medium_effort_alias_reaches_machx_as_medium():
    from dream.core.effort import normalize
    b = backend()
    b.set_effort(normalize("medium"))
    b.n_ctx = 16384
    b._client = _FakeClient([_text_round()])
    events = [event async for event in b.ask("hi")]
    assert b._client.payloads[0]["reasoning_effort"] == "medium"
    assert any(e.kind == "assistant_done" for e in events)
