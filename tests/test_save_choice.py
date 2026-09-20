"""Ask-to-save (owner choice, DREAM-083): closing Dream or starting a new chat asks
whether the session goes to long-term memory; the desktop close dialog and the
new-chat dialog answer it with "/quit save|nosave" and "/new save|nosave"."""
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright, expect

from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from dream.tui.app import App

pytestmark = pytest.mark.asyncio


def _app():
    app = object.__new__(App)
    app.engine = SimpleNamespace(store=None)
    app.renderer = SimpleNamespace(console=None)
    app._exit_consolidate = None
    return app


@pytest.mark.parametrize("line,decided", [("/quit", None), ("/quit save", True), ("/quit nosave", False),
                                          ("/exit save", True)])
async def test_quit_carries_the_save_answer(line, decided):
    app = _app()
    assert await app._command(line) is True
    assert app._exit_consolidate is decided


async def test_new_chat_asks_before_saving(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    prompts = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, session={'workspace': str(tmp_path), 'model': 'fixture'})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1100, 'height': 800})
            page.set_default_timeout(4000)
            await page.goto(url + '&companion=1')
            await page.locator('#studio-new').click()
            await expect(page.locator('#new-chat-ask')).to_be_visible()
            await page.locator('#new-chat-ask button[data-new="cancel"]').click()
            await expect(page.locator('#new-chat-ask')).to_have_count(0)
            assert prompts == []
            await page.locator('#studio-new').click()
            await page.locator('#new-chat-ask button[data-new="nosave"]').click()
            await page.wait_for_timeout(300)
            assert prompts == ['/new nosave']
            await browser.close()
    finally:
        await srv.stop()
