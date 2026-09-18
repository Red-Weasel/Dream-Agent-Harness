"""Task scope reaches the actual isolated verifier request, without a live model."""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_schema_deferral import _backend
from test_verifier_prerequisites import viewer_backend
from dream.core.subagents import local_subagents


@pytest.mark.asyncio
@pytest.mark.parametrize("user_task", [
    "Change only the heading to 'Arrival'. Check that it fits on mobile.",
    "Audit every slide, all navigation states and both viewport sizes.",
])
async def test_automatic_review_receives_exact_current_scope(user_task):
    b = viewer_backend()
    b.messages.extend([
        {"role": "user", "content": "OLD TASK: inspect all menus"},
        {"role": "assistant", "name": "dream_verifier_report", "content": "OLD FINDING"},
        {"role": "user", "content": user_task},
        {"role": "assistant", "content": "AUTHOR CLAIM: everything passed"},
        {"role": "user", "name": "dream_visual_evidence", "content": "IMAGE ONLY"},
    ])
    captured = []

    def respond(req):
        captured.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "PASS"}, "finish_reason": "stop"}]})

    async with httpx.AsyncClient(base_url="http://fixture.invalid", transport=httpx.MockTransport(respond)) as client:
        b._client = client
        b._verify_at_turn_end = "deck.html"
        await b._verifier_sweep()
    assert len(captured) == 1
    prompt = captured[0]["messages"][-1]["content"]
    assert user_task in prompt
    assert all(old not in prompt for old in ["OLD TASK", "OLD FINDING", "AUTHOR CLAIM", "IMAGE ONLY"])
    assert "one per state you can reach" not in prompt
    assert "show_html" in prompt and "get_webview_logs" in prompt
    assert "do not report PASS" in prompt


def test_missing_scope_is_explicit_and_directed_scope_is_preserved():
    b = _backend(subagents=local_subagents())
    prompt = b._verification_prompt("deck.html")
    assert "unavailable" in prompt and "do not report PASS" in prompt
    directed = b._verification_prompt("deck.html", task="Check the open menu at 390px")
    assert "Check the open menu at 390px" in directed
    assert "Directed check" in directed


def test_shared_verifier_contract_stops_after_required_coverage():
    prompt = local_subagents()["verifier"].prompt
    assert "Stop once" in prompt
    assert "explicitly requested" in prompt
    assert "incomplete" in prompt
