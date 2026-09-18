"""Private, frame-linked manual evidence without desktop capture or inference."""
import json
from copy import deepcopy

import pytest

from dream import demonstrations as demos


@pytest.fixture
def recording():
    folder = demos.directory('demo-evidence')
    (folder / 'frames').mkdir(parents=True)
    from PIL import Image
    Image.new('RGB', (32, 32), 'blue').save(folder / 'frames/00001.jpg')
    (folder / 'manifest.json').write_text(json.dumps({'id':'demo-evidence', 'status':'ready',
        'frames':[{'file':'frames/00001.jpg','seconds':0}]}))
    return folder


def annotation():
    return {'application':'Example editor', 'app_version':'1.2', 'events':[{
        'id':'step-1', 'seconds':0, 'frame':'frames/00001.jpg', 'action':'Open project',
        'target':'Project menu', 'before':'Welcome', 'after':'Project visible',
        'outcome':'Correct project loaded', 'basis':'user-confirmed', 'confirmed':True}]}


def test_saved_evidence_is_private_linked_and_reused_in_draft(recording):
    saved = demos.save_evidence('demo-evidence', annotation())
    assert saved['source'] == 'manual annotation'
    assert demos.read_evidence('demo-evidence') == saved
    assert (recording / 'annotations.json').stat().st_mode & 0o777 == 0o600
    draft = demos.create_draft('demo-evidence', goal='Open project', steps=[{
        'action':'Open project', 'frames':['frames/00001.jpg'], 'evidence_ids':['step-1']}])
    assert 'Example editor' in draft.read_text() and 'Correct project loaded' in draft.read_text()
    stored = json.loads((draft.parent / 'evidence.json').read_text())
    assert stored['annotations']['events'][0]['confirmed'] is True


@pytest.mark.parametrize('change', [
    {'frame':'../outside'}, {'seconds':float('nan')}, {'seconds':-1}, {'seconds':9999},
    {'confirmed':'yes'}, {'confirmed':True,'basis':'inferred'}, {'action':'x'*2001},
    {'id':'../bad'}, {'source':'captured action'}, {'outcome':''},
])
def test_invalid_evidence_does_not_replace_previous_save(recording, change):
    demos.save_evidence('demo-evidence', annotation())
    previous = (recording / 'annotations.json').read_bytes()
    changed = annotation()
    changed['events'][0].update(change)
    with pytest.raises(ValueError):
        demos.save_evidence('demo-evidence', changed)
    assert (recording / 'annotations.json').read_bytes() == previous


def test_legacy_recording_and_symlink_refusal(recording, tmp_path):
    assert demos.read_evidence('demo-evidence')['events'] == []
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    (recording / 'annotations.json').symlink_to(outside)
    with pytest.raises(ValueError):
        demos.save_evidence('demo-evidence', annotation())
    assert outside.read_text() == '{}'


def test_stale_edits_are_rejected(recording):
    first = demos.save_evidence('demo-evidence', annotation(), expected_revision=0)
    edited = deepcopy(annotation())
    edited['application'] = 'New name'
    demos.save_evidence('demo-evidence', edited, expected_revision=first['revision'])
    with pytest.raises(ValueError, match='changed'):
        demos.save_evidence('demo-evidence', annotation(), expected_revision=first['revision'])


def test_model_cannot_invent_user_confirmation(recording):
    step = {'action':'Delete project', 'frames':['frames/00001.jpg'], 'basis':'user-confirmed'}
    with pytest.raises(ValueError, match='human annotation'):
        demos.create_draft('demo-evidence', goal='Test', steps=[step])
    demos.save_evidence('demo-evidence', annotation())
    step['evidence_ids'] = ['step-1']
    with pytest.raises(ValueError, match='human annotation'):
        demos.create_draft('demo-evidence', goal='Test', steps=[step])


@pytest.mark.asyncio
async def test_evidence_routes_auth_revision_frame_and_size(recording):
    from starlette.applications import Starlette
    import httpx
    from dream.gui.bus import EventBus
    from dream.gui.server import StudioServer
    from dream.gui.workflow_routes import routes
    server = StudioServer(EventBus())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes(server))), base_url='http://testserver') as client:
        url = '/api/learning/demo-evidence/evidence'
        assert (await client.get(url)).status_code == 401
        client.headers['x-dream-token'] = server.token
        assert (await client.get(url)).json()['revision'] == 0
        assert (await client.post(url, json={**annotation(), 'revision':0}, headers={'origin':'https://other.test'})).status_code == 403
        assert (await client.post(url, json=annotation())).status_code == 400
        saved = await client.post(url, json={**annotation(), 'revision':0})
        assert saved.status_code == 200
        assert (await client.post(url, json={**annotation(), 'revision':0})).status_code == 409
        assert (await client.get(url)).json()['events'][0]['outcome'] == 'Correct project loaded'
        assert (await client.post(url, content='x'*512001)).status_code == 413
        frame = await client.get('/api/learning/demo-evidence/frames/00001.jpg')
        assert frame.status_code == 200 and frame.content == (recording / 'frames/00001.jpg').read_bytes()
        assert (await client.get('/api/learning/demo-evidence/frames/99999.jpg')).status_code == 400
        assert (await client.get('/api/learning/demo-evidence/frames/00001.jpg', headers={'x-dream-token':'bad'})).status_code == 401


from test_studio_controls import controls, open_controls, no_overflow


@pytest.mark.asyncio
async def test_learn_authors_reopens_and_reuses_real_evidence(controls, recording):
    from playwright.async_api import expect
    _, page, api, url, errors = controls
    api.learning['demonstrations'] = [{'id':'demo-evidence', 'name':'Synthetic editor', 'status':'ready',
                                      'frames':[{'file':'frames/00001.jpg','seconds':0}]}]
    await open_controls(page, url, 'Learn')
    await page.get_by_role('button', name='Annotate evidence').click()
    await page.wait_for_function("document.querySelector('#dc-evidence-image')?.naturalWidth === 32")
    await page.locator('#dc-evidence-app').fill('Example editor')
    await page.locator('#dc-evidence-version').fill('1.2')
    await page.locator('#dc-evidence-action').fill('Open project')
    await page.locator('#dc-evidence-before').fill('Welcome')
    await page.locator('#dc-evidence-after').fill('Project visible')
    await page.locator('#dc-evidence-outcome').fill('Correct project loaded')
    await page.locator('#dc-evidence-basis').select_option('user-confirmed')
    await page.locator('#dc-evidence-confirmed').check()
    await page.get_by_role('button', name='Save event', exact=True).click()
    await expect(page.locator('#dc-evidence-status')).to_contain_text('Saved locally')
    evidence = demos.read_evidence('demo-evidence')
    assert evidence['events'][0]['confirmed'] is True
    await page.get_by_role('button', name='Annotate evidence').click()
    await page.locator('#dc-evidence-event').select_option(evidence['events'][0]['id'])
    await expect(page.locator('#dc-evidence-outcome')).to_have_value('Correct project loaded')
    await expect(page.locator('#dc-evidence-app')).to_have_value('Example editor')
    await page.set_viewport_size({'width':390, 'height':844})
    await no_overflow(page)
    assert not errors


@pytest.mark.asyncio
async def test_tool_pages_annotations_without_truncating_json(recording):
    from dream.tools.demonstration_tools import demonstration_read
    payload = annotation()
    payload['events'] = [{**payload['events'][0], 'id':f'event-{i}', 'before':'b'*2000, 'after':'a'*2000,
                         'target':'t'*2000, 'outcome':'o'*2000, 'action':'x'*2000} for i in range(5)]
    demos.save_evidence('demo-evidence', payload)
    response = await demonstration_read.handler({'id':'demo-evidence', 'event_offset':2})
    text = response['content'][0]['text']
    result = json.loads(text)
    assert len(text) < 16000
    assert result['annotations']['events'][0]['id'] == 'event-2'
    assert result['annotations']['next_event_offset'] == 3


@pytest.mark.asyncio
async def test_late_draft_review_cannot_replace_new_evidence_editor(controls, recording):
    import asyncio
    from playwright.async_api import expect
    _, page, api, url, errors = controls
    api.learning['demonstrations'] = [{'id':'demo-evidence', 'name':'Synthetic editor', 'status':'draft',
                                      'frames':[{'file':'frames/00001.jpg','seconds':0}]}]
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(route):
        entered.set()
        await release.wait()
        await route.fulfill(json={'body':'An earlier skill draft'})
    await page.route('**/api/learning/demo-evidence/draft', delayed)
    await open_controls(page, url, 'Learn')
    try:
        await page.get_by_role('button', name='Review skill draft', exact=True).click()
        await asyncio.wait_for(entered.wait(), 2)
        await page.get_by_role('button', name='Annotate evidence').click()
        await page.locator('#dc-evidence-action').fill('Preserve this unsaved action')
        async with page.expect_response('**/api/learning/demo-evidence/draft'):
            release.set()
        await page.wait_for_timeout(100)
        await expect(page.locator('#dc-evidence-action')).to_have_value('Preserve this unsaved action')
        assert not errors
    finally:
        release.set()


@pytest.mark.asyncio
async def test_pending_evidence_save_locks_event_navigation_and_preserves_fields(controls, recording):
    import asyncio
    from playwright.async_api import expect
    _, page, api, url, errors = controls
    api.learning['demonstrations'] = [{'id':'demo-evidence', 'name':'Synthetic editor', 'status':'ready',
                                      'frames':[{'file':'frames/00001.jpg','seconds':0}]}]
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(route):
        if route.request.method != 'POST':
            await route.continue_()
            return
        entered.set()
        await release.wait()
        await route.continue_()
    await page.route('**/api/learning/demo-evidence/evidence', delayed)
    await open_controls(page, url, 'Learn')
    await page.get_by_role('button', name='Annotate evidence').click()
    await page.locator('#dc-evidence-action').fill('First action')
    try:
        await page.get_by_role('button', name='Save event', exact=True).click()
        await asyncio.wait_for(entered.wait(), 2)
        await expect(page.locator('#dc-evidence-new')).to_be_disabled()
        await expect(page.locator('#dc-evidence-event')).to_be_disabled()
        await expect(page.locator('#dc-evidence-action')).to_be_disabled()
        release.set()
        await expect(page.locator('#dc-evidence-status')).to_contain_text('Saved locally')
        await expect(page.locator('#dc-evidence-action')).to_have_value('First action')
        await page.locator('#dc-evidence-new').click()
        await page.locator('#dc-evidence-action').fill('Second action')
        await page.get_by_role('button', name='Save event', exact=True).click()
        await expect(page.locator('#dc-evidence-status')).to_contain_text('Saved locally')
        await expect(page.locator('#dc-evidence-event option')).to_have_count(3)
        assert [e['action'] for e in demos.read_evidence('demo-evidence')['events']] == ['First action', 'Second action']
        assert not errors
    finally:
        release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize('cap', [24000, 1800])
async def test_escaped_evidence_obeys_real_transport_cap_and_can_be_reassembled(recording, monkeypatch, cap):
    from dream import config
    from dream.tools.demonstration_tools import demonstration_read
    monkeypatch.setattr(config, 'TOOL_RESULT_CAP', cap)
    payload = annotation()
    for field in ('action', 'target', 'before', 'after', 'outcome'):
        payload['events'][0][field] = '\x00'*2000
    demos.save_evidence('demo-evidence', payload)
    result = await demonstration_read.handler({'id':'demo-evidence'})
    text = result['content'][0]['text']
    assert len(text) <= cap
    summary = json.loads(text)
    assert 'action' in summary['annotations']['events'][0]['shortened_fields']
    reconstructed, offset = '', 0
    while offset is not None:
        result = await demonstration_read.handler({'id':'demo-evidence', 'event_offset':0,
                                                   'event_field':'action', 'field_offset':offset, 'evidence_revision':summary['annotations']['revision']})
        text = result['content'][0]['text']
        assert len(text) <= cap
        part = json.loads(text)
        reconstructed += part['text']
        offset = part['next_field_offset']
    assert reconstructed == '\x00'*2000


@pytest.mark.asyncio
async def test_evidence_field_pages_reject_changed_revision(recording):
    from dream.tools.demonstration_tools import demonstration_read
    old = demos.save_evidence('demo-evidence', annotation())
    demos.save_evidence('demo-evidence', annotation())
    result = await demonstration_read.handler({'id':'demo-evidence', 'event_field':'action',
                                               'evidence_revision':old['revision']})
    assert result['is_error'] is True
    assert 'Evidence changed' in result['content'][0]['text']


@pytest.mark.asyncio
@pytest.mark.parametrize('extra', [{}, {'event_field':'action'}])
async def test_impossibly_small_evidence_cap_is_an_error(recording, monkeypatch, extra):
    from dream import config
    from dream.tools.demonstration_tools import demonstration_read
    demos.save_evidence('demo-evidence', annotation())
    monkeypatch.setattr(config, 'TOOL_RESULT_CAP', 100)
    result = await demonstration_read.handler({'id':'demo-evidence', **extra})
    assert result.get('is_error') is True
    text = result['content'][0]['text']
    assert len(text) <= 100
    assert isinstance(json.loads(text), dict)
