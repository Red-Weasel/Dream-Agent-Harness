"""Account sign-in pills and retained internal Council API; isolated HTTP, no model calls."""
import asyncio
import copy
import re

import pytest
from playwright.async_api import async_playwright, expect
from dream.core.provider_login import LoginRequests
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_council_ui import CouncilAPI

FRONTIERS = [("anthropic", "Claude"), ("grok", "Grok"), ("antigravity", "Gemini"), ("codex", "Codex")]

@pytest.fixture
async def frontiers(tmp_path, monkeypatch):
    monkeypatch.delenv("DREAM_DESKTOP_SESSION_FILE", raising=False)
    server = StudioServer(EventBus(), session={"session_id": "frontier-fixture", "workspace": str(tmp_path), "provider": "machx", "model": "local-fixture"})
    url = await server.start()
    api = CouncilAPI()
    api.status["config"].update(orchestrator="machx", advisors=[], advisor_models={}, advisor_efforts={})
    api.status["model"] = "local-fixture"
    api.status["choices"] = [{"key": key, "label": label, "available": True, "efforts": [], "models": []} for key, label in [("machx", "Local"), *FRONTIERS]]
    requests = LoginRequests(True)
    accounts = {key: {'provider': key, 'status': 'signed_out', 'detail': 'Sign in required.'} for key, _ in FRONTIERS}
    api.accounts = accounts
    async def route(control):
        payload = control.request.post_data_json
        if payload["action"] in ("provider_login", "provider_login_status"):
            api.login_calls.append(payload)
            result = requests.control(payload)
            if payload["action"] == "provider_login":
                requests.ack(result["id"], result["provider"], "launched")
                result = requests.status(result["id"])["request"]
            await control.fulfill(json={"ok":True, "result":result})
        else:
            await api.route(control)
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=["--disable-gpu"])
            page = await browser.new_page(viewport={"width":1280, "height":940}, reduced_motion="reduce")
            page.set_default_timeout(5000)
            errors=[]
            page.on("pageerror", lambda error: errors.append(error.stack))
            await page.route("**/api/control", route)
            await page.route("**/api/accounts", lambda request: request.fulfill(json={'accounts': list(accounts.values())}))
            await page.goto(url.replace("/#", "/?companion=1#"))
            await expect(page.locator("#dream-frontier-codex")).to_have_attribute("data-account-status", "signed_out")
            try:
                yield page, api, requests, errors
            finally:
                for gate in api.pause.values(): gate.set()
                await browser.close()
    finally:
        await server.stop()

async def select(page, name, on=True):
    # Roster manipulation remains an explicit Council API, never a sidebar sign-in click.
    key = next(key for key, label in FRONTIERS if label == name)
    await page.evaluate("([key,on]) => DreamFrontiers.toggle(key,on)", [key,on])

async def test_controls_below_understand_without_top_model_pill(frontiers):
    page, api, _, errors=frontiers
    assert await page.locator("#dream-model").count() == 0
    assert await page.locator("#dream-frontiers .dream-account-label").all_text_contents() == ["Claude", "Grok", "Gemini", "Codex"]
    boxes=[await page.locator(selector).bounding_box() for selector in ["#dream-nav-understand", *["#dream-frontier-"+key for key,_ in FRONTIERS]]]
    assert all(left["y"] < right["y"] for left,right in zip(boxes, boxes[1:]))
    await page.screenshot(path="/tmp/dream-frontier-sidebar-desktop.png")
    assert not any(call["action"] == "council_configure" for call in api.calls)
    assert not errors

async def test_first_frontier_leads_members_join_and_last_returns_to_local(frontiers):
    page, api, _, errors=frontiers
    await select(page,"Codex")
    assert api.status["config"]["orchestrator"] == "codex"
    await select(page,"Gemini")
    assert api.status["config"]["advisors"] == ["antigravity"]
    await select(page,"Codex",False)
    assert api.status["config"]["orchestrator"] == "antigravity"
    await select(page,"Gemini",False)
    assert api.status["config"]["orchestrator"] == "machx"
    assert api.status["model"] == "local-fixture"
    assert api.status["config"]["advisors"] == []
    assert not errors

async def test_member_toggle_preserves_models_efforts_and_unsaved_settings(frontiers):
    page, api, _, errors=frontiers
    await select(page,"Codex")
    api.status["model"]="selected-codex"
    api.status["config"].update(orchestrator_effort="high", advisor_models={"antigravity":"gemini-selected"}, advisor_efforts={"antigravity":"medium"}, advisors=["antigravity"])
    await page.evaluate("window.DreamFrontiers.refreshCouncil()")
    await page.locator("#dream-nav-council").click()
    await expect(page.get_by_label("Main provider")).to_have_value("codex")
    await page.get_by_label("Main model",exact=True).select_option("__custom__")
    await page.get_by_label("Main model custom ID",exact=True).fill("keep-my-draft")
    await page.get_by_role("button",name="Close Council",exact=True).click()
    await select(page,"Claude")
    assert api.status["model"] == "selected-codex"
    assert api.status["config"]["orchestrator_effort"] == "high"
    assert api.status["config"]["advisor_models"] == {"antigravity":"gemini-selected"}
    await page.locator("#dream-nav-council").click()
    await expect(page.get_by_label("Main model custom ID",exact=True)).to_have_value("keep-my-draft")
    assert not errors

async def test_refused_configuration_does_not_light_switch_or_retry(frontiers):
    page, api, _, errors=frontiers
    api.fail["council_configure"]=(400,"Authentication required")
    await select(page,"Gemini")
    await expect(page.locator("#dream-frontier-feedback")).to_contain_text("Authentication required")
    await expect(page.locator("#dream-frontier-antigravity")).to_have_attribute("data-account-status","signed_out")
    assert api.status["config"]["orchestrator"] == "machx"
    assert len([call for call in api.calls if call["action"] == "council_configure"]) == 1
    assert not errors

async def test_busy_council_refuses_roster_changes_but_keeps_login_available(frontiers):
    page, api, _, errors=frontiers
    api.status["busy"]=True
    await select(page,"Claude")
    await expect(page.locator("#dream-frontier-feedback")).to_contain_text("cannot change now")
    await expect(page.locator("#dream-frontier-anthropic")).to_be_enabled()
    assert not any(call["action"] == "council_configure" for call in api.calls)
    assert not errors

@pytest.mark.parametrize("key,name",FRONTIERS)
async def test_sidebar_login_uses_canonical_native_target(frontiers,key,name):
    page, api, requests, errors=frontiers
    await page.locator("#dream-frontier-"+key).click()
    await expect(page.locator("#dream-frontier-feedback")).to_contain_text("separate sign-in window is open")
    assert [call for call in api.login_calls if call["action"] == "provider_login"] == [{"action":"provider_login", "provider":key}]
    assert requests.status()["request"]["provider"] == key
    if key == "antigravity": assert requests.status()["request"]["command"] == "agy"
    assert requests.status()["sign_in_checked"] is False
    await expect(page.locator("#dream-frontier-"+key)).to_have_attribute("data-account-status","signed_out")
    assert not any(call["action"] == "council_configure" for call in api.calls)
    assert not errors

async def test_mobile_frontiers_and_settings_remain_reachable(frontiers):
    page, _, _, errors=frontiers
    await page.set_viewport_size({"width":390,"height":844})
    await page.locator("#dream-frontier-antigravity").scroll_into_view_if_needed()
    await expect(page.locator("#dream-frontier-antigravity")).to_be_visible()
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    await page.screenshot(path="/tmp/dream-frontier-sidebar-mobile.png")
    await page.locator("#dream-nav-settings").click()
    await expect(page.get_by_role('tab',name='Settings',exact=True)).to_have_attribute('aria-selected','true')
    assert not errors

async def test_native_login_event_does_not_change_roster_or_claim_account_success(frontiers):
    page, api, _, errors=frontiers
    before = copy.deepcopy(api.status['config'])
    await page.evaluate("window.dispatchEvent(new CustomEvent('dream:frontier-login',{detail:{provider:'antigravity'}}))")
    await expect(page.locator("#dream-frontier-feedback")).to_contain_text("Gemini (Antigravity)")
    state=await page.evaluate("window.DreamFrontiers.state()")
    assert state["controls"]["antigravity"]["signed_in"] is False
    assert api.status['config'] == before
    assert not any(call['action']=='council_configure' for call in api.calls)
    api.accounts['antigravity'].update(status='signed_in',username='Research <user>')
    await page.evaluate('DreamFrontiers.refresh()')
    state=await page.evaluate('DreamFrontiers.state()')
    assert state['controls']['antigravity']['signed_in'] is True
    assert state['controls']['antigravity']['username'] == 'Research <user>'
    # DREAM-271: the identity is the pill's tooltip, as literal text; the row under the pill stays hidden.
    await expect(page.locator('#dream-account-antigravity')).to_be_hidden()
    assert 'Research <user>' in await page.locator('#dream-frontier-antigravity').get_attribute('title')
    assert await page.locator('#dream-account-antigravity user').count() == 0
    assert not errors

async def test_switching_member_off_and_on_retains_its_model_and_effort(frontiers):
    page, api, _, errors=frontiers
    api.status['config'].update(orchestrator='codex',advisors=['antigravity'],advisor_models={'antigravity':'chosen-gemini'},advisor_efforts={'antigravity':'high'})
    api.status['model']='chosen-codex'
    await page.evaluate('window.DreamFrontiers.refreshCouncil()')
    await select(page,'Gemini',False)
    await select(page,'Gemini')
    assert api.status['config']['advisor_models'] == {'antigravity':'chosen-gemini'}
    assert api.status['config']['advisor_efforts'] == {'antigravity':'high'}
    assert api.status['model'] == 'chosen-codex'
    assert not errors

async def test_frontier_only_session_cannot_silently_remove_last_main(frontiers):
    page, api, _, errors=frontiers
    # A new page/session starts with Codex, so there is no confirmed local fallback.
    api.status['config'].update(orchestrator='codex',advisors=[])
    await page.reload()
    await expect(page.locator('#dream-frontier-codex')).to_be_enabled()
    api.calls.clear()
    await select(page,'Codex',False)
    await expect(page.locator('#dream-frontier-feedback')).to_contain_text('Turn on another frontier')
    assert api.status['config']['orchestrator'] == 'codex'
    await expect(page.locator('#dream-frontier-codex')).to_have_attribute('data-account-status','signed_out')
    assert not any(call['action']=='council_configure' for call in api.calls)
    assert not errors


async def test_unknown_account_is_amber_and_never_inferred_from_running_provider(frontiers):
    page,api,_,errors=frontiers
    api.accounts['grok'].update(status='unknown',detail='No supported read-only account command.')
    api.accounts['antigravity'].update(status='unknown',detail='No supported read-only account command.')
    api.accounts['codex'].update(status='signed_in',detail='Codex reports signed in.')
    await page.evaluate('DreamFrontiers.refresh()')
    for key in ('grok','antigravity'):
        pill=page.locator('#dream-frontier-'+key)
        await expect(pill).to_have_attribute('data-account-status','unknown')
        await expect(pill).to_have_attribute('aria-label', re.compile('account unverified'))
        assert await pill.locator('.dream-account-light').evaluate('(el)=>getComputedStyle(el).backgroundColor') == 'rgb(231, 184, 104)'
    await expect(page.locator('#dream-frontier-codex')).to_have_attribute('data-account-status','signed_in')
    assert await page.locator('#dream-frontier-anthropic .dream-account-light').evaluate('(el)=>getComputedStyle(el).backgroundColor') == 'rgb(241, 108, 127)'
    assert not errors
