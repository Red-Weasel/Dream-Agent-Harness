"""Receipt-only handoff decoration with real DOM ports; no backend or models."""
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

STATIC = Path(__file__).resolve().parents[1] / 'dream/gui/static'
LINK = {'source': 'source', 'target': 'target'}


@pytest.fixture
async def flow_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-gpu'])
        page = await browser.new_page(viewport={'width': 1500, 'height': 900})
        page.set_default_timeout(3000)
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url))
        await page.set_content('''<!doctype html><html class="dream-native"><body>
          <main id="dream-deepsleep"><div class="ds-toolbar"><h1>DeepSleep</h1></div>
          <div class="ds-viewport" tabindex="0" aria-label="Terminal board">
          <div class="ds-board" style="width:1400px;height:820px"></div></div></main>
          </body></html>''')
        await page.add_style_tag(path=str(STATIC / 'deepsleep.css'))
        await page.add_script_tag(path=str(STATIC / 'deepsleep-flow.js'))
        await page.clock.install()
        await page.evaluate('''() => {
          window.flowVisible=true;
          window.board=document.querySelector('.ds-board');
          window.viewport=document.querySelector('.ds-viewport');
          window.nodes=new Map();
          for (const [id,title,x,y] of [['source','Claude',24,40],['target','Codex',760,240]]) {
            const element=document.createElement('section');element.className='ds-node';
            element.style.cssText=`left:${x}px;top:${y}px;width:400px;height:340px;z-index:100`;
            element.innerHTML=`<div class="ds-node-head"><h2>${title}</h2></div>
              <div class="ds-ports"><button>Input</button><span>Workflow session</span><button>Output</button></div>
              <div class="ds-terminal"><p style="padding:12px;color:#d8cdbb">Recorded workspace session</p></div>
              <div class="ds-footer-tools"><button class="fixture-action">Terminal tools</button></div>`;
            board.append(element);
            const ports=element.querySelectorAll('.ds-ports button');
            nodes.set(id,{data:{id},element,layout:{x,y},inputPort:ports[0],outputPort:ports[1]});
          }
          window.createFlow=()=>DreamHandoffFlow.create({board,viewport,isVisible:()=>flowVisible,getNodes:()=>nodes});
          window.flow=createFlow();
          window.deliver=(receipts,links=[{source:'source',target:'target'}])=>flow.update({receipts,links});
        }''')
        yield page
        await page.evaluate('flow.dispose()')
        assert not errors
        assert not requests, 'The helper must never issue API, model or asset requests'
        await browser.close()


def receipt(id='fresh', state='queued', source='source', target='target', result=''):
    return {'id': id, 'state': state, 'source': source, 'target': target, 'result': result}


async def deliver(page, receipts, links=None):
    await page.evaluate('([receipts,links])=>deliver(receipts,links)', [receipts, [LINK] if links is None else links])


async def test_prime_poll_state_changes_expiry_and_reload_never_replay(flow_page):
    page = flow_page
    pulse = page.locator('.ds-flow-pulse')
    await deliver(page, [receipt('old'), receipt('old-sending', 'sending')])
    await expect(pulse).to_have_count(0)
    await deliver(page, [receipt('old', 'completed'), receipt('old-sending'), receipt()])
    await expect(pulse).to_have_count(1)
    await expect(pulse.locator('text')).to_have_text('Task accepted')
    await page.clock.fast_forward(1500)
    await deliver(page, [receipt('fresh', 'running')])
    await page.clock.fast_forward(501)
    await expect(pulse).to_have_count(0)
    await deliver(page, [receipt('fresh', 'completed')])
    await expect(pulse).to_have_count(0)
    await page.evaluate('flow.dispose();flow=createFlow()')
    await deliver(page, [receipt('fresh', 'completed')])
    await expect(pulse).to_have_count(0)


async def test_unconfirmed_receipts_can_qualify_but_failed_drafts_and_owner_cannot(flow_page):
    page = flow_page
    await deliver(page, [])
    await deliver(page, [receipt('a', 'sending'), receipt('b', 'uncertain')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [receipt('a', 'queued'), receipt('b', 'completed')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(2)
    await deliver(page, [receipt('a', 'failed'), receipt('b', 'interrupted'),
                         receipt('c', 'prepared'), receipt('d', 'sending'),
                         receipt('e', 'uncertain'), receipt('f', source='owner')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await page.clock.fast_forward(5000)
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)


async def test_real_nodes_directed_links_required_and_suppressed_events_consumed(flow_page):
    page = flow_page
    await deliver(page, [])
    await deliver(page, [receipt('reverse')], [{'source': 'target', 'target': 'source'}])
    await deliver(page, [receipt('absent', target='missing')], [{'source': 'source', 'target': 'missing'}])
    await deliver(page, [receipt('unlinked')], [])
    await page.evaluate("nodes.get('target').element.style.visibility='hidden'")
    await deliver(page, [receipt('hidden-port')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await page.evaluate("nodes.get('target').element.style.visibility=''")
    await deliver(page, [receipt('reverse'), receipt('absent'), receipt('unlinked'), receipt('hidden-port')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [receipt('valid')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(1)
    await deliver(page, [receipt('valid')], [])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [receipt('valid')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [receipt('detached')])
    await page.evaluate("nodes.get('target').element.remove();flow.place()")
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)


async def test_hidden_reset_invalid_snapshot_and_dispose_cancel_everything(flow_page):
    page = flow_page
    await deliver(page, [])
    await deliver(page, [receipt()])
    await page.evaluate('flowVisible=false;flow.place()')
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [receipt('while-hidden')])
    await page.evaluate('flowVisible=true')
    await deliver(page, [receipt('while-hidden')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [receipt('new')])
    await page.evaluate('flow.update(null)')
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [receipt('new')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [receipt('active')])
    await page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await page.evaluate("delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
    await deliver(page, [receipt('active')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await page.evaluate('flow.reset()')
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [])
    await deliver(page, [receipt('dispose')])
    await page.evaluate('flow.dispose();flow.dispose();flow.update({receipts:[],links:[]});flow.place()')
    await page.clock.fast_forward(10000)
    await expect(page.locator('.ds-handoff-flow')).to_have_count(0)


async def test_ordinary_burst_drains_every_recorded_delivery_once_without_continuous_animation(flow_page):
    page = flow_page
    await deliver(page, [])
    entries = [receipt(f'burst-{i}') for i in range(80)]
    await deliver(page, entries)
    observed = []
    for _ in range(10):
        await expect(page.locator('.ds-flow-pulse')).to_have_count(8)
        observed.extend(await page.locator('.ds-flow-pulse').evaluate_all('(els)=>els.map(e=>e.dataset.receipt)'))
        await page.clock.fast_forward(2001)
    assert len(observed) == len(set(observed)) == 80
    assert set(observed) == {item['id'] for item in entries}
    await deliver(page, entries)
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await expect(page.locator('.ds-flow-queue')).to_have_text('')
    await page.clock.fast_forward(60000)
    assert await page.evaluate('board.getAnimations({subtree:true}).length') == 0


async def test_overload_is_bounded_and_visible_and_failure_cancels_waiting_delivery(flow_page):
    page = flow_page
    await page.set_viewport_size({'width': 390, 'height': 900})
    await page.evaluate("viewport.style.flex='none';viewport.style.height='400px';viewport.scrollLeft=350;viewport.scrollTop=170")
    await deliver(page, [])
    for batch in range(4):
        await deliver(page, [receipt(f'{batch}-{i}') for i in range(80)])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(8)
    await expect(page.locator('.ds-flow-queue')).to_have_text(
        '160 effects waiting · 152 extra recorded')
    assert await page.evaluate('''() => {
      const label=board.querySelector('.ds-flow-queue').getBoundingClientRect(),view=viewport.getBoundingClientRect();
      return viewport.scrollLeft>0&&viewport.scrollTop>0&&label.left>=view.left&&label.right<=view.right&&label.top>=view.top&&label.bottom<=view.bottom;
    }''')
    await deliver(page, [receipt('0-8', 'failed')])
    seen = set()
    for _ in range(22):
        seen.update(await page.locator('.ds-flow-pulse').evaluate_all('(els)=>els.map(e=>e.dataset.receipt)'))
        await page.clock.fast_forward(2001)
    assert '0-8' not in seen
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await expect(page.locator('.ds-flow-queue')).to_have_text('')


@pytest.mark.parametrize('width', [1500, 390])
async def test_port_geometry_scrolling_keyboard_and_rendered_flow(flow_page, width):
    page = flow_page
    await page.set_viewport_size({'width': width, 'height': 900})
    await deliver(page, [])
    await deliver(page, [receipt()])
    first_path = await page.locator('.ds-flow-packet').get_attribute('d')
    await page.evaluate("nodes.get('target').element.style.top='350px';viewport.scrollLeft=180;flow.place()")
    assert await page.locator('.ds-flow-packet').get_attribute('d') != first_path
    geometry = await page.evaluate('''() => {
      const path=board.querySelector('.ds-flow-packet'), b=board.getBoundingClientRect();
      const start=path.getPointAtLength(0),end=path.getPointAtLength(path.getTotalLength());
      const a=nodes.get('source').outputPort.getBoundingClientRect(),z=nodes.get('target').inputPort.getBoundingClientRect();
      return {start:Math.hypot(start.x-(a.left+a.width/2-b.left),start.y-(a.top+a.height/2-b.top)),
        end:Math.hypot(end.x-(z.left+z.width/2-b.left),end.y-(z.top+z.height/2-b.top)),
        z:getComputedStyle(board.querySelector('.ds-handoff-flow')).zIndex,
        pointer:getComputedStyle(path).pointerEvents};
    }''')
    assert geometry['start'] < 1 and geometry['end'] < 1, geometry
    assert geometry['z'] == '300' and geometry['pointer'] == 'none'
    source_input = page.locator('.ds-ports button').nth(0)
    await source_input.focus()
    await page.keyboard.press('Tab')
    await expect(page.locator('.ds-ports button').nth(1)).to_be_focused()
    await page.keyboard.press('Enter')
    await expect(page.locator('.ds-ports button').nth(1)).to_be_focused()
    await expect(page.locator('.ds-handoff-flow')).to_have_attribute('aria-hidden', 'true')
    if width == 390:
        await page.evaluate('viewport.scrollLeft=350;flow.place()')
    await page.clock.fast_forward(400)
    await page.screenshot(path=f'/tmp/dream276-flow-{width}.png')


async def test_reduced_motion_has_static_indicator_and_finite_lifetime(flow_page):
    page = flow_page
    await page.emulate_media(reduced_motion='reduce')
    await deliver(page, [])
    await deliver(page, [receipt()])
    assert await page.locator('.ds-flow-packet').evaluate('(e)=>getComputedStyle(e).animationName') == 'none'
    await expect(page.locator('.ds-flow-label')).to_have_text('Task accepted')
    await page.screenshot(path='/tmp/dream276-flow-reduced.png')
    await page.clock.fast_forward(2001)
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)


async def add_character_ports(page):
    await page.evaluate('''() => {
      for (const [id,x,y] of [['source',200,460],['target',900,560]]) {
        const node=nodes.get(id),avatar=document.createElement('div');
        avatar.style.cssText=`position:absolute;left:${x}px;top:${y}px;width:120px;height:100px;display:flex;align-items:center;justify-content:space-between;background:#392c1a;border:1px solid #e3ad54`;
        avatar.innerHTML='<button>In</button><span>Character</span><button>Out</button>';
        board.append(avatar);node.avatar=avatar;
        node.avatarInputPort=avatar.firstElementChild;
        node.avatarOutputPort=avatar.lastElementChild;
      }
    }''')


async def assert_flow_anchors(page, source_port, target_port):
    errors = await page.evaluate('''([sourcePort,targetPort]) => {
      const path=board.querySelector('.ds-flow-packet'),b=board.getBoundingClientRect();
      const points=[path.getPointAtLength(0),path.getPointAtLength(path.getTotalLength())];
      return [['source',sourcePort],['target',targetPort]].map(([id,port],i)=>{
        const r=nodes.get(id)[port].getBoundingClientRect();
        return Math.hypot(points[i].x-(r.left+r.width/2-b.left)*board.offsetWidth/b.width,
          points[i].y-(r.top+r.height/2-b.top)*board.offsetHeight/b.height);
      });
    }''', [source_port, target_port])
    assert max(errors) < 1, errors


@pytest.mark.parametrize('hidden', [None, 'source', 'target', 'both'])
async def test_character_ports_choose_each_visible_endpoint_independently(flow_page, hidden):
    page = flow_page
    await add_character_ports(page)
    await page.evaluate('''hidden => {
      for(const [id,node] of nodes)if(hidden==='both'||hidden===id)node.avatar.hidden=true;
    }''', hidden)
    await deliver(page, [])
    await deliver(page, [receipt()])
    await assert_flow_anchors(page,
        'outputPort' if hidden in ('both', 'source') else 'avatarOutputPort',
        'inputPort' if hidden in ('both', 'target') else 'avatarInputPort')


@pytest.mark.parametrize('width', [1500, 390])
async def test_character_ports_follow_move_resize_collapse_scale_and_board_scroll(flow_page, width):
    page = flow_page
    await page.set_viewport_size({'width': width, 'height': 900})
    await add_character_ports(page)
    await deliver(page, [])
    await deliver(page, [receipt()])
    original = await page.locator('.ds-flow-packet').get_attribute('d')
    await page.evaluate('''() => {
      const source=nodes.get('source'),target=nodes.get('target');
      source.avatar.style.left='340px';source.avatar.style.top='390px';
      target.avatar.style.width='210px';target.avatar.style.height='180px';
      target.element.style.height='44px';target.element.classList.add('ds-collapsed');
      board.style.transformOrigin='top left';board.style.transform='scale(.8)';
      viewport.style.flex='none';viewport.style.height='400px';viewport.scrollLeft=240;viewport.scrollTop=170;
      flow.place();
    }''')
    assert await page.evaluate("nodes.get('target').element.offsetHeight") == 44
    assert await page.evaluate('viewport.scrollTop') > 0
    if width == 390:
        assert await page.evaluate('viewport.scrollLeft') > 0
    assert await page.locator('.ds-flow-packet').get_attribute('d') != original
    await assert_flow_anchors(page, 'avatarOutputPort', 'avatarInputPort')
    await page.clock.fast_forward(400)
    await page.screenshot(path=f'/tmp/dream283-character-flow-{width}.png')
    await page.clock.fast_forward(1601)
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)


async def test_missing_character_port_falls_back_without_using_character_center(flow_page):
    page = flow_page
    await add_character_ports(page)
    await page.evaluate("delete nodes.get('source').avatarOutputPort")
    await deliver(page, [])
    await deliver(page, [receipt()])
    await assert_flow_anchors(page, 'outputPort', 'avatarInputPort')
    await page.evaluate("nodes.get('target').avatarInputPort.style.visibility='hidden';flow.place()")
    await assert_flow_anchors(page, 'outputPort', 'inputPort')
    await page.evaluate("nodes.get('target').inputPort.style.display='none';flow.place()")
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await page.evaluate("nodes.get('target').avatarInputPort.style.visibility='';flow.place()")
    await deliver(page, [receipt()])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)


async def test_completed_reply_reverses_actual_delivery_path_once_with_distinct_color(flow_page):
    page = flow_page
    await add_character_ports(page)
    await deliver(page, [])
    await deliver(page, [receipt()])
    packet = page.locator('.ds-flow-packet')
    delivery_path = await packet.get_attribute('d')
    orange = await packet.evaluate('(e)=>getComputedStyle(e).stroke')
    await deliver(page, [receipt(state='completed', result='Recorded reply')])
    await expect(page.locator('.ds-flow-pulse[data-stage=reply]')).to_have_count(0)
    await page.clock.fast_forward(2001)
    await expect(page.locator('.ds-flow-label')).to_have_text('Reply complete')
    blue = await packet.evaluate('(e)=>getComputedStyle(e).stroke')
    assert orange == 'rgb(255, 138, 28)' and blue == 'rgb(255, 210, 74)'   # lightning: orange out, yellow back (2026-10-10)
    reverse = await page.evaluate('''before => {
      const old=document.createElementNS('http://www.w3.org/2000/svg','path');old.setAttribute('d',before);
      const now=document.querySelector('.ds-flow-packet');
      const a=old.getPointAtLength(0),b=old.getPointAtLength(old.getTotalLength());
      const x=now.getPointAtLength(0),z=now.getPointAtLength(now.getTotalLength());
      return Math.hypot(a.x-z.x,a.y-z.y)<1&&Math.hypot(b.x-x.x,b.y-x.y)<1;
    }''', delivery_path)
    assert reverse
    await page.screenshot(path='/tmp/dream285-reply-flow.png')
    await page.clock.fast_forward(2001)
    await deliver(page, [receipt(state='completed', result='Recorded reply')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)


async def test_initial_pending_can_complete_but_initial_completed_and_nonreplies_do_not_replay(flow_page):
    page = flow_page
    await deliver(page, [receipt('old', 'completed', result='Old reply'), receipt('pending', 'running')])
    await deliver(page, [receipt('old', 'completed', result='Old reply'),
                         receipt('pending', 'completed', result='New reply'),
                         receipt('prepared', 'prepared', result='Draft text'),
                         receipt('failed', 'failed', result='Incomplete text'),
                         receipt('uncertain', 'uncertain', result='Unconfirmed text')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(1)
    await expect(page.locator('.ds-flow-pulse')).to_have_attribute('data-stage', 'reply')
    await expect(page.locator('.ds-flow-pulse')).to_have_attribute('data-receipt', 'pending')
    await page.clock.fast_forward(2001)
    await deliver(page, [receipt('blank', 'completed', result='  ')])
    await expect(page.locator('.ds-flow-label')).to_have_text('Task accepted')
    await page.clock.fast_forward(2001)
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)


async def test_direct_completion_records_delivery_then_reply_with_reduced_motion_and_link_authority(flow_page):
    page = flow_page
    await page.emulate_media(reduced_motion='reduce')
    await deliver(page, [])
    await deliver(page, [receipt(state='completed', result='Result ready')])
    await expect(page.locator('.ds-flow-label')).to_have_text('Task accepted')
    await page.clock.fast_forward(2001)
    await expect(page.locator('.ds-flow-label')).to_have_text('Reply complete')
    assert await page.locator('.ds-flow-packet').evaluate('(e)=>getComputedStyle(e).animationName') == 'none'
    await deliver(page, [receipt(state='completed', result='Result ready')], [])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
    await deliver(page, [receipt(state='completed', result='Result ready')])
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)
