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
            await page.goto(url.replace('/#', '/?companion=1#'))
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


# --- DREAM-185: the owner labels the memory a save writes ------------------------------------------------------

async def test_quit_save_carries_the_owners_label():
    app = _app()
    assert await app._command("/quit save 09.28.26 - rocket animation - day 2") is True
    assert app._exit_consolidate is True and app._exit_label == "09.28.26 - rocket animation - day 2"
    app = _app()
    await app._command("/quit nosave ignored words")
    assert app._exit_consolidate is False and app._exit_label is None


def test_a_label_is_one_short_printable_line():
    from dream.tui.app import memory_label
    assert memory_label("  a\nb\x07   c ") == "a b c"
    assert memory_label("") is None and memory_label("   ") is None
    assert len(memory_label("x" * 500)) == 120


async def test_the_label_titles_the_session_and_asks_consolidation_for_a_memory_of_that_title(tmp_path, monkeypatch):
    from dream.core import engine as engine_module
    from dream.memory.store import MemoryStore
    monkeypatch.setattr(engine_module.config, "SESSIONS_DIR", tmp_path / "sessions")
    e = engine_module.Engine(provider="machx", model="fixture", workspace=tmp_path)
    e.store = MemoryStore(tmp_path / "t.db")
    e.store.start_session(e.session_id)
    e._started, e._turn_index = True, 1
    asked = []

    async def fake_consolidate():
        asked.append(getattr(e, "_memory_label", None))
        return "summary"

    async def no_cleanup():
        e._started = False
    monkeypatch.setattr(e, "consolidate", fake_consolidate)
    monkeypatch.setattr(e, "_cleanup", no_cleanup)
    assert await e.stop(consolidate=True, label="09.28.26 - rocket animation - day 2") == "summary"
    assert asked == ["09.28.26 - rocket animation - day 2"]
    row = e.store._conn.execute("SELECT title FROM sessions WHERE id=?", (e.session_id,)).fetchone()
    assert row[0] == "09.28.26 - rocket animation - day 2"
    e.store.close()


def test_the_consolidation_prompt_names_the_label_as_data():
    import inspect
    from dream.core import engine as engine_module
    source = inspect.getsource(engine_module.Engine._consolidate)
    assert "_memory_label" in source and "<label>" in source and "title is exactly that label" in source
