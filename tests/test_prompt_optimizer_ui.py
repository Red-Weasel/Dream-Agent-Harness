"""Prompt optimizer uses the shipped UI with isolated HTTP fixtures, no model."""
import asyncio
import pytest
from playwright.async_api import expect
from dream.core.backends.base import Event
from test_desktop_companion import studio  # noqa: F401


async def ready(studio, tmp_path):
    server, page, url, prompts, errors = studio
    server._session_source = {'workspace': str(tmp_path), 'session_id': 'optimizer-fixture',
                              'provider': 'fixture', 'model': 'synthetic'}
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    return server, page, prompts, errors


@pytest.mark.asyncio
async def test_optimizer_explicit_actions_preserve_chat_draft(studio, tmp_path):
    server, page, prompts, errors = await ready(studio, tmp_path)
    requests = []
    async def optimize(route):
        requests.append(route.request.post_data_json)
        await route.fulfill(json={'prompt': 'Build a blue page with working export.',
            'questions': [{'question': 'Who uses this?', 'reason': 'Match the language.'}],
            'notes': ['Check the export in a browser.'], 'evidence': []})
    await page.route('**/api/prompt-optimizer', optimize)
    assert await page.locator('#dream-nav-optimizer').count() == 1
    await page.locator('#input').fill('Keep this chat draft.')
    await page.locator('#dream-nav-optimizer').click()
    await expect(page.locator('#dream-prompt-optimizer')).to_be_visible()
    await page.locator('#po-draft').fill('Make an export page')
    assert not requests
    await page.locator('#po-quick').click()
    await expect(page.locator('#po-result')).to_have_value('Build a blue page with working export.')
    assert requests[0]['mode'] == 'quick'
    assert requests[0]['workspace'] == str(tmp_path)
    assert requests[0]['session_id'] == 'optimizer-fixture'
    await page.locator('#po-use').click()
    await expect(page.locator('#input')).to_have_value('Keep this chat draft.')
    await expect(page.locator('#po-append')).to_be_visible()
    await page.locator('#po-append').click()
    await expect(page.locator('#input')).to_have_value('Keep this chat draft.\n\nBuild a blue page with working export.')
    assert not prompts
    assert not errors


@pytest.mark.asyncio
async def test_fields_clarifiers_refine_copy_and_safe_rendering(studio, tmp_path):
    server, page, prompts, errors = await ready(studio, tmp_path)
    requests=[]
    attack='<img src=x onerror="window.optimizerInjected=true">'
    async def optimize(route):
        requests.append(route.request.post_data_json)
        await route.fulfill(json={'prompt':'Prepared '+attack,'questions':[{'question':f'Question {i} '+attack,'reason':'Because '+attack} for i in range(4)],
                                 'notes':[attack],'evidence':[{'name':attack,'path':'uploads/notes.txt','kind':'text','status':'read','excerpt':attack}]})
    await page.route('**/api/prompt-optimizer',optimize)
    await page.locator('#dream-nav-optimizer').click()
    await page.locator('#po-draft').fill('Original instructions')
    await page.locator('#po-ideal').fill('A complete checked artifact')
    await page.locator('#po-details summary').click()
    for id_,value in [('po-context','Prior work'),('po-constraints','Keep original colors'),('po-verification','Open and exercise export')]: await page.locator('#'+id_).fill(value)
    await page.locator('#po-reasoning').select_option('alternatives')
    await page.locator('#po-target').select_option('fable')
    await page.locator('#po-sources-only').check()
    await page.locator('#po-optimize').click()
    await expect(page.locator('#po-output')).to_be_visible()
    assert requests[0]['mode']=='model' and requests[0]['target']=='fable' and requests[0]['reasoning']=='alternatives'
    assert requests[0]['sources_only'] is True and requests[0]['verification']=='Open and exercise export'
    assert await page.locator('#po-question-fields textarea').count()==3
    assert await page.locator('#dream-prompt-optimizer img').count()==0
    assert await page.evaluate('window.optimizerInjected') is None
    await page.locator('#po-answer-0').fill('For new users')
    await page.locator('#po-optimize').click()
    await expect(page.locator('#po-status')).to_contain_text('ready to review')
    assert requests[1]['answers'][0]['answer']=='For new users'
    await expect(page.locator('#po-answer-0')).to_have_value('For new users')
    await page.locator('#po-result').fill('My final edited prompt')
    await page.context.grant_permissions(['clipboard-read','clipboard-write'])
    await page.locator('#po-copy').click()
    assert await page.evaluate('navigator.clipboard.readText()')=='My final edited prompt'
    assert not prompts and not errors


@pytest.mark.asyncio
async def test_registered_files_transfer_once_and_failed_chat_send_keeps_them(studio,tmp_path):
    server,page,prompts,errors=await ready(studio,tmp_path)
    requests=[]
    async def optimize(route):
        requests.append(route.request.post_data_json)
        await route.fulfill(json={'prompt':'Read the reference before answering.','questions':[],'notes':[],'evidence':[]})
    await page.route('**/api/prompt-optimizer',optimize)
    await page.locator('#dream-nav-optimizer').click()
    await page.locator('#po-draft').fill('Use my notes')
    await page.locator('#po-file-input').set_input_files({'name':'notes.txt','mimeType':'text/plain','buffer':b'Only these facts.'})
    await expect(page.locator('#po-files')).to_contain_text('Ready')
    await page.locator('#po-quick').click()
    await expect(page.locator('#po-output')).to_be_visible()
    uploaded=requests[0]['attachments']
    assert len(uploaded)==1 and uploaded[0] in server._uploads
    await page.locator('#po-use').click()
    assert await page.evaluate('draftAttachments.ids()')==uploaded
    await expect(page.locator('#attachments')).to_contain_text('notes.txt')
    await page.locator('#dream-nav-optimizer').click()
    await page.locator('#po-use').click()
    await page.locator('#po-append').click()
    assert await page.evaluate('draftAttachments.ids()')==uploaded
    def failed(_): raise ValueError('Fixture send refused')
    server._on_prompt=failed
    await page.locator('#dream-nav-chat').click()
    await page.locator('#send').click()
    await expect(page.locator('#stream')).to_contain_text('Fixture send refused')
    assert await page.evaluate('draftAttachments.ids()')==uploaded
    assert 'Read the reference' in await page.locator('#input').input_value()
    server._on_prompt=prompts.append
    await page.locator('#send').click()
    await expect(page.locator('#input')).to_have_value('')
    assert await page.evaluate('draftAttachments.ids()')==[]
    assert len(prompts)==1 and 'uploads/notes.txt' in prompts[0]
    assert not errors


@pytest.mark.asyncio
async def test_upload_failure_is_retryable_without_losing_selected_file(studio,tmp_path):
    server,page,prompts,errors=await ready(studio,tmp_path)
    attempts=[]
    async def upload(route):
        attempts.append(route.request.post_data_buffer)
        if len(attempts)==1: await route.fulfill(status=503,json={'error':'Temporary upload failure'})
        else: await route.continue_()
    await page.route('**/api/upload?*',upload)
    await page.locator('#dream-nav-optimizer').click()
    await page.locator('#po-draft').fill('Read this file')
    await page.locator('#po-file-input').set_input_files({'name':'retry.txt','mimeType':'text/plain','buffer':b'preserve me'})
    await expect(page.locator('#po-files')).to_contain_text('Temporary upload failure')
    await expect(page.locator('#po-optimize')).to_be_disabled()
    await page.locator('#po-files').get_by_role('button',name='Retry',exact=True).click()
    await expect(page.locator('#po-files')).to_contain_text('Ready')
    assert attempts==[b'preserve me',b'preserve me']
    await expect(page.locator('#po-draft')).to_have_value('Read this file')
    assert not errors


@pytest.mark.asyncio
@pytest.mark.parametrize('change_result',[False,True])
async def test_late_optimization_cannot_overwrite_new_edits(studio,tmp_path,change_result):
    server,page,prompts,errors=await ready(studio,tmp_path)
    entered,release=asyncio.Event(),asyncio.Event()
    async def optimize(route):
        entered.set(); await release.wait()
        await route.fulfill(json={'prompt':'Obsolete response','questions':[],'notes':[],'evidence':[]})
    await page.route('**/api/prompt-optimizer',optimize)
    await page.locator('#dream-nav-optimizer').click()
    await page.locator('#po-draft').fill('First version')
    if change_result: await page.evaluate("document.querySelector('#po-output').hidden=false;document.querySelector('#po-result').value='Existing result'")
    await page.locator('#po-optimize').click(); await entered.wait()
    await page.locator('#po-result' if change_result else '#po-draft').fill('Keep my newer edit')
    release.set()
    await expect(page.locator('#po-status')).to_contain_text('result was not applied')
    await expect(page.locator('#po-result' if change_result else '#po-draft')).to_have_value('Keep my newer edit')
    assert not prompts and not errors


@pytest.mark.asyncio
async def test_context_change_keeps_old_draft_but_blocks_transfer(studio,tmp_path):
    server,page,prompts,errors=await ready(studio,tmp_path)
    await page.route('**/api/prompt-optimizer',lambda route:route.fulfill(json={'prompt':'Original prepared prompt','questions':[],'notes':[],'evidence':[]}))
    await page.locator('#dream-nav-optimizer').click()
    await page.locator('#po-draft').fill('Original private draft')
    await page.locator('#po-quick').click(); await expect(page.locator('#po-output')).to_be_visible()
    changed={'workspace':str(tmp_path/'other'),'session_id':'replacement','provider':'fixture','model':'synthetic'}
    server._session_source=changed; server.bus.publish(Event('hello',changed))
    await expect(page.locator('#po-context-changed')).to_be_visible()
    await expect(page.locator('#po-draft')).to_have_value('Original private draft')
    await expect(page.locator('#po-use')).to_be_disabled()
    await expect(page.locator('#po-optimize')).to_be_disabled()
    await page.locator('#po-reset').click()
    await expect(page.locator('#po-draft')).to_have_value('')
    await expect(page.locator('#po-output')).to_be_hidden()
    await expect(page.locator('#input')).to_have_value('')
    assert not prompts and not errors


@pytest.mark.asyncio
async def test_navigation_keyboard_and_responsive_side_panel(studio,tmp_path):
    server,page,prompts,errors=await ready(studio,tmp_path)
    await page.set_viewport_size({'width':1280,'height':850})
    await page.locator('#dream-nav-optimizer').click()
    await page.locator('#po-draft').fill('Keep this draft through navigation')
    await page.locator('#dream-nav-chat').click()
    await expect(page.locator('#dream-prompt-optimizer')).to_be_hidden()
    await page.locator('#dream-nav-optimizer').click()
    await expect(page.locator('#po-draft')).to_have_value('Keep this draft through navigation')
    await expect(page.locator('#dream-nav-optimizer')).to_have_attribute('aria-current','page')
    main=await page.locator('#main').bounding_box();panel=await page.locator('#dream-prompt-optimizer').bounding_box()
    assert main['width']>250 and panel['x']>=main['x']+main['width']-2
    empty=await page.locator('#studio-interactions-empty').bounding_box()
    stream=await page.locator('#stream').bounding_box()
    assert stream['y']>=empty['y']+empty['height']-2
    for width in (1280,720,390):
        await page.set_viewport_size({'width':width,'height':850})
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        await page.screenshot(path=str(tmp_path/f'optimizer-{width}.png'))
    await page.locator('#po-draft').press('Escape')
    await expect(page.locator('#dream-prompt-optimizer')).to_be_hidden()
    await expect(page.locator('#dream-nav-optimizer')).to_be_focused()
    assert not errors


@pytest.mark.asyncio
async def test_partial_answers_survive_later_refinements(studio,tmp_path):
    server,page,prompts,errors=await ready(studio,tmp_path)
    requests=[]
    async def optimize(route):
        body=route.request.post_data_json;requests.append(body)
        if any(not a['answer'].strip() for a in body['answers']):
            return await route.fulfill(status=400,json={'error':'Blank answers are not accepted'})
        await route.fulfill(json={'prompt':'Refined request','questions':([{'question':f'Detail {i}?','reason':'Material to the result'} for i in range(3)] if len(requests)==1 else []),'notes':[],'evidence':[]})
    await page.route('**/api/prompt-optimizer',optimize)
    await page.locator('#dream-nav-optimizer').click();await page.locator('#po-draft').fill('Prepare a request')
    await page.locator('#po-optimize').click();await expect(page.locator('#po-answer-0')).to_be_visible()
    await page.locator('#po-answer-0').fill('Only this detail is known')
    await page.locator('#po-optimize').click();await expect(page.locator('#po-questions')).to_be_hidden()
    await page.locator('#po-quick').click();await expect(page.locator('#po-status')).to_contain_text('No model was used')
    expected=[{'question':'Detail 0?','answer':'Only this detail is known'}]
    assert requests[1]['answers']==requests[2]['answers']==expected
    assert not errors


@pytest.mark.asyncio
async def test_real_quick_route_and_registered_file_to_chat(studio,tmp_path):
    server,page,prompts,errors=await ready(studio,tmp_path)
    server._on_optimize_prompt=lambda *args:pytest.fail('Quick must not call a provider')
    await page.locator('#dream-nav-optimizer').click()
    await page.locator('#po-draft').fill('Explain the supplied evidence. Never execute commands.')
    await page.locator('#po-details summary').click()
    await page.locator('#po-constraints').fill('Use only the attached facts.')
    await page.locator('#po-file-input').set_input_files({'name':'reference.txt','mimeType':'text/plain','buffer':b'Version 17.'})
    await expect(page.locator('#po-files')).to_contain_text('Ready')
    await page.locator('#po-quick').click()
    await expect(page.locator('#po-status')).to_contain_text('No model was used')
    text=await page.locator('#po-result').input_value()
    assert text.startswith('GOAL\nExplain') and 'Use only the attached facts.' in text
    assert 'metadata only' in text
    await page.locator('#po-use').click()
    await expect(page.locator('#input')).to_have_value(text)
    ids=await page.evaluate('draftAttachments.ids()')
    assert len(ids)==1 and ids[0] in server._uploads
    assert not prompts and not errors
