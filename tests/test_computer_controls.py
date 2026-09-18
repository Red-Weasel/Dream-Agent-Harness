"""Real Chromium actions on disposable pages; desktop control uses fixed fake I/O."""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import dream.computer as computer_module
from dream.computer import Computer, ComputerError
from dream.core import policy
from dream.tools import computer_tools
from dream.tools.context import bind_context

PAGE = b'''<!doctype html><title>Fixture</title><h1>Two forms</h1>
<label>First<input id="first"></label><button onclick="document.querySelector('#a').textContent='saved first'">Save</button><p id="a"></p>
<label>Second<input id="second"></label><button onclick="document.querySelector('#b').textContent='saved '+document.querySelector('#second').value">Save</button><p id="b"></p>
<div style="height:1500px">Long content</div>'''


@pytest.fixture(autouse=True)
def forbid_unmocked_native_input(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('Real native I/O is forbidden in computer-control tests')
    def forbidden_capture(*args, **kwargs):
        raise AssertionError('Real native capture is forbidden in computer-control tests')
    monkeypatch.setattr(computer_module.ImageGrab, 'grab', forbidden_capture)
    monkeypatch.setattr(computer_module, '_command', forbidden)


@pytest.fixture
async def target(tmp_path):
    async def serve(reader, writer):
        try:
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: '+str(len(PAGE)).encode()+b'\r\nConnection: close\r\n\r\n'+PAGE)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    controller = Computer(tmp_path, tmp_path/'captures')
    url = 'http://127.0.0.1:'+str(server.sockets[0].getsockname()[1])
    try:
        observed = await controller.open('browser', url=url)
        yield controller, observed
    finally:
        await controller.aclose()
        server.close()
        await server.wait_closed()


async def test_actual_form_actions_preserve_other_form(target):
    c, state = target
    ident = state['target_id']
    field = next(e for e in state['state']['elements'] if e['name']=='Second')
    filled = await c.act(ident, state['observation_id'], 'type', element_id=field['id'], text='launch v2')
    buttons = [e for e in filled['state']['elements'] if e['name']=='Save']
    saved = await c.act(ident, filled['observation_id'], 'click', element_id=buttons[1]['id'])
    page = c.targets[ident]['page']
    assert await page.locator('#b').inner_text() == 'saved launch v2'
    assert await page.locator('#a').inner_text() == ''
    assert await page.locator('#first').input_value() == ''
    assert Path(saved['screenshot']).read_bytes().startswith(b'\x89PNG')
    assert saved['action_result']['task_success']=='unverified'
    assert saved['observation_id'] != filled['observation_id']


@pytest.mark.parametrize('mutation', ['resize', 'dom', 'focus', 'navigation'])
async def test_changed_state_refuses_old_action(target, mutation):
    c, old = target
    page = c.targets[old['target_id']]['page']
    if mutation == 'resize':
        await page.set_viewport_size({'width':600,'height':500})
    elif mutation == 'dom':
        await page.locator('#second').fill('other user edit')
    elif mutation == 'focus':
        await page.locator('#second').focus()
    else:
        await page.reload()
    with pytest.raises(ComputerError, match='state changed'):
        await c.act(old['target_id'], old['observation_id'], 'click', element_id='1')
    assert await page.locator('#a').inner_text()==''


async def test_timeout_consumes_token_and_does_not_replay_completed_save(target, monkeypatch):
    c, old = target
    original=c._browser_action
    calls=[]
    async def timeout_after_action(*args):
        calls.append(True)
        await original(*args)
        raise TimeoutError('response lost after click')
    monkeypatch.setattr(c, '_browser_action', timeout_after_action)
    with pytest.raises(ComputerError, match='outcome is uncertain'):
        await c.act(old['target_id'], old['observation_id'], 'click', element_id='1')
    with pytest.raises(ComputerError, match='already used'):
        await c.act(old['target_id'], old['observation_id'], 'click', element_id='1')
    observed=await c.observe(old['target_id'])
    assert 'saved first' in observed['state']['text']
    assert len(calls)==1


async def test_session_ownership_and_close(target, tmp_path):
    c, state=target
    other=Computer(tmp_path,tmp_path/'other')
    with pytest.raises(ComputerError,match='Unknown target'):
        await other.observe(state['target_id'])
    await c.close(state['target_id'])
    with pytest.raises(ComputerError,match='Unknown target'):
        await c.observe(state['target_id'])


async def test_key_and_scroll_return_new_observations(target):
    c,state=target
    keyed=await c.act(state['target_id'],state['observation_id'],'key',key='Tab')
    assert keyed['state']['focus'] >= 0
    scrolled=await c.act(state['target_id'],keyed['observation_id'],'scroll',dy=400)
    assert scrolled['state']['scroll'][1] > 0


@pytest.mark.parametrize('url',['file:///etc/passwd','javascript:alert(1)','data:text/html,hello','about:blank'])
async def test_browser_open_rejects_other_schemes(tmp_path,url):
    c=Computer(tmp_path,tmp_path/'captures')
    with pytest.raises(ComputerError,match='http or https'):
        await c.open('browser',url=url)
    assert c._pw is None


def test_permissions_keep_real_actions_gated_in_auto_and_plan(tmp_path):
    assert policy.decide('computer_observe',{},'plan',tmp_path)[0]=='allow'
    for name in ['computer_open','computer_action','computer_close']:
        assert policy.decide(name,{},'auto',tmp_path)[0]=='ask'
        assert policy.decide(name,{},'plan',tmp_path)[0]=='deny'


async def test_wayland_reports_unavailable_without_running_desktop_commands(tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_SESSION_TYPE','wayland')
    c=Computer(tmp_path,tmp_path/'captures')
    assert not c.capabilities()['desktop']
    with pytest.raises(ComputerError,match='Wayland'):
        await c.open('desktop',window_id='123')


async def test_desktop_geometry_change_refuses_dispatch(tmp_path,monkeypatch):
    c=Computer(tmp_path,tmp_path/'captures')
    monkeypatch.setattr(c,'capabilities',lambda:{'desktop':True})
    state={'window_id':'123','focus':'123','title':'Fixture','rect':[0,0,800,600]}
    async def read(_):return dict(state)
    async def capture(*_):return None
    monkeypatch.setattr(c,'_desktop_state',read)
    monkeypatch.setattr(c,'_capture',capture)
    observed=await c.open('desktop',window_id='123')
    state['rect']=[0,0,400,300]
    with pytest.raises(ComputerError,match='state changed'):
        await c.act(observed['target_id'],observed['observation_id'],'click',x=20,y=30)


async def test_text_only_endpoint_gets_dom_without_unseen_image_claim(target):
    c,state=target
    context=SimpleNamespace(computer=c,multimodal=False)
    with bind_context(context):
        out=await computer_tools.computer_observe.handler({'target_id':state['target_id']})
    assert all(part['type']=='text' for part in out['content'])
    body=json.loads(out['content'][0]['text'])
    assert 'unavailable' in body['image_note'] and body['state']['elements']


async def test_changed_destination_and_replaced_node_reject_old_click(target):
    c,first=target
    ident=first['target_id'];page=c.targets[ident]['page']
    await page.evaluate("document.body.insertAdjacentHTML('afterbegin','<a href=\"/one\">Next</a>')")
    observed=await c.observe(ident)
    await page.locator('a').evaluate("e=>e.href='/two'")
    with pytest.raises(ComputerError,match='state changed'):
        await c.act(ident,observed['observation_id'],'click',element_id='0')
    observed=await c.observe(ident)
    await page.locator('a').evaluate('e=>e.replaceWith(e.cloneNode(true))')
    with pytest.raises(ComputerError,match='replaced'):
        await c.act(ident,observed['observation_id'],'click',element_id='0')
    assert not page.url.endswith('/two')


async def test_observation_images_are_immutable(target):
    c,first=target
    image=Path(first['screenshot']);data=image.read_bytes()
    await c.targets[first['target_id']]['page'].evaluate("document.body.style.background='red'")
    second=await c.observe(first['target_id'])
    assert second['screenshot'] != first['screenshot']
    assert image.read_bytes()==data
    assert Path(second['screenshot']).read_bytes()!=data


async def test_unused_targeting_parameters_are_rejected_without_consuming_observation(target):
    c,state=target
    with pytest.raises(ComputerError,match='unused parameters'):
        await c.act(state['target_id'],state['observation_id'],'key',key='Tab',element_id='2')
    assert c.targets[state['target_id']]['observation']==state['observation_id']


async def test_desktop_scroll_moves_inside_selected_window_and_checks_focus(tmp_path,monkeypatch):
    import dream.computer as module
    c=Computer(tmp_path,tmp_path/'captures');calls=[]
    async def command(*args):
        calls.append(args)
        return '123' if args[1]=='getwindowfocus' else ''
    monkeypatch.setattr(module,'_command',command)
    monkeypatch.setattr(module.shutil,'which',lambda _:'/usr/bin/xdotool')
    await c._desktop_action({'window_id':'123'}, {'focus':'123','rect':[0,0,800,600]}, 'scroll', {'dy':400})
    assert calls[0][1:]==('mousemove','--window','123','400','300')
    assert calls[1][1:]==('getwindowfocus',)
    assert calls[2][1:]==('click','--repeat','4','5')


async def test_failed_observation_invalidates_prior_handles(target,monkeypatch):
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.locator('button').first.evaluate('e=>e.replaceWith(e.cloneNode(true))')
    async def failed(*_):raise TimeoutError('capture failed')
    monkeypatch.setattr(c,'_capture',failed)
    with pytest.raises(TimeoutError):
        await c.observe(ident)
    with pytest.raises(ComputerError,match='stale or already used'):
        await c.act(ident,old['observation_id'],'click',element_id='1')
    assert await page.locator('#a').inner_text()==''


async def test_changed_containing_form_destination_refuses_old_submit(target):
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.evaluate("document.body.innerHTML='<form action=\"/one\" method=\"post\"><button>Submit</button></form>'")
    observed=await c.observe(ident)
    assert observed['state']['elements'][0]['formAction'].endswith('/one')
    await page.locator('form').evaluate("e=>e.action='/two'")
    with pytest.raises(ComputerError,match='state changed'):
        await c.act(ident,observed['observation_id'],'click',element_id='0')
    assert not page.url.endswith('/two')


async def test_large_escaped_observations_page_under_actual_transport_cap(target):
    from dream import config
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.evaluate('''() => {document.body.innerHTML='';for(let i=0;i<60;i++){
      const b=document.createElement('button');b.textContent='Control '+i+'\\x00'.repeat(200);
      b.onclick=()=>document.title='chosen '+i;document.body.append(b);}}''')
    state=await c.observe(ident)
    assert len(json.dumps(state,ensure_ascii=False))<config.TOOL_RESULT_CAP
    assert state['state']['next_element_offset'] is not None
    first_ids={e['id'] for e in state['state']['elements']}
    with pytest.raises(ComputerError,match='enabled element_id'):
        await c.act(ident,state['observation_id'],'click',element_id='59')
    state=await c.observe(ident,offset=state['state']['next_element_offset'])
    assert not first_ids.intersection(e['id'] for e in state['state']['elements'])
    assert len(json.dumps(state,ensure_ascii=False))<config.TOOL_RESULT_CAP
    entry=next(e for e in state['state']['elements'] if e['visible'])
    done=await c.act(ident,state['observation_id'],'click',element_id=entry['id'])
    assert done['state']['title']=='chosen '+entry['id']
    assert len(json.dumps(done,ensure_ascii=False))<config.TOOL_RESULT_CAP


async def prepare_canvas(c, ident):
    page = c.targets[ident]['page']
    await page.set_content('''<style>body{margin:0}</style><canvas width=400 height=300></canvas><script>
    const c=document.querySelector('canvas'),ctx=c.getContext('2d');let down=false;
    c.onmousedown=e=>{down=true;ctx.beginPath();ctx.moveTo(e.offsetX,e.offsetY)};
    c.onmousemove=e=>{if(down){ctx.lineTo(e.offsetX,e.offsetY);ctx.stroke()}};
    window.onmouseup=()=>down=false;
    window.onkeydown=e=>{if(e.ctrlKey&&e.key==='z')ctx.clearRect(0,0,400,300)};
    </script>''')
    return page, await c.observe(ident)


async def test_real_canvas_stroke_and_undo(target):
    c, old = target; ident=old['target_id']
    page, state = await prepare_canvas(c, ident)
    original=Path(state['screenshot']).read_bytes()
    drawn=await c.act(ident,state['observation_id'],'stroke',points=[{'x':20,'y':20},{'x':100,'y':100},{'x':200,'y':20}])
    assert await page.evaluate("ctx.getImageData(99,99,1,1).data[3]") > 0
    assert Path(drawn['screenshot']).read_bytes()!=original
    assert Path(state['screenshot']).read_bytes()==original
    undone=await c.act(ident,drawn['observation_id'],'key',key='Control+z')
    assert Path(undone['screenshot']).read_bytes()==original
    clicked=await c.act(ident,undone['observation_id'],'click',x=30,y=30)
    assert clicked['observation_id']!=undone['observation_id']


@pytest.mark.parametrize('points',[[],[{'x':1,'y':2}], [{'x':1,'y':2},{'x':1280,'y':2}], [{'x':True,'y':2},{'x':3,'y':4}], [{'x':1,'y':2}]*129])
async def test_bad_stroke_never_dispatches(target, points):
    c,state=target
    with pytest.raises(ComputerError,match='points|integer'):
        await c.act(state['target_id'],state['observation_id'],'stroke',points=points)
    assert c.targets[state['target_id']]['observation']==state['observation_id']


async def test_canvas_pixel_change_refuses_coordinate_action(target):
    c,old=target;ident=old['target_id']
    page,state=await prepare_canvas(c,ident)
    await page.evaluate('ctx.fillRect(1,1,20,20)')
    with pytest.raises(ComputerError,match='pixels changed'):
        await c.act(ident,state['observation_id'],'click',x=50,y=50)
    with pytest.raises(ComputerError,match='already used'):
        await c.act(ident,state['observation_id'],'click',x=50,y=50)


@pytest.mark.parametrize('cancel',[False,True])
async def test_browser_failed_stroke_releases_button(target,monkeypatch,cancel):
    c,old=target;ident=old['target_id']
    page,state=await prepare_canvas(c,ident)
    real_move=page.mouse.move; calls=0; entered=asyncio.Event()
    async def move(*a,**kw):
        nonlocal calls
        calls+=1
        if calls==2:
            entered.set()
            if cancel: await asyncio.Event().wait()
            raise RuntimeError('injected mid-stroke failure')
        await real_move(*a,**kw)
    monkeypatch.setattr(page.mouse,'move',move)
    task=asyncio.create_task(c.act(ident,state['observation_id'],'drag',points=[{'x':20,'y':20},{'x':100,'y':100}]))
    if cancel:
        await asyncio.wait_for(entered.wait(),5);task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else ComputerError):await task
    assert await page.evaluate('down') is False
    with pytest.raises(ComputerError,match='already used'):
        await c.act(ident,state['observation_id'],'click',x=30,y=30)


@pytest.mark.parametrize('failure',['none','focus','geometry','cancel','move'])
async def test_desktop_stroke_scopes_moves_and_releases_on_failure(tmp_path,monkeypatch,failure):
    import dream.computer as module
    c=Computer(tmp_path,tmp_path/'captures');calls=[]
    state={'window_id':'123','focus':'123','title':'Fixture','rect':[0,0,800,600]}
    pressed=False
    async def read(_):
        current=dict(state)
        if pressed and failure=='focus':current['focus']='456'
        if pressed and failure=='geometry':current['rect']=[1,1,800,600]
        return current
    async def command(*args):
        nonlocal pressed
        calls.append(args[1:])
        if args[1]=='mousedown':pressed=True
        if args[1]=='mouseup':pressed=False
        if args[1]=='mousemove' and pressed:
            if failure=='cancel':raise asyncio.CancelledError()
            if failure=='move':raise RuntimeError('injected native failure')
        return ''
    monkeypatch.setattr(c,'_desktop_state',read)
    monkeypatch.setattr(module,'_command',command)
    monkeypatch.setattr(module.shutil,'which',lambda _:'/usr/bin/xdotool')
    action=c._desktop_action({'window_id':'123'},state,'stroke',{'points':[{'x':10,'y':20},{'x':30,'y':40}]})
    if failure=='none':await action
    else:
        with pytest.raises(asyncio.CancelledError if failure=='cancel' else (ComputerError,RuntimeError)):await action
    assert calls[0]==('mousemove','--window','123','10','20')
    assert calls[-1]==('mouseup','1')
    assert not pressed
    if failure in {'focus','geometry'}:assert len(calls)==3


async def test_coordinate_click_rejects_mixed_target_and_bounds(target):
    c,state=target
    for args in ({'element_id':'1','x':20,'y':20},{'x':1280,'y':10},{'x':1}):
        with pytest.raises(ComputerError):
            await c.act(state['target_id'],state['observation_id'],'click',**args)
    assert c.targets[state['target_id']]['observation']==state['observation_id']


async def test_browser_stroke_stops_if_viewport_changes_after_press(target,monkeypatch):
    c,old=target;ident=old['target_id']
    page,state=await prepare_canvas(c,ident)
    real_down=page.mouse.down
    async def down(**kw):
        await real_down(**kw)
        await page.set_viewport_size({'width':600,'height':400})
    monkeypatch.setattr(page.mouse,'down',down)
    with pytest.raises(ComputerError,match='viewport or navigation changed'):
        await c.act(ident,state['observation_id'],'drag',points=[{'x':20,'y':20},{'x':100,'y':100}])
    assert await page.evaluate('down') is False
    assert await page.evaluate('ctx.getImageData(50,50,1,1).data[3]')==0


@pytest.mark.parametrize('duration',[None,500])
async def test_repeated_cancellation_waits_for_release_before_unlock(target,monkeypatch,duration):
    c,old=target;ident=old['target_id']
    page,state=await prepare_canvas(c,ident)
    real_move,real_up=page.mouse.move,page.mouse.up
    pressed=asyncio.Event();releasing=asyncio.Event();finish=asyncio.Event();calls=0
    async def move(*a,**kw):
        nonlocal calls
        calls+=1
        if calls==2:
            pressed.set();await asyncio.Event().wait()
        await real_move(*a,**kw)
    async def up(**kw):
        releasing.set();await finish.wait();await real_up(**kw)
    monkeypatch.setattr(page.mouse,'move',move)
    monkeypatch.setattr(page.mouse,'up',up)
    task=asyncio.create_task(c.act(ident,state['observation_id'],'drag',points=[{'x':10,'y':10},{'x':50,'y':50}],**({} if duration is None else {'duration_ms':duration})))
    await asyncio.wait_for(pressed.wait(),5);task.cancel()
    await asyncio.wait_for(releasing.wait(),5);task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and c._lock.locked()
    finish.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert await page.evaluate('down') is False
    assert not c._lock.locked()


@pytest.mark.parametrize('change',['overlay','replace','destination'])
async def test_coordinate_hover_change_refuses_unobserved_press(target,change):
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.set_content('''<style>body{margin:0}a{display:block;width:400px;height:300px}</style>
    <a id="area" href="/original">Target</a><script>window.clicked=0;document.addEventListener('click',e=>{e.preventDefault();window.clicked++});</script>''')
    await page.mouse.move(600,600)
    await page.evaluate('''change=>{area.onmouseenter=()=>{
      if(change==='overlay')area.innerHTML='<button style="position:absolute;inset:0;width:400px;height:300px">Unexpected</button>';
      if(change==='replace')area.replaceWith(area.cloneNode(true));
      if(change==='destination')area.href='/unexpected';
    }}''',change)
    state=await c.observe(ident)
    with pytest.raises(ComputerError,match='changed|replaced'):
        await c.act(ident,state['observation_id'],'click',x=100,y=100)
    assert await page.evaluate('window.clicked')==0


async def test_coordinate_hover_status_text_is_allowed(target):
    c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
    await page.set_content('''<style>body{margin:0}#area{width:400px;height:300px}</style>
    <div id="area"></div><p id="status">Ready</p><script>window.clicked=0;
    area.onmousemove=e=>document.querySelector('#status').textContent='Coordinates '+e.clientX+','+e.clientY;
    area.onclick=()=>window.clicked++;</script>''')
    await page.mouse.move(600,600)
    state=await c.observe(ident)
    await c.act(ident,state['observation_id'],'click',x=100,y=100)
    assert await page.evaluate('window.clicked')==1
    assert await page.locator('#status').inner_text()=='Coordinates 100,100'


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['CTRL+z','Ctrl+z','ctrl+z','Control+z'])
async def test_common_aliases_real_key_events_and_release(target,key):
 c,state=target;ident=state['target_id'];page=c.targets[ident]['page']
 await page.evaluate('() => {window.events=[];document.onkeydown=e=>events.push([e.key,e.ctrlKey]);document.onclick=e=>events.push(["click",e.ctrlKey])}')
 await c.act(ident,state['observation_id'],'key',key=key)
 await page.locator('button').first.click()
 assert await page.evaluate('events')==[['Control',True],['z',True],['click',False]]


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['Control+BOGUSKEY','Control+','CTRL+BOGUSKEY','a+b','Control+Clear','Control+Control+z','CTRL+Control+z',None])
async def test_bad_chord_preserves_token_and_sends_no_events(target,key):
 c,state=target;ident=state['target_id'];page=c.targets[ident]['page']
 await page.evaluate('() => {window.events=[];document.onkeydown=e=>events.push(e.key)}')
 with pytest.raises(ComputerError):await c.act(ident,state['observation_id'],'key',key=key)
 assert await page.evaluate('events')==[]
 assert c.targets[ident]['observation']==state['observation_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel',[False,True])
async def test_chord_failure_cancellation_releases_held_control(target,monkeypatch,cancel):
 c,state=target;ident=state['target_id'];page=c.targets[ident]['page'];real=page.keyboard.down
 async def down(key):
  if key=='z':
   if cancel:raise asyncio.CancelledError()
   raise RuntimeError('injected after modifier')
  await real(key)
 monkeypatch.setattr(page.keyboard,'down',down)
 await page.evaluate('() => {window.ctrl=null;document.onclick=e=>window.ctrl=e.ctrlKey}')
 with pytest.raises(asyncio.CancelledError if cancel else ComputerError):await c.act(ident,state['observation_id'],'key',key='Control+z')
 await page.locator('button').first.click();assert await page.evaluate('ctrl') is False


@pytest.mark.asyncio
async def test_native_delayed_redraw_waits_for_new_pixels(tmp_path,monkeypatch):
 c=computer_module.Computer(tmp_path,tmp_path/'caps');started=time.monotonic();state={'window_id':'123','focus':'123','rect':[0,0,800,600],'title':'Synthetic'}
 async def read(_):return dict(state)
 async def capture(*_):return b'old pixels' if time.monotonic()-started<.35 else b'new pixels'
 monkeypatch.setattr(c,'_state',read);monkeypatch.setattr(c,'_capture',capture)
 got,pixels,quiet=await c._settled_capture({'kind':'desktop'})
 assert quiet and pixels==b'new pixels' and time.monotonic()-started>=.6


@pytest.mark.asyncio
async def test_never_stable_pixels_keep_fresh_unsettled_observation(tmp_path,monkeypatch):
 c=computer_module.Computer(tmp_path,tmp_path/'caps');target={'kind':'desktop','observation':'old'};count=0
 async def read(_):return {'window_id':'123'}
 async def capture(*_):
  nonlocal count
  count+=1;return str(count).encode()
 monkeypatch.setattr(c,'_state',read);monkeypatch.setattr(c,'_capture',capture)
 result=await c._observe('id',target)
 assert result['readiness']['status']=='unsettled'
 assert target['observation']!='old'
 assert Path(result['screenshot']).exists()


@pytest.mark.asyncio
async def test_cancelled_settle_invalidates_old_token(tmp_path,monkeypatch):
 c=computer_module.Computer(tmp_path,tmp_path/'caps');target={'kind':'desktop','observation':'old'};entered=asyncio.Event()
 async def read(_):entered.set();await asyncio.Event().wait()
 monkeypatch.setattr(c,'_state',read)
 task=asyncio.create_task(c._observe('id',target));await entered.wait();task.cancel()
 with pytest.raises(asyncio.CancelledError):await task
 assert 'observation' not in target


@pytest.mark.asyncio
async def test_every_named_key_in_inventory_is_accepted_by_actual_playwright(target):
 c,state=target;page=c.targets[state['target_id']]['page']
 await page.goto('about:blank')
 keys=computer_module._BROWSER_KEYS|computer_module._BROWSER_MODIFIERS|{'Key'+chr(n) for n in range(65,91)}|{'Digit'+str(n) for n in range(10)}|{'Numpad'+str(n) for n in range(10)}|{'F'+str(n) for n in range(1,13)}
 for key in sorted(keys):
  await page.keyboard.down(key)
  await page.keyboard.up(key)


@pytest.mark.asyncio
async def test_dispatch_and_cleanup_failures_both_reported_and_other_keys_released(target,monkeypatch):
 c,state=target;ident=state['target_id'];page=c.targets[ident]['page'];real_down,real_up=page.keyboard.down,page.keyboard.up
 async def down(key):
  if key=='z':raise RuntimeError('dispatch sentinel')
  await real_down(key)
 async def up(key):
  if key=='z':raise RuntimeError('cleanup sentinel')
  await real_up(key)
 monkeypatch.setattr(page.keyboard,'down',down);monkeypatch.setattr(page.keyboard,'up',up)
 with pytest.raises(ComputerError,match='dispatch sentinel; keyboard release also failed: cleanup sentinel'):
  await c.act(ident,state['observation_id'],'key',key='Control+z')
 await page.evaluate('() => {window.ctrl=null;document.onclick=e=>window.ctrl=e.ctrlKey}')
 await page.locator('button').first.click();assert await page.evaluate('ctrl') is False


@pytest.mark.asyncio
async def test_unmocked_native_io_is_fail_closed():
 with pytest.raises(AssertionError,match='Real native I/O is forbidden'):
  await computer_module._command('/usr/bin/xdotool','mousemove','--window','123','1','1')


@pytest.mark.asyncio
async def test_animated_canvas_keeps_read_only_evidence_without_claiming_quiet(target):
 c,old=target;ident=old['target_id'];page=c.targets[ident]['page']
 await page.set_content('<canvas width=400 height=300></canvas><script>let n=0;window.timer=setInterval(()=>{const c=document.querySelector("canvas").getContext("2d");c.fillStyle=`rgb(${n++%255},0,0)`;c.fillRect(0,0,400,300)},30)</script>')
 state=await c.observe(ident)
 assert state['readiness']['status']=='unsettled'
 assert Path(state['screenshot']).read_bytes().startswith(b'\x89PNG')
 await page.evaluate('clearInterval(timer);const c=document.querySelector("canvas").getContext("2d");c.fillStyle="blue";c.fillRect(0,0,400,300)')
 with pytest.raises(ComputerError,match='pixels changed'):
  await c.act(ident,state['observation_id'],'click',x=50,y=50)


@pytest.mark.asyncio
async def test_no_animation_frames_still_returns_fresh_coherent_evidence(target,monkeypatch):
 c,old=target;ident=old['target_id'];page=c.targets[ident]['page'];original=page.evaluate
 async def evaluate(expression,*a,**kw):
  if 'new Promise(resolve => requestAnimationFrame' in expression:
   await asyncio.Event().wait()
  return await original(expression,*a,**kw)
 monkeypatch.setattr(page,'evaluate',evaluate)
 state=await c.observe(ident)
 assert state['readiness']['status']=='unsettled'
 assert state['observation_id']!=old['observation_id']
 assert state['state']['elements']
 assert Path(state['screenshot']).read_bytes().startswith(b'\x89PNG')


def test_paced_plan_retains_corners_and_bounds():
    import math
    points=[(10,10),(110,10),(110,90)]
    plan=Computer._paced_plan(points,1000)
    assert plan[0]==(0,points[0]) and plan[-1]==(1,points[-1])
    assert points[1] in [p for _,p in plan]
    assert len(plan)<=256
    for (ta,a),(tb,b) in zip(plan,plan[1:]):
        assert 0<=tb-ta<=.02000001
        assert math.dist(a,b)<=4
    hold=Computer._paced_plan([(10,10)]*2,1000)
    assert hold[-1]==(1,(10,10)) and len(hold)>=51
    with pytest.raises(ComputerError,match='subdivisions'):
        Computer._paced_plan([(0,0),(1279,799)],2000)


@pytest.mark.parametrize('duration',[None,True,0,-1,2001,'100',1.5])
async def test_invalid_duration_preserves_observation_and_no_input(target,monkeypatch,duration):
    c,state=target;ident=state['target_id'];page=c.targets[ident]['page']
    async def forbidden(*a,**kw):raise AssertionError('input dispatched')
    monkeypatch.setattr(page.mouse,'move',forbidden)
    with pytest.raises(ComputerError,match='duration_ms'):
        await c.act(ident,state['observation_id'],'stroke',points=[{'x':10,'y':10},{'x':20,'y':20}],duration_ms=duration)
    assert c.targets[ident]['observation']==state['observation_id']


async def test_paced_excessive_plan_and_wrong_action_preserve_token(target,monkeypatch):
    c,state=target;ident=state['target_id'];page=c.targets[ident]['page']
    async def forbidden(*a,**kw):raise AssertionError('input dispatched')
    monkeypatch.setattr(page.mouse,'move',forbidden)
    for action,args in [('drag',{'points':[{'x':0,'y':0},{'x':1279,'y':799}]}),('click',{'x':10,'y':10})]:
        with pytest.raises(ComputerError,match='subdivisions|unused parameters'):
            await c.act(ident,state['observation_id'],action,duration_ms=1000,**args)
        assert c.targets[ident]['observation']==state['observation_id']


async def test_real_paced_drag_interpolates_and_reports_hold(target):
    c,old=target;ident=old['target_id'];page,state=await prepare_canvas(c,ident)
    await page.evaluate('()=>{window.moves=[];document.addEventListener("mousemove",e=>{if(e.buttons) moves.push([e.clientX,e.clientY])})}')
    result=await c.act(ident,state['observation_id'],'drag',points=[{'x':20,'y':20},{'x':220,'y':20}],duration_ms=400)
    moves=await page.evaluate('moves')
    assert any(80<x<160 and y==20 for x,y in moves)
    assert result['action_result']['requested_duration_ms']==400
    assert result['action_result']['actual_hold_ms']>=390
    assert await page.evaluate('down') is False


@pytest.mark.parametrize('failure',['cancel','viewport'])
async def test_paced_wait_failure_releases_before_next_move(target,monkeypatch,failure):
    c,old=target;ident=old['target_id'];page,state=await prepare_canvas(c,ident)
    original=page.mouse.down;pressed=asyncio.Event()
    async def down(**kw):await original(**kw);pressed.set()
    monkeypatch.setattr(page.mouse,'down',down)
    task=asyncio.create_task(c.act(ident,state['observation_id'],'stroke',points=[{'x':20,'y':20}]*2,duration_ms=1000))
    await pressed.wait();await asyncio.sleep(.05)
    if failure=='cancel':task.cancel()
    else:await page.set_viewport_size({'width':600,'height':400})
    with pytest.raises(asyncio.CancelledError if failure=='cancel' else ComputerError):await task
    assert await page.evaluate('down') is False
    assert not c._lock.locked()


@pytest.mark.parametrize('failure',['none','focus','cancel','timeout'])
async def test_native_paced_hold_checks_and_releases(tmp_path,monkeypatch,failure):
    c=Computer(tmp_path,tmp_path/'caps');calls=[];pressed=False;checks=0
    state={'window_id':'123','focus':'123','title':'Mock','rect':[0,0,800,600]}
    async def read(_):
        nonlocal checks
        checks+=1
        if pressed and checks>4:
            if failure=='focus':return {**state,'focus':'456'}
            if failure=='cancel':raise asyncio.CancelledError()
            if failure=='timeout':await asyncio.Event().wait()
        return dict(state)
    async def command(*args):
        nonlocal pressed
        calls.append(args[1:])
        assert args[1] in {'mousemove','mousedown','mouseup'}
        if args[1]=='mousedown':pressed=True
        if args[1]=='mouseup':pressed=False
        return ''
    monkeypatch.setattr(c,'_desktop_state',read)
    monkeypatch.setattr(computer_module,'_command',command)
    monkeypatch.setattr(computer_module.shutil,'which',lambda _:'/fake/xdotool')
    task=c._desktop_action({'window_id':'123'},state,'stroke',{'points':[{'x':10,'y':20}]*2,'duration_ms':100})
    if failure=='none':
        result=await task;assert result['actual_hold_ms']>=90
    else:
        expected=asyncio.CancelledError if failure=='cancel' else (TimeoutError if failure=='timeout' else ComputerError)
        with pytest.raises(expected):await asyncio.wait_for(task,.3)
    assert calls[-1]==('mouseup','1') and not pressed
    assert all(call[1:3]==('--window','123') for call in calls if call[0]=='mousemove')


@pytest.mark.parametrize('origin', [(1, 22), (-120, -40)])
async def test_native_client_geometry_uses_absolute_origin(tmp_path, monkeypatch, origin):
    c = Computer(tmp_path, tmp_path/'caps')
    calls = []
    async def command(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0].endswith('xwininfo'):
            assert kwargs['env']['LC_ALL'] == 'C'
            return f'  Absolute upper-left X: {origin[0]}\n  Absolute upper-left Y: {origin[1]}\n  Relative upper-left X: 1\n  Relative upper-left Y: 22\n  Width: 640\n  Height: 220\n'
        return {'getwindowname': 'Fixture', 'getwindowfocus': '123',
                'getwindowgeometry': 'X=2\nY=44\nWIDTH=640\nHEIGHT=220'}[args[1]]
    monkeypatch.setattr(computer_module.shutil, 'which', lambda name: '/fake/'+name)
    monkeypatch.setattr(computer_module, '_command', command)
    state = await c._desktop_state({'window_id': '123'})
    assert state['rect'] == [*origin, 640, 220]
    assert not any('getwindowgeometry' in args for args, _ in calls)


@pytest.mark.parametrize('geometry', ['', 'Width: 640\nHeight: 220',
    'Absolute upper-left X: 1\nAbsolute upper-left Y: 22\nWidth: 0\nHeight: 220',
    'Absolute upper-left X: 1\nAbsolute upper-left Y: 22\nWidth: 640\nWidth: 12\nHeight: 220'])
async def test_native_malformed_geometry_refuses_attach(tmp_path, monkeypatch, geometry):
    c = Computer(tmp_path, tmp_path/'caps')
    monkeypatch.setenv('DISPLAY', ':96')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
    monkeypatch.setattr(computer_module.shutil, 'which', lambda name: '/fake/'+name)
    async def command(*args, **kwargs):
        if args[0].endswith('xwininfo'): return geometry
        if args[1] == 'getwindowgeometry': return 'X=2\nY=44\nWIDTH=640\nHEIGHT=220'
        return '123'
    monkeypatch.setattr(computer_module, '_command', command)
    with pytest.raises(ComputerError, match='geometry|dimensions'):
        await c.open('desktop', window_id='123')
    assert not c.targets


def test_native_missing_xwininfo_reports_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv('DISPLAY', ':96')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
    monkeypatch.setattr(computer_module.shutil, 'which', lambda name: None if name == 'xwininfo' else '/fake/'+name)
    assert Computer(tmp_path, tmp_path/'caps').capabilities()['desktop'] is False


async def test_native_full_text_chunks_preserve_literal_characters_and_check_target(tmp_path, monkeypatch):
    c = Computer(tmp_path, tmp_path/'caps')
    state = {'window_id': '123', 'focus': '123', 'title': 'Fixture', 'rect': [1,22,640,220]}
    text = ('22 Aa+;&\n-é' * 400)[:4000]
    typed = []; checks = []
    async def read(_):
        checks.append(len(''.join(typed)))
        return dict(state)
    async def command(*args):
        if args[1] == 'getwindowfocus': return '123'
        assert args[1:6] == ('type', '--clearmodifiers', '--delay', '12', '--')
        assert len(args[6]) <= 128
        typed.append(args[6]); return ''
    monkeypatch.setattr(c, '_desktop_state', read)
    monkeypatch.setattr(computer_module, '_command', command)
    monkeypatch.setattr(computer_module.shutil, 'which', lambda _: '/fake/xdotool')
    await c._desktop_action({'window_id':'123'}, state, 'type', {'text':text})
    assert ''.join(typed) == text
    assert checks[0] == 0 and checks[-1] == 4000
    assert len(checks) >= len(typed)+1


@pytest.mark.parametrize('changed', ['focus', 'title', 'rect'])
async def test_native_type_target_change_stops_remaining_chunks(tmp_path, monkeypatch, changed):
    c = Computer(tmp_path, tmp_path/'caps'); typed=[]
    state = {'window_id':'123','focus':'123','title':'Fixture','rect':[1,22,640,220]}
    async def read(_):
        return {**state, changed: {'focus':'456','title':'Other','rect':[5,22,640,220]}[changed]} if typed else dict(state)
    async def command(*args):
        if args[1] == 'getwindowfocus': return '123'
        typed.append(args[-1]); return ''
    monkeypatch.setattr(c, '_desktop_state', read)
    monkeypatch.setattr(computer_module, '_command', command)
    monkeypatch.setattr(computer_module.shutil, 'which', lambda _: '/fake/xdotool')
    with pytest.raises(ComputerError, match='changed.*typ'):
        await c._desktop_action({'window_id':'123'}, state, 'type', {'text':'2'*300})
    assert len(''.join(typed)) == 128


async def test_native_type_cancel_finishes_current_chunk_restoration_only(tmp_path, monkeypatch):
    c = Computer(tmp_path, tmp_path/'caps'); entered=asyncio.Event(); finish=asyncio.Event(); calls=[]; restored=False
    state={'window_id':'123','focus':'123','title':'Fixture','rect':[1,22,640,220]}
    async def read(_): return dict(state)
    async def command(*args):
        nonlocal restored
        if args[1]=='getwindowfocus': return '123'
        calls.append(args[-1]); entered.set()
        await finish.wait(); restored=True; return ''
    monkeypatch.setattr(c, '_desktop_state', read)
    monkeypatch.setattr(computer_module, '_command', command)
    monkeypatch.setattr(computer_module.shutil, 'which', lambda _: '/fake/xdotool')
    task=asyncio.create_task(c._desktop_action({'window_id':'123'},state,'type',{'text':'A'*300}))
    await entered.wait(); task.cancel(); await asyncio.sleep(0)
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert restored and len(calls)==1 and len(calls[0])==128


async def test_native_long_type_deadline_allows_full_input_and_consumes_token(tmp_path, monkeypatch):
    c=Computer(tmp_path,tmp_path/'caps'); state={'window_id':'123','focus':'123','title':'Fixture','rect':[0,0,640,220]}
    owned={'kind':'desktop','window_id':'123','observation':'o','observed':time.monotonic(),'fingerprint':computer_module._fingerprint(state)}
    c.targets['id']=owned; dispatched=[]; deadlines=[]
    async def read(_):return dict(state)
    async def action(*args):dispatched.append(args[-1]['text'])
    async def observe(*_):return {'observation_id':'next'}
    async def wait_for(coro, timeout):
        deadlines.append(timeout)
        if timeout < 48:
            coro.close();raise TimeoutError('4000 conservative native keystroke intervals need 48s')
        return await coro
    monkeypatch.setattr(c,'_state',read);monkeypatch.setattr(c,'_desktop_action',action);monkeypatch.setattr(c,'_observe',observe)
    monkeypatch.setattr(computer_module.asyncio,'wait_for',wait_for)
    result=await c.act('id','o','type',text='2'*4000)
    assert dispatched==['2'*4000] and 48<=deadlines[0]<=120
    assert result['action_result']['task_success']=='unverified' and 'observation' not in owned


async def test_native_typing_command_failure_reports_partial_input_without_retry(tmp_path, monkeypatch):
    c=Computer(tmp_path,tmp_path/'caps');state={'window_id':'123','focus':'123','title':'Fixture','rect':[0,0,640,220]};calls=[]
    async def read(_):return dict(state)
    async def command(*args):
        calls.append(args)
        raise TimeoutError('fixture subprocess expired')
    monkeypatch.setattr(c,'_desktop_state',read);monkeypatch.setattr(computer_module,'_command',command)
    monkeypatch.setattr(computer_module.shutil,'which',lambda _: '/fake/xdotool')
    with pytest.raises(ComputerError,match='partial text or held keys/modifiers.*cleanup could not be confirmed'):
        await c._desktop_action({'window_id':'123'},state,'type',{'text':'A'*300})
    assert len(calls)==1 and len(calls[0][-1])==128


async def test_native_capture_uses_true_client_bounds(tmp_path, monkeypatch):
    from PIL import Image
    c=Computer(tmp_path,tmp_path/'caps');calls=[]
    monkeypatch.setenv('DISPLAY',':96')
    def capture(*,bbox,xdisplay):
        calls.append((bbox,xdisplay));return Image.new('RGB',(640,220),'white')
    monkeypatch.setattr(computer_module.ImageGrab,'grab',capture)
    result=await c._capture({'kind':'desktop'}, {'window_id':'123','focus':'123','rect':[1,22,640,220]})
    assert calls==[((1,22,641,242),':96')] and result.startswith(b'\x89PNG')


async def test_native_missing_geometry_binary_blocks_state_read(tmp_path, monkeypatch):
    monkeypatch.setattr(computer_module.shutil,'which',lambda name: None if name=='xwininfo' else '/fake/'+name)
    with pytest.raises(ComputerError,match='xwininfo.*unavailable'):
        await Computer(tmp_path,tmp_path/'caps')._desktop_state({'window_id':'123'})
