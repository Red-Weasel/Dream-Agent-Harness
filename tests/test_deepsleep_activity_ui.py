"""Read-only text layouts use real xterm with controlled recorded activity."""
import json
from copy import deepcopy

import pytest
from playwright.async_api import expect
from test_deepsleep_ui import board_page, add, xterm_assets, STATIC
from test_deepsleep_native_ui import native_page, create


def recorded():
    return {'available': True, 'generation': 'session-1', 'trimmed': False, 'events': [
        {'id': '0', 'kind': 'user', 'data': 'Inspect the current implementation.'},
        {'id': '1', 'kind': 'thinking_delta', 'data': 'Reported planning text from the fixture.'},
        {'id': '2', 'kind': 'tool_use', 'data': {'id': 'tool-1', 'name': 'read_file', 'input': {'path': 'src/layout.js'}}},
        {'id': '3', 'kind': 'tool_result', 'data': {'id': 'tool-1', 'content': '<script>window.injected=true</script>\nFile contents returned.', 'is_error': False}},
        {'id': '4', 'kind': 'agent_activity', 'data': {'agent': 'reviewer', 'run_id': 'r1', 'kind': 'response', 'text': 'The input and approval flow remains in the terminal.'}},
        {'id': '5', 'kind': 'text_delta', 'data': 'The layout keeps the transcript readable.\nTools and reported thinking can be expanded.'},
    ]}


async def setup(page, api):
    state = {'snapshot': recorded(), 'status': 200, 'requests': []}
    async def route(request):
        state['requests'].append((request.request.method, request.request.headers))
        await request.fulfill(status=state['status'], json=deepcopy(state['snapshot']))
    await page.route('**/api/deepsleep/activity?*', route)
    await add(page)
    card = page.locator('.ds-node').first
    await card.get_by_role('button', name='Terminal tools for Claude').click()
    await page.evaluate('window.originalTerminal=fixtureTerminals[0]')
    api.sockets['node-1'].send(json.dumps({'type': 'output', 'data': '\r\nAllow read_file? [y/n]\r\nyou> '}))
    return card, state


async def test_layouts_retain_terminal_input_draft_and_read_only_transport(board_page):
    page, api, errors = board_page
    card, state = await setup(page, api)
    assert not state['requests']
    await card.get_by_role('button', name='Terminal tools for Claude').click()
    await card.get_by_role('button', name='Terminal tools for Claude').click()
    calls=len(api.calls)
    for layout in ['conversation', 'activity', 'terminal', 'conversation']:
        await page.get_by_label('Text layout', exact=True).select_option(layout)
        await expect(card.locator('.xterm')).to_be_visible()
    await expect(card.locator('.ds-event')).to_have_count(6)
    assert await page.evaluate('fixtureTerminals.length===1 && fixtureTerminals[0]===originalTerminal')
    assert len(api.sockets)==1 and len(api.calls)==calls
    await expect(card.locator('.ds-event pre').nth(3)).to_have_text('<script>window.injected=true</script>\nFile contents returned.')
    assert not await page.evaluate('Boolean(window.injected)')
    tool=card.locator('.ds-event').nth(2)
    await tool.locator('summary').focus()
    await page.keyboard.press('Enter')
    await expect(tool).to_have_attribute('open', '')
    state['snapshot']['events'][-1]['data'] += '\nNew result.'
    await expect(card.locator('.ds-event pre').last).to_contain_text('New result.')
    await expect(tool).to_have_attribute('open', '')
    await card.get_by_role('button', name='Toggle terminal input and approvals').click()
    await expect(card.locator('.xterm')).to_be_hidden()
    await card.get_by_role('button', name='Toggle terminal input and approvals').press('Enter')
    await expect(card.locator('.xterm')).to_be_visible()
    await page.keyboard.type('y')
    assert any(m.get('type')=='input' and m.get('data')=='y' for _,m in api.socket_messages)
    assert all(method=='GET' and headers['x-dream-token']=='fixture-token' for method,headers in state['requests'])
    await page.get_by_role('button', name='Characters', exact=True).click()
    await expect(page.get_by_label('Text layout', exact=True)).to_have_value('conversation')
    assert not errors


async def test_activity_bound_history_scroll_trim_generation_and_poll_visibility(board_page):
    page, api, errors = board_page
    card, state = await setup(page, api)
    state['snapshot']['events']=[{'id':str(i),'kind':'text_delta','data':f'Unique recorded message {i}'} for i in range(260)]
    await page.get_by_label('Text layout', exact=True).select_option('activity')
    await expect(card.locator('.ds-event')).to_have_count(200)
    await expect(card.locator('.ds-event-status')).to_contain_text('omitted')
    listing=card.locator('.ds-event-list')
    await listing.evaluate('(e)=>e.scrollTop=250')
    before=await listing.evaluate('(e)=>e.scrollTop')
    await card.locator('.ds-event').nth(10).locator('summary').evaluate('(e)=>e.click()')
    state['snapshot']['events'].append({'id':'260','kind':'text_delta','data':'Newest recorded message'})
    await expect(card.locator('.ds-event').last).to_contain_text('Newest recorded message')
    assert abs(await listing.evaluate('(e)=>e.scrollTop')-before)<45
    # Stable IDs survive trimming; newly recorded content must not inherit expansion.
    state['snapshot']['trimmed']=True
    state['snapshot']['events']=[{'id':'261','kind':'tool_use','data':{'name':'new_tool','input':'new content'}}]
    await expect(card.locator('.ds-event')).to_have_count(1)
    assert not await card.locator('.ds-event').evaluate('(e)=>e.open')
    await card.locator('.ds-event summary').click()
    state['snapshot']['generation']='session-2'
    await expect(card.locator('.ds-event')).not_to_have_attribute('open','')
    await page.get_by_label('Text layout', exact=True).select_option('terminal')
    count=len(state['requests'])
    await page.wait_for_timeout(1200)
    assert len(state['requests'])==count
    await card.get_by_role('button', name='Terminal tools for Claude').click()
    await card.get_by_role('button', name='Recorded activity for Claude').click()
    await expect(card.locator('.ds-event-panel')).to_be_visible()
    await page.evaluate("document.documentElement.dataset.dreamView='chat'")
    count=len(state['requests'])
    await page.wait_for_timeout(1200)
    assert len(state['requests'])==count
    assert not errors


async def test_unavailable_error_recovery_preserves_terminal(board_page):
    page, api, errors = board_page
    card, state = await setup(page, api)
    state['snapshot']={'available':False,'generation':'','events':[],'trimmed':False,'reason':'Native CLI does not publish structured Dream activity.'}
    await page.get_by_label('Text layout', exact=True).select_option('conversation')
    await expect(card.locator('.ds-event-status')).to_have_text('Native activity is available in the terminal.')
    await expect(card.locator('.ds-event-status')).to_have_attribute('title','Native CLI does not publish structured Dream activity.')
    await expect(card.locator('.xterm')).to_be_visible()
    toggle=card.get_by_role('button',name='Toggle terminal input and approvals',include_hidden=True)
    await expect(toggle).to_be_disabled()
    await expect(toggle).to_be_hidden()
    state['status']=503
    await expect(card.get_by_role('button',name='Retry activity')).to_be_visible()
    await expect(card.locator('.ds-event-status')).to_contain_text('503')
    retry=await card.get_by_role('button',name='Retry activity').bounding_box()
    terminal=await card.locator('.ds-terminal').bounding_box()
    assert retry['y']+retry['height']<=terminal['y']
    await card.screenshot(path='/tmp/dream277-activity-error.png')
    state['status']=200
    state['snapshot']=recorded()
    await card.get_by_role('button',name='Retry activity').click()
    await expect(card.locator('.ds-event')).to_have_count(6)
    await expect(card.get_by_role('button',name='Retry activity')).to_be_hidden()
    assert len(api.sockets)==1
    assert not errors


@pytest.mark.parametrize('with_records',[False,True])
async def test_narrow_outage_retry_focus_and_terminal_geometry(board_page,with_records):
    page,api,errors=board_page
    card,state=await setup(page,api)
    if not with_records:state['snapshot']['events']=[]
    await page.get_by_label('Text layout',exact=True).select_option('conversation')
    await expect(card.locator('.ds-event-panel')).to_have_attribute('data-has-content',str(with_records).lower())
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    state['status']=503
    retry=card.get_by_role('button',name='Retry activity')
    await expect(retry).to_be_visible()
    # Ask the server for the minimum supported narrow/short card geometry.
    api.state['nodes'][0]['layout'].update(width=340,height=300)
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.get_by_role('button',name='Refresh workspace')).to_be_enabled()
    await retry.focus()
    before=len(state['requests'])
    await page.wait_for_function('()=>document.activeElement?.classList.contains("ds-event-retry")')
    await page.wait_for_timeout(2200)
    assert len(state['requests'])>=before+2
    await expect(retry).to_be_focused()
    geometry=await card.evaluate('e=>{const box=s=>e.querySelector(s).getBoundingClientRect();return {card:box(".ds-content").toJSON(),terminal:box(".ds-terminal").toJSON(),retry:box(".ds-event-retry").toJSON()};}')
    assert geometry['retry']['bottom']<=geometry['terminal']['top']
    assert geometry['terminal']['height']>=(130 if with_records else 70)
    assert geometry['terminal']['bottom']<=geometry['card']['bottom']+1
    assert geometry['retry']['right']<=geometry['card']['right']+1
    await card.screenshot(path=f'/tmp/dream277-activity-narrow-error-{with_records}.png')
    assert not errors


@pytest.mark.parametrize('width',[1500,390])
async def test_three_layout_rendering_and_geometry(board_page,width):
    page, api, errors = board_page
    await page.set_viewport_size({'width':width,'height':980})
    card,state=await setup(page,api)
    await card.get_by_role('button',name='Maximize Claude').click()
    for mode in ['terminal','conversation','activity']:
        await page.get_by_label('Text layout',exact=True).select_option(mode)
        if mode=='terminal':
            await card.get_by_role('button',name='Terminal tools for Claude').click()
            await card.get_by_role('button',name='Recorded activity for Claude').click()
            await card.get_by_role('button',name='Terminal tools for Claude').click()
        await expect(card.locator('.ds-event')).to_have_count(6)
        if not await card.locator('.ds-event').nth(2).evaluate('(e)=>e.open'):
            await card.locator('.ds-event').nth(2).locator('summary').click()
        await page.screenshot(path=f'/tmp/dream272-{mode}-{width}.png')
        bounds=await card.evaluate('''e=>{const c=e.getBoundingClientRect(), t=e.querySelector('.ds-terminal').getBoundingClientRect(),f=e.querySelector('.ds-footer-tools').getBoundingClientRect(); return {overflow:e.scrollWidth>e.clientWidth,terminal:t.height,fit:t.bottom<=f.top+1,width:c.width};}''')
        assert not bounds['overflow'] and bounds['terminal']>=70 and bounds['fit'],bounds
    assert not errors


async def test_layout_preference_reload_and_xterm_scrollback(board_page):
    page,api,errors=board_page
    card,state=await setup(page,api)
    api.sockets['node-1'].send(json.dumps({'type':'output','data':''.join(f'\r\nRetained history {i}' for i in range(130))}))
    await page.wait_for_function('fixtureTerminals[0].buffer.active.baseY>50')
    await card.locator('.xterm-screen').hover()
    await page.mouse.wheel(0,-600)
    await expect(card.get_by_role('button',name='Follow latest output in Claude')).to_be_visible()
    await page.wait_for_timeout(100)
    anchor=await page.evaluate('fixtureTerminals[0].buffer.active.viewportY')
    for mode in ['conversation','activity','terminal']:
        await page.get_by_label('Text layout',exact=True).select_option(mode)
        await page.wait_for_timeout(60)
        assert await page.evaluate('fixtureTerminals[0].buffer.active.viewportY')==anchor,mode
    await page.get_by_label('Text layout',exact=True).select_option('activity')
    await page.locator('#ds-character-toggle').click()
    await page.reload()
    terminal_js,fit_js,terminal_css=xterm_assets()
    for path in [terminal_css,STATIC/'deepsleep.css',STATIC/'deepsleep-drawer.css']:
        await page.add_style_tag(path=str(path))
    for path in [terminal_js,fit_js,STATIC/'deepsleep-drawer.js',STATIC/'deepsleep-activity.js',STATIC/'deepsleep.js']:
        await page.add_script_tag(path=str(path))
    await expect(page.get_by_label('Text layout',exact=True)).to_have_value('activity')
    await expect(page.locator('#ds-character-toggle')).to_have_attribute('aria-pressed','true')
    await expect(page.locator('.ds-event')).to_have_count(6)
    api.state['workspace']='/different-workspace'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.get_by_label('Text layout',exact=True)).to_have_value('terminal')
    assert not errors


async def test_native_unavailable_keeps_existing_terminal_and_shows_reason(native_page):
    page,api,errors=native_page
    await page.route('**/api/deepsleep/activity?*',lambda route:route.fulfill(json={
        'available':False,'events':[],'trimmed':False,'generation':'',
        'reason':'Native CLI does not expose Dream recorded activity. Use its terminal.'}))
    await create(page,expand=False)
    await page.get_by_label('Text layout',exact=True).select_option('activity')
    card=page.locator('.ds-node')
    await expect(card.locator('.ds-event-status')).to_have_text('Native activity is available in the terminal.')
    await expect(card.locator('.ds-event-status')).to_have_attribute('title','Native CLI does not expose Dream recorded activity. Use its terminal.')
    await expect(card.locator('.ds-event')).to_have_count(0)
    await expect(card.locator('.xterm')).to_be_visible()
    toggle=card.get_by_role('button',name='Toggle terminal input and approvals',include_hidden=True)
    await expect(toggle).to_be_disabled()
    await expect(toggle).to_be_hidden()
    assert not errors


from test_frontier_switches_ui import frontiers
from test_deepsleep_ui import BoardAPI


async def test_complete_shell_three_text_layout_previews(frontiers):
    page,_,_,errors=frontiers
    api=BoardAPI()
    api.state['workspace']='/workspace/Dream'
    api.state['nodes']=[{'id':'node-1','provider':'anthropic','profile':'dream','title':'Claude',
        'model':'Workflow session','state':'ready','workspace':'/workspace/Dream',
        'initial_permissions':'ask','initial_effort':'high',
        'capabilities':{'commands':{'model':'/model','effort':'/effort','changes':'/changes'}},
        'layout':{'x':6,'y':6,'width':750,'height':670}}]
    snapshot=recorded()
    snapshot['events'][3]['data']['content']='Read completed. The terminal input and linked receiver remain available.'
    await page.route('**/api/deepsleep',api.route)
    await page.route('**/api/deepsleep/activity?*',lambda route:route.fulfill(json=snapshot))
    await page.route_web_socket('**/ws/deepsleep/*',api.socket)
    await page.reload()
    await page.set_viewport_size({'width':1680,'height':1180})
    await page.locator('#dream-nav-deepsleep').click()
    card=page.locator('.ds-node')
    await expect(card.locator('.xterm')).to_be_visible()
    await card.get_by_role('button',name='Maximize Claude').click()
    api.sockets['node-1'].send(json.dumps({'type':'output','data':'\x1b[2J\x1b[HReviewing the recorded activity.\r\n\r\nAllow read_file? [y/n]\r\nyou> '}))
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    await card.get_by_role('button',name='Recorded activity for Claude').click()
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    for width in [1680,390]:
        await page.set_viewport_size({'width':width,'height':1180 if width==1680 else 1040})
        for mode in ['terminal','conversation','activity']:
            await page.get_by_label('Text layout',exact=True).select_option(mode)
            await expect(card.locator('.ds-event')).to_have_count(6)
            tool=card.locator('.ds-event').nth(2)
            if not await tool.evaluate('(e)=>e.open'):
                await tool.locator('summary').click()
            await expect(card.locator('.xterm')).to_be_visible()
            await page.screenshot(path=f'/tmp/dream272-complete-{mode}-{width}.png')
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert sum('token' in message for _,message in api.socket_messages)==1
    assert not errors


async def test_later_approval_output_reopens_hidden_terminal(board_page):
    page,api,errors=board_page
    card,state=await setup(page,api)
    await page.get_by_label('Text layout',exact=True).select_option('conversation')
    await expect(card.locator('.ds-event')).to_have_count(6)
    await card.get_by_role('button',name='Toggle terminal input and approvals').click()
    await expect(card.locator('.xterm')).to_be_hidden()
    before=[message for _,message in api.socket_messages if message.get('type')=='input']
    api.sockets['node-1'].send(json.dumps({'type':'output','data':'\r\nAllow write_file? [y/n] '}))
    await expect(card.locator('.xterm')).to_be_visible()
    await expect(card.locator('.ds-node-note')).to_contain_text('Terminal reopened for new output')
    assert [message for _,message in api.socket_messages if message.get('type')=='input']==before
    assert not errors


@pytest.mark.parametrize('failure',['snapshot','http'])
async def test_unavailable_snapshot_retains_stale_rows_and_scroll_until_generation_changes(board_page,failure):
    page,api,errors=board_page
    card,state=await setup(page,api)
    current={'available':True,'generation':'session-1','trimmed':False,'events':[
        {'id':str(i),'kind':'tool_use','data':{'name':f'tool_{i}','input':{'path':f'file-{i}'}}} for i in range(60)]}
    state['snapshot']=current
    await page.get_by_label('Text layout',exact=True).select_option('activity')
    await expect(card.locator('.ds-event')).to_have_count(60)
    entry=card.locator('.ds-event').nth(12)
    await entry.locator('summary').click()
    listing=card.locator('.ds-event-list')
    await listing.evaluate('(e)=>e.scrollTop=300')
    scroll=await listing.evaluate('(e)=>e.scrollTop')
    state['snapshot']={'available':False,'generation':'','trimmed':False,'events':[],'reason':'Temporarily unavailable.'}
    if failure=='http':state['status']=503
    await expect(card.locator('.ds-event-status')).to_contain_text('Previous records are stale')
    await expect(card.locator('.ds-event')).to_have_count(60)
    state['snapshot']=current
    state['status']=200
    await expect(card.locator('.ds-event-panel')).to_have_attribute('data-stale','false')
    await expect(entry).to_have_attribute('open','')
    assert abs(await listing.evaluate('(e)=>e.scrollTop')-scroll)<2
    state['snapshot']={**current,'generation':'session-2','events':current['events'][:1]}
    await expect(card.locator('.ds-event')).to_have_count(1)
    assert not errors


@pytest.mark.parametrize('gesture',['wheel','keyboard'])
async def test_outage_manual_scroll_replaces_pre_outage_anchor_on_recovery(board_page,gesture):
    page,api,errors=board_page
    card,state=await setup(page,api)
    current={'available':True,'generation':'session-1','trimmed':False,'events':[
        {'id':str(i),'kind':'tool_use','data':{'name':f'tool_{i}','input':{'path':f'file-{i}'}}} for i in range(60)]}
    state['snapshot']=current
    await page.get_by_label('Text layout',exact=True).select_option('activity')
    await expect(card.locator('.ds-event')).to_have_count(60)
    listing=card.locator('.ds-event-list')
    await listing.evaluate('(e)=>e.scrollTop=300')
    state['snapshot']={'available':False,'generation':'','trimmed':False,'events':[],'reason':'Temporarily unavailable.'}
    await expect(card.locator('.ds-event-status')).to_contain_text('Previous records are stale')
    if gesture=='wheel':
        await listing.hover()
        await page.mouse.wheel(0,180)
    else:
        await listing.focus()
        await page.keyboard.press('ArrowDown')
        await page.keyboard.press('ArrowDown')
    await page.wait_for_timeout(250)
    chosen=await listing.evaluate('(e)=>e.scrollTop')
    assert chosen>320
    # A subsequent unavailable poll must not reinstate the pre-outage anchor.
    await page.wait_for_timeout(1100)
    state['snapshot']=current
    await expect(card.locator('.ds-event-panel')).to_have_attribute('data-stale','false')
    assert abs(await listing.evaluate('(e)=>e.scrollTop')-chosen)<2
    assert not errors


async def test_workspace_display_name_updates_existing_and_future_labels_only(board_page):
    page,api,errors=board_page
    api.state['workspace']='/workspace/alpha'
    card,state=await setup(page,api)
    await add(page,'codex')
    await page.get_by_label('Text layout',exact=True).select_option('conversation')
    labels=page.locator('.ds-event[data-kind=user] .ds-event-label')
    await expect(labels).to_have_text(['You','You'])
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    await page.evaluate('window.originalTerminals=[...fixtureTerminals]')
    calls=deepcopy(api.calls)
    messages=deepcopy(api.socket_messages)
    name=card.get_by_label('Your message label (all terminals)',exact=True)
    await name.fill('  <b>River</b>  ')
    await name.press('Enter')
    await expect(name).to_have_value('<b>River</b>')
    await expect(labels).to_have_text(['<b>River</b>','<b>River</b>'])
    assert await labels.locator('b').count()==0
    await expect(page.locator('.ds-node').nth(1).get_by_label('Your message label (all terminals)',exact=True)).to_have_value('<b>River</b>')
    state['snapshot']['events'].append({'id':'6','kind':'user','data':'Next recorded user message.'})
    await expect(labels).to_have_text(['<b>River</b>']*4)
    await expect(card.locator('.ds-event[data-kind=user] pre')).to_have_text(['Inspect the current implementation.','Next recorded user message.'])
    assert state['snapshot']['events'][0]['data']=='Inspect the current implementation.'
    await name.fill('x'*80)
    await name.press('Enter')
    await expect(name).to_have_value('x'*60)
    await expect(labels).to_have_text(['x'*60]*4)
    await name.fill('   ')
    await name.press('Enter')
    await expect(name).to_have_value('You')
    await expect(labels).to_have_text(['You']*4)
    assert api.calls==calls
    assert [m for _,m in api.socket_messages if m.get('type')=='input']==[m for _,m in messages if m.get('type')=='input']
    assert sum('token' in m for _,m in api.socket_messages)==2
    assert await page.evaluate('fixtureTerminals.length===2 && fixtureTerminals.every((term,i)=>term===originalTerminals[i])')
    assert not errors


async def test_display_name_persistence_workspace_isolation_and_blocked_storage(board_page):
    page,api,errors=board_page
    api.state['workspace']='/workspace/alpha'
    card,state=await setup(page,api)
    await page.get_by_label('Text layout',exact=True).select_option('conversation')
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    name=card.get_by_label('Your message label (all terminals)',exact=True)
    await name.fill('  River  ')
    await name.press('Tab')  # Blur saves through the ordinary change event.
    await expect(name).to_have_value('River')
    await page.reload()
    terminal_js,fit_js,terminal_css=xterm_assets()
    for path in [terminal_css,STATIC/'deepsleep.css',STATIC/'deepsleep-drawer.css']:
        await page.add_style_tag(path=str(path))
    for path in [terminal_js,fit_js,STATIC/'deepsleep-drawer.js',STATIC/'deepsleep-activity.js',STATIC/'deepsleep.js']:
        await page.add_script_tag(path=str(path))
    card=page.locator('.ds-node')
    await expect(card.locator('.ds-event[data-kind=user] .ds-event-label')).to_have_text('River')
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    name=card.get_by_label('Your message label (all terminals)',exact=True)
    await expect(name).to_have_value('River')
    api.state['workspace']='/workspace/beta'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(name).to_have_value('You')
    await name.fill('Sky')
    await name.press('Enter')
    api.state['workspace']='/workspace/alpha'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(name).to_have_value('River')
    await name.fill('')
    await name.press('Enter')
    await expect(name).to_have_value('You')
    assert await page.evaluate('localStorage.getItem(\'dream.deepsleep.display-name:"/workspace/alpha"\')')=='You'
    await page.evaluate("() => {Storage.prototype.setItem=()=>{throw new DOMException('Blocked','SecurityError')};Storage.prototype.getItem=()=>{throw new DOMException('Blocked','SecurityError')}}")
    await name.fill('Local label')
    await name.press('Enter')
    await expect(card.locator('.ds-event[data-kind=user] .ds-event-label')).to_have_text('Local label')
    await expect(page.locator('.ds-feedback')).to_contain_text('Browser storage is unavailable')
    api.state['workspace']='/workspace/gamma'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(name).to_have_value('You')
    assert not errors


async def test_display_name_tools_field_is_readable_at_phone_width(board_page):
    page,api,errors=board_page
    card,state=await setup(page,api)
    await page.set_viewport_size({'width':390,'height':1040})
    await card.get_by_role('button',name='Maximize Claude').click()
    await page.get_by_label('Text layout',exact=True).select_option('conversation')
    await card.get_by_role('button',name='Terminal tools for Claude').click()
    name=card.get_by_label('Your message label (all terminals)',exact=True)
    await name.fill('River')
    await name.press('Enter')
    await expect(card.locator('.ds-event[data-kind=user] .ds-event-label')).to_have_text('River')
    await name.scroll_into_view_if_needed()
    await card.locator('.ds-event-list').evaluate('(e)=>e.scrollTop=0')
    await page.screenshot(path='/tmp/dream274-display-name-390.png')
    assert await name.evaluate('(e)=>e.getBoundingClientRect().right<=e.closest(".ds-node").getBoundingClientRect().right && parseFloat(getComputedStyle(e).fontSize)>=13')
    assert not errors


async def test_empty_lifecycle_records_are_compact_but_real_details_remain(board_page):
    page,api,errors=board_page
    card,state=await setup(page,api)
    state['snapshot']['events']=[
        {'id':'1','kind':'turn_start','data':{}},
        {'id':'2','kind':'turn_end','data':{}},
        {'id':'3','kind':'turn_start','data':None},
    ]
    await page.get_by_label('Text layout',exact=True).select_option('activity')
    panel=card.locator('.ds-event-panel')
    await expect(panel).to_have_attribute('data-has-content','false')
    await expect(panel).to_have_attribute('data-busy','true')
    await expect(card.locator('.ds-event')).to_have_count(0)
    await expect(card.locator('.ds-event-status')).to_have_text('Working… Activity will appear here.')
    await expect(card.locator('.xterm')).to_be_visible()
    await card.screenshot(path='/tmp/dream277-activity-busy.png')
    state['snapshot']['events'].append({'id':'4','kind':'turn_end','data':''})
    await expect(panel).to_have_attribute('data-busy','false')
    await expect(card.locator('.ds-event-status')).to_have_text('No activity yet.')
    state['snapshot']['events'] += [
        {'id':'5','kind':'turn_end','data':{'usage':{'output_tokens':12}}},
        {'id':'6','kind':'thinking_delta','data':'Reported thought'},
        {'id':'7','kind':'tool_use','data':{'name':'read_file','input':{}}},
    ]
    await expect(card.locator('.ds-event')).to_have_count(3)
    await expect(panel).to_have_attribute('data-has-content','true')
    await expect(card.locator('.ds-event[data-kind=turn_end] pre')).to_contain_text('output_tokens')
    tool=card.locator('.ds-event[data-kind=tool_use]')
    await tool.locator('summary').press('Enter')
    await expect(tool).to_have_attribute('open','')
    state['snapshot']['events'].append({'id':'8','kind':'turn_end','data':[]})
    await page.wait_for_timeout(1100)
    await expect(card.locator('.ds-event')).to_have_count(3)
    await expect(tool).to_have_attribute('open','')
    assert not errors
