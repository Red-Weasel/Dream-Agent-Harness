"""Session drawer behavior over the single recorded-activity reader."""
import json
import time

import pytest
from playwright.async_api import expect
from test_deepsleep_ui import board_page, BoardAPI
from test_deepsleep_activity_ui import setup, recorded
from test_frontier_switches_ui import frontiers
from test_deepsleep_native_ui import native_page, create


def metadata():
    return {'session_id':'recorded-session',
        'changes':{'available':True,'total':6,'trimmed':False,'reason':'Confirmed file tools only; not a Git diff.',
            'items':[{'path':f'/workspace/Dream/src/layout-{i}.js'} for i in range(6)]},
        'outputs':{'available':True,'total':6,'trimmed':True,
            'items':[{'id':f'artifact-{i}','title':f'Layout preview {i}','type':'image','path':f'artifacts/preview-{i}.png','bytes':1200,'version':1} for i in range(6)]},
        'agents':{'available':True,'total':2,'trimmed':False,'items':[
            {'id':'agent-1','name':'Reviewer','state':'working','model':'fixture-model'},
            {'id':'agent-2','name':'Verifier','state':'completed','model':'fixture-model'}]},
        'sources':{'available':False,'total':0,'trimmed':False,'items':[],
            'reason':'No per-session submitted-attachment ledger is available.'}}


async def test_drawer_reuses_polling_preserves_metadata_state_and_keyboard_focus(board_page):
    page,api,errors=board_page
    card,state=await setup(page,api)
    state['snapshot']['session']=metadata()
    requests=[]
    async def route(request):
        requests.append(time.monotonic())
        await request.fulfill(json=state['snapshot'])
    await page.route('**/api/deepsleep/activity?*',route)
    opener=card.get_by_role('button',name='Session details for Claude')
    await opener.click()
    drawer=page.get_by_role('dialog',name='Session details for Claude')
    await expect(drawer).to_be_visible()
    await expect(drawer.locator('.ds-session-status')).to_contain_text('metadata only')
    sections=drawer.locator('.ds-session-section')
    changes=sections.nth(0)
    await expect(changes.locator('.ds-session-item')).to_have_count(4)
    await expect(changes.locator('.ds-session-item-name').first).to_have_text('layout-0.js')
    row=changes.locator('.ds-session-item').first
    await row.locator('summary').click()
    await expect(row.locator('pre')).to_contain_text('/workspace/Dream/src/layout-0.js')
    await row.evaluate('(e)=>window.retainedDrawerRow=e')
    await page.get_by_label('Text layout',exact=True).select_option('conversation')
    await expect(card.locator('.ds-event')).to_have_count(6)
    state['snapshot']['session']['agents']['items'][0]['state']='completed'
    await expect(sections.nth(2)).not_to_contain_text('working')
    assert all(b-a>.85 for a,b in zip(requests,requests[1:])),requests
    assert await row.evaluate('(e)=>e===window.retainedDrawerRow && e.open')
    await changes.get_by_role('button',name='Show more (6) changes').click()
    await expect(changes.locator('.ds-session-item')).to_have_count(6)
    await expect(sections.nth(1)).to_contain_text('Some recorded items are omitted')
    await expect(sections.nth(3)).to_contain_text('No per-session submitted-attachment ledger')
    # A section may recover with byte-identical data; it must rebuild its rows.
    saved=state['snapshot']['session']['changes']
    state['snapshot']['session']['changes']={'available':False,'items':[],'reason':'Changes unavailable.'}
    await expect(changes).to_contain_text('Changes unavailable')
    state['snapshot']['session']['changes']=saved
    await expect(changes.locator('.ds-session-item')).to_have_count(6)
    snapshot=state['snapshot']
    state['snapshot']={'available':False,'generation':'','events':[],'trimmed':False,'reason':'Temporary read failure.'}
    await expect(drawer).to_have_attribute('data-stale','true')
    await expect(changes.locator('.ds-session-item')).to_have_count(6)
    state['snapshot']={'available':False,'generation':'session-1','events':[],'trimmed':False,'reason':'Child feed unavailable.',
        'session':{'sources':{'available':True,'total':1,'trimmed':False,'items':[{'id':'upload-1','name':'notes.md','path':'uploads/notes.md'}]}}}
    await expect(sections.nth(3)).to_contain_text('notes.md')
    await expect(sections.nth(3)).to_have_attribute('data-stale','false')
    await expect(changes).to_have_attribute('data-stale','true')
    state['snapshot']=snapshot
    await expect(drawer).to_have_attribute('data-stale','false')
    await expect(changes.locator('.ds-session-item').first).to_have_attribute('open','')
    state['snapshot']['generation']='session-2'
    await expect(changes.locator('.ds-session-item')).to_have_count(4)
    assert not await changes.locator('.ds-session-item').first.evaluate('(e)=>e.open')
    # No payload can become a resource request or executable HTML.
    state['snapshot']['session']['outputs']['items'][0].update(path='javascript:alert(1)',title='<img src=x onerror=alert(1)>')
    await expect(sections.nth(1)).to_contain_text('javascript:alert(1)')
    assert await drawer.locator('a,img,iframe,script').count()==0
    await drawer.get_by_role('button',name='Close session details').focus()
    await page.keyboard.press('Escape')
    await expect(drawer).to_be_hidden()
    await expect(opener).to_be_focused()
    await page.get_by_label('Text layout',exact=True).select_option('terminal')
    count=len(requests)
    await page.wait_for_timeout(1200)
    assert len(requests)==count
    assert sum('token' in message for _,message in api.socket_messages)==1
    assert await page.evaluate('fixtureTerminals.length===1 && fixtureTerminals[0]===originalTerminal')
    assert not errors


@pytest.mark.parametrize('width',[1680,390])
async def test_drawer_full_shell_preview_responsive_placement(frontiers,width):
    page,_,_,errors=frontiers
    api=BoardAPI();api.state['workspace']='/workspace/Dream'
    api.state['nodes']=[{'id':'node-1','provider':'anthropic','profile':'dream','title':'Claude',
        'model':'Workflow session','state':'ready','workspace':'/workspace/Dream',
        'initial_permissions':'ask','initial_effort':'high',
        'capabilities':{'commands':{'model':'/model','effort':'/effort','changes':'/changes'}},
        'layout':{'x':6,'y':6,'width':780,'height':790}}]
    snapshot=recorded();snapshot['session']=metadata()
    snapshot['session']['sources']={'available':True,'total':1,'trimmed':False,
        'items':[{'id':'upload-reference','name':'reference.png','path':'uploads/reference.png','size':84217}],
        'reason':'Files attached in Dream; adding to draft does not confirm submission or reading.'}
    snapshot['events'][3]['data']['content']='Read completed. Input and approval requests stay in the terminal.'
    await page.route('**/api/deepsleep',api.route)
    await page.route('**/api/deepsleep/activity?*',lambda route:route.fulfill(json=snapshot))
    await page.route_web_socket('**/ws/deepsleep/*',api.socket)
    await page.reload()
    await page.set_viewport_size({'width':width,'height':1180 if width==1680 else 1040})
    await page.locator('#dream-nav-deepsleep').click()
    card=page.locator('.ds-node')
    if width==390:await card.get_by_role('button',name='Maximize Claude').click()
    else:
        await page.locator('#ds-character-toggle').click()
        await page.wait_for_function('document.querySelector(".ds-avatar-slot img").naturalWidth>0')
    await page.get_by_label('Text layout',exact=True).select_option('conversation')
    await expect(card.locator('.ds-event')).to_have_count(6)
    api.sockets['node-1'].send(json.dumps({'type':'output','data':'\x1b[2J\x1b[HReviewing the layout.\r\nAllow read_file? [y/n]\r\nyou> '}))
    await card.get_by_role('button',name='Session details for Claude').click()
    drawer=page.get_by_role('dialog',name='Session details for Claude')
    await expect(drawer.locator('.ds-session-status')).to_contain_text('metadata only')
    await page.screenshot(path=f'/tmp/dream273-complete-drawer-{width}.png')
    geometry=await drawer.evaluate('''e=>{const d=e.getBoundingClientRect(),v=document.querySelector('.ds-viewport').getBoundingClientRect(),n=document.querySelector('.ds-node').getBoundingClientRect();return {contained:d.left>=v.left&&d.right<=v.right&&d.top>=v.top&&d.bottom<=v.bottom,adjacent:d.left>=n.right,overflow:e.scrollWidth>e.clientWidth};}''')
    assert geometry['contained'] and not geometry['overflow'],geometry
    if width==1680:assert geometry['adjacent'],geometry
    # Overlay can always be dismissed to reveal unchanged input and controls.
    await drawer.get_by_role('button',name='Close session details').press('Escape')
    await expect(card.locator('.xterm')).to_be_visible()
    await expect(card.get_by_role('button',name='Session details for Claude')).to_be_focused()
    assert sum('token' in message for _,message in api.socket_messages)==1
    assert not errors


async def test_native_uploaded_sources_are_fresh_without_child_activity(native_page):
    page,api,errors=native_page
    current={'available':False,'generation':'native-launch-1','events':[],'trimmed':False,
        'reason':'Native CLI does not expose structured activity.',
        'session':{'session_id':'','sources':{'available':True,'items':[],'total':0,'trimmed':False,
            'reason':'Uploaded attachments only; not evidence of submission to the provider.'}}}
    async def activity(route):
        if api.uploads:
            current['session']['sources']['items']=[{'id':'fixture-upload','path':'uploads/note.txt','name':'note.txt','size':4}]
            current['session']['sources']['total']=1
        await route.fulfill(json=current)
    await page.route('**/api/deepsleep/activity?*',activity)
    card=await create(page)
    await card.locator('input[type=file]').set_input_files({'name':'note.txt','mimeType':'text/plain','buffer':b'note'})
    # The upload's path is typed into the terminal's own prompt (the draft box went, 2026-10-10).
    for _ in range(100):
        if 'note.txt' in ''.join(m.get('data','') for _,m in api.socket_messages if m.get('type')=='input'):
            break
        await page.wait_for_timeout(20)
    await card.get_by_role('button',name='Session details for Claude').click()
    drawer=page.get_by_role('dialog',name='Session details for Claude')
    sources=drawer.locator('.ds-session-section').nth(3)
    await expect(sources).to_contain_text('note.txt')
    await expect(sources).to_have_attribute('data-stale','false')
    await expect(sources).to_contain_text('not evidence of submission')
    await expect(drawer.locator('.ds-session-section').first).to_contain_text('does not provide')
    await sources.locator('.ds-session-item summary').click()
    # A confirmed new native launch cannot retain the previous upload list.
    api.uploads.clear()
    current['generation']='native-launch-2'
    current['session']['sources']['items']=[]
    current['session']['sources']['total']=0
    await expect(sources.locator('.ds-session-item')).to_have_count(0)
    await expect(card.locator('.xterm')).to_be_visible()
    assert '\r' not in ''.join(m.get('data','') for _,m in api.socket_messages if m.get('type')=='input')   # typed, never submitted
    assert not errors


async def test_empty_drawer_sections_are_quiet_and_user_expansion_survives_polls(board_page):
    page,api,errors=board_page
    card,state=await setup(page,api)
    state['snapshot']['session']={key:{'available':True,'items':[],'total':0,'trimmed':False,
        'reason':f'Original {key} record limitation.'} for key in ['changes','outputs','agents','sources']}
    await card.get_by_role('button',name='Session details for Claude').click()
    drawer=page.get_by_role('dialog',name='Session details for Claude')
    sections=drawer.locator('.ds-session-section')
    await expect(sections.first.locator('summary').first).to_have_text('Changes · 0')
    assert await sections.evaluate_all('(nodes)=>nodes.every(node=>!node.open)')
    await page.screenshot(path='/tmp/dream277-empty-drawer.png')
    assert (await drawer.bounding_box())['height']<300
    changes=sections.first
    await changes.locator('summary').first.press('Enter')
    await expect(changes.locator('.ds-session-note')).to_have_text('No changes yet.')
    about=changes.locator('.ds-session-about')
    await expect(about).not_to_have_attribute('open','')
    await about.locator('summary').press('Enter')
    await expect(about.locator('p')).to_have_text('Original changes record limitation.')
    state['snapshot']['session']['changes']['items']=[{'path':'src/layout.js'}]
    state['snapshot']['session']['changes']['total']=1
    await expect(changes.locator('.ds-session-item')).to_have_count(1)
    await expect(changes).to_have_attribute('open','')
    await expect(about).to_have_attribute('open','')
    await changes.locator('summary').first.press('Enter')
    state['snapshot']['session']['changes']['items'].append({'path':'src/second.js'})
    await expect(changes.locator('.ds-session-item')).to_have_count(2)
    await expect(changes).not_to_have_attribute('open','')
    # A new generation resets defaults, without reusing the old user's choice.
    state['snapshot']['generation']='session-2'
    await expect(changes).to_have_attribute('open','')
    await expect(about).not_to_have_attribute('open','')
    assert not errors
