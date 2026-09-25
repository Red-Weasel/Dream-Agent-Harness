"""DREAM-125: a reasoning cap for build turns. 2026-09-25 the local MiMo model spent 16,384-token
reasoning passes (22-28 minutes each) on internal geometry proofs during a Blender build, three of them
cut at the ceiling and lost. `ie serve` gives MiMo no reasoning budget and no effort levels (a
reasoning_effort is a 400 there): only `enable_thinking` on/off. So once a turn has made a change -- a
write, an edit, a consequential shell command -- each later request that follows tool results runs with
thinking off: the model plans with thinking, then iterates. Local MachX models that advertise the
`thinking` control only; DREAM_BUILD_THINKING_CAP=0 turns it off; the usage event says `thinking_capped`.
Only MiMo (`mimo_v2`): DeepSeek-V4.1 renders message 0 and past assistant turns by the flag, so a flip there
re-reads the whole prompt. A failed round keeps thinking (the model may need it to debug), and only a change
that succeeded makes a build turn."""
from __future__ import annotations
import json
from types import SimpleNamespace

import pytest
from test_compaction import _FakeClient, _backend, _sse, _text_round, _tool, _tool_round
from test_cache_friendly_head import Meter

pytestmark = pytest.mark.asyncio

WRITE = json.dumps({"path": "a.py", "content": "x"})


def _tools():
    async def ok(args):
        return {"content": [{"type": "text", "text": "ok"}]}
    return [_tool("read_file", ok), _tool("write_file", ok), _tool("run_bash", ok)]


def _b(monkeypatch, *, thinking=None, load=("thinking",), key="machx", tools=None, arch="mimo_v2"):
    monkeypatch.delenv("DREAM_BUILD_THINKING_CAP", raising=False)
    b = _backend(tools or _tools(), n_ctx=32768)
    b.provider.key = key
    if key == "machx":
        b._local_options = {} if thinking is None else {"thinking": thinking}
        b._local_options_model = b.model
        if thinking is not None:
            b._sampling["enable_thinking"] = thinking
        b._local_capabilities = {"architecture": arch, "load": list(load)}
    return b


def _with_usage(round_lines):
    return round_lines[:-1] + [_sse({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}),
                               round_lines[-1]]


def _thinking(b):
    return [p.get("enable_thinking", "absent") for p in b._client.payloads]


async def _turn(b, prompt="build it"):
    return [ev async for ev in b.ask(prompt)]


async def test_after_a_change_requests_that_follow_tool_results_run_without_thinking(monkeypatch):
    b = _b(monkeypatch)
    b._client = _FakeClient([_tool_round("read_file"), _tool_round("write_file", WRITE),
                             _tool_round("run_bash", json.dumps({"command": "ls"})), _text_round()])
    await _turn(b)
    # plan and orientation keep the engine's thinking; after the write every request iterates without it
    assert _thinking(b) == ["absent", "absent", False, False]


async def test_a_consequential_shell_command_makes_it_a_build_turn(monkeypatch):
    b = _b(monkeypatch, thinking=True)
    b._client = _FakeClient([_tool_round("run_bash", json.dumps({"command": "blender -b -P build.py"})),
                             _text_round()])
    await _turn(b)
    assert _thinking(b) == [True, False]


async def test_the_next_turn_plans_with_thinking_again(monkeypatch):
    b = _b(monkeypatch, thinking=True)
    b._client = _FakeClient([_tool_round("write_file", WRITE), _text_round()])
    await _turn(b)
    b._client = _FakeClient([_tool_round("read_file"), _text_round()])
    await _turn(b, "now explain it")
    assert _thinking(b) == [True, True]


async def test_the_usage_event_names_a_capped_request(monkeypatch):
    b = _b(monkeypatch)
    b.runtime_meter = Meter()
    b._client = _FakeClient([_with_usage(_tool_round("write_file", WRITE)), _with_usage(_text_round())])
    await _turn(b)
    usages = [u for _, u in b.runtime_meter.usages]
    assert len(usages) == 2
    assert "thinking_capped" not in usages[0] and usages[1]["thinking_capped"] is True


async def test_the_run_meter_writes_thinking_capped_on_the_usage_event(tmp_path):
    from dream.telemetry.runtime import RunMeter
    meter = RunMeter("s", 1, SimpleNamespace(max_run_tools=10, max_run_tokens=None, max_run_seconds=None),
                     path=tmp_path / "run.jsonl")
    meter.usage({"prompt_tokens": 5, "completion_tokens": 1, "thinking_capped": True})
    meter.usage({"prompt_tokens": 5, "completion_tokens": 1})
    first, second = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert first["event"] == "usage" and first["thinking_capped"] is True
    assert "thinking_capped" not in second


async def test_images_returned_after_a_change_still_count_as_following_tool_results(monkeypatch):
    async def render(args):
        return {"content": [{"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgo="}]}
    b = _b(monkeypatch, thinking=True, tools=[_tool("run_bash", render)])
    b.provider.multimodal = True
    b._client = _FakeClient([_tool_round("run_bash", json.dumps({"command": "blender -b -P render.py"})),
                             _text_round()])
    await _turn(b)
    assert b._client.payloads[1]["messages"][-1]["name"] == "dream_visual_evidence"
    assert _thinking(b) == [True, False]


def _payload_dumps(b):
    return [json.dumps(p, sort_keys=True) for p in b._client.payloads]


async def _run_both(monkeypatch, rounds, **kw):
    """The same turn with the cap on and with it off: returns both payload lists."""
    b_on = _b(monkeypatch, **kw)
    b_on._client = _FakeClient(rounds)
    await _turn(b_on)
    b_off = _b(monkeypatch, **kw)
    monkeypatch.setenv("DREAM_BUILD_THINKING_CAP", "0")
    b_off._client = _FakeClient(rounds)
    await _turn(b_off)
    return _payload_dumps(b_on), _payload_dumps(b_off)


async def test_the_setting_turns_it_off(monkeypatch):
    on, off = await _run_both(monkeypatch, [_tool_round("write_file", WRITE), _text_round()], thinking=True)
    assert on != off
    assert [json.loads(p).get("enable_thinking") for p in off] == [True, True]


async def test_remote_providers_are_untouched(monkeypatch):
    on, off = await _run_both(monkeypatch, [_tool_round("write_file", WRITE), _text_round()], key="openai")
    assert on == off and all("enable_thinking" not in json.loads(p) for p in on)


async def test_turns_without_a_change_are_untouched(monkeypatch):
    for rounds in ([_text_round()], [_tool_round("read_file"), _tool_round("read_file"), _text_round()]):
        on, off = await _run_both(monkeypatch, rounds, thinking=True)
        assert on == off


async def test_models_without_the_thinking_control_are_untouched(monkeypatch):
    on, off = await _run_both(monkeypatch, [_tool_round("write_file", WRITE), _text_round()], load=())
    assert on == off and all("enable_thinking" not in json.loads(p) for p in on)


async def test_thinking_already_off_stays_off_and_is_not_reported_as_capped(monkeypatch):
    b = _b(monkeypatch, thinking=False)
    b.runtime_meter = Meter()
    b._client = _FakeClient([_with_usage(_tool_round("write_file", WRITE)), _with_usage(_text_round())])
    await _turn(b)
    assert _thinking(b) == [False, False]
    assert all("thinking_capped" not in u for _, u in b.runtime_meter.usages)


async def test_other_thinking_architectures_are_untouched(monkeypatch):
    # DeepSeek-V4.1 puts "Reasoning Effort" at the start of message 0 only with thinking on and renders past
    # assistant turns by the flag: a flip there would re-read the whole conversation.
    for arch in ("deepseek_v41", "deepseek4", "glm5next", None):
        on, off = await _run_both(monkeypatch, [_tool_round("write_file", WRITE), _text_round()],
                                  thinking=True, arch=arch)
        assert on == off and [json.loads(p)["enable_thinking"] for p in on] == [True, True]


def _flaky_write(outcomes):
    """write_file whose calls go as `outcomes` says: "ok", "isError" (a failure result) or "raise"."""
    seq = iter(outcomes)

    async def handler(args):
        outcome = next(seq)
        if outcome == "raise":
            raise OSError("disk full")
        return {"content": [{"type": "text", "text": outcome}], **({"isError": True} if outcome == "isError" else {})}
    return [_tool("write_file", handler), _tool("read_file", handler)]


async def test_a_failed_round_keeps_thinking_and_a_later_success_caps_again(monkeypatch):
    b = _b(monkeypatch, thinking=True, tools=_flaky_write(["ok", "isError", "ok"]))
    b._client = _FakeClient([_tool_round("write_file", WRITE), _tool_round("write_file", WRITE),
                             _tool_round("write_file", WRITE), _text_round()])
    await _turn(b)
    assert _thinking(b) == [True, False, True, False]


async def test_a_raising_handler_keeps_thinking(monkeypatch):
    b = _b(monkeypatch, thinking=True, tools=_flaky_write(["ok", "raise"]))
    b._client = _FakeClient([_tool_round("write_file", WRITE), _tool_round("write_file", WRITE), _text_round()])
    await _turn(b)
    assert _thinking(b) == [True, False, True]


async def test_only_a_change_that_succeeded_makes_a_build_turn(monkeypatch):
    b = _b(monkeypatch, thinking=True, tools=_flaky_write(["isError", "ok"]))
    b._client = _FakeClient([_tool_round("write_file", WRITE), _tool_round("read_file"), _text_round()])
    await _turn(b)
    assert _thinking(b) == [True, True, True]
