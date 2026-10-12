"""Six floating sessions and local header collapse; no real provider processes."""
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest
from playwright.async_api import expect
from test_deepsleep_ui import board_page, STATIC, BoardAPI
from test_deepsleep_activity_ui import setup, recorded
from test_frontier_switches_ui import frontiers


async def test_header_collapse_retains_terminal_geometry_draft_and_flags_output(board_page):
    page,api,errors=board_page
    card,state=await setup(page,api)
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    await card.get_by_role('button',name='Place character for Claude').click()
    await page.locator('#ds-character-toggle').click()
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    await page.get_by_label('Text layout',exact=True).select_option('conversation')
    await expect(card.locator('.ds-event')).to_have_count(6)
    await page.locator('#ds-character-toggle').click()
    await page.wait_for_function('document.querySelector(".ds-avatar-slot img").naturalWidth>0')
    await card.get_by_role('button',name='Toggle terminal input and approvals').click()
    await expect(card.locator('.xterm')).to_be_hidden()
    before=await card.bounding_box()
    stored=deepcopy(api.state['nodes'][0]['layout'])
    calls=len(api.calls)
    await page.evaluate('window.originalTerminals=[...fixtureTerminals]')
    await card.get_by_role('button',name='Collapse Claude to header').press('Enter')
    await expect(card.locator('.xterm')).to_be_hidden()
    assert (await card.bounding_box())['height']==44
    assert (await card.bounding_box())['width']<=420
    await expect(page.locator('.ds-avatar-slot').first).to_be_visible()
    assert api.state['nodes'][0]['layout']==stored and len(api.calls)==calls
    count=len(state['requests'])
    await page.wait_for_timeout(1100)
    assert len(state['requests'])==count
    # PTY output remains buffered, but does not expand or send any response.
    api.sockets['node-1'].send(json.dumps({'type':'output','data':'\r\nAllow file write? [y/n] COLLAPSED_MARKER'}))
    await expect(card.get_by_role('button',name='Expand Claude terminal; new output')).to_be_visible()
    await expect(card.locator('.ds-collapsed-output')).to_have_text('New output')
    await expect(card.locator('.xterm')).to_be_hidden()
    await page.wait_for_function('''() => {const b=fixtureTerminals[0].buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i).translateToString()).some(line=>line.includes('COLLAPSED_MARKER'));}''')
    assert not any(message.get('type')=='input' for _,message in api.socket_messages)
    # A visible drawer may use the same activity reader while the pane is hidden.
    await card.get_by_role('button',name='Session details for Claude').click()
    await expect(page.get_by_role('dialog')).to_be_visible()
    await page.wait_for_function('document.querySelector(".ds-session-status").textContent.includes("metadata only")')
    assert len(state['requests'])>count
    await page.get_by_role('button',name='Close session details').press('Escape')
    # Collapsed movement saves expanded dimensions, not the visual header size.
    await card.get_by_role('button',name='Move Claude; use arrow keys').press('ArrowRight')
    await expect(page.get_by_role('button',name='Refresh workspace')).to_be_enabled()
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(card).to_have_class(re.compile('ds-collapsed'))
    assert api.state['nodes'][0]['layout']['width']==stored['width']
    assert api.state['nodes'][0]['layout']['height']==stored['height']
    await card.get_by_role('button',name='Expand Claude terminal; new output').click()
    await expect(card.locator('.xterm')).to_be_visible()
    after=await card.bounding_box()
    assert after['width']==before['width'] and after['height']==before['height']
    assert after['x']==before['x']+10 and after['y']==before['y']
    await expect(card.locator('.ds-collapsed-output')).to_be_hidden()
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    assert await page.evaluate('fixtureTerminals.length===1 && fixtureTerminals[0]===originalTerminals[0]')
    assert sum('token' in message for _,message in api.socket_messages)==1
    assert not any(call['action']=='restart' for call in api.calls)
    assert not errors


@pytest.mark.parametrize('size',[(1920,1080),(2348,1251)])
@pytest.mark.parametrize('profile',['native','dream'])
async def test_six_full_shell_panes_keep_grid_instances_and_readability(frontiers,size,profile):
    page,_,_,errors=frontiers
    api=BoardAPI();api.state['workspace']='/workspace/Dream'
    providers=['anthropic','codex','antigravity','grok','anthropic' if profile=='native' else 'machx','codex']
    titles=['Claude','Codex','Gemini','Grok','Reviewer' if profile=='native' else 'Local','Verifier']
    for i,(provider,title) in enumerate(zip(providers,titles),1):
        api.state['nodes'].append({'id':f'node-{i}','provider':provider,'profile':profile,'title':title,
            'model':'Fixture session','state':'ready','workspace':'/workspace/Dream',
            'initial_permissions':'ask','initial_effort':'high',
            'capabilities':{'linked':True,'automatic':profile=='dream','commands':{'model':'/model','effort':'/effort','permissions':'/permissions','changes':'/changes'}},
            'layout':{'x':6,'y':6,'width':600,'height':500}})
    snapshot=recorded();snapshot['events'][3]['data']['content']='Source inspection completed. No session was restarted.'
    async def activity(route):
        await route.fulfill(json=snapshot if profile=='dream' else {'available':False,'events':[],'trimmed':False,'generation':'','reason':'Native CLI does not expose structured activity. Use its terminal.'})
    await page.route('**/api/deepsleep',api.route)
    await page.route('**/api/deepsleep/activity?*',activity)
    await page.route_web_socket('**/ws/deepsleep/*',api.socket)
    source=(STATIC/'vendor/xterm.js').read_text()+'''\n;(()=>{window.fixtureTerminals=[];const Base=window.Terminal;window.Terminal=class extends Base {constructor(options){super(options);fixtureTerminals.push(this);}};})();'''
    await page.route('**/assets/vendor/xterm.js',lambda route:route.fulfill(content_type='application/javascript',body=source))
    await page.reload()
    await page.set_viewport_size({'width':size[0],'height':size[1]})
    await page.locator('#dream-nav-deepsleep').click()
    await expect(page.locator('.xterm')).to_have_count(6)
    await page.evaluate('window.originalTerminals=[...fixtureTerminals]')
    await page.locator('#ds-character-toggle').click()
    await page.wait_for_function('Array.from(document.querySelectorAll(".ds-avatar-slot img")).every(i=>i.naturalWidth>0)')
    tile=page.get_by_role('button',name='Tile terminals',exact=True)
    await tile.click();await expect(tile).to_be_enabled()
    for i,title in enumerate(titles,1):
        api.sockets[f'node-{i}'].send(json.dumps({'type':'output','data':
            '\x1b[2J\x1b[H\x1b[38;2;242;166;92m'+title+' · session '+str(i)+'\x1b[0m\r\n\r\n'
            'Six independent floating terminals.\r\n'
            'Review files, compare output, keep context.\r\n\r\n'
            '  • Same running terminal\r\n  • Retained scrollback and draft\r\n'
            '  • Manual linked handoff available\r\n\r\n'
            '\x1b[38;2;228;197;138mpermissions ask · effort high\r\nDream · context fixture\x1b[0m\r\n\r\nyou> '}))
    measures={}
    for mode in ['terminal','conversation']:
        await page.get_by_label('Text layout',exact=True).select_option(mode)
        if mode=='conversation':
            if profile=='dream':await expect(page.locator('.ds-event')).to_have_count(36)
            else:await expect(page.locator('.ds-event-status').last).to_contain_text('Native activity')
        await page.wait_for_timeout(100)
        boxes=await page.locator('.ds-node').evaluate_all('''nodes=>{const v=document.querySelector('.ds-viewport').getBoundingClientRect();return nodes.map((n,i)=>{const b=n.getBoundingClientRect(),t=n.querySelector('.ds-terminal').getBoundingClientRect(),events=n.querySelector('.ds-event-list').getBoundingClientRect();return {x:b.x,y:b.y,w:b.width,h:b.height,inside:b.left>=v.left&&b.right<=v.right+1&&b.top>=v.top&&b.bottom<=v.bottom+1,cols:fixtureTerminals[i].cols,rows:fixtureTerminals[i].rows,font:fixtureTerminals[i].options.fontSize,terminalHeight:t.height,eventHeight:events.height};});}''')
        assert all(b['inside'] for b in boxes),boxes
        assert len({b['x'] for b in boxes})==3 and len({b['y'] for b in boxes})==2
        assert all(b['cols']>=50 and b['font']>=(12 if profile=='native' else 13) for b in boxes),boxes
        assert all(b['rows']>=(5 if profile=='dream' and mode=='conversation' else 16) for b in boxes),boxes
        if profile=='dream' and mode=='conversation':assert all(b['eventHeight']>=100 for b in boxes),boxes
        for i,a in enumerate(boxes):
            for b in boxes[i+1:]:assert a['x']+a['w']<=b['x'] or b['x']+b['w']<=a['x'] or a['y']+a['h']<=b['y'] or b['y']+b['h']<=a['y']
        if profile=='dream':
            assert await page.locator('.ds-handoff-row:visible').count()==0
            assert await page.locator('.ds-launch-info:visible').count()==0
        assert await page.locator('.ds-repo:visible').count()==6
        assert await page.evaluate('fixtureTerminals.length===6 && fixtureTerminals.every((t,i)=>t===originalTerminals[i])')
        if mode=='conversation' and profile=='dream':
            await page.locator('.ds-event-list').evaluate_all('lists=>lists.forEach(e=>e.scrollTop=0)')
        await page.screenshot(path=f'/tmp/dream275-six-{profile}-{mode}-{size[0]}.png')
        measures[mode]=boxes
    # Collapse one pane without resizing its five neighbors; restore exact grid.
    cards=page.locator('.ds-node');first=cards.first
    original=[await cards.nth(i).bounding_box() for i in range(6)]
    await first.get_by_role('button',name='Collapse Claude to header').click()
    api.sockets['node-1'].send(json.dumps({'type':'output','data':'\r\nNew output while collapsed. Review when ready.'}))
    await expect(first.locator('.ds-collapsed-output')).to_be_visible()
    assert [await cards.nth(i).bounding_box() for i in range(1,6)]==original[1:]
    await page.screenshot(path=f'/tmp/dream275-six-{profile}-collapsed-{size[0]}.png')
    await first.get_by_role('button',name='Expand Claude terminal; new output').click()
    assert [await cards.nth(i).bounding_box() for i in range(6)]==original
    assert sum('token' in message for _,message in api.socket_messages)==6
    assert not any(call['action'] in ['send','restart','create'] for call in api.calls)
    Path(f'/tmp/dream275-six-{profile}-{size[0]}-geometry.json').write_text(json.dumps(measures,indent=2))
    if profile=='dream':
        await page.emulate_media(reduced_motion='no-preference')
        avatar=page.locator('.ds-avatar-slot').first
        await avatar.press('Shift+ArrowLeft');await avatar.press('Shift+ArrowUp')
        api.state['links']=[{'id':'visual-link','source':'node-1','target':'node-2','auto_forward':False}]
        await page.get_by_role('button',name='Refresh workspace').click()
        api.state['receipts']=[{'id':'visual-receipt','source':'node-1','target':'node-2','state':'queued'}]
        await page.get_by_role('button',name='Refresh workspace').click()
        await expect(page.locator('.ds-flow-pulse')).to_have_count(1)
        # The send is a script-drawn electric bolt along the cord (the owner, 2026-10-11), no longer a CSS dash animation.
        await page.wait_for_function("document.querySelector('.ds-flow-core')?.getAttribute('d')?.split('L').length>5")
        await page.wait_for_timeout(100)
        await page.screenshot(path=f'/tmp/dream276-six-floating-transfer-{size[0]}.png')
    assert not errors


async def test_collapse_preserves_intentional_terminal_scrollback_and_pointer_drag(board_page):
    page,api,errors=board_page
    card,state=await setup(page,api)
    api.sockets['node-1'].send(json.dumps({'type':'output','data':''.join(f'\r\nHistory line {i:03}' for i in range(160))}))
    await page.wait_for_function('fixtureTerminals[0].buffer.active.baseY>80')
    await card.locator('.xterm-screen').hover()
    await page.mouse.wheel(0,-650)
    await expect(card.get_by_role('button',name='Follow latest output in Claude')).to_be_visible()
    await page.wait_for_timeout(100)
    anchor=await page.evaluate('fixtureTerminals[0].buffer.active.viewportY')
    await card.get_by_role('button',name='Collapse Claude to header').click()
    before=await card.bounding_box()
    handle=await card.get_by_role('button',name='Move Claude; use arrow keys').bounding_box()
    await page.mouse.move(handle['x']+handle['width']/2,handle['y']+handle['height']/2)
    await page.mouse.down();await page.mouse.move(handle['x']+handle['width']/2+25,handle['y']+handle['height']/2+20);await page.mouse.up()
    await page.wait_for_function('document.querySelector(".ds-node").style.left==="55px"')
    api.sockets['node-1'].send(json.dumps({'type':'output','data':'\r\nRetained after collapse'}))
    await card.get_by_role('button',name='Expand Claude terminal; new output').click()
    await expect(card.locator('.xterm')).to_be_visible()
    await page.wait_for_timeout(100)
    assert await page.evaluate('fixtureTerminals[0].buffer.active.viewportY')==anchor
    after=await card.bounding_box()
    assert after['x']==before['x']+25 and after['y']==before['y']+20
    assert sum('token' in message for _,message in api.socket_messages)==1
    assert not errors


async def test_tile_all_six_collapsed_and_maximize_restore_preserve_sessions(board_page):
    from test_deepsleep_ui import add
    page,api,errors=board_page
    await page.set_viewport_size({'width':1920,'height':1080})
    for provider in ['anthropic','codex','antigravity']*2:
        await add(page,provider)
        await page.locator('.ds-node').last.get_by_role('button',name='Terminal tools for ',exact=False).click()
    tile=page.get_by_role('button',name='Tile terminals',exact=True)
    await tile.click();await expect(tile).to_be_enabled()
    cards=page.locator('.ds-node')
    expanded=[await cards.nth(i).bounding_box() for i in range(6)]
    await page.evaluate('window.originalTerminals=[...fixtureTerminals]')
    for i in range(6):
        await cards.nth(i).get_by_role('button',name=re.compile('^Collapse .* to header$')).click()
    await expect(page.locator('.ds-node.ds-collapsed')).to_have_count(6)
    await tile.click();await expect(tile).to_be_enabled()
    await expect(page.locator('.ds-node.ds-collapsed')).to_have_count(6)
    assert all([(await cards.nth(i).bounding_box())['height']==44 for i in range(6)])
    assert all(n['layout']['height']>=300 for n in api.state['nodes'])
    first=cards.first
    await first.get_by_role('button',name='Maximize Claude').click()
    await expect(first.locator('.xterm')).to_be_visible()
    await expect(page.locator('.ds-node.ds-collapsed')).to_have_count(5)
    await first.get_by_role('button',name='Restore Claude',exact=True).click()
    assert await first.bounding_box()==expanded[0]
    # Collapsing a maximized view returns it to its saved floating geometry.
    await first.get_by_role('button',name='Maximize Claude').click()
    await first.get_by_role('button',name='Collapse Claude to header').click()
    await expect(page.locator('.ds-node.ds-maximized')).to_have_count(0)
    await expect(page.locator('.ds-node.ds-collapsed')).to_have_count(6)
    await first.get_by_role('button',name=re.compile('^Expand Claude terminal')).click()
    assert await first.bounding_box()==expanded[0]
    assert await page.evaluate('fixtureTerminals.length===6 && fixtureTerminals.every((t,i)=>t===originalTerminals[i])')
    assert sum('token' in message for _,message in api.socket_messages)==6
    assert not any(call['action'] in ['restart','send'] for call in api.calls)
    assert not errors


async def test_activity_scrollback_survives_collapse_with_drawer_polling(board_page):
    page,api,errors=board_page
    card,state=await setup(page,api)
    state['snapshot']['events']=[{'id':str(i),'kind':'tool_use','data':{'name':f'tool_{i}','input':{'path':f'file-{i}'}}} for i in range(60)]
    await page.get_by_label('Text layout',exact=True).select_option('activity')
    await expect(card.locator('.ds-event')).to_have_count(60)
    listing=card.locator('.ds-event-list')
    await listing.evaluate('(e)=>e.scrollTop=e.scrollHeight')
    await listing.hover();await page.mouse.wheel(0,-120)
    await page.wait_for_timeout(150)
    position=await listing.evaluate('(e)=>e.scrollTop')
    assert position>0
    await card.get_by_role('button',name='Collapse Claude to header').click()
    count=len(state['requests'])
    await card.get_by_role('button',name='Session details for Claude').click()
    await expect(page.get_by_role('dialog')).to_be_visible()
    await page.wait_for_timeout(1200)
    assert len(state['requests'])>=count+2
    await card.get_by_role('button',name=re.compile('^Expand Claude terminal')).click()
    await expect(listing).to_be_visible()
    await page.wait_for_timeout(150)
    assert abs(await listing.evaluate('(e)=>e.scrollTop')-position)<2
    assert not errors
