"""Real shipped Council UI, isolated HTTP fixtures, GPU-disabled Chromium."""
from __future__ import annotations

import asyncio
import copy

MODEL_IDS = ["main-model", "review-model", "new-main", "review-local", "draft-main", "retained-draft", "reviewer-model", "separate-main-model", "replacement-model", "gpt-6-astra"]

import pytest
from playwright.async_api import async_playwright, expect

from dream.gui.bus import EventBus
from dream.gui.server import StudioServer


STATUS = {
    "config": {"orchestrator": "openai", "advisors": ["claude"],
               "advisor_models": {"claude": "review-model"}, "max_concurrency": 2,
               "timeout_seconds": 120, "legal_review": False,
               "orchestrator_effort": None, "advisor_efforts": {}},
    "model": "main-model", "busy": False, "main_available": True, "warning": "",
    "choices": [{"key": "openai", "label": "Local HTTP", "available": True, "note": "Existing endpoint", "efforts": [], "effort_note": "Effort levels not reported"},
                {"key": "claude", "label": "Claude", "available": True, "note": "CLI installed", "efforts": ["low", "medium", "high", "xhigh", "max"], "effort_note": "Provider effort"},
                {"key": "codex", "label": "Codex", "available": True, "note": "CLI installed", "efforts": ["low", "medium", "high", "xhigh"], "effort_note": "Provider effort"},
                {"key": "gemini", "label": "Gemini", "available": False, "note": "CLI unavailable", "efforts": [], "effort_note": "Effort unavailable"}],
}

for choice in STATUS["choices"]:
    choice["models"] = [{"id": value, "label": "Astra" if value == "gpt-6-astra" else value} for value in MODEL_IDS]


class CouncilAPI:
    def __init__(self):
        self.status = copy.deepcopy(STATUS)
        self.calls = []
        self.headers = []
        self.fail = {}
        self.pause = {}
        self.disconnect = set()
        self.interrupted = True
        self.results = [{"advisor": "claude", "label": "Claude", "model": "review-model",
                         "answer": '<img src=x onerror="window.pwned=true">\nUse evidence.'},
                        {"advisor": "codex", "label": "Codex", "model": "", "error": "Advisor timed out", "answer": ""}]

    async def route(self, route):
        payload = route.request.post_data_json
        action = payload["action"]
        if action == "permission_mode_status":
            await route.fulfill(json={"ok": True, "result": {"mode": "auto", "modes": ["ask", "accept", "auto", "plan"], "labels": {"auto": "Auto"}}})
            return
        self.calls.append(payload)
        self.headers.append(route.request.headers)
        if action in self.disconnect:
            await route.abort("failed")
            return
        if action in self.pause:
            await self.pause[action].wait()
        if action in self.fail:
            status, error = self.fail[action]
            await route.fulfill(status=status, json={"ok": False, "error": error})
            return
        if action == "council_configure":
            self.status.update(config=payload["config"], model=payload["model"])
        if action == "interrupt":
            if self.interrupted:
                self.status["busy"] = False
            await route.fulfill(json={"ok": True, "result": {"interrupted": self.interrupted}})
            return
        result = ({"results": self.results} if action == "council_ask" else
                  {"completed": [payload.get("advisor", "claude")], "main_restored": True} if action == "council_work" else self.status)
        await route.fulfill(json={"ok": True, "result": result})


@pytest.fixture
async def council(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    server = StudioServer(EventBus(), session={"workspace": str(tmp_path), "provider": "test", "model": "fixture"})
    url = await server.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width": 1000, "height": 940}, reduced_motion="reduce")
            page.set_default_timeout(3000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            api = CouncilAPI()
            await page.route("**/api/control", api.route)
            await page.goto(url + "&companion=1")
            yield page, api, server, errors
            await browser.close()
    finally:
        await server.stop()


async def open_council(page):
    await page.get_by_role("button", name="Council", exact=True).click()
    await expect(page.get_by_label("Main provider")).to_have_value("openai")


async def test_configuration_is_explicit_and_reload_reads_applied_choices(council):
    page, api, server, errors = council
    await open_council(page)
    await page.get_by_label("Main provider").select_option("codex")
    await page.get_by_label("Main model", exact=True).select_option("new-main")
    await page.get_by_label("Enable Claude").uncheck()
    await page.get_by_label("Enable Local HTTP").check()
    await page.get_by_label("Local HTTP member model", exact=True).select_option("review-local")
    assert [c["action"] for c in api.calls] == ["council_status"]
    await expect(page.get_by_role("button", name="Review with council", exact=True)).to_be_disabled()
    await page.get_by_role("button", name="Apply Council").click()
    await expect(page.locator("#council-feedback")).to_contain_text("applied")
    assert api.calls[-1] == {"action": "council_configure", "model": "new-main", "config": {
        "orchestrator": "codex", "advisors": ["openai"], "advisor_models": {"openai": "review-local"},
        "max_concurrency": 2, "timeout_seconds": 120, "legal_review": False,
        "orchestrator_effort": None, "advisor_efforts": {}}}
    assert all(h.get("x-dream-token") == server.token for h in api.headers)
    await page.reload()
    await page.get_by_role("button", name="Council", exact=True).click()
    await expect(page.get_by_label("Main provider")).to_have_value("codex")
    await expect(page.get_by_label("Main model", exact=True)).to_have_value("new-main")
    await expect(page.get_by_label("Enable Local HTTP")).to_be_checked()
    assert not errors


async def test_failed_save_and_refresh_preserve_draft_and_question(council):
    page, api, _, errors = council
    await open_council(page)
    await page.get_by_label("Main model", exact=True).select_option("draft-main")
    await page.get_by_label("Question for advisors").fill("Keep this question")
    api.fail["council_configure"] = (409, "Wait for the active turn")
    await page.get_by_role("button", name="Apply Council").click()
    await expect(page.get_by_role("alert")).to_contain_text("Wait for the active turn")
    await expect(page.get_by_label("Main model", exact=True)).to_have_value("draft-main")
    await page.get_by_role("button", name="Refresh Council").click()
    await expect(page.locator("#council-feedback")).to_contain_text("draft")
    await expect(page.get_by_label("Main model", exact=True)).to_have_value("draft-main")
    await expect(page.get_by_label("Question for advisors")).to_have_value("Keep this question")
    await page.get_by_role("button", name="Close Council").click()
    await page.get_by_role("button", name="Council", exact=True).click()
    await expect(page.get_by_label("Main model", exact=True)).to_have_value("draft-main")
    await page.get_by_role("button", name="Use current configuration").click()
    await expect(page.get_by_label("Main model", exact=True)).to_have_value("main-model")
    assert not errors


async def test_one_and_all_advice_render_failures_and_hostile_text_safely(council):
    page, api, _, errors = council
    await open_council(page)
    await page.get_by_label("Question for advisors").fill("Review this plan")
    await page.get_by_role("button", name="Review with selected member").click()
    await expect(page.locator("#council-results")).to_contain_text("Use evidence.")
    assert api.calls[-1] == {"action": "council_ask", "question": "Review this plan", "advisor": "claude"}
    await expect(page.locator("#council-results")).to_contain_text("Advisor timed out")
    assert await page.locator("#council-results img").count() == 0
    assert await page.evaluate("window.pwned") is None
    await page.get_by_role("button", name="Review with council", exact=True).click()
    await expect(page.locator("#council-feedback")).to_contain_text("finished")
    assert api.calls[-1] == {"action": "council_ask", "question": "Review this plan"}
    api.fail["council_ask"] = (500, "Provider disconnected")
    await page.get_by_role("button", name="Review with council", exact=True).click()
    await expect(page.get_by_role("alert")).to_contain_text("Provider disconnected")
    await expect(page.get_by_label("Question for advisors")).to_have_value("Review this plan")
    await expect(page.get_by_role("button", name="Review with council", exact=True)).to_be_enabled()
    assert not errors


async def test_busy_status_stop_and_unavailable_provider(council):
    page, api, _, errors = council
    api.status["busy"] = True
    await open_council(page)
    await expect(page.get_by_role("button", name="Apply Council")).to_be_disabled()
    await expect(page.get_by_label("Enable Gemini")).to_be_disabled()
    await page.get_by_role("button", name="Stop active operation").click()
    await expect(page.locator("#council-feedback")).to_contain_text("Stop requested")
    assert api.calls[-1]["action"] == "interrupt"
    await page.get_by_role("button", name="Refresh Council").click()
    await expect(page.locator("#council-session-state")).to_contain_text("Ready")
    await page.get_by_label("Question for advisors").fill("Question")
    gate = asyncio.Event()
    api.pause["council_ask"] = gate
    await page.get_by_role("button", name="Review with council", exact=True).click()
    await expect(page.get_by_role("button", name="Review with selected member")).to_be_disabled()
    await expect(page.get_by_role("button", name="Stop active operation")).to_be_enabled()
    gate.set()
    await expect(page.locator("#council-feedback")).to_contain_text("finished")
    assert not errors


async def test_keyboard_mobile_and_status_failure_recovery(council):
    page, api, _, errors = council
    await page.set_viewport_size({"width": 390, "height": 844})
    api.fail["council_status"] = (401, "Expired")
    await page.get_by_role("button", name="Council", exact=True).click()
    await expect(page.get_by_role("alert")).to_contain_text("token")
    del api.fail["council_status"]
    await page.get_by_role("button", name="Refresh Council").click()
    await expect(page.get_by_label("Main model", exact=True)).to_have_value("main-model")
    assert await page.locator("#dream-council").evaluate("e => e.scrollWidth <= e.clientWidth")
    await page.screenshot(path="/tmp/dream-council-mobile.png")
    await page.keyboard.press("Escape")
    await expect(page.get_by_role("button", name="Council", exact=True)).to_be_focused()
    assert not errors


async def test_effort_choices_propagate_without_inventing_unknown_levels(council):
    page, api, _, errors = council
    await open_council(page)
    await expect(page.get_by_label("Main effort", exact=True)).to_be_disabled()
    await page.get_by_label("Main provider").select_option("codex")
    await page.get_by_label("Main effort", exact=True).select_option("high")
    await page.get_by_label("Claude member effort").select_option("max")
    assert [c["action"] for c in api.calls] == ["council_status"]
    await page.get_by_role("button", name="Apply Council").click()
    await expect(page.locator("#council-feedback")).to_contain_text("applied")
    assert api.calls[-1]["config"]["orchestrator_effort"] == "high"
    assert api.calls[-1]["config"]["advisor_efforts"] == {"claude": "max"}
    await page.locator(".council-scroll").evaluate("e => { e.scrollTop = 0; }")
    await page.screenshot(path="/tmp/dream-council-desktop.png")
    await page.reload()
    await page.get_by_role("button", name="Council", exact=True).click()
    await expect(page.get_by_label("Main effort", exact=True)).to_have_value("high")
    await expect(page.get_by_label("Claude member effort")).to_have_value("max")
    await page.get_by_label("Main provider").select_option("openai")
    await expect(page.get_by_label("Main effort", exact=True)).to_have_value("")
    await expect(page.get_by_label("Main effort", exact=True)).to_be_disabled()
    assert not errors


async def test_disconnect_requires_status_refresh_before_repeating_action(council):
    page, api, _, errors = council
    await open_council(page)
    await page.get_by_label("Main model", exact=True).select_option("retained-draft")
    api.disconnect.add("council_configure")
    await page.get_by_role("button", name="Apply Council").click()
    await expect(page.get_by_role("alert")).to_contain_text("may still be running")
    await expect(page.get_by_role("button", name="Apply Council")).to_be_disabled()
    await expect(page.get_by_label("Main model", exact=True)).to_have_value("retained-draft")
    api.disconnect.clear()
    await page.get_by_role("button", name="Refresh Council").click()
    await expect(page.get_by_role("button", name="Apply Council")).to_be_enabled()
    await expect(page.get_by_label("Main model", exact=True)).to_have_value("retained-draft")
    assert len([c for c in api.calls if c["action"] == "council_configure"]) == 1
    assert not errors


async def test_inherited_legal_review_is_preserved_with_explicit_source_limit(council):
    page, api, _, errors = council
    api.status["config"]["legal_review"] = True
    await open_council(page)
    await expect(page.locator("#council-legal-note")).to_contain_text("sources")
    await page.get_by_label("Question for advisors").fill("Review the legal claim")
    await expect(page.get_by_role("button", name="Review with council", exact=True)).to_be_disabled()
    await page.get_by_label("Main model", exact=True).select_option("new-main")
    await page.get_by_role("button", name="Apply Council").click()
    await expect(page.locator("#council-feedback")).to_contain_text("applied")
    assert api.calls[-1]["config"]["legal_review"] is True
    assert not errors


async def test_same_provider_advisor_preserves_independent_model_and_effort(council):
    page, api, _, errors = council
    api.status["config"].update(orchestrator="codex", advisors=["codex"],
                                advisor_models={"codex": "reviewer-model"},
                                advisor_efforts={"codex": "xhigh"})
    await page.get_by_role("button", name="Council", exact=True).click()
    await expect(page.get_by_label("Enable Codex")).to_be_checked()
    await expect(page.get_by_label("Codex member model", exact=True)).to_have_value("reviewer-model")
    await expect(page.get_by_label("Codex member effort")).to_have_value("xhigh")
    await page.get_by_label("Main model", exact=True).select_option("separate-main-model")
    assert [c["action"] for c in api.calls] == ["council_status"]
    await page.get_by_role("button", name="Apply Council").click()
    await expect(page.locator("#council-feedback")).to_contain_text("applied")
    assert api.calls[-1]["config"]["advisors"] == ["codex"]
    assert api.calls[-1]["config"]["advisor_models"] == {"codex": "reviewer-model"}
    assert api.calls[-1]["config"]["advisor_efforts"] == {"codex": "xhigh"}
    assert api.calls[-1]["model"] == "separate-main-model"
    assert not errors


async def test_disconnected_main_warning_does_not_claim_ready(council):
    page, api, _, errors = council
    api.status.update(main_available=False, warning="Main connection unavailable. Start a new Dream application.")
    await open_council(page)
    await expect(page.locator("#council-session-state")).to_contain_text("disconnected")
    await expect(page.locator("#council-main-warning")).to_contain_text("Start a new Dream application")
    await expect(page.get_by_role("button", name="Apply Council")).to_be_disabled()
    await page.get_by_label("Question for advisors").fill("Review this plan")
    await expect(page.get_by_role("button", name="Review with council", exact=True)).to_be_disabled()
    api.status.update(main_available=True, warning="")
    await page.get_by_role("button", name="Refresh Council").click()
    await expect(page.locator("#council-session-state")).to_contain_text("Ready")
    await expect(page.locator("#council-main-warning")).to_be_hidden()
    assert not errors


async def test_stop_false_response_does_not_claim_cancellation(council):
    page, api, _, errors = council
    api.status["busy"] = True
    api.interrupted = False
    await open_council(page)
    await page.get_by_role("button", name="Stop active operation").click()
    await expect(page.locator("#council-feedback")).to_contain_text("No cancellable operation")
    await expect(page.locator("#council-feedback")).not_to_contain_text("Stop requested")
    assert not errors


async def test_applying_configuration_does_not_offer_stop(council):
    page, api, _, errors = council
    await open_council(page)
    gate = asyncio.Event()
    api.pause["council_configure"] = gate
    await page.get_by_label("Main model", exact=True).select_option("replacement-model")
    await page.get_by_role("button", name="Apply Council").click()
    try:
        await expect(page.locator("#council-session-state")).to_contain_text("Applying")
        await expect(page.get_by_role("button", name="Stop active operation")).to_be_hidden()
        assert not any(call["action"] == "interrupt" for call in api.calls)
    finally:
        gate.set()
    await expect(page.locator("#council-feedback")).to_contain_text("applied")
    assert not errors


async def test_friendly_model_dropdown_and_active_assignment(council):
    page, api, _, errors = council
    await open_council(page)
    await page.get_by_label("Main provider").select_option("codex")
    await page.get_by_label("Main model", exact=True).select_option(label="Astra")
    await page.get_by_role("button", name="Apply Council").click()
    await expect(page.locator("#council-feedback")).to_contain_text("applied")
    assert api.calls[-1]["model"] == "gpt-6-astra"
    await page.get_by_label("Question for advisors").fill("Improve the saved animation")
    await page.get_by_role("button", name="Assign to selected member", exact=True).click()
    await expect(page.locator("#council-feedback")).to_contain_text("returned")
    assert api.calls[-1] == {"action": "council_work", "question": "Improve the saved animation", "advisor": "claude"}
    assert not errors


async def test_custom_model_remains_available(council):
    page, api, _, errors = council
    await open_council(page)
    await page.get_by_label("Main model", exact=True).select_option("__custom__")
    await page.get_by_label("Main model custom ID", exact=True).fill("private-model")
    await page.get_by_role("button", name="Apply Council").click()
    await expect(page.locator("#council-feedback")).to_contain_text("applied")
    assert api.calls[-1]["model"] == "private-model"
    assert not errors


async def test_member_custom_model_and_model_specific_effort(council):
    page, api, _, errors = council
    api.status["choices"][1]["models"][0]["efforts"] = []
    await open_council(page)
    await page.get_by_label("Claude member model", exact=True).select_option("main-model")
    await expect(page.get_by_label("Claude member effort", exact=True)).to_be_disabled()
    await page.get_by_label("Claude member model", exact=True).select_option("__custom__")
    await page.get_by_label("Claude member model custom ID", exact=True).fill("custom-claude")
    await page.get_by_role("button", name="Apply Council").click()
    await expect(page.locator("#council-feedback")).to_contain_text("applied")
    assert api.calls[-1]["config"]["advisor_models"] == {"claude": "custom-claude"}
    assert not errors


async def test_active_work_disconnect_never_replays(council):
    page, api, _, errors = council
    await open_council(page)
    await page.get_by_label("Question for advisors").fill("Polish")
    api.disconnect.add("council_work")
    await page.get_by_role("button", name="Run team relay", exact=True).click()
    await expect(page.get_by_role("alert")).to_contain_text("may still be running")
    await expect(page.get_by_role("button", name="Run team relay", exact=True)).to_be_disabled()
    api.disconnect.clear()
    await page.get_by_role("button", name="Refresh Council").click()
    await expect(page.get_by_role("button", name="Run team relay", exact=True)).to_be_enabled()
    assert len([c for c in api.calls if c["action"] == "council_work"]) == 1
    assert not errors
