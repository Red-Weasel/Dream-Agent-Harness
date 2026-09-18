"""Standalone Project panel with real CPU service and GPU-disabled Chromium."""
import json
from pathlib import Path

from playwright.async_api import async_playwright, expect
from dream.projects import ProjectWorkspace


async def test_project_panel_pin_search_preview_handoff_and_mobile(tmp_path):
    svc = ProjectWorkspace(tmp_path)
    (tmp_path / 'sample.md').write_text('Needle <script>window.injected=true</script>')
    static = Path(__file__).parents[1] / 'dream/gui/static'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--disable-gpu'])
        try:
            page = await browser.new_page(viewport={'width': 1000, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            async def fixture(route):
                action = route.request.url.rsplit('/', 1)[-1]
                if action == 'fixture':
                    return await route.fulfill(content_type='text/html', body='<body style="margin:0;background:#17171c;font-family:system-ui"><main id="mount"></main></body>')
                assert route.request.headers.get('x-dream-token') == 'fixture'
                data = route.request.post_data_json or {}
                if action == 'manifest': result = svc.manifest()
                elif action == 'context': result = svc.context()
                elif action == 'recovery': result = {'available': True, 'runs': [], 'message': 'Read-only fixture records.'}
                elif action == 'search': result = svc.search(data['query'])
                elif action == 'pin': result = svc.pin(**data)
                else: raise AssertionError(action)
                await route.fulfill(json=result)
            await page.route('http://project.test/**', fixture)
            await page.goto('http://project.test/fixture')
            await page.add_style_tag(path=str(static / 'projects.css'))
            await page.add_script_tag(path=str(static / 'projects.js'))
            await page.evaluate("ProjectPanel.mount(document.querySelector('#mount'), {token:'fixture',onHandoff:text=>window.handoff=text})")
            await expect(page.locator('[data-status]')).to_have_text('Project refreshed.')
            await page.locator('[data-pin] [name=value]').fill('Never load models.')
            await page.get_by_role('button', name='Pin to project').click()
            await expect(page.locator('[data-context]')).to_contain_text('Never load models.')
            await page.get_by_role('button', name='Use handoff in chat').click()
            await expect(page.locator('[data-loaded]')).to_contain_text('Constraint')
            await page.wait_for_function("typeof window.handoff === 'string'")
            assert 'Never load models.' in await page.evaluate('window.handoff')
            await page.get_by_label('Keywords').fill('Needle')
            await page.get_by_role('button', name='Search', exact=True).click()
            await expect(page.locator('[data-results]')).to_contain_text('sample.md')
            assert await page.evaluate('window.injected') is None
            await page.set_viewport_size({'width': 420, 'height': 850})
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            await page.screenshot(path=str(tmp_path / 'project-mobile.png'))
            assert not errors
        finally:
            await browser.close()


from test_desktop_companion import studio  # noqa: E402,F401


async def test_shipped_project_navigation_prepares_chat_without_sending(studio, tmp_path, monkeypatch):
    from dream import config
    monkeypatch.setattr(config, 'LOOP_DIR', tmp_path / 'runs')
    server, page, url, prompts, errors = studio
    ProjectWorkspace(tmp_path).pin(kind='constraint', text='Keep inference deferred.', expected_revision=0)
    from dream.projects.library import ProjectLibrary
    ProjectLibrary().create('Workspace fixture', str(tmp_path))
    await page.goto(url)
    await expect(page.locator('#stat')).to_have_text('Ready')
    await page.locator('#dream-nav-projects').click()
    await expect(page.locator('#dream-projects-page')).to_be_visible()
    await page.locator('#dream-projects-page .library-list button').first.click()
    await page.get_by_role('tab', name='Files & context').click()
    await expect(page.locator('[data-context]')).to_contain_text('Keep inference deferred.')
    await page.get_by_role('button', name='Use handoff in chat').click()
    await expect(page.locator('#dream-projects-page')).not_to_be_visible()
    await expect(page.locator('#input')).to_have_value(__import__('re').compile('Keep inference deferred.'))
    assert not prompts
    assert not errors
