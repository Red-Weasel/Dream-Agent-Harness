"""Board channel uses explicit receiver IDs and real server receipt state."""
from copy import deepcopy
from pathlib import Path
import pytest
from playwright.async_api import expect
from test_deepsleep_ui import board_page, add


async def channel_setup(page,api):
    api.state['channel']=[]
    for provider in ['anthropic','codex','antigravity']: await add(page,provider)
    api.state['nodes'][2]['profile']='native'
    async def route(request):
        body=request.request.post_data_json if request.request.method=='POST' else None
        if body and body['action']=='channel_send' and api.fail!='channel_send':
            if not any(item['id']==body['id'] for item in api.state['channel']):
                api.state['channel'].append({'id':body['id'],'source':'owner','targets':body['targets'],'text':body['prompt'],'state':'queued','trimmed':False,
                    'receipts':[{'id':body['id']+target,'target':target,'state':'queued','result':'','error':''} for target in body['targets']]})
        await api.route(request)
    await page.route('**/api/deepsleep',route)
    await page.get_by_role('button',name='Refresh workspace').click()
    await page.get_by_role('button',name='Show connections and delivery activity').click()
    return page.get_by_label('Board channel message',exact=True),page.get_by_role('button',name='Send board channel message')


async def test_channel_mentions_exact_receiver_result_failure_and_keyboard(board_page):
    page,api,errors=board_page
    draft,send=await channel_setup(page,api)
    await expect(page.locator('.ds-channel-empty')).to_contain_text('No messages')
    await draft.fill('@Cod');await draft.press('ArrowDown');await page.keyboard.press('Enter')
    await expect(draft).to_have_value('@Codex ')
    await draft.type('Review the change');await draft.press('Control+Enter')
    await expect(draft).to_have_value('')
    call=api.calls[-1];assert call['action']=='channel_send' and call['targets']==['node-2']
    assert call['prompt']=='@Codex Review the change' and call['id'].startswith('channel-')
    row=api.state['channel'][0];row['state']='completed';row['receipts'][0].update(state='completed',result='<b>Real recorded reply</b>')
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-channel-history')).to_contain_text('<b>Real recorded reply</b>')
    assert await page.locator('.ds-channel-history b').count()==0
    await draft.fill('@Claude Keep this on failure');api.fail='channel_send';await send.click()
    await expect(page.locator('.ds-channel-status')).to_contain_text('Terminal is busy')
    await expect(draft).to_have_value('@Claude Keep this on failure')
    failed=deepcopy(api.calls[-1]);api.fail=None;await send.click()
    await expect(draft).to_have_value('')
    assert api.calls[-1]==failed
    assert not any(m.get('type')=='input' for _,m in api.socket_messages)
    assert sum('token' in m for _,m in api.socket_messages)==3
    assert not errors


async def test_everyone_is_explicit_and_native_is_never_submitted(board_page):
    page,api,errors=board_page
    draft,send=await channel_setup(page,api)
    count=len(api.calls)
    for message in ['@Unknown task','@Gemini task']:
        await draft.fill(message);await send.click()
        assert len(api.calls)==count
    await draft.fill('No receiver');await send.click()      # no @: a board post (2026-10-10), never a task
    await expect(draft).to_have_value('')
    assert api.calls[-1]=={'action':'board_post','text':'No receiver'};count=len(api.calls)
    await draft.fill('@Gemini task');await send.click();assert len(api.calls)==count
    await expect(page.locator('.ds-channel-status')).to_contain_text('reads the board')   # a native CLI (2026-10-10)
    await draft.fill('@G');await expect(page.get_by_role('button',name='Mention Gemini')).to_be_disabled()
    await draft.fill('@everyone Compare notes');await send.click()
    await expect(draft).to_have_value('')
    assert api.calls[-1]['targets']==['node-1','node-2']
    await expect(page.locator('.ds-channel-request')).to_contain_text('You → @Claude, @Codex')
    assert not errors


@pytest.mark.parametrize('width,height',[(2557,1348),(390,844)])
async def test_channel_close_drag_full_height_and_no_link_autoopen(board_page,width,height):
    page,api,errors=board_page
    await page.set_viewport_size({'width':width,'height':height})
    draft,send=await channel_setup(page,api)
    await page.get_by_role('button',name='Close board channel').click()
    await expect(page.locator('.ds-activity')).to_be_hidden()
    await page.get_by_role('button',name='Link from Claude',exact=True).click()
    await page.get_by_role('button',name='Link to Codex',exact=True).click()
    await expect(page.locator('.ds-wire')).to_have_count(1)
    await expect(page.locator('.ds-activity')).to_be_hidden()
    source=page.locator('.ds-node').first
    await expect(source.get_by_label('Handoff receiver for Claude')).to_have_count(0)   # Forward selected text went (2026-10-10)
    await page.get_by_role('button',name='Show connections and delivery activity').click()
    await draft.fill('@everyone Check the shared task');await send.click();await expect(draft).to_have_value('')
    item=api.state['channel'][0];item['state']='failed';item['receipts'][0].update(state='completed',result='Source files inspected. The connection points use exact terminal IDs.');item['receipts'][1].update(state='failed',error='Receiver disconnected before accepting the task. Retry from its terminal after reconnecting.')
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-channel-history')).to_contain_text('Receiver disconnected')
    panel=page.locator('.ds-activity');before=await panel.bounding_box()
    handle=page.get_by_role('button',name='Move board channel; arrow keys move, Home resets')
    await handle.press('ArrowLeft');after=await panel.bounding_box()
    if width>500: assert after['x']==before['x']-10
    assert after['height']>height/2 and after['x']>=0 and after['x']+after['width']<=width
    await draft.fill('@Gemini Needs a manual handoff');await send.click()
    for _ in range(35): await handle.press('Shift+ArrowDown')
    moved=await panel.bounding_box();submit=await send.bounding_box();close=await page.get_by_role('button',name='Close board channel').bounding_box()
    assert submit['y']+submit['height']<=moved['y']+moved['height'] and close['y']>=moved['y']
    await handle.press('Home')
    Path('/tmp/dream284-ui-evidence').mkdir(exist_ok=True)
    await page.screenshot(path=f'/tmp/dream284-ui-evidence/board-channel-{width}.png')
    await handle.press('Escape');await expect(panel).to_be_hidden()
    await expect(page.get_by_role('button',name='Show connections and delivery activity')).to_be_focused()
    assert not errors


async def test_mention_email_and_reserved_terminal_name_do_not_misroute(board_page):
    page,api,errors=board_page
    draft,send=await channel_setup(page,api)
    api.state['nodes'][0]['title']='everyone'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-node').first.locator('h2')).to_have_text('everyone')
    await draft.fill('@eve')
    await page.get_by_role('button',name='Mention everyone · node-1',exact=True).click()
    await draft.type(' Review contact@example.test')
    await send.click();await expect(draft).to_have_value('')
    assert api.calls[-1]['targets']==['node-1']
    assert 'contact@example.test' in api.calls[-1]['prompt']
    assert not errors


async def test_broadcast_retry_keeps_original_id_and_receivers(board_page):
    page,api,errors=board_page
    draft,send=await channel_setup(page,api)
    api.fail='channel_send'
    await draft.fill('@everyone Review once');await send.click()
    await expect(page.locator('.ds-channel-status')).to_contain_text('Terminal is busy')
    attempt=deepcopy(api.calls[-1]);assert attempt['targets']==['node-1','node-2']
    api.state['nodes'][2]['profile']='dream'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.get_by_role('button',name='Refresh workspace')).to_be_enabled()
    api.fail=None;await send.click();await expect(draft).to_have_value('')
    assert api.calls[-1]==attempt
    assert not errors


async def test_cleared_mention_resolves_current_name_without_stale_binding(board_page):
    page,api,errors=board_page
    draft,send=await channel_setup(page,api)
    await draft.fill('@Cod');await page.get_by_role('button',name='Mention Codex',exact=True).click()
    await draft.fill('')
    api.state['nodes'][1]['title']='Research';api.state['nodes'][0]['title']='Codex'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-node').first.locator('h2')).to_have_text('Codex')
    await draft.fill('@Codex Current receiver');await send.click();await expect(draft).to_have_value('')
    assert api.calls[-1]['targets']==['node-1']
    assert not errors


async def test_active_selected_mention_keeps_exact_id_after_rename(board_page):
    page,api,errors=board_page
    draft,send=await channel_setup(page,api)
    await draft.fill('@Cod');await page.get_by_role('button',name='Mention Codex',exact=True).click()
    api.state['nodes'][1]['title']='Research';api.state['nodes'][0]['title']='Codex'
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.ds-node').first.locator('h2')).to_have_text('Codex')
    await draft.type(' Keep the selected receiver');await send.click();await expect(draft).to_have_value('')
    assert api.calls[-1]['targets']==['node-2']
    assert not errors


async def test_channel_draft_and_attempt_are_isolated_by_workspace(board_page):
    page,api,errors=board_page
    api.state['workspace']='/fixture/alpha'
    draft,send=await channel_setup(page,api)
    api.fail='channel_send';await draft.fill('@Codex Alpha draft');await send.click()
    await expect(page.locator('.ds-channel-status')).to_contain_text('Terminal is busy')
    attempt=deepcopy(api.calls[-1]);api.state['workspace']='/fixture/beta'
    await page.get_by_role('button',name='Refresh workspace').click();await expect(draft).to_have_value('')
    await draft.fill('@Claude Beta draft')
    api.state['workspace']='/fixture/alpha'
    await page.get_by_role('button',name='Refresh workspace').click();await expect(draft).to_have_value('@Codex Alpha draft')
    api.fail=None;await send.click();await expect(draft).to_have_value('')
    assert api.calls[-1]==attempt
    api.state['workspace']='/fixture/beta'
    await page.get_by_role('button',name='Refresh workspace').click();await expect(draft).to_have_value('@Claude Beta draft')
    assert not errors


async def test_channel_poll_preserves_selected_text_and_closed_history_position(board_page):
    page,api,errors=board_page
    draft,send=await channel_setup(page,api)
    api.state['channel']=[{'id':str(i),'source':'owner','targets':['node-1'],'text':'Recorded message '+str(i)+'\n'+('Long recorded text. '*20),'state':'completed','receipts':[]} for i in range(15)]
    await page.get_by_role('button',name='Refresh workspace').click()
    history=page.locator('.ds-channel-history')
    await expect(history.locator('article')).to_have_count(15)
    await history.evaluate('(e)=>e.scrollTop=125')
    await history.locator('article').first.locator('p').evaluate('(e)=>{const r=document.createRange();r.selectNodeContents(e);const s=getSelection();s.removeAllRanges();s.addRange(r)}')
    selected=await page.evaluate('getSelection().toString()')
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.get_by_role('button',name='Refresh workspace')).to_be_enabled()
    assert await page.evaluate('getSelection().toString()')==selected
    assert await history.evaluate('(e)=>e.scrollTop')==125
    await page.get_by_role('button',name='Close board channel').click()
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.get_by_role('button',name='Refresh workspace')).to_be_enabled()
    await page.get_by_role('button',name='Show connections and delivery activity').click()
    assert await history.evaluate('(e)=>e.scrollTop')==125
    assert not errors
