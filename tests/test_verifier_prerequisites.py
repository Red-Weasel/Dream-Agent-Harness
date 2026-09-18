"""Known missing inspection tools cannot consume inference or produce PASS."""

import json
from types import SimpleNamespace

import httpx
import pytest

from dream.core.backends.openai_compat import OpenAICompatBackend
from dream.core.subagents import local_subagents

REQUIRED = ("show_html", "get_webview_logs", "save_screenshot", "see")


def viewer_backend(*, images=True, omitted=None, allowed=None):
    async def forbidden(args):
        raise AssertionError("No actual viewer operation expected from a terminal fixture response")

    tools = [SimpleNamespace(name=name, description=name,
                             input_schema={"type": "object", "properties": {}}, handler=forbidden)
             for name in REQUIRED if name != omitted]
    specs = local_subagents()
    if allowed is not None:
        specs["verifier"] = SimpleNamespace(prompt=specs["verifier"].prompt,
            description="verifier", tool_names=allowed)
    provider = SimpleNamespace(key="openai", label="Fixture", base_url="http://fixture.invalid",
                               multimodal=images, api_key=lambda: "fixture")
    b = OpenAICompatBackend(provider=provider, model="fixture", system_prompt="fixture",
                           tools=tools, permission_cb=None, subagents=specs)
    b.n_ctx = 16384
    b.messages.append({"role": "user", "content": "Check the delivered page visually."})
    return b


@pytest.mark.asyncio
@pytest.mark.parametrize("omitted", REQUIRED)
@pytest.mark.parametrize("directed", [False, True])
async def test_missing_required_tool_never_requests_a_verifier_response(omitted, directed):
    b = viewer_backend(omitted=omitted)
    requests = []

    def respond(req):
        requests.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "PASS"}, "finish_reason": "stop"}]})

    async with httpx.AsyncClient(base_url="http://fixture.invalid", transport=httpx.MockTransport(respond)) as client:
        b._client = client
        if directed:
            text, failed = await b._fork_verifier({"path": "deck.html", "task": "Check mobile layout"})
            assert failed and "unverified" in text and omitted in text
        else:
            b._verify_at_turn_end = "deck.html"
            events = await b._verifier_sweep()
            assert b._verify_at_turn_end is None
            assert b._pending_findings and "unverified" in b._pending_findings
            assert omitted in b._pending_findings
            assert all("verifier: PASS" not in str(event.data) for event in events)
    assert requests == []
    assert b.messages[-1]["content"] == "Check the delivered page visually."


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["filtered", "allowlist"])
async def test_registration_alone_cannot_bypass_disabled_vision(mode):
    b = viewer_backend(images=mode != "filtered",
                       allowed=REQUIRED[:-1] if mode == "allowlist" else None)
    # No client at all: dispatch would fail for the wrong reason.
    text, failed = await b._run_subagent("verifier", "Check the page")
    assert failed and "unverified" in text and "see" in text
    assert "unavailable" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["verifier", "filer"])
async def test_available_tools_keep_normal_route_without_claiming_image_qualification(agent):
    b = viewer_backend()
    calls = []

    def respond(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "No inspection performed in this scripted control."},
            "finish_reason": "stop"}]})

    async with httpx.AsyncClient(base_url="http://fixture.invalid", transport=httpx.MockTransport(respond)) as client:
        b._client = client
        text, failed = await b._run_subagent(agent, "Check the page")
    assert not failed and "No inspection performed" in text
    assert len(calls) == 1
    assert {s["function"]["name"] for s in calls[0]["tools"]} == (
        set(REQUIRED) if agent == "verifier" else set())
