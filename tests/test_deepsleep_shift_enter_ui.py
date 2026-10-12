"""Workflow-only modified Enter framing through the actual shipped xterm."""
import json
from playwright.async_api import expect
from test_deepsleep_ui import board_page, add

SHIFT_ENTER = '\x1b[13;2u'


def inputs(api):
    return [message['data'] for _,message in api.socket_messages if message.get('type')=='input']


async def test_workflow_shift_enter_has_one_distinct_frame_and_plain_enter_unchanged(board_page):
    page,api,errors=board_page
    await add(page)
    await page.locator('.xterm-helper-textarea').focus()
    before=len(inputs(api))
    await page.keyboard.press('Shift+Enter')
    assert inputs(api)[before:]==[SHIFT_ENTER]
    await page.keyboard.press('Enter')
    assert inputs(api)[before:]==[SHIFT_ENTER,'\r']
    assert not errors


async def test_other_modifiers_and_composition_do_not_become_workflow_newline(board_page):
    page,api,errors=board_page
    await add(page)
    keyboard=page.locator('.xterm-helper-textarea');await keyboard.focus()
    for combination in ['Control+Shift+Enter','Alt+Shift+Enter','Meta+Shift+Enter']:
        before=len(inputs(api));await page.keyboard.press(combination)
        assert SHIFT_ENTER not in inputs(api)[before:]
    before=len(inputs(api))
    await keyboard.dispatch_event('keydown',{'key':'Enter','code':'Enter','keyCode':229,'shiftKey':True,'isComposing':True,'bubbles':True,'cancelable':True})
    await keyboard.dispatch_event('keyup',{'key':'Enter','code':'Enter','keyCode':229,'shiftKey':True,'isComposing':True,'bubbles':True,'cancelable':True})
    assert SHIFT_ENTER not in inputs(api)[before:]
    await keyboard.dispatch_event('compositionstart',{'data':''})
    await keyboard.dispatch_event('keydown',{'key':'Enter','code':'Enter','keyCode':13,'shiftKey':True,'bubbles':True,'cancelable':True})
    await keyboard.dispatch_event('compositionend',{'data':''})
    assert SHIFT_ENTER not in inputs(api)[before:]
    assert not errors


async def test_shift_enter_blocked_when_process_stops_or_transport_disconnects(board_page):
    page,api,errors=board_page
    await add(page)
    card=page.locator('.ds-node');keyboard=card.locator('.xterm-helper-textarea')
    api.sockets['node-1'].send(json.dumps({'type':'state','state':'stopping'}))
    await expect(card.locator('.ds-state')).to_have_text('stopping')
    await keyboard.focus();before=len(inputs(api));await page.keyboard.press('Shift+Enter')
    assert len(inputs(api))==before
    await expect(card.locator('.ds-node-note')).to_contain_text('input was not sent')
    await api.sockets['node-1'].close()
    await expect(card.locator('.ds-terminal-status')).to_contain_text('Disconnected')
    await page.keyboard.press('Shift+Enter')
    assert len(inputs(api))==before
    assert not errors


async def test_native_shift_enter_is_not_rewritten(board_page):
    page,api,errors=board_page
    api.state['nodes']=[{'id':'native-1','provider':'anthropic','profile':'native','title':'Native Claude','state':'ready','layout':{'x':30,'y':30,'width':580,'height':480}}]
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(page.locator('.xterm-helper-textarea')).to_have_count(1)
    await page.locator('.xterm-helper-textarea').focus();before=len(inputs(api))
    await page.keyboard.press('Shift+Enter')
    assert inputs(api)[before:]==['\r']
    assert not errors
