"""'Read ×2' (owner idea, DREAM-083): with the pill on, the model receives the user's
typed words twice; the chat shows them once; steering, slash commands and Continue
buttons are never doubled."""
import httpx
import pytest
from playwright.async_api import async_playwright, expect

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import READ_AGAIN, StudioServer

pytestmark = pytest.mark.asyncio


@pytest.fixture
def server(tmp_path):
    prompts = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append,
                       session={'workspace': str(tmp_path), 'model': 'fixture'})
    return srv, prompts


async def test_only_the_typed_words_are_doubled_and_shown_once(server):
    srv, prompts = server
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://test',
                                 headers={'X-Dream-Token': srv.token}) as client:
        await client.post('/api/prompt', json={'prompt': 'Build the orbit shot', 'read_twice': True})
        await client.post('/api/prompt', json={'prompt': 'Build the orbit shot'})
        await client.post('/api/prompt', json={'prompt': '/new', 'read_twice': True})
    assert prompts[0] == 'Build the orbit shot' + READ_AGAIN + 'Build the orbit shot'
    assert prompts[1] == 'Build the orbit shot' and prompts[2] == '/new'
    shown = [e['data'] for e in srv.bus.conversation.snapshot()['events'] if e['kind'] == 'user']
    assert shown[0] == 'Build the orbit shot'


async def test_the_pill_sends_the_flag_and_replay_shows_the_words_once(server, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    srv, prompts = server
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1100, 'height': 800})
            page.set_default_timeout(4000)
            await page.goto(url + '&companion=1')
            pill = page.locator('#read-twice')
            await expect(pill).to_have_attribute('aria-pressed', 'false')
            await pill.click()
            await expect(pill).to_have_attribute('aria-pressed', 'true')
            await page.locator('#input').fill('Fix the fringing')
            await page.locator('#send').click()
            await expect(page.locator('#stream .you')).to_have_count(1)
            assert prompts == ['Fix the fringing' + READ_AGAIN + 'Fix the fringing']
            await expect(page.locator('#stream .you').last).to_have_text('YouFix the fringing')
            # a doubled message replayed from history is shown once
            srv.bus.publish(Event('user', 'Old request' + READ_AGAIN + 'Old request'))
            await expect(page.locator('#stream .you').last).to_have_text('YouOld request')
            # the preference survives a reload (this viewer only)
            await page.reload()
            await expect(page.locator('#read-twice')).to_have_attribute('aria-pressed', 'true')
            await browser.close()
    finally:
        await srv.stop()
