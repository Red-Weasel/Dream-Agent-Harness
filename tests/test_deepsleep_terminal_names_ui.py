"""Pane naming preserves session ownership; enlarged characters keep exact links."""
from copy import deepcopy
from pathlib import Path

import pytest
from playwright.async_api import expect
from test_deepsleep_ui import board_page, add, STATIC, xterm_assets
from test_deepsleep_character_ports_ui import endpoints

EVIDENCE = Path('/tmp/dream284-ui-evidence')


async def rename_route(page, api):
    async def route(request):
        body=request.request.post_data_json if request.request.method=='POST' else None
        if body and body['action']=='rename' and api.fail!='rename':
            next(n for n in api.state['nodes'] if n['id']==body['node'])['title']=body['title']
        await api.route(request)
    await page.route('**/api/deepsleep',route)


async def test_terminal_name_saves_exact_node_and_retains_drafts_links_and_session(board_page):
    page,api,errors=board_page
    for provider in ['anthropic','codex']: await add(page,provider)
    await rename_route(page,api)
    first,second=page.locator('.ds-node').nth(0),page.locator('.ds-node').nth(1)
    await first.get_by_role('button',name='Link from Claude',exact=True).click()
    await second.get_by_role('button',name='Link to Codex',exact=True).click()
    await page.evaluate('window.originalTerminals=[...fixtureTerminals]')
    links=deepcopy(api.state['links']);layouts=deepcopy([n['layout'] for n in api.state['nodes']])
    name=first.get_by_label('Terminal name',exact=True)
    await name.fill('  Research <one>  ')
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(name).to_have_value('  Research <one>  ')
    await name.press('Enter')
    await expect(first.locator('h2')).to_have_text('Research <one>')
    await expect(first.locator('.ds-name-status')).to_have_text('Terminal name saved.')
    assert api.calls[-1]=={'action':'rename','node':'node-1','title':'Research <one>'}
    await expect(second.locator('h2')).to_have_text('Codex')
    await expect(second.get_by_label('Terminal name',exact=True)).to_have_value('Codex')
    await expect(first.get_by_label('Your message label (all terminals)',exact=True)).to_have_value('You')
    await expect(first.get_by_role('button',name='Link from Research <one>',exact=True)).to_be_visible()
    await expect(page.locator('.ds-wire')).to_have_count(1)   # the link survives the rename (its forwarding list went, 2026-10-10)
    await first.get_by_role('button',name='Session details for Research <one>').click()
    await expect(page.get_by_role('dialog',name='Session details for Research <one>')).to_be_visible()
    await expect(page.locator('.ds-session-title')).to_contain_text(['Research <one>','Codex'])
    assert api.state['links']==links and [n['layout'] for n in api.state['nodes']]==layouts
    assert await page.evaluate('fixtureTerminals.length===2 && fixtureTerminals.every((t,i)=>t===originalTerminals[i])')
    assert sum('token' in m for _,m in api.socket_messages)==2
    assert not any(m.get('type')=='input' for _,m in api.socket_messages)
    assert not errors


async def test_name_validation_refusal_and_retry_preserve_edit(board_page):
    page,api,errors=board_page
    await add(page);await rename_route(page,api)
    card=page.locator('.ds-node');name=card.get_by_label('Terminal name',exact=True)
    count=len(api.calls)
    await name.fill(' ');await name.press('Enter')
    await expect(card.locator('.ds-name-status')).to_contain_text('1–100 characters')
    assert len(api.calls)==count
    await name.fill('Reviewer');api.fail='rename';await name.press('Enter')
    await expect(card.locator('.ds-name-status')).to_contain_text('Terminal is busy')
    await expect(card.locator('h2')).to_have_text('Claude')
    await expect(name).to_have_value('Reviewer')
    await page.get_by_role('button',name='Refresh workspace').click()
    await expect(name).to_have_value('Reviewer')
    api.fail=None;await card.get_by_role('button',name='Save terminal name').press('Enter')
    await expect(card.locator('h2')).to_have_text('Reviewer')
    await expect(card.get_by_role('button',name='Save terminal name')).to_be_enabled()
    assert not errors


@pytest.mark.parametrize('width,height',[(2557,1348),(390,844)])
async def test_six_enlarged_characters_and_independent_names_render(board_page,width,height):
    page,api,errors=board_page
    await page.set_viewport_size({'width':width,'height':height})
    await rename_route(page,api)
    for provider in ['anthropic','codex','antigravity','anthropic','codex','antigravity']:
        await add(page,provider)
    await page.get_by_role('button',name='Tile terminals',exact=True).click()
    await expect(page.get_by_role('button',name='Tile terminals',exact=True)).to_be_enabled()
    await page.locator('#ds-character-toggle').click()
    for i,card in enumerate(await page.locator('.ds-node').all()):
        await card.get_by_role('button',name='Terminal tools for ',exact=False).click()
        name=card.get_by_label('Terminal name',exact=True)
        await name.fill(['Research','Review','Build','Verify','Notes','Plan'][i]);await name.press('Enter')
        await expect(card.locator('.ds-name-status')).to_have_text('Terminal name saved.')
        await card.get_by_label('Character size for ',exact=False).select_option(['small','medium','large'][i%3])
        await card.get_by_role('button',name='Place character for ',exact=False).click()
        avatar=page.locator('.ds-avatar-slot').nth(i)
        await avatar.press('Shift+ArrowRight')
        await card.get_by_role('button',name='Terminal tools for ',exact=False).click()
    await page.get_by_role('button',name='Link from Research character').click()
    await page.get_by_role('button',name='Link to Review character').click()
    await expect(page.locator('.ds-wire')).to_have_count(1)
    await endpoints(page,'.ds-avatar-anchor[data-avatar-node="node-1"] .ds-avatar-port:last-child','.ds-avatar-anchor[data-avatar-node="node-2"] .ds-avatar-port:first-child')
    sizes=await page.locator('.ds-avatar-slot').evaluate_all('(items)=>items.map(e=>e.getBoundingClientRect().height)')
    assert sizes==[72,104,144,72,104,144]
    assert len(api.sockets)==6 and sum('token' in m for _,m in api.socket_messages)==6
    if width==390:
        card=page.locator('.ds-node').first
        await card.get_by_role('button',name='Maximize Research').click()
        await card.get_by_role('button',name='Terminal tools for Research').click()
        name=card.get_by_label('Terminal name',exact=True);await name.focus();await name.press('End')
        assert await name.evaluate('(e)=>e.getBoundingClientRect().right<=e.closest(".ds-node").getBoundingClientRect().right')
    await page.locator('.ds-viewport').evaluate('(e)=>e.scrollTo(0,0)')
    EVIDENCE.mkdir(exist_ok=True)
    await page.screenshot(path=str(EVIDENCE/f'characters-names-{width}.png'),full_page=True)
    assert not any(m.get('type')=='input' for _,m in api.socket_messages)
    assert not errors
