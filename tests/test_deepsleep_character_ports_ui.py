"""Floating character controls route the same explicit links as pane controls."""
from copy import deepcopy
import pytest
from playwright.async_api import expect
from test_deepsleep_ui import board_page, add, STATIC, xterm_assets


async def endpoints(page, source_selector, target_selector):
    """Compare rendered path endpoints with actual target centers, including transforms."""
    result = await page.evaluate('''([source,target])=>{
      const path=document.querySelector('.ds-wire'),board=document.querySelector('.ds-board');
      const origin=board.getBoundingClientRect();
      // A tile's In/Out pill is reached at its edge (Out's bottom, In's top; 2026-10-10); a character's port at its centre.
      const center=(selector,out)=>{const e=document.querySelector(selector),r=e.getBoundingClientRect(),y=e.classList.contains('ds-port-pill')?(out?r.bottom:r.top):r.top+r.height/2;return {x:(r.left+r.width/2-origin.left)*board.offsetWidth/origin.width,y:(y-origin.top)*board.offsetHeight/origin.height};};
      const start=path.getPointAtLength(0),end=path.getPointAtLength(path.getTotalLength());
      return {start:{x:start.x,y:start.y},end:{x:end.x,y:end.y},source:center(source,true),target:center(target,false)};
    }''', [source_selector, target_selector])
    for actual, expected in [('start','source'),('end','target')]:
        assert result[actual]['x'] == pytest.approx(result[expected]['x'], abs=.1)
        assert result[actual]['y'] == pytest.approx(result[expected]['y'], abs=.1)


async def test_detached_character_ports_create_link_without_drag_or_terminal_input(board_page):
    page,api,errors=board_page
    for provider in ['anthropic','codex']: await add(page,provider)
    await page.locator('#ds-character-toggle').click()
    for card in await page.locator('.ds-node').all(): await card.get_by_role('button',name='Place character for ',exact=False).click()
    source=page.locator('.ds-avatar-anchor[data-avatar-node="node-1"]')
    target=page.locator('.ds-avatar-anchor[data-avatar-node="node-2"]')
    for anchor in [source,target]:
        for _ in range(13): await anchor.locator('.ds-avatar-slot').press('Shift+ArrowDown')
    original=[await anchor.bounding_box() for anchor in [source,target]]
    layout=deepcopy([node['layout'] for node in api.state['nodes']])
    await page.get_by_role('button',name='Show connections and delivery activity').click()
    await page.locator('.ds-channel-connections>summary').click()
    await page.locator('#ds-forward').check()
    await page.get_by_role('button',name='Show connections and delivery activity').click()
    await source.get_by_role('button',name='Link from Claude character').click()
    await expect(source.get_by_role('button',name='Link from Claude character')).to_have_attribute('aria-pressed','true')
    await target.get_by_role('button',name='Link to Codex character').click()
    # The fixture records the request before fetch parses the response and
    # reconcile draws its wire. Wait for the completed mutation, not dispatch.
    await expect(page.locator('.ds-feedback')).to_have_text('Terminals connected.')
    assert api.calls[-1]=={'action':'link','source':'node-1','target':'node-2','auto_forward':True}
    assert [await anchor.bounding_box() for anchor in [source,target]]==original
    assert [node['layout'] for node in api.state['nodes']]==layout
    await endpoints(page,'.ds-avatar-anchor[data-avatar-node="node-1"] .ds-avatar-port:last-child','.ds-avatar-anchor[data-avatar-node="node-2"] .ds-avatar-port:first-child')
    assert 'ds-character-wires' in await page.locator('.ds-wires').get_attribute('class')
    before=await page.locator('.ds-wire').get_attribute('d')
    await source.locator('.ds-avatar-slot').press('Shift+ArrowDown')
    assert await page.locator('.ds-wire').get_attribute('d')!=before
    await source.locator('.ds-avatar-slot').press('Escape')
    await page.screenshot(path='/tmp/dream283-detached-connections.png')
    await page.locator('#ds-character-toggle').click()
    assert 'ds-character-wires' not in await page.locator('.ds-wires').get_attribute('class')
    # Exact aria labels select the two header controls without depending on sibling order.
    await endpoints(page,'[data-node="node-1"] [aria-label="Link from Claude"]','[data-node="node-2"] [aria-label="Link to Codex"]')
    assert not any(m.get('type')=='input' for _,m in api.socket_messages)
    assert not errors


async def test_size_presets_survive_refresh_reload_and_workspace_switch(board_page):
    page,api,errors=board_page
    api.state['workspace']='/fixture/one';await add(page)
    card=page.locator('.ds-node')
    await page.locator('#ds-character-toggle').click()
    await card.get_by_role('button',name='Place character for Claude').click()
    avatar=page.locator('.ds-avatar-slot');size=page.get_by_label('Character size for Claude')
    await expect(size).to_have_value('medium')
    assert (await avatar.bounding_box())['height']==104
    await avatar.press('Shift+ArrowDown')
    baseline=await page.evaluate('({cols:fixtureTerminals[0].cols,rows:fixtureTerminals[0].rows})')
    calls=deepcopy(api.calls)
    for value,height in [('small',72),('large',144)]:
        await size.select_option(value)
        assert (await avatar.bounding_box())['height']==height
    assert await page.evaluate('({cols:fixtureTerminals[0].cols,rows:fixtureTerminals[0].rows})')==baseline
    assert api.calls==calls
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(size).to_have_value('large')
    await page.screenshot(path='/tmp/dream283-character-size.png')
    await page.reload()
    terminal_js,fit_js,terminal_css=xterm_assets()
    for path in [terminal_css,STATIC/'deepsleep.css',STATIC/'deepsleep-drawer.css']: await page.add_style_tag(path=str(path))
    for path in [terminal_js,fit_js,STATIC/'deepsleep-drawer.js',STATIC/'deepsleep-activity.js',STATIC/'deepsleep-flow.js',STATIC/'deepsleep.js']: await page.add_script_tag(path=str(path))
    await expect(avatar).to_be_visible()
    assert (await avatar.bounding_box())['height']==144
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    api.state['workspace']='/fixture/two';await page.get_by_role('button',name='Refresh workspace').click()
    await page.locator('#ds-character-toggle').click()
    await expect(size).to_have_value('medium')
    await card.get_by_role('button',name='Place character for Claude').click()
    assert (await avatar.bounding_box())['height']==104
    await page.evaluate('Object.defineProperty(window,"localStorage",{get(){throw new Error("blocked")},configurable:true})')
    await size.select_option('small')
    assert (await avatar.bounding_box())['height']==72
    await expect(page.locator('.ds-feedback')).to_contain_text('Browser storage is unavailable')
    assert not errors


async def test_keyboard_ports_failure_retry_and_native_manual_permission(board_page):
    page,api,errors=board_page
    for provider in ['anthropic','codex']: await add(page,provider)
    # Native terminal metadata guarantees the existing handler keeps handoffs manual.
    api.state['nodes'][0]['profile']='native'
    await page.get_by_role('button',name='Refresh workspace').click()
    await page.locator('#ds-character-toggle').click()
    await page.get_by_role('button',name='Show connections and delivery activity').click()
    await page.locator('.ds-channel-connections>summary').click()
    await page.locator('#ds-forward').check()
    await page.get_by_role('button',name='Show connections and delivery activity').click()
    for card in await page.locator('.ds-node').all(): await card.get_by_role('button',name='Place character for ',exact=False).click()
    out=page.get_by_role('button',name='Link from Claude character')
    incoming=page.get_by_role('button',name='Link to Codex character')
    await incoming.click();await expect(page.locator('.ds-feedback')).to_contain_text('Choose Output')
    await out.press('Enter')
    await page.get_by_role('button',name='Link to Claude character').click()
    await expect(page.locator('.ds-feedback')).to_contain_text('different terminal')
    assert not [call for call in api.calls if call['action']=='link']
    await out.press('Escape');await expect(out).to_have_attribute('aria-pressed','false')
    await out.press('Enter');api.fail='link';await incoming.press('Enter')
    await expect(page.locator('.ds-feedback')).to_contain_text('Terminal is busy')
    await expect(out).to_have_attribute('aria-pressed','true')
    api.fail=None;await incoming.press('Enter')
    assert api.calls[-1]['auto_forward'] is False
    await expect(out).to_have_attribute('aria-pressed','false')
    assert not any(m.get('type')=='input' for _,m in api.socket_messages)
    assert not errors


async def test_one_hidden_character_falls_back_independently_with_scaled_board(board_page):
    page,api,errors=board_page
    for provider in ['anthropic','codex']: await add(page,provider)
    await page.locator('#ds-character-toggle').click()
    for card in await page.locator('.ds-node').all(): await card.get_by_role('button',name='Place character for ',exact=False).click()
    await page.get_by_role('button',name='Link from Claude character').click()
    await page.get_by_role('button',name='Link to Codex character').click()
    await page.locator('.ds-node').nth(1).get_by_role('button',name='Reset character position for Codex').click()
    # Long real title leaves insufficient header space for its automatic character.
    api.state['nodes'][1]['title']='Codex receiver with a very long descriptive title'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-node').nth(1).locator('h2')).to_have_text('Codex receiver with a very long descriptive title')
    await expect(page.locator('.ds-avatar-anchor').nth(1)).to_be_hidden()
    source='.ds-avatar-anchor[data-avatar-node="node-1"] .ds-avatar-port:last-child'
    target='[data-node="node-2"] [aria-label="Link to Codex receiver with a very long descriptive title"]'
    await endpoints(page,source,target)
    await page.locator('.ds-board').evaluate("e=>{e.style.transformOrigin='top left';e.style.transform='scale(.8)'}")
    await page.get_by_role('button',name='Refresh workspace').click()
    await endpoints(page,source,target)
    await page.set_viewport_size({'width':390,'height':844})
    await page.get_by_role('button',name='Refresh workspace').click()
    await endpoints(page,source,target)
    assert not errors


async def test_unavailable_links_disable_character_and_header_controls(board_page):
    page,api,errors=board_page
    api.state['nodes']=[{'id':'node-blocked','provider':'anthropic','title':'Unavailable',
        'profile':'native','state':'ready','capabilities':{'linked':False},
        'layout':{'x':30,'y':30,'width':760,'height':480}}]
    await page.get_by_role('button',name='Refresh workspace').click()
    await page.locator('#ds-character-toggle').click()
    await page.get_by_role('button',name='Terminal tools for Unavailable').click()
    await page.get_by_role('button',name='Place character for Unavailable').click()
    # A shown character carries the tile's In and Out (2026-10-10): the pills step aside until characters are off.
    for name in ['Link from Unavailable character','Link to Unavailable character']:
        await expect(page.get_by_role('button',name=name,exact=True)).to_be_disabled()
    await page.locator('#ds-character-toggle').click()
    for name in ['Link from Unavailable','Link to Unavailable']:
        await expect(page.get_by_role('button',name=name,exact=True)).to_be_disabled()
    assert not [call for call in api.calls if call['action']=='link']
    assert not any(m.get('type')=='input' for _,m in api.socket_messages)
    assert not errors


async def test_character_link_busy_guard_does_not_duplicate_request(board_page):
    import asyncio
    page,api,errors=board_page
    for provider in ['anthropic','codex']: await add(page,provider)
    await page.locator('#ds-character-toggle').click()
    started=asyncio.Event();release=asyncio.Event()
    async def delayed(route):
        if route.request.method=='POST' and route.request.post_data_json['action']=='link':
            started.set();await release.wait()
        await api.route(route)
    await page.route('**/api/deepsleep',delayed)
    for card in await page.locator('.ds-node').all(): await card.get_by_role('button',name='Place character for ',exact=False).click()
    await page.get_by_role('button',name='Link from Claude character').click()
    incoming=page.get_by_role('button',name='Link to Codex character')
    try:
        await incoming.click();await asyncio.wait_for(started.wait(),2)
        await incoming.click()
        await expect(page.locator('.ds-feedback')).to_contain_text('Wait for the current workspace change')
    finally: release.set()
    await expect(page.locator('.ds-feedback')).to_have_text('Terminals connected.')
    assert len([call for call in api.calls if call['action']=='link'])==1
    assert not errors


async def test_large_automatic_character_at_top_uses_safe_header_fallback(board_page):
    page,api,errors=board_page
    await add(page)
    api.state['nodes'][0]['layout']['y']=8
    await page.get_by_role('button',name='Refresh workspace').click()
    await page.locator('#ds-character-toggle').click()
    await page.get_by_label('Character size for Claude').select_option('large')
    anchor=page.locator('.ds-avatar-anchor')
    await expect(anchor).to_be_hidden()
    await expect(page.get_by_role('button',name='Link from Claude',exact=True)).to_be_visible()
    # When sufficient canvas is available, the same size is shown without covering text.
    api.state['nodes'][0]['layout']['y']=180
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(anchor).to_be_visible()
    box=await anchor.bounding_box();terminal=await page.locator('.ds-terminal').bounding_box()
    assert box['height']==144 and box['y']+box['height']<=terminal['y']
    await page.get_by_role('button',name='Link from Claude character').click()
    await expect(page.locator('.ds-feedback')).to_contain_text('Now choose Input')
    assert not errors
