"""Real Studio DOM and websocket fixtures; Chromium runs with --disable-gpu."""
import copy

from playwright.async_api import expect

from dream.core.backends.base import Event
from dream.gui.conversation import Conversation
from test_studio_controls import controls  # noqa: F401


TIMING = {
    'outcome':'completed', 'elapsed_s':12.4, 'first_activity_s':1.2, 'first_text_s':3.4,
    'phases':{'preparation':{'count':1,'seconds':0.4}, 'approval':{'count':1,'seconds':2},
              'tool_execution':{'count':2,'seconds':3.5}, 'post_processing':{'count':1,'seconds':0.2}},
    'phases_may_overlap':True, 'request_count':2, 'requests_dropped':0,
    'cache':{'reported_requests':1,'unreported_requests':1,'cached_tokens':128},
    'requests':[
        {'index':1,'client_elapsed_s':5.0,'first_activity_s':1.0,'first_text_s':3.0,
         'usage':{'cached_tokens':128},'server_timings':{'prompt_ms':900,'predicted_ms':1800},
         'schema_fingerprint':'a'*64},
        {'index':2,'client_elapsed_s':2.0,'first_activity_s':0.5,'first_text_s':None,
         'usage':{},'server_timings':{},'schema_fingerprint':'a'*64},
    ],
}


async def test_timing_card_separates_client_server_and_cache_evidence(controls):
    server, page, api, url, errors = controls
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    server.bus.publish(Event('result', {'stats':{'timing':TIMING}}))
    server.bus.publish(Event('turn_timing', TIMING))
    card = page.locator('.turn-timing')
    await expect(card).to_have_count(1)
    await expect(card.locator(':scope > summary')).to_contain_text('12.4 s')
    await expect(card.locator(':scope > summary')).to_contain_text('First answer 3.4 s')
    await card.locator(':scope > summary').focus()
    await page.keyboard.press('Enter')
    await expect(card).to_have_attribute('open', '')
    await card.locator('.tt-request summary').first.click()
    await expect(card.get_by_text('Server-reported prompt processing', exact=True)).to_be_visible()
    for text in ['First activity', '1.2 s', 'Approval wait', 'Tool execution',
                 'may overlap', 'Client request elapsed', 'Server-reported prompt processing',
                 '900 ms', 'Cached tokens reported: 128', '1 request did not report cache usage',
                 'does not prove a prompt-cache hit']:
        await expect(card).to_contain_text(text)
    # The companion reads its confirmed permission mode on connection.
    # Expanding timing evidence must not issue any mutating control action.
    assert api.calls == [{"action": "permission_mode_status"}] and errors == []


async def test_unknown_cache_and_incomplete_turn_are_not_zero_or_success(controls):
    server, page, _, url, errors = controls
    data = copy.deepcopy(TIMING)
    data.update(outcome='interrupted', first_activity_s=None, first_text_s=None, requests=[], request_count=0)
    data['cache']={'reported_requests':0,'unreported_requests':0,'cached_tokens':None}
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    server.bus.publish(Event('turn_timing', data))
    card=page.locator('.turn-timing')
    await expect(card.locator(':scope > summary')).to_contain_text('Interrupted')
    await card.locator(':scope > summary').click()
    await expect(card).to_contain_text('First answer not observed')
    await expect(card).to_contain_text('Cached tokens: unknown')
    await expect(card).to_contain_text('Server timings: not reported')
    assert 'Cached tokens reported: 0' not in await card.inner_text()
    assert errors == []


async def test_timing_plain_text_mobile_and_history_replace(controls):
    server, page, _, url, errors = controls
    data=copy.deepcopy(TIMING)
    data['outcome']='<img src=x onerror="window.timingPwned=1">'
    data['phases']={'<script>window.timingPwned=1</script>':{'count':1,'seconds':1}}
    await page.set_viewport_size({'width':390,'height':844})
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    server.bus.publish(Event('turn_timing', data))
    card=page.locator('.turn-timing')
    await card.locator(':scope > summary').click()
    await expect(card).to_contain_text('<img src=x')
    assert await card.locator('img,script').count() == 0
    assert await page.evaluate('window.timingPwned') is None
    assert await card.evaluate('e => e.scrollWidth <= e.clientWidth')
    box=await card.bounding_box()
    assert box['x'] >= 0 and box['x']+box['width'] <= 390
    data['outcome']='reconnected'
    server.bus.publish(Event('history', {'events':[{'kind':'turn_timing','data':data}]}))
    await expect(card.locator(':scope > summary')).to_contain_text('reconnected')
    await expect(card).to_have_count(1)
    assert errors == []


async def test_background_job_states_are_visible_without_claiming_memory_saved(controls):
    server,page,_,url,errors=controls
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    for state in ['queued','running','completed','interrupted','dropped','failed']:
        server.bus.publish(Event('background_work', {'kind':state,'label':'auto-filing <script>bad()</script>',
                                                    'queued':1,'reason':'full' if state=='dropped' else ''}))
    rows=page.locator('.sys').filter(has_text='Optional filing')
    await expect(rows).to_have_count(6)
    text=' '.join(await rows.all_inner_texts())
    for state in ['queued','running','completed','interrupted','dropped','failed']:
        assert state in text
    assert 'memory saved' not in text.lower() and 'filed successfully' not in text.lower()
    assert await rows.locator('script').count() == 0
    assert errors == []


def test_conversation_retains_timing_and_background_for_reconnect():
    history=Conversation()
    history.append(Event('turn_timing', TIMING))
    history.append(Event('background_work', {'kind':'completed','label':'auto-filing'}))
    snapshot=history.snapshot()
    assert [event['kind'] for event in snapshot['events']] == ['turn_timing','background_work']
    snapshot['events'][0]['data']['elapsed_s']=0
    assert history.snapshot()['events'][0]['data']['elapsed_s']==12.4


async def test_actual_reload_preserves_two_turns_and_background_status(controls):
    server,page,_,url,errors=controls
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    server.bus.publish(Event('turn_timing', TIMING))
    server.bus.publish(Event('turn_timing', TIMING))
    server.bus.publish(Event('background_work', {'kind':'completed','label':'auto-filing','queued':0}))
    await expect(page.locator('.turn-timing')).to_have_count(2)
    await page.reload()
    await expect(page.locator('#stat')).to_have_text('Ready')
    await expect(page.locator('.turn-timing')).to_have_count(2)
    await expect(page.locator('.sys').filter(has_text='Optional filing completed')).to_have_count(1)
    assert errors == []


async def test_timing_card_exposes_errors_without_calling_them_task_failure(controls):
    from dream.telemetry.turn import TurnTiming
    server, page, api, url, errors = controls
    timing = TurnTiming()
    timing.observe_tool('tool_use', {'name': 'read_file'})
    timing.observe_tool('tool_result', {'name': 'read_file', 'is_error': True})
    timing.observe_tool('tool_result', {'name': 'see'})
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    server.bus.publish(Event('turn_timing', timing.finish('completed')))
    card = page.locator('details.turn-timing')
    await card.locator(':scope > summary').click()
    await expect(card).to_contain_text('1 reported error')
    await expect(card).to_contain_text('1 unknown outcome')
    await expect(card).to_contain_text('does not establish task success')
    assert api.calls == [{"action": "permission_mode_status"}] and errors == []
