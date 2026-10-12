"""The Sign in section of the shipped Council dialog (DREAM-246): isolated HTTP fixtures, GPU-disabled Chromium.

The page's /api/control calls are answered by this file, so no sign-in window, process or provider is involved.

What passing here does and does not show: every result is conditional on these fixtures. The desktop window's answers
are given by the test through the request record, the Council status is a fixed dictionary, and the browser is
headless Chromium. Nothing here opens a real sign-in window, runs a provider CLI or checks an account, so a pass says
the page behaves correctly for these answers, not that sign-in works on a real desktop.
"""
from __future__ import annotations

import asyncio
import copy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright, expect

from dream.core import provider_login as pl
from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from chat_more import more

COUNCIL = {
    "config": {"orchestrator": "openai", "advisors": ["claude"], "advisor_models": {}, "max_concurrency": 2,
               "timeout_seconds": 120, "legal_review": False, "orchestrator_effort": None, "advisor_efforts": {}},
    "model": "main-model", "busy": False, "main_available": True, "warning": "",
    "choices": [{"key": "openai", "label": "Local HTTP", "available": True, "note": "", "efforts": [],
                 "effort_note": "", "models": []},
                {"key": "claude", "label": "Claude", "available": True, "note": "", "efforts": [],
                 "effort_note": "", "models": []}],
}


class API:
    """Council answers from a fixed status; sign-in answers from the real request record, which the test answers
    in place of the desktop window."""

    def __init__(self, desktop=True):
        self.council = copy.deepcopy(COUNCIL)
        self.requests = pl.LoginRequests(desktop)
        self.calls, self.login_calls, self.fail, self.pause = [], [], {}, {}
        self.garble = {}      # action -> (HTTP status, body text): an answer the page cannot read; nothing is recorded

    async def route(self, route):
        payload = route.request.post_data_json
        action = payload["action"]
        if action == "permission_mode_status":
            await route.fulfill(json={"ok": True, "result": {"mode": "auto", "modes": ["auto"], "labels": {"auto": "Auto"}}})
            return
        if action in pl.LOGIN_ACTIONS:
            self.login_calls.append(payload)
            if action in self.fail:
                status, error = self.fail[action]
                await route.fulfill(status=status, json={"ok": False, "error": error})
                return
            if action in self.garble:
                status, body = self.garble[action]
                await route.fulfill(status=status, content_type="text/html", body=body)
                return
            try:
                await route.fulfill(json={"ok": True, "result": self.requests.control(payload)})
            except ValueError as exc:
                await route.fulfill(status=400, json={"error": str(exc)})
            return
        self.calls.append(payload)
        if action in self.pause:
            await self.pause[action].wait()
        result = ({"completed": ["claude"], "main_restored": True} if action == "council_work" else self.council)
        await route.fulfill(json={"ok": True, "result": result})


async def open_page(tmp_path, monkeypatch, api):
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    server = StudioServer(EventBus(), session={"workspace": str(tmp_path), "provider": "test", "model": "fixture"})
    url = await server.start()
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, args=["--disable-gpu"])
    page = await browser.new_page(viewport={"width": 1000, "height": 940}, reduced_motion="reduce")
    page.set_default_timeout(4000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.route("**/api/control", api.route)
    await page.goto(url.replace("/#", "/?companion=1#"))

    async def close():
        await browser.close()
        await playwright.stop()
        await server.stop()
    return page, errors, close


@pytest.fixture
async def desktop(tmp_path, monkeypatch):
    api = API(desktop=True)
    page, errors, close = await open_page(tmp_path, monkeypatch, api)
    try:
        yield page, api, errors
    finally:
        await close()


@pytest.fixture
async def plain(tmp_path, monkeypatch):
    api = API(desktop=False)
    page, errors, close = await open_page(tmp_path, monkeypatch, api)
    try:
        yield page, api, errors
    finally:
        await close()


async def open_council(page):
    await more(page, "dream-council-open")
    await expect(page.get_by_label("Main provider")).to_have_value("openai")


def start(page):
    return page.get_by_role("button", name="Open sign-in window")


async def test_all_four_native_providers_are_listed_outside_the_council_fields(desktop):
    page, api, errors = desktop
    await open_council(page)
    options = page.locator("#council-login-provider option")
    await expect(options).to_have_text(["Claude", "ChatGPT (Codex)", "Grok", "Gemini (Antigravity)"])
    assert await options.evaluate_all("nodes => nodes.map(node => node.value)") == list(pl.TARGET_KEYS)
    assert await page.locator("#council-fields #council-signin").count() == 0
    assert await page.locator("#council-config-form #council-signin").count() == 0
    # The list's name is exactly "Provider": a wrapping label alone would be named with every option's text too.
    chooser = page.get_by_label("Provider", exact=True)
    await expect(chooser).to_have_count(1)
    await expect(chooser).to_have_id("council-login-provider")
    await expect(chooser).to_have_attribute("aria-label", "Provider")
    await expect(page.locator('#council-signin label[for="council-login-provider"]')).to_have_count(1)
    await expect(page.locator("#council-signin label > #council-login-provider")).to_have_count(1)   # layout unchanged
    await chooser.select_option("antigravity")
    await expect(page.locator("#council-login-note")).to_contain_text("CLI sign-in is separate from desktop sign-in")
    await expect(page.get_by_label("Main provider")).to_have_value("openai")      # the main agent is not touched
    await expect(page.locator("#council-draft")).to_have_text("")                 # and no Council draft was made
    assert not errors


async def test_sign_in_sends_only_the_provider_and_reports_the_window_not_the_account(desktop):
    page, api, errors = desktop
    await open_council(page)
    await page.get_by_label("Provider", exact=True).select_option("codex")
    await start(page).click()
    feedback = page.locator("#council-login-feedback")
    await expect(feedback).to_contain_text("Opening a separate sign-in window for ChatGPT (Codex)")
    await expect(start(page)).to_be_disabled()
    asks = [call for call in api.login_calls if call["action"] == "provider_login"]
    assert asks == [{"action": "provider_login", "provider": "codex"}]
    waiting = api.requests.desktop_view()
    api.requests.ack(waiting["id"], "codex", "launched")          # what the desktop window would answer
    await expect(feedback).to_contain_text("A separate sign-in window is open")
    await expect(feedback).to_contain_text("did not check whether you are signed in")
    await expect(feedback).to_have_attribute("role", "status")
    await expect(start(page)).to_be_enabled()
    for call in api.login_calls:
        assert set(call) <= {"action", "provider", "id"}
    assert [c["action"] for c in api.calls if c["action"] != "council_status"] == []      # no Council action ran
    assert not errors


async def test_sign_in_stays_available_while_council_is_busy_and_while_work_is_running(desktop):
    page, api, errors = desktop
    api.council["busy"] = True
    await open_council(page)
    await expect(page.get_by_role("button", name="Apply Council")).to_be_disabled()
    await expect(start(page)).to_be_enabled()
    api.council["busy"] = False
    await page.get_by_role("button", name="Refresh Council").click()
    await expect(page.locator("#council-session-state")).to_contain_text("Ready")
    await page.get_by_label("Question for advisors").fill("Polish it")
    gate = asyncio.Event()
    api.pause["council_work"] = gate
    await page.get_by_role("button", name="Assign to selected member").click()
    # The Council form is locked. Playwright's to_be_disabled() does not apply to a fieldset itself, so the lock is
    # read from the fieldset's own attribute and from a control inside it that a person would actually try to use.
    fields = page.locator("#council-fields")
    await expect(fields).to_have_attribute("disabled", "")
    await expect(fields).to_have_js_property("disabled", True)
    await expect(page.get_by_label("Main provider")).to_be_disabled()
    await expect(page.get_by_label("Enable Claude")).to_be_disabled()
    await expect(page.get_by_label("Provider", exact=True)).to_be_enabled()       # sign-in is outside that lock
    await expect(start(page)).to_be_enabled()
    await start(page).click()
    await expect(page.locator("#council-login-feedback")).to_contain_text("Opening a separate sign-in window for Claude")
    assert api.requests.desktop_view()["provider"] == "anthropic"
    await expect(page.locator("#council-session-state")).to_contain_text("working")      # the work was not disturbed
    api.requests.ack(api.requests.desktop_view()["id"], "anthropic", "busy")
    await expect(page.locator("#council-login-feedback")).to_contain_text("Another sign-in window is already open")
    gate.set()
    await expect(page.locator("#council-feedback")).to_contain_text("finished")
    assert not errors


@pytest.mark.parametrize("answer, text", [("failed", "could not start"), ("unavailable", "was not found")])
async def test_a_window_that_did_not_open_is_an_error_with_focus_and_the_manual_way(desktop, answer, text):
    page, api, errors = desktop
    await open_council(page)
    await page.get_by_label("Provider", exact=True).select_option("grok")
    await start(page).click()
    await expect(page.locator("#council-login-feedback")).to_contain_text("Opening")
    api.requests.ack(api.requests.desktop_view()["id"], "grok", answer)
    feedback = page.locator("#council-login-feedback")
    await expect(feedback).to_contain_text(text)
    await expect(feedback).to_have_attribute("role", "alert")
    await expect(feedback).to_be_focused()
    await expect(start(page)).to_be_enabled()                     # trying again is possible
    assert not errors


async def test_a_refused_or_unauthorized_request_is_shown_and_nothing_is_claimed(desktop):
    page, api, errors = desktop
    await open_council(page)
    api.fail["provider_login"] = (400, "Another sign-in request is still waiting for the desktop window.")
    await start(page).click()
    feedback = page.locator("#council-login-feedback")
    await expect(feedback).to_contain_text("still waiting")
    await expect(feedback).to_have_attribute("role", "alert")
    await expect(feedback).to_be_focused()
    api.fail["provider_login"] = (401, "unauthorized")
    await start(page).click()
    await expect(feedback).to_contain_text("no longer authorized")
    assert api.requests.desktop_view() is None
    await expect(page.locator("#council-feedback")).not_to_have_class("council-error")    # Council's own state is separate
    assert not errors


@pytest.mark.parametrize("status, body", [(200, "<html>not an answer</html>"), (200, '{"ok": true}'),
                                          (502, "<html>bad gateway</html>")])
async def test_an_answer_that_cannot_be_read_is_an_unknown_outcome_and_is_not_asked_again(desktop, status, body):
    page, api, errors = desktop
    await open_council(page)
    api.garble["provider_login"] = (status, body)
    await start(page).click()
    feedback = page.locator("#council-login-feedback")
    await expect(feedback).to_contain_text("could not be read")
    await expect(feedback).to_contain_text("not known whether a sign-in window opened")
    await expect(feedback).to_contain_text("look for one before trying again")
    await expect(feedback).not_to_contain_text("Request failed")          # the request was sent: nothing says it was not
    await expect(feedback).not_to_contain_text("Done")
    await expect(feedback).to_have_attribute("role", "alert")
    await expect(start(page)).to_be_enabled()                             # trying again is the person's choice
    assert [c for c in api.login_calls if c["action"] == "provider_login"] == [
        {"action": "provider_login", "provider": "anthropic"}]            # asked once, never again by the page
    assert not errors


async def test_the_sign_in_text_says_what_dream_does_with_the_window_and_promises_nothing_about_credentials(desktop):
    page, api, errors = desktop
    await open_council(page)
    text = page.locator("#council-signin > .council-help").first
    await expect(text).to_contain_text("does not read or save what is typed or shown in it")
    await expect(text).to_contain_text("does not send it to agents")
    await expect(text).to_contain_text("checks account status separately from opening the sign-in window")
    await expect(text).not_to_contain_text("never sees")
    await expect(text).not_to_contain_text("credentials")
    assert not errors


async def test_outside_the_desktop_the_button_is_disabled_and_the_manual_command_is_explained(plain):
    page, api, errors = plain
    await open_council(page)
    await expect(start(page)).to_be_disabled()
    note = page.locator("#council-login-note")
    await expect(note).to_contain_text("not inside the Dream desktop window")
    await expect(note).to_contain_text("claude auth login")
    await page.get_by_label("Provider", exact=True).select_option("grok")
    await expect(note).to_contain_text("grok login --oauth")
    assert await page.locator("#council-login-provider").get_attribute("aria-describedby") == "council-login-note"
    assert [call for call in api.login_calls if call["action"] == "provider_login"] == []
    assert not errors


async def test_keyboard_reaches_the_provider_list_and_the_button(desktop):
    page, api, errors = desktop
    await open_council(page)
    await page.locator("#council-login-provider").focus()
    await page.keyboard.press("Tab")
    await expect(start(page)).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(page.locator("#council-login-feedback")).to_contain_text("Opening a separate sign-in window for Claude")
    assert not errors


async def test_the_section_says_which_window_the_desktop_reported_open_and_still_takes_a_request(desktop):
    page, api, errors = desktop
    await open_council(page)
    await page.get_by_label("Provider", exact=True).select_option("codex")
    await start(page).click()
    await expect(page.locator("#council-login-feedback")).to_contain_text("Opening")
    first = api.requests.desktop_view()
    api.requests.ack(first["id"], "codex", "launched")
    note = page.locator("#council-login-note")
    await expect(note).to_contain_text("A sign-in window is open for ChatGPT (Codex)")
    await expect(note).to_contain_text("Finish or close it before opening another")
    # A second request is still made (the desktop window is what knows), and its refusal leaves the first on record.
    await page.get_by_label("Provider", exact=True).select_option("grok")
    await expect(start(page)).to_be_enabled()
    await start(page).click()
    await expect(page.locator("#council-login-feedback")).to_contain_text("Opening a separate sign-in window for Grok")
    api.requests.ack(api.requests.desktop_view()["id"], "grok", "busy")
    await expect(page.locator("#council-login-feedback")).to_contain_text("Another sign-in window is already open")
    assert api.requests.status(first["id"])["request"]["status"] == "launched"
    api.requests.ack(first["id"], "codex", "closed")
    await page.get_by_label("Provider", exact=True).select_option("antigravity")
    await start(page).click()
    await expect(page.locator("#council-login-feedback")).to_contain_text("Opening a separate sign-in window for Gemini")
    api.requests.ack(api.requests.desktop_view()["id"], "antigravity", "failed")
    await expect(page.locator("#council-login-feedback")).to_contain_text("could not start")
    await expect(note).not_to_contain_text("A sign-in window is open")
    for call in api.login_calls:
        assert set(call) <= {"action", "provider", "id"}
    assert not errors


# --- /login typed into a prompt box: the chat's and the Nested view's ------------------------------------------------
#
# A real Studio server answers these, in a session that says it runs in the desktop window. The test answers in place
# of the desktop window through the server's own request record. The session's message callbacks only record: a
# /login must never reach them. HOME and CODEX_HOME are temporary; no provider file or CLI is read or run.

@pytest.fixture
async def composer(tmp_path, monkeypatch):
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / ".cache" / "ms-playwright")
    if Path(browsers).is_dir():
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", browsers)      # the test browser stays findable
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("DREAM_DESKTOP_SESSION_FILE", str(tmp_path / "session.json"))
    prompts, steers = [], []

    async def on_steer(text, identifier, target):
        steers.append((text, identifier, target))
        return {"status": "pending"}

    server = StudioServer(EventBus(), on_prompt=prompts.append, on_steer=on_steer,
                          session={"provider": "MachX", "model": "fixture", "session_id": "login-test"})
    url = await server.start()
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, args=["--disable-gpu"])
    page = await browser.new_page(viewport={"width": 1400, "height": 940}, reduced_motion="reduce")
    page.set_default_timeout(5000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        await page.goto(url.replace("/#", "/?companion=1#"))
        await expect(page.locator("#stat")).to_have_text("Ready")
        yield SimpleNamespace(server=server, page=page, prompts=prompts, steers=steers, errors=errors)
    finally:
        await browser.close()
        await playwright.stop()
        await server.stop()


async def is_working(page):
    return await page.evaluate("working")


async def turn(s, on):
    s.server.bus.publish(Event("turn_start" if on else "turn_end", {}))
    await s.page.wait_for_function("working === true" if on else "working === false")


def hold_prompts():
    """A route for /api/prompt that lets the request reach the session only when the test says so."""
    arrived, release = asyncio.Event(), asyncio.Event()

    async def route(route):
        arrived.set()
        await release.wait()
        await route.continue_()
    return route, arrived, release


async def test_login_in_the_chat_box_shows_the_sessions_answer_and_sends_no_message(composer):
    s, page = composer, composer.page
    box, receipts = page.locator("#input"), page.locator(".sys.login-receipt")
    await box.fill("/login codex")
    await box.press("Enter")
    await expect(receipts.last).to_contain_text("Opening a separate sign-in window for ChatGPT (Codex)")
    await expect(box).to_have_value("")
    assert await is_working(page) is False                         # asking for sign-in is not work
    waiting = s.server.login.desktop_view()
    assert waiting["provider"] == "codex"
    s.server.login.ack(waiting["id"], "codex", "launched")         # what the desktop window would answer
    await expect(receipts.last).to_contain_text("A separate sign-in window is open for ChatGPT (Codex)")
    await expect(receipts.last).to_contain_text("did not check whether you are signed in")
    await box.fill("/login")
    await box.press("Enter")
    await expect(receipts.last).to_contain_text("usage: /login <provider>")
    await expect(receipts.last).to_contain_text("antigravity")
    assert await receipts.last.locator("*").count() == 0           # plain text, nothing rendered from the answer
    assert s.prompts == [] and s.steers == []
    assert await page.locator(".sys").filter(has_text="Correction").count() == 0
    assert await is_working(page) is False and not s.errors


async def test_login_in_the_chat_box_during_a_turn_is_not_steered_or_queued_and_leaves_the_turn_alone(composer):
    s, page = composer, composer.page
    box, receipts = page.locator("#input"), page.locator(".sys.login-receipt")
    await turn(s, True)
    await box.fill("/login grok")
    await page.locator("#steer").click()                           # the Steer button
    await expect(receipts.last).to_contain_text("Opening a separate sign-in window for Grok")
    await expect(box).to_have_value("")
    assert await is_working(page) is True
    waiting = s.server.login.desktop_view()
    s.server.login.ack(waiting["id"], "grok", "unavailable")
    await expect(page.locator(".sys.error").last).to_contain_text("grok command was not found")
    await box.fill("/LOGIN gemini")
    await page.locator("#send").click()                            # the Queue button
    await expect(receipts.last).to_contain_text("Opening a separate sign-in window for Gemini")
    assert s.prompts == [] and s.steers == []                      # neither route got a /login
    assert await page.locator(".sys").filter(has_text="Correction").count() == 0
    assert await page.locator(".sys").filter(has_text="next model request").count() == 0
    assert await is_working(page) is True
    # A real correction still steers, with its own receipt id.
    await box.fill("Prefer the smaller change.")
    await page.locator("#steer").click()
    await expect(page.locator(".sys").filter(has_text="Correction saved")).to_have_count(1)
    assert [text for text, _, _ in s.steers] == ["Prefer the smaller change."] and s.prompts == []
    await turn(s, False)
    assert not s.errors


async def composer_shows(page, running, word=True):
    """What the chat composer shows for a turn that is running, or for none: the send button's name, whether Stop
    and Steer are offered, and the status word. These are the page's own elements, not its variables. `word=False`
    leaves the status word out, for after an error line: what the pane's status says then is not this file's to fix."""
    send, steer, stop, stat = (page.locator(f"#{name}") for name in ("send", "steer", "stop", "stat"))
    await expect(send).to_have_text("Queue" if running else "Send")
    await expect(steer).to_have_js_property("hidden", not running)
    await expect(stop).to_have_js_property("hidden", not running)
    if running:
        await expect(steer).to_be_visible()
    if word and running:
        await expect(stat).not_to_have_text("Ready")
    elif word:
        await expect(stat).to_have_text("Ready")
    assert await is_working(page) is running


async def is_sending(page):
    return await page.evaluate("sending")


async def test_a_login_answer_that_arrives_after_the_turn_changed_does_not_bring_the_old_state_back(composer):
    s, page = composer, composer.page
    box, receipts = page.locator("#input"), page.locator(".sys.login-receipt")
    route, arrived, release = hold_prompts()
    await page.route("**/api/prompt", route)
    await turn(s, True)
    await composer_shows(page, True)
    await box.fill("/login gemini")
    await box.press("Enter")
    await asyncio.wait_for(arrived.wait(), 5)
    assert await is_sending(page) is True
    await composer_shows(page, True)                               # asking changed nothing the turn shows
    await turn(s, False)                                           # the turn ends while the answer is on its way
    await composer_shows(page, False)
    await box.fill("A newer draft")                                # and the person has typed something else by then
    release.set()
    await expect(receipts).to_have_count(1)
    await expect(receipts.last).to_contain_text("Opening a separate sign-in window for Gemini")
    await composer_shows(page, False)                              # ended stays ended: Send, no Stop, no Steer, Ready
    await expect(box).to_have_value("A newer draft")               # the late answer did not clear the newer draft
    assert await is_sending(page) is False
    assert await page.evaluate("steeringDraft") is None            # and made no steering draft
    # What the desktop window then answers arrives later still, as its own line, and changes nothing either.
    s.server.login.ack(s.server.login.desktop_view()["id"], "antigravity", "closed")
    await expect(receipts).to_have_count(2)
    await expect(receipts.last).to_contain_text("The Gemini (Antigravity) sign-in window has closed")
    await composer_shows(page, False)
    await expect(box).to_have_value("A newer draft")
    assert s.prompts == [] and s.steers == []
    # The next message is an ordinary message and goes where ordinary messages go.
    await box.press("Enter")
    await expect(box).to_have_value("")
    assert s.prompts == ["A newer draft"] and s.steers == []
    assert await is_sending(page) is False
    await turn(s, False)                                           # that message's turn, over

    arrived.clear()
    release.clear()
    await composer_shows(page, False)
    await box.fill("/login grok")
    await box.press("Enter")
    await asyncio.wait_for(arrived.wait(), 5)
    await turn(s, True)                                            # and a turn that starts meanwhile stays running
    await composer_shows(page, True)
    await box.fill("Prefer the smaller change.")                   # a correction typed while the answer is on its way
    release.set()
    await expect(receipts).to_have_count(3)
    await expect(receipts.last).to_contain_text("Opening a separate sign-in window for Grok")
    await composer_shows(page, True)                               # running stays running: Queue, Stop, Steer
    await expect(box).to_have_value("Prefer the smaller change.")
    assert await is_sending(page) is False
    assert await page.evaluate("steeringDraft") is None
    assert s.prompts == ["A newer draft"] and s.steers == []
    s.server.login.ack(s.server.login.desktop_view()["id"], "grok", "launched")
    await expect(receipts).to_have_count(4)
    await expect(receipts.last).to_contain_text("A separate sign-in window is open for Grok")
    await composer_shows(page, True)                               # the window's answer did not end the turn
    await expect(box).to_have_value("Prefer the smaller change.")
    await page.locator("#steer").click()                           # the correction still steers the running turn
    await expect(page.locator(".sys").filter(has_text="Correction saved")).to_have_count(1)
    await expect(box).to_have_value("")
    assert [text for text, _, _ in s.steers] == ["Prefer the smaller change."] and s.prompts == ["A newer draft"]
    await composer_shows(page, True)
    await turn(s, False)
    await composer_shows(page, False)
    assert not s.errors


def unanswered_prompts():
    """A route for /api/prompt that never lets a request reach the session and never answers it. `seen` counts them."""
    seen, arrived, done = [], asyncio.Event(), asyncio.Event()

    async def route(route):
        seen.append(route.request.post_data_json)
        arrived.set()
        await done.wait()
        try:
            await route.abort()
        except Exception:
            pass                                                   # the page gave up on it already
    return route, seen, arrived, done


async def test_a_login_request_that_gets_no_answer_ends_in_time_as_an_unknown_outcome_and_changes_nothing(composer):
    s, page = composer, composer.page
    box, receipts = page.locator("#input"), page.locator(".sys.login-receipt")
    # The page's 15 s limit, shortened for the test; every other timer keeps its time.
    await page.evaluate("""() => { const real = window.setTimeout;
        window.setTimeout = (fn, ms, ...rest) => real(fn, ms === 15000 ? 300 : ms, ...rest); }""")
    route, seen, arrived, done = unanswered_prompts()
    await page.route("**/api/prompt", route)
    await turn(s, True)
    await composer_shows(page, True)
    messages = await page.locator(".msg").count()
    await box.fill("/login codex")
    await page.locator("#steer").click()
    await asyncio.wait_for(arrived.wait(), 5)
    said = page.locator(".sys.error").last
    await expect(said).to_contain_text("Dream did not answer in time")
    await expect(said).to_contain_text("not known whether a sign-in window was asked for")
    await expect(said).to_contain_text("Look for one before trying again")
    await expect(box).to_have_value("/login codex")                # an unknown outcome keeps the draft
    assert await is_sending(page) is False                         # the box is not left waiting
    await composer_shows(page, True, word=False)                   # and the turn is exactly as it was
    assert await page.evaluate("steeringDraft") is None
    assert await receipts.count() == 0                             # nothing says a window was asked for
    assert await page.locator(".sys").filter(has_text="Correction").count() == 0
    assert await page.locator(".msg").count() == messages          # and nothing shows a sent message
    assert seen == [{"prompt": "/login codex", "attachments": []}]     # asked once: the page does not ask again
    assert s.prompts == [] and s.steers == [] and s.server.login.desktop_view() is None
    done.set()
    await page.unroute("**/api/prompt", route)
    await box.fill("Prefer the smaller change.")                   # the next message is usable, and steers
    await page.locator("#steer").click()
    await expect(page.locator(".sys").filter(has_text="Correction saved")).to_have_count(1)
    assert [text for text, _, _ in s.steers] == ["Prefer the smaller change."] and s.prompts == []
    assert len(seen) == 1
    await turn(s, False)
    await composer_shows(page, False, word=False)
    assert not s.errors


@pytest.mark.parametrize("status, body, said", [
    (200, "<html>not an answer</html>", "could not be read as a sign-in answer"),
    (200, '{"ok": true}', "could not be read as a sign-in answer"),      # how an ordinary message is answered
    (502, "<html>bad gateway</html>", "an error it did not explain (HTTP 502)"),
])
async def test_a_login_answer_that_cannot_be_read_is_an_unknown_outcome_in_the_chat_box(composer, status, body, said):
    s, page = composer, composer.page
    box, seen = page.locator("#input"), []

    async def route(route):
        seen.append(route.request.post_data_json)
        await route.fulfill(status=status, content_type="text/html", body=body)
    await page.route("**/api/prompt", route)
    await composer_shows(page, False)
    messages = await page.locator(".msg").count()
    await box.fill("/login grok")
    await box.press("Enter")
    line = page.locator(".sys.error").last
    await expect(line).to_contain_text(said)
    await expect(line).to_contain_text("not known whether a sign-in window was asked for")
    await expect(line).not_to_contain_text("no sign-in window was asked for")     # that is not known either
    await expect(box).to_have_value("/login grok")
    await composer_shows(page, False, word=False)
    assert await is_sending(page) is False
    assert await page.locator(".sys.login-receipt").count() == 0
    assert await page.locator(".msg").count() == messages          # not shown as a sent message
    assert seen == [{"prompt": "/login grok", "attachments": []}]  # asked once, not again
    assert s.prompts == [] and s.steers == [] and not s.errors


async def test_when_the_window_answer_cannot_be_had_the_chat_says_the_outcome_is_unknown_and_asks_for_no_second_window(composer):
    s, page = composer, composer.page
    box, receipts = page.locator("#input"), page.locator(".sys.login-receipt")

    async def route(route):
        try:
            action = route.request.post_data_json.get("action")
        except Exception:
            action = None
        if action == "provider_login_status":
            await route.fulfill(status=200, content_type="text/html", body="<html>not an answer</html>")
        else:
            await route.continue_()
    await page.route("**/api/control", route)
    await box.fill("/login codex")
    await box.press("Enter")
    await expect(receipts).to_have_count(1)
    await expect(receipts.last).to_contain_text("Opening a separate sign-in window for ChatGPT (Codex)")
    waiting = s.server.login.desktop_view()
    line = page.locator(".sys.error").last
    await expect(line).to_contain_text("not known whether a sign-in window opened")
    await expect(line).to_contain_text("Look for one before trying again")
    assert s.server.login.desktop_view() == waiting                # the one request, still the same one
    await expect(receipts).to_have_count(1)
    await composer_shows(page, False, word=False)
    assert s.prompts == [] and s.steers == [] and not s.errors


async def test_a_refused_login_in_the_chat_box_keeps_the_draft_and_other_slash_words_are_messages(composer):
    s, page = composer, composer.page
    box = page.locator("#input")
    await box.fill("/login claude --dangerously-skip-permissions")
    await box.press("Enter")
    refusal = page.locator(".sys.error").last
    await expect(refusal).to_contain_text("Unknown sign-in provider")
    await expect(refusal).not_to_contain_text("dangerously")
    await expect(box).to_have_value("/login claude --dangerously-skip-permissions")
    assert s.server.login.desktop_view() is None and s.server.login.status()["request"] is None
    assert await is_working(page) is False and s.prompts == []
    assert await page.locator(".sys.login-receipt").count() == 0
    await box.fill("/loginx hello")                                # not the command: an ordinary message
    await box.press("Enter")
    await expect(box).to_have_value("")
    assert s.prompts == ["/loginx hello"] and s.server.login.desktop_view() is None
    assert not s.errors


async def open_nested(page):
    await page.locator("#dream-nav-nested").click()
    await expect(page.locator("#dream-nested-page")).to_be_visible()
    return page.locator("#nd-compose"), page.locator("#nd-send"), page.locator("#nd-status")


async def test_login_in_the_nested_box_shows_the_sessions_answer_idle_and_during_a_turn(composer):
    s, page = composer, composer.page
    box, send, status = await open_nested(page)
    await box.fill("/login claude")
    await box.press("Enter")
    await expect(status).to_contain_text("Opening a separate sign-in window for Claude")
    await expect(box).to_have_value("")
    await expect(send).to_have_text("Send")
    s.server.login.ack(s.server.login.desktop_view()["id"], "anthropic", "launched")
    await expect(status).to_contain_text("A separate sign-in window is open for Claude")
    await expect(status).to_contain_text("did not check whether you are signed in")
    assert await status.get_attribute("data-error") is None

    await turn(s, True)
    await expect(send).to_have_text("Steer")
    await box.fill("/login grok")
    await send.click()
    await expect(status).to_contain_text("Opening a separate sign-in window for Grok")
    await expect(status).not_to_contain_text("orchestrator")       # not "Sent to the orchestrator", not a receipt
    await expect(status).not_to_contain_text("next model request")
    await expect(send).to_have_text("Steer")
    assert s.prompts == [] and s.steers == []
    s.server.login.ack(s.server.login.desktop_view()["id"], "grok", "busy")       # the first window is still open
    await expect(status).to_contain_text("Another sign-in window is already open")
    await expect(status).to_have_attribute("data-error", "true")

    await box.fill("/login grok --device")
    await send.click()
    await expect(status).to_contain_text("Unknown sign-in provider")
    await expect(status).not_to_contain_text("device")
    await expect(status).to_have_attribute("data-error", "true")
    await expect(box).to_have_value("/login grok --device")        # refused: the draft stays
    assert s.server.login.desktop_view() is None

    await box.fill("Prefer the smaller change.")                   # a real correction still steers, with its own id
    await send.click()
    await expect(status).to_contain_text("next model request")
    assert [text for text, _, _ in s.steers] == ["Prefer the smaller change."] and s.prompts == []
    assert len(s.steers[0][1]) == 32
    await turn(s, False)
    await expect(send).to_have_text("Send")
    assert not s.errors


async def test_a_late_login_answer_in_the_nested_box_does_not_change_what_the_turn_says(composer):
    s, page = composer, composer.page
    box, send, status = await open_nested(page)
    route, arrived, release = hold_prompts()
    await page.route("**/api/prompt", route)
    await turn(s, True)
    await box.fill("/login gemini")
    await send.click()
    await asyncio.wait_for(arrived.wait(), 5)
    await turn(s, False)
    await expect(send).to_have_text("Send")
    release.set()
    await expect(status).to_contain_text("Opening a separate sign-in window for Gemini")
    await expect(send).to_have_text("Send")
    await expect(send).to_be_enabled()
    assert await is_working(page) is False
    assert s.prompts == [] and s.steers == [] and not s.errors


async def test_a_login_answer_that_cannot_be_read_is_an_unknown_outcome_in_the_nested_box(composer):
    s, page = composer, composer.page
    box, send, status = await open_nested(page)
    seen = []

    async def route(route):
        seen.append(route.request.post_data_json)
        await route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
    await page.route("**/api/prompt", route)
    await box.fill("/login claude")
    await box.press("Enter")
    await expect(status).to_contain_text("could not be read as a sign-in answer")
    await expect(status).to_contain_text("not known whether a sign-in window was asked for")
    await expect(status).not_to_contain_text("Sent to the orchestrator")      # not reported as a sent message
    await expect(status).to_have_attribute("data-error", "true")
    await expect(box).to_have_value("/login claude")               # the draft stays
    await expect(send).to_have_text("Send")
    await expect(send).to_be_enabled()
    assert seen == [{"prompt": "/login claude", "attachments": []}]           # asked once, not again
    assert await is_working(page) is False
    assert s.prompts == [] and s.steers == [] and not s.errors
