"""Profiles must reduce real request overhead without losing tools or instructions."""
import asyncio
import json
from dataclasses import replace

import pytest

from dream import config
from dream.core.context_budget import ContextOverflow, admit
from dream.core.profiles import PROFILES, resolve_profile
from dream.core.providers import get_provider, Provider
from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core import tool_budget_schemas
from dream.telemetry.runtime import RunMeter, RunLimit
from dream.tools.native import NATIVE_TOOLS
from dream.tools.registry import _BASE_TOOLS


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    for key in ("DREAM_PROFILE", "DREAM_CONTEXT_WINDOW", "DREAM_MAX_PARALLEL"):
        monkeypatch.delenv(key, raising=False)


def backend(window=8192):
    b = OpenAICompatBackend(provider=get_provider("machx"), model="fixture", system_prompt="Follow the user.",
                            tools=list(_BASE_TOOLS) + list(NATIVE_TOOLS), permission_cb=None,
                            profile=PROFILES["lean"])
    b.n_ctx = window
    return b


@pytest.mark.parametrize("window", [8192, 16384])
def test_small_context_real_tools_and_discovery_fit(window):
    b = backend(window)
    schemas = b._request_tools()
    pinned = [s for s in b.tool_schemas if s["function"]["name"] in b._pinned]
    optional = [s for s in b.tool_schemas if s["function"]["name"] not in b._pinned]
    mandatory = pinned + [tool_budget_schemas.lookup_schema(optional, 0)]
    target = int(window * b.profile.schema_fraction)
    if tool_budget_schemas.measure(mandatory) > target:
        assert schemas == mandatory
    else:
        assert tool_budget_schemas.measure(schemas) <= target
        assert tool_budget_schemas.measure(schemas) <= window * .15
    names = {s["function"]["name"] for s in schemas}
    assert {"tool_schema", "read_file", "write_file", "run_bash"} <= names
    reader = next(s for s in schemas if s["function"]["name"] == "read_file")
    native_reader = next(t for t in NATIVE_TOOLS if t.name == "read_file")
    # Fit the real request without dropping paging/sheet arguments or their
    # constraints to pay for the discovery hatch.
    assert reader["function"]["parameters"] == native_reader.input_schema
    assert {"offset", "limit", "page_start", "page_count", "sheet"} <= set(reader["function"]["parameters"]["properties"])
    deferred = next(iter(b._deferred_now))
    text, failed = b._lookup_tool_schema(deferred)
    assert not failed and json.loads(text)["name"] == deferred
    assert deferred in {s["function"]["name"] for s in b._request_tools()}
    report = b._admit_request(b.messages + [{"role": "user", "content": "Build and verify a web page."}], b._request_tools())
    assert report.remaining >= 0 and report.tools > 0 and report.instructions > 0


def test_oversized_instructions_are_preserved_and_refused():
    messages = [{"role": "system", "content": "mandatory " * 8000}, {"role": "user", "content": "Keep my instructions"}]
    original = json.dumps(messages)
    with pytest.raises(ContextOverflow, match="no request was sent"):
        admit(messages, [], 8192, 2048)
    assert json.dumps(messages) == original


def test_server_window_cannot_be_overridden_upward():
    p = replace(PROFILES["lean"], context_limit=131072)
    assert p.window(8192) == 8192
    assert replace(p, context_limit=4096).window(8192) == 4096


def test_auto_local_endpoint_and_explicit_overrides():
    assert resolve_profile(get_provider("machx")).name == "lean"
    assert resolve_profile(get_provider("codex")).name == "frontier"
    p = Provider("openai", "local alias", "openai", base_url="http://127.0.0.1:8000/v1")
    assert resolve_profile(p).max_parallel == 1
    assert resolve_profile(p, "frontier", overrides={"max_parallel": 2}).max_parallel == 2
    with pytest.raises(ValueError):
        resolve_profile(p, overrides={"max_parallel": 100})


@pytest.mark.asyncio
async def test_delegation_capacity_is_enforced(monkeypatch):
    b = backend()
    b._task_slots = asyncio.Semaphore(2)
    active, peak = 0, 0
    async def run(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        return "ok", False, False
    monkeypatch.setattr(b, "_guarded_exec", run)
    await asyncio.gather(*(b._bounded_task({}, {}) for _ in range(12)))
    assert peak == 2


def test_run_meter_counts_delegation_without_prompt_capture(tmp_path):
    meter = RunMeter("s", 1, replace(PROFILES["lean"], max_run_tools=2), tmp_path / "trace.jsonl")
    meter.usage({"prompt_tokens": 100, "completion_tokens": 30})
    meter.usage({"prompt_tokens": 50, "completion_tokens": 20}, "verifier")
    meter.before_tool("read_file")
    meter.before_tool("write_file")
    with pytest.raises(RunLimit):
        meter.before_tool("run_bash")
    assert meter.summary()["prompt_tokens"] == 150
    assert meter.summary()["phases"]["verifier"]["output_tokens"] == 20
    assert "arguments" not in meter.path.read_text()


@pytest.mark.asyncio
async def test_images_reach_model_as_images_and_keep_user_instruction(monkeypatch):
    from types import SimpleNamespace
    from test_schema_deferral import _FakeClient, _text_round
    from dream.core.backends.openai_compat import _compact_messages, _visual_message
    async def image_tool(args):
        return {"content": [{"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgo="},
                            {"type": "text", "text": "fixture screenshot"}]}
    tool = SimpleNamespace(name="see", description="Image", input_schema={"type": "object"}, handler=image_tool)
    b = OpenAICompatBackend(provider=get_provider("openai"), model="fixture", system_prompt="System",
                            tools=[tool], permission_cb=None, profile=PROFILES["balanced"])
    content, failed = await b._exec_tool("see", {})
    assert not failed and content.images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    b.messages += [{"role": "user", "content": "Preserve my actual task"}, _visual_message(content.images)]
    _compact_messages(b.messages, 100)
    assert b.messages[1]["content"] == "Preserve my actual task"
    report = b._admit_request(b.messages, [])
    assert report.history < 5000


@pytest.mark.asyncio
async def test_context_binding_isolates_simultaneous_sessions():
    from dream.tools.context import ToolContext, bind_context, ctx
    async def work(name):
        with bind_context(ToolContext(None, None, None, name)):
            await asyncio.sleep(.01)
            return ctx().session_id
    assert await asyncio.gather(work("one"), work("two")) == ["one", "two"]


async def test_screenshot_is_in_the_next_actual_http_request():
    from types import SimpleNamespace
    from test_schema_deferral import _FakeClient, _text_round, _tool_round
    async def capture(args):
        return {"content": [{"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgo="}]}
    tool = SimpleNamespace(name="see", description="Screenshot", input_schema={"type": "object"}, handler=capture)
    b = OpenAICompatBackend(provider=get_provider("openai"), model="fixture", system_prompt="Inspect visible evidence.",
                            tools=[tool], permission_cb=None, profile=PROFILES["balanced"])
    b._client = _FakeClient([_tool_round("see"), _text_round("The image arrived.")])
    events = [event async for event in b.ask("Inspect this screenshot")]
    payload = b._client.payloads[1]
    evidence = next(message for message in payload["messages"] if message.get("name") == "dream_visual_evidence")
    assert evidence["content"][1]["image_url"]["url"] == "data:image/png;base64,iVBORw0KGgo="
    assert any(event.kind == "assistant_done" and event.data == "The image arrived." for event in events)


async def test_recovery_obeys_context_admission_and_run_budget(tmp_path):
    from test_salvage import _FakeClient
    b = backend()
    b._client = _FakeClient()
    b.messages = [{"role": "system", "content": "required " * 15000}]
    assert await b._salvage("fixture failure") == ""
    assert b._client.posted == []
    b.messages = [{"role": "system", "content": "small"}]
    b.runtime_meter = RunMeter("s", 1, replace(PROFILES["lean"], max_run_tools=1))
    b.runtime_meter.before_tool("fixture")
    assert await b._salvage("budget limit") == "" and b._client.posted == []
