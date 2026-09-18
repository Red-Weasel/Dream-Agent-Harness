"""Workspace navigation keeps the current session and unsent work intact."""
from test_desktop_chat import chat
from playwright.async_api import expect

async def test_home_palette_and_navigation_preserve_draft(chat):
    _, page, prompts, controls = chat
    await page.locator('#input').fill('Keep my rocket changes')
    await page.locator('#dream-nav-home').click()
    await expect(page.locator('#dream-home')).to_be_visible()
    await expect(page.locator('#dream-home')).to_contain_text('Your ideas')
    await page.keyboard.press('Control+k')
    await page.locator('#dream-command-search').fill('chat')
    await page.locator('#dream-command-list button').first.click()
    await expect(page.locator('#input')).to_have_value('Keep my rocket changes')
    assert prompts == []
    assert controls == [{'action': 'permission_mode_status'}]

async def test_native_view_messages_require_matching_session(chat):
    _, page, _, _ = chat
    await page.evaluate("window.dispatchEvent(new CustomEvent('dream:navigate',{detail:{view:'home',session_id:'wrong'}}))")
    await expect(page.locator('#dream-home')).to_be_hidden()
    await page.evaluate("window.dispatchEvent(new CustomEvent('dream:navigate',{detail:{view:'home',session_id:'chat-test'}}))")
    await expect(page.locator('#dream-home')).to_be_visible()

async def test_small_screen_navigation_and_no_inference(chat):
    _, page, prompts, _ = chat
    await page.set_viewport_size({'width':390,'height':844})
    await page.locator('#dream-nav-home').click()
    await page.locator('#dream-resume').click()
    await expect(page.locator('#input')).to_be_visible()
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert prompts == []

async def test_output_compare_does_not_overwrite_or_send(chat):
    from dream.core.backends.base import Event
    srv,page,prompts,_=chat
    for text in ['first preserved version','second preserved version']:
        srv.bus.publish(Event('studio',{'op':'show','path':'rocket.html','content':'<!doctype html><h1>'+text+'</h1>'}))
        await expect(page.frame_locator('#artbody iframe').locator('h1')).to_have_text(text)
    await page.locator('#dream-output-compare').click()
    await expect(page.locator('#dream-compare')).to_contain_text('first preserved version')
    await expect(page.locator('#dream-compare')).to_contain_text('second preserved version')
    assert prompts == []

async def test_home_and_chat_visual_fixtures(chat):
    _,page,_,_=chat
    for width,height in [(1600,1000),(1280,800),(720,520),(390,844)]:
        await page.set_viewport_size({'width':width,'height':height})
        await page.locator('#dream-nav-home').click()
        await page.evaluate("async()=>{const i=new Image();i.src='/assets/dream-eclipse.png';await i.decode();}")
        await page.screenshot(path=f'/tmp/dream-home-{width}.png')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.locator('#dream-resume').click()
        await page.screenshot(path=f'/tmp/dream-chat-{width}.png')
        await expect(page.locator('#input')).to_be_visible()

async def test_classic_switch_preserves_unsent_draft_without_reload(chat):
    _,page,prompts,_=chat
    await page.locator('#input').fill('Keep this draft across presentation changes')
    await page.locator('#dream-inspect-toggle').click()
    await page.get_by_role('button',name='Use classic presentation',exact=True).click()
    await expect(page.locator('#dream-nav')).to_be_hidden()
    await expect(page.locator('#input')).to_have_value('Keep this draft across presentation changes')
    await page.locator('#dream-restore-design').click()
    await expect(page.locator('#dream-nav')).to_be_visible()
    assert prompts==[]

async def test_council_activity_reconnect_does_not_repeat_work(chat):
    from dream.core.backends.base import Event
    srv,page,prompts,_=chat
    srv.bus.publish(Event('council_activity',{'task_id':'task-a','member':'codex','state':'completed','model':'fixture','ownership':'sequential workspace writer'}))
    await page.locator('#dream-inspect-toggle').click()
    await expect(page.locator('#dream-council-activity')).to_contain_text('completed')
    await page.reload()
    await page.locator('#dream-inspect-toggle').click()
    await expect(page.locator('#dream-council-activity')).to_contain_text('completed')
    assert prompts==[]

async def test_context_overlay_cannot_hide_new_approval(chat):
    import asyncio
    srv,page,_,_=chat
    await page.set_viewport_size({'width':390,'height':844})
    await page.locator('#dream-inspect-toggle').click()
    pending=asyncio.create_task(srv.request_permission('run_bash',{'command':'pwd'},'Fixture approval',{'y':'Allow once','n':'Deny'}))
    try:
        await expect(page.locator('#dream-inspector')).to_be_hidden()
        await expect(page.get_by_role('button',name='Allow once',exact=True)).to_be_visible()
        await page.get_by_role('button',name='Deny',exact=True).click()
        assert await asyncio.wait_for(pending,2)=='n'
    finally:
        pending.cancel()
        await asyncio.gather(pending,return_exceptions=True)
