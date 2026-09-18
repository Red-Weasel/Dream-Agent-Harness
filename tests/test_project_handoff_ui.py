"""Project handoff review uses the shipped UI in CPU Chromium, without inference."""
import asyncio

import pytest
from playwright.async_api import expect
from test_workspace_library_ui import library  # noqa: F401
from test_desktop_chat import chat  # noqa: F401


DRAFT = {'document': {'title': 'Landing page handoff', 'kind': 'memory',
                     'content': '## Goal\nReview the landing page.\n\n## Source\nsession-a',
                     'include': False},
         'source': {'session_id': 'session-a', 'project_id': 'alpha', 'partial': True},
         'notice': 'Partial saved evidence. Review before saving.'}


async def open_conversation(page):
    await page.locator('#dream-nav-projects').click()
    await page.locator('#dream-projects-page .library-list button').first.click()
    await page.get_by_role('button', name='Open conversation', exact=True).click()
    await expect(page.get_by_role('button', name='Continue conversation', exact=True)).to_be_visible()


async def test_handoff_error_keeps_conversation_and_retry_opens_unsaved_review(library):
    _, page, prompts, _, writes = library
    await open_conversation(page)
    await page.route('**/sessions/session-a/handoff', lambda r: r.fulfill(status=404, json={'error': 'Saved source unavailable'}))
    draft = page.get_by_role('button', name='Draft project handoff', exact=True)
    await draft.click()
    await expect(page.locator('#dream-projects-page .library-status')).to_contain_text('Saved source unavailable')
    await expect(page.get_by_role('button', name='Continue conversation', exact=True)).to_be_visible()
    await page.route('**/sessions/session-a/handoff', lambda r: r.fulfill(json=DRAFT))
    await draft.click()
    await expect(page.get_by_label('Document Markdown', exact=True)).to_have_value(DRAFT['document']['content'])
    await expect(page.get_by_label('Include in project context', exact=True)).not_to_be_checked()
    await expect(page.locator('[data-document-editor]')).to_contain_text('Unsaved changes')
    await page.get_by_role('button', name='Close editor', exact=True).click()
    await expect(page.get_by_label('Document Markdown', exact=True)).to_be_visible()
    assert writes == [] and prompts == []


async def test_handoff_explicit_save_failure_retry_and_context_opt_in(library):
    _, page, prompts, _, writes = library
    await open_conversation(page)
    await page.route('**/sessions/session-a/handoff', lambda r: r.fulfill(json=DRAFT))
    await page.get_by_role('button', name='Draft project handoff', exact=True).click()
    body = page.get_by_label('Document Markdown', exact=True)
    await body.fill('Reviewed facts and next action')
    await page.get_by_label('Include in project context', exact=True).check()
    await page.route('**/alpha/documents', lambda r: r.fulfill(status=409, json={'error': 'Save conflict'}))
    await page.get_by_role('button', name='Save document', exact=True).click()
    await expect(page.locator('#dream-projects-page .library-status')).to_contain_text('Save conflict')
    await expect(body).to_have_value('Reviewed facts and next action')
    async def save(route):
        if route.request.method == 'GET':
            return await route.fulfill(json={'documents': []})
        writes.append(route.request.post_data_json)
        await route.fulfill(json={**writes[-1], 'id': 'handoff-note', 'sha256': 'saved'})
    await page.route('**/alpha/documents', save)
    await page.get_by_role('button', name='Save document', exact=True).click()
    await expect(page.locator('[data-document-editor]')).to_contain_text('Document saved')
    assert writes == [{'title': DRAFT['document']['title'], 'kind': 'memory', 'content': 'Reviewed facts and next action', 'include': True}]
    assert prompts == []


@pytest.mark.parametrize('change', ['tab', 'project', 'session', 'navigation', 'typing'])
async def test_late_handoff_cannot_replace_new_target_or_draft(library, change):
    _, page, prompts, _, writes = library
    await open_conversation(page)
    ready, release = asyncio.Event(), asyncio.Event()
    async def delayed(route):
        ready.set()
        await release.wait()
        await route.fulfill(json=DRAFT)
    await page.route('**/sessions/session-a/handoff', delayed)
    await page.get_by_role('button', name='Draft project handoff', exact=True).click()
    await asyncio.wait_for(ready.wait(), 2)
    if change in ('tab', 'typing'):
        await page.get_by_role('tab', name='Documents', exact=True).click()
        if change == 'typing':
            await page.get_by_role('button', name='New document', exact=True).click()
            await page.get_by_label('Document Markdown', exact=True).fill('Preserve my draft')
    elif change == 'project':
        await page.get_by_role('button', name='New project', exact=True).click()
    elif change == 'session':
        await page.evaluate("window.dispatchEvent(new Event('dream:session'))")
    else:
        await page.locator('#dream-nav-chat').click()
    async with page.expect_response('**/sessions/session-a/handoff'):
        release.set()
    # A following API round trip gives the response handler a deterministic turn.
    await page.evaluate("fetch('/api/projects').then(r=>r.json())")
    if change == 'typing':
        await expect(page.get_by_label('Document Markdown', exact=True)).to_have_value('Preserve my draft')
    else:
        await expect(page.locator('[data-document-editor]')).to_have_count(0)
    assert writes == [] and prompts == []


async def test_new_handoff_template_is_dirty_and_blocks_conversation_capture(library):
    _, page, prompts, _, writes = library
    await open_conversation(page)
    await page.get_by_role('tab', name='Documents', exact=True).click()
    await page.get_by_role('button', name='New handoff', exact=True).click()
    body = page.get_by_label('Document Markdown', exact=True)
    value = await body.input_value()
    for heading in ['Goal', 'Current artifacts', 'Checks performed', 'Known defects', 'Next action', 'Source']:
        assert '## ' + heading in value
    await expect(page.get_by_label('Include in project context', exact=True)).not_to_be_checked()
    await expect(page.locator('[data-document-editor]')).to_contain_text('Unsaved changes')
    await page.get_by_role('tab', name='Conversations', exact=True).click()
    await page.get_by_role('button', name='Draft project handoff', exact=True).click()
    await expect(page.locator('#dream-projects-page .library-status')).to_contain_text('Save this project draft')
    await page.get_by_role('tab', name='Documents', exact=True).click()
    await expect(body).to_have_value(value)
    assert writes == [] and prompts == []


async def test_handoff_rejects_mismatched_source(library):
    _, page, prompts, _, writes = library
    await open_conversation(page)
    await page.route('**/sessions/session-a/handoff', lambda r: r.fulfill(json={**DRAFT, 'source': {**DRAFT['source'], 'session_id': 'other-session'}}))
    await page.get_by_role('button', name='Draft project handoff', exact=True).click()
    await expect(page.locator('#dream-projects-page .library-status')).to_contain_text('did not match')
    await expect(page.locator('[data-document-editor]')).to_have_count(0)
    assert writes == [] and prompts == []


async def test_handoff_save_preserves_typing_and_updates_same_document(library):
    _, page, prompts, _, writes = library
    await open_conversation(page)
    await page.route('**/sessions/session-a/handoff', lambda r: r.fulfill(json=DRAFT))
    await page.get_by_role('button', name='Draft project handoff', exact=True).click()
    ready, release = asyncio.Event(), asyncio.Event()
    async def delayed(route):
        if route.request.method == 'GET':
            return await route.fulfill(json={'documents': []})
        writes.append(route.request.post_data_json)
        ready.set()
        await release.wait()
        await route.fulfill(json={**writes[-1], 'id': 'saved-note', 'sha256': 'v1'})
    await page.route('**/alpha/documents', delayed)
    await page.get_by_role('button', name='Save document', exact=True).click()
    await asyncio.wait_for(ready.wait(), 2)
    await page.get_by_label('Document Markdown', exact=True).fill('Newer review while saving')
    release.set()
    await expect(page.locator('[data-document-editor]')).to_contain_text('newer edits unsaved')
    await expect(page.get_by_label('Document Markdown', exact=True)).to_have_value('Newer review while saving')
    async def update(route):
        writes.append(route.request.post_data_json)
        await route.fulfill(json={**writes[-1], 'id': 'saved-note', 'sha256': 'v2'})
    await page.route('**/alpha/documents/saved-note', update)
    await page.get_by_role('button', name='Save document', exact=True).click()
    await expect(page.locator('[data-document-editor]')).to_contain_text('Document saved')
    assert writes[0]['include'] is False
    assert writes[1]['expected_sha256'] == 'v1'
    assert writes[1]['content'] == 'Newer review while saving'
    assert prompts == []
