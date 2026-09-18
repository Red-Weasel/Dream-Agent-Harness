"""Exercise real feed layout and scroll position without model requests."""
import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_desktop_chat import chat  # noqa: F401


async def at_latest(page):
    await page.wait_for_function('''() => {
      const el = document.getElementById('main');
      return el.scrollHeight > el.clientHeight &&
        el.scrollHeight - el.scrollTop - el.clientHeight <= 2;
    }''')


async def settle_layout(page):
    await page.evaluate('''() => new Promise(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve))))''')


@pytest.mark.parametrize('desktop', [True, False])
@pytest.mark.parametrize('kind', ['text_delta', 'tool_result', 'agent_activity'])
async def test_feed_follows_large_updates_and_bursts(chat, desktop, kind):
    server, page, prompts, _ = chat
    if not desktop:
        await page.goto(server.url)
    text = '\n\n'.join(f'Visible activity paragraph {n}.' for n in range(60))
    if kind == 'text_delta':
        server.bus.publish(Event(kind, text))
    elif kind == 'tool_result':
        server.bus.publish(Event('tool_use', {'id': 'long-result', 'name': 'read_file', 'input': {}}))
        server.bus.publish(Event(kind, {'id': 'long-result', 'content': '\n'.join(['Result line'] * 20)}))
        # A burst of separate tool cards can exceed a viewport between frames.
        for n in range(20):
            server.bus.publish(Event('tool_use', {'id': str(n), 'name': 'read_file', 'input': {}}))
    else:
        for n in range(20):
            server.bus.publish(Event(kind, {'run_id': str(n), 'agent': 'Verifier',
                                           'kind': 'text_delta', 'text': text}))
    await at_latest(page)
    server.bus.publish(Event('system', 'Latest activity marker'))
    await expect(page.locator('#stream > .sys').last).to_have_text('Latest activity marker')
    await at_latest(page)
    assert prompts == []


@pytest.mark.parametrize('desktop', [True, False])
async def test_feed_keeps_history_reading_position_and_resumes_at_bottom(chat, desktop):
    server, page, prompts, _ = chat
    if not desktop:
        await page.goto(server.url)
    server.bus.publish(Event('text_delta', '\n\n'.join(['Earlier paragraph'] * 80)))
    await expect(page.locator('#stream .dream')).to_have_count(1)
    # Put the reader at the latest content, then genuinely scroll upward.
    await page.locator('#main').evaluate("el => el.scrollTo({top:el.scrollHeight, behavior:'instant'})")
    await settle_layout(page)
    before = await page.locator('#main').evaluate('el => el.scrollTop')
    await page.locator('#main').hover(position={'x': 5, 'y': 50})
    await page.mouse.wheel(0, -500)
    await page.wait_for_function('(before) => document.getElementById("main").scrollTop < before - 100', arg=before)
    await settle_layout(page)
    reading = await page.locator('#main').evaluate('el => el.scrollTop')
    server.bus.publish(Event('assistant_done', {}))
    server.bus.publish(Event('text_delta', 'A new answer block\n\n' * 40))
    server.bus.publish(Event('tool_use', {'id': 'after-reading', 'name': 'read_file', 'input': {}}))
    await expect(page.locator('#stream > .tool')).to_have_count(1)
    await settle_layout(page)
    assert abs(await page.locator('#main').evaluate('el => el.scrollTop') - reading) <= 2

    await page.locator('#main').evaluate("el => el.scrollTo({top:el.scrollHeight, behavior:'instant'})")
    await settle_layout(page)
    server.bus.publish(Event('system', 'Another long update\n' * 50))
    await expect(page.locator('#stream > .sys').last).to_contain_text('Another long update')
    await at_latest(page)
    assert prompts == []


async def test_feed_follows_relayout_and_mobile_streaming(chat):
    server, page, prompts, _ = chat
    server.bus.publish(Event('text_delta', '\n\n'.join(['A wrapping paragraph with enough words to occupy several lines on a narrow screen.'] * 60)))
    await expect(page.locator('#stream .dream')).to_have_count(1)
    await page.locator('#main').evaluate("el => el.scrollTo({top:el.scrollHeight, behavior:'instant'})")
    await settle_layout(page)
    await page.set_viewport_size({'width': 480, 'height': 650})
    await at_latest(page)
    server.bus.publish(Event('text_delta', '\n\nMobile update. ' * 50))
    await expect(page.locator('#stream .dream')).to_contain_text('Mobile update')
    await at_latest(page)
    assert prompts == []


async def test_returning_to_bottom_and_stream_update_in_same_frame_resumes_following(chat):
    server, page, _, _ = chat
    server.bus.publish(Event('text_delta', '\n\n'.join(['Earlier paragraph'] * 80)))
    await expect(page.locator('#stream .dream')).to_have_count(1)
    await page.locator('#main').evaluate("el => el.scrollTo({top:el.scrollHeight, behavior:'instant'})")
    await settle_layout(page)
    await page.locator('#main').evaluate('el => el.scrollTop -= 500')
    await settle_layout(page)
    # Arrival can beat the browser's queued scroll event after the reader moves.
    await page.evaluate('''() => {
      const el = document.getElementById('main');
      el.scrollTop = el.scrollHeight;
      handle({kind:'text_delta', data:'\\n\\nSimultaneous update. '.repeat(80)});
    }''')
    await at_latest(page)
