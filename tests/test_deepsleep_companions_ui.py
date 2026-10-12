"""Decorative companions move independently without allocating terminal space."""
import json
from copy import deepcopy
from playwright.async_api import expect
from test_deepsleep_ui import board_page, add, STATIC, xterm_assets


async def test_free_drag_keyboard_reset_preserve_session_and_controls(board_page):
    page,api,errors=board_page
    await add(page)
    card=page.locator('.ds-node')
    await card.get_by_role('button',name='Place character for Claude').click()
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    avatar=page.locator('.ds-avatar-slot')
    await expect(avatar).to_be_visible()
    await page.wait_for_function('document.querySelector(".ds-avatar-slot img").naturalWidth>0')
    pane=await card.bounding_box();terminal=await card.locator('.ds-terminal').bounding_box()
    assert abs(terminal['x']-pane['x'])<=2
    before=await avatar.bounding_box();tools=await card.locator('.ds-footer-tools').bounding_box()
    assert before['y']+before['height']<=tools['y']
    layout=deepcopy(api.state['nodes'][0]['layout']);calls=deepcopy(api.calls)
    await page.evaluate('window.originalTerminal=fixtureTerminals[0]')
    await page.mouse.move(before['x']+20,before['y']+20);await page.mouse.down()
    await page.mouse.move(before['x']+620,before['y']+160);await page.mouse.up()
    moved=await avatar.bounding_box()
    assert moved['x']==before['x']+600 and moved['y']==before['y']+140
    assert moved['x']>pane['x']+pane['width']
    await avatar.press('ArrowLeft');await avatar.press('Shift+ArrowDown')
    adjusted=await avatar.bounding_box()
    assert adjusted['x']==moved['x']-10 and adjusted['y']==moved['y']+40
    assert api.calls==calls and api.state['nodes'][0]['layout']==layout
    assert await page.evaluate('fixtureTerminals.length===1 && fixtureTerminals[0]===originalTerminal')
    assert not any(m.get('type')=='input' for _,m in api.socket_messages)
    await page.get_by_role('button',name='Refresh workspace').click()
    assert await avatar.bounding_box()==adjusted
    await card.get_by_role('button',name='Move Claude; use arrow keys').press('ArrowRight')
    assert await avatar.bounding_box()==adjusted
    await avatar.press('Home')
    await expect(avatar).to_be_hidden()
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    await card.get_by_role('button',name='Place character for Claude').click()
    reset=await avatar.bounding_box()
    assert reset['x']==before['x']+10 and reset['y']==before['y']
    await card.get_by_role('button',name='Reset character position for Claude').click()
    await card.get_by_role('button',name='Close Claude',exact=True).click()
    await expect(avatar).to_have_count(0)
    assert not errors


async def test_position_reload_workspace_and_unavailable_storage(board_page):
    page,api,errors=board_page
    api.state['workspace']='/work/one';await add(page)
    await page.get_by_role('button',name='Place character for Claude').click();avatar=page.locator('.ds-avatar-slot')
    await avatar.press('ArrowLeft');await avatar.press('ArrowUp')
    saved=await avatar.bounding_box()
    await page.reload()
    terminal_js,fit_js,terminal_css=xterm_assets()
    for path in [terminal_css,STATIC/'deepsleep.css',STATIC/'deepsleep-drawer.css']:
        await page.add_style_tag(path=str(path))
    for path in [terminal_js,fit_js,STATIC/'deepsleep-drawer.js',STATIC/'deepsleep-activity.js',STATIC/'deepsleep-flow.js',STATIC/'deepsleep.js']:
        await page.add_script_tag(path=str(path))
    await expect(avatar).to_be_visible()
    assert await avatar.bounding_box()==saved
    api.state['workspace']='/work/two'
    await page.get_by_role('button',name='Refresh workspace').click()
    await page.locator('#ds-character-toggle').click()
    await expect(avatar).to_be_hidden()
    await page.get_by_role('button',name='Terminal tools for Claude').click()
    await page.get_by_role('button',name='Place character for Claude').click()
    assert await avatar.bounding_box()!=saved
    await page.evaluate('Object.defineProperty(window,"localStorage",{get(){throw new Error("blocked")},configurable:true})')
    await avatar.press('ArrowRight')
    await expect(page.locator('.ds-feedback')).to_contain_text('Browser storage is unavailable')
    assert not errors


async def test_drag_cancel_clamp_and_overlay_not_blocking_drawer(board_page):
    page,api,errors=board_page
    await add(page);await page.get_by_role('button',name='Place character for Claude').click()
    avatar=page.locator('.ds-avatar-slot');before=await avatar.bounding_box()
    await page.mouse.move(before['x']+20,before['y']+20);await page.mouse.down()
    await page.mouse.move(before['x']+120,before['y']+40)
    await avatar.press('Escape');await page.mouse.up()
    assert await avatar.bounding_box()==before
    for _ in range(40):await avatar.press('Shift+ArrowRight')
    await page.set_viewport_size({'width':390,'height':800})
    await page.wait_for_function('()=>{const a=document.querySelector(".ds-avatar-slot").getBoundingClientRect(),b=document.querySelector(".ds-board").getBoundingClientRect();return window.innerWidth===390&&b.width<=620&&a.right<=b.right;}')
    bounds=await avatar.bounding_box()
    board=await page.locator('.ds-board').bounding_box()
    assert bounds['x']+bounds['width']<=board['x']+board['width']
    await page.set_viewport_size({'width':1500,'height':980})
    await page.locator('.ds-node').get_by_role('button',name='Session details for Claude').click()
    await expect(page.get_by_role('dialog')).to_be_visible()
    assert await page.locator('.ds-companions').evaluate('(e)=>getComputedStyle(e).pointerEvents')=='none'
    await page.locator('#ds-character-toggle').click();await expect(avatar).to_be_hidden()
    assert not errors


async def test_recorded_transfer_is_integrated_without_terminal_input(board_page):
    page,api,errors=board_page
    for provider in ['anthropic','codex']:
        await add(page,provider)
        await page.locator('.ds-node').last.get_by_role('button',name='Place character for ',exact=False).click()
        await page.locator('.ds-node').last.get_by_role('button',name='Terminal tools for ',exact=False).click()
    api.state['links']=[{'id':'link-1','source':'node-1','target':'node-2','auto_forward':False}]
    await page.get_by_role('button',name='Refresh workspace').click()
    api.state['receipts']=[{'id':'fresh-1','source':'node-1','target':'node-2','state':'queued'}]
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-flow-pulse')).to_have_count(1)
    await expect(page.locator('.ds-flow-label')).to_have_text('Task accepted')
    geometry=await page.evaluate('''()=>{const path=document.querySelector('.ds-flow-packet'),origin=document.querySelector('.ds-board').getBoundingClientRect();const center=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {x:r.left+r.width/2-origin.left,y:r.top+r.height/2-origin.top}};const a=path.getPointAtLength(0),b=path.getPointAtLength(path.getTotalLength());return {a:{x:a.x,y:a.y},b:{x:b.x,y:b.y},source:center('[data-avatar-node="node-1"] .ds-avatar-port:last-child'),target:center('[data-avatar-node="node-2"] .ds-avatar-port:first-child')}}''')
    for actual,expected in [('a','source'),('b','target')]:
        assert abs(geometry[actual]['x']-geometry[expected]['x'])<.2 and abs(geometry[actual]['y']-geometry[expected]['y'])<.2
    assert not any(m.get('type')=='input' for _,m in api.socket_messages)
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0,timeout=3500)
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    api.state['receipts'].append({'id':'failed-1','source':'node-1','target':'node-2','state':'failed'})
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    assert not errors


async def test_bottom_edge_collapsed_default_does_not_cover_header(board_page):
    page,api,errors=board_page
    await add(page);await page.locator('#ds-character-toggle').click()
    api.state['nodes'][0]['layout']['y']=1500
    await page.get_by_role('button',name='Refresh workspace').click()
    card=page.locator('.ds-node')
    await card.get_by_role('button',name='Collapse Claude to header').click()
    avatar=await page.locator('.ds-avatar-slot').bounding_box()
    header=await card.locator('.ds-node-head').bounding_box()
    assert avatar['y']<=header['y'] and avatar['y']+avatar['height']<=header['y']+header['height']
    for control in await card.locator('.ds-node-head button').all():
        box=await control.bounding_box()
        if box: assert avatar['x']+avatar['width']<=box['x'] or avatar['x']>=box['x']+box['width']
    await card.get_by_role('button',name='Expand Claude terminal',exact=True).click()
    await expect(card.locator('.xterm')).to_be_visible()
    assert not errors
