"""Dream Chat, batch B1 (DREAM-228): the conversation is one centered column, Dream's replies are plain text,
Dream's own cards stay inside the column, and the model chip leads the work bar where a view shows it. Since
DREAM-240 the column reads as the Nested view's does: 960 px at most, a name over each message, no bubble. Real Chromium against an isolated StudioServer with synthetic events; no model, no engine."""
import asyncio

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_desktop_chat import chat  # noqa: F401
from test_turn_timing_ui import TIMING

COLUMN = 960        # the text column's cap, in px (768 until DREAM-240)
GEOMETRY = """() => {
  const box = el => { const r = el.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right}; };
  const main = document.getElementById('main');
  return {col: box(document.getElementById('stream')), pane: box(main), composer: box(document.querySelector('footer .composer')),
          paneScrolls: main.scrollWidth > main.clientWidth, pageScrolls: document.documentElement.scrollWidth > innerWidth};
}"""
REPLY = ('## The plan\n\nA reply long enough to wrap: ' + 'the column keeps one readable measure. ' * 12
         + '\n\n- first point\n- second point\n\nA closing paragraph.')


async def converse(srv, page, said='Summarise the release for me.', reply=REPLY):
    await srv._accept_prompt(said)
    srv.bus.publish(Event('text_delta', reply))
    srv.bus.publish(Event('tool_use', {'id': 'c1', 'name': 'read_file', 'input': {'path': 'README.md'}}))
    srv.bus.publish(Event('tool_result', {'id': 'c1', 'content': 'Project notes', 'is_error': False}))
    srv.bus.publish(Event('text_delta', 'Done.'))
    srv.bus.publish(Event('result', {}))
    await expect(page.locator('#stream .dream')).to_have_count(2)


async def inside_column(page, selector, g):
    for box in await page.locator(selector).evaluate_all('els => els.map(e => { const r = e.getBoundingClientRect(); return [r.x, r.right]; })'):
        assert box[0] >= g['col']['x'] - 0.5 and box[1] <= g['col']['r'] + 0.5, (selector, box, g['col'])


@pytest.mark.parametrize('width,height', [(1600, 1000), (1100, 800), (390, 844)])
async def test_conversation_is_one_centered_column_at_three_sizes(chat, width, height):
    srv, page, _, _ = chat
    await page.set_viewport_size({'width': width, 'height': height})
    await converse(srv, page)
    g = await page.evaluate(GEOMETRY)
    col, pane, composer = g['col'], g['pane'], g['composer']
    assert col['w'] <= COLUMN + 0.5, col                                    # 960 px of text at most
    assert abs((col['x'] + col['w'] / 2) - (pane['x'] + pane['w'] / 2)) <= 1.5, (col, pane)   # centered in the pane
    assert abs((composer['x'] + composer['w'] / 2) - (col['x'] + col['w'] / 2)) <= 1.5, (composer, col)   # the composer shares it
    assert composer['w'] <= COLUMN + 0.5, composer
    if width >= 1600:                                                       # room for the whole column: both are exactly it
        assert abs(col['w'] - COLUMN) <= 1 and abs(composer['w'] - COLUMN) <= 1, (col, composer)
        assert abs(composer['x'] - col['x']) <= 1.5, (composer, col)
    else:
        assert abs(composer['w'] - col['w']) <= 32, (composer, col)
    for selector in ('#stream .msg', '#stream .tool', '#stream .msg .body > *'):
        await inside_column(page, selector, g)
    assert not g['pageScrolls'] and not g['paneScrolls'], g                 # the page never scrolls sideways


@pytest.mark.parametrize('width', [1280, 800])
async def test_column_width_and_centering_at_1280_and_800(chat, width):
    srv, page, _, _ = chat
    await page.set_viewport_size({'width': width, 'height': 800})
    await converse(srv, page)
    g = await page.evaluate(GEOMETRY)
    col, pane = g['col'], g['pane']
    left, right = col['x'] - pane['x'], pane['r'] - col['r']
    assert abs(left - right) <= 3 and left >= 12, (left, right)             # equal margins: centered
    if width == 1280:
        assert abs(col['w'] - COLUMN) <= 1, col                             # the cap
    else:
        assert 560 <= col['w'] <= COLUMN, col                               # the pane is narrower than the cap: it fills it
    assert not g['pageScrolls'] and not g['paneScrolls'], g


async def test_user_message_sits_under_its_name_without_a_bubble(chat):
    # DREAM-240 (the owner: [feedback]): as in the Nested view, the owner's message is plain text
    # under "You", from the column's left edge; it was a right-hand bubble with a hidden label (DREAM-228).
    srv, page, _, _ = chat
    await srv._accept_prompt('Short question')
    await srv._accept_prompt('A much longer message from the owner. ' * 20)
    you = page.locator('#stream .msg.you')
    await expect(you).to_have_count(2)
    await expect(you.first).to_have_attribute('data-role', 'user')
    g = await page.evaluate(GEOMETRY)
    for i in range(2):
        text = await you.nth(i).locator('.body').bounding_box()
        assert abs(text['x'] - g['col']['x']) <= 1 and text['x'] + text['width'] <= g['col']['r'] + 0.5, (text, g['col'])   # from the column's left edge, inside it
    await expect(you.first.locator('.who')).to_have_text('You')
    label = await you.first.locator('.who').bounding_box()
    body = await you.first.locator('.body').bounding_box()
    assert label['height'] >= 12 and label['y'] + label['height'] <= body['y'] and abs(label['x'] - body['x']) <= 1, (label, body)   # the name, over the text
    look = await you.first.locator('.body').evaluate(
        'e => { const c = getComputedStyle(e); return [c.backgroundColor, c.backgroundImage, c.boxShadow, c.borderTopWidth]; }')
    assert look == ['rgba(0, 0, 0, 0)', 'none', 'none', '0px'], look


async def test_assistant_message_has_no_bubble(chat):
    srv, page, _, _ = chat
    await converse(srv, page)
    dream = page.locator('#stream .msg.dream').first
    await expect(dream).to_have_attribute('data-role', 'assistant')
    look = await dream.evaluate("""e => { const c = getComputedStyle(e), b = getComputedStyle(e.querySelector('.body'));
      return {border: [c.borderTopWidth, c.borderRightWidth, c.borderBottomWidth, c.borderLeftWidth], background: c.backgroundColor,
              image: c.backgroundImage, shadow: c.boxShadow, blur: c.backdropFilter, pad: [c.paddingLeft, b.paddingLeft],
              body: [b.backgroundColor, b.borderTopWidth]}; }""")
    assert look['border'] == ['0px'] * 4 and look['background'] == 'rgba(0, 0, 0, 0)', look
    assert look['image'] == 'none' and look['shadow'] == 'none' and look['blur'] == 'none', look
    assert look['pad'] == ['0px', '0px'] and look['body'] == ['rgba(0, 0, 0, 0)', '0px'], look
    g = await page.evaluate(GEOMETRY)
    text = await dream.locator('.body p').first.bounding_box()
    assert abs(text['x'] - g['col']['x']) <= 1, (text, g['col'])             # the text starts at the column's left edge
    # Its name is over it (DREAM-240, as the Nested view's "Orchestrator"); the logo mark is not drawn.
    await expect(dream.locator('.who')).to_have_text('Dream')
    label = await dream.locator('.who').evaluate("e => { const r = e.getBoundingClientRect(); return [r.height >= 12, getComputedStyle(e, '::before').content]; }")
    assert label == [True, 'none'], label


@pytest.mark.parametrize('width,height', [(1100, 800), (390, 844)])
async def test_dream_cards_stay_inside_the_column(chat, width, height):
    srv, page, _, _ = chat
    await page.set_viewport_size({'width': width, 'height': height})
    await srv._accept_prompt('Build the page')
    srv.bus.publish(Event('thinking_delta', 'The owner wants a page. ' * 30))
    srv.bus.publish(Event('tool_use', {'id': 't1', 'name': 'read_file', 'input': {'path': 'a/very/long/path/' + 'segment/' * 30 + 'README.md'}}))
    srv.bus.publish(Event('tool_result', {'id': 't1', 'content': 'x' * 400, 'is_error': False}))
    srv.bus.publish(Event('text_delta', 'Working.'))
    srv.bus.publish(Event('result', {}))
    srv.bus.publish(Event('turn_timing', TIMING))
    srv.bus.publish(Event('studio', {'op': 'ask', 'form': {'title': 'Deck', 'questions': [
        {'id': 'who', 'kind': 'text-options', 'title': 'Who is it for?', 'options': ['Executives', 'Engineers']},
        {'id': 'notes', 'kind': 'freeform', 'title': 'Notes'}]}}))
    srv.bus.publish(Event('studio', {'op': 'download', 'path': 'deck.pdf', 'label': 'The deck', 'kind': 'file (4 bytes)'}))
    srv.bus.publish(Event('studio', {'op': 'widget', 'widget': 'link_preview_display_v0', 'data': {'links': [
        {'url': 'https://example.org/a/long/address/' + 'part/' * 20, 'title': 'An example page', 'snippet': 'What it says. ' * 20}]}}))
    srv.bus.publish(Event('plan', {'title': 'Page', 'updated_at': 0, 'phases': [
        {'name': 'Sketch', 'status': 'in_progress', 'summary': '', 'steps': [{'name': 'layout', 'status': 'pending'}]}]}))
    pending = asyncio.create_task(srv.request_permission('run_bash', {'command': 'ls ' + 'a' * 300}, 'Run a command', {'y': 'Allow once', 'n': 'Deny'}))
    try:
        await expect(page.get_by_role('button', name='Allow once', exact=True)).to_be_visible()
        cards = ('.think', '.tool', '.turn-timing', '.qform', '.dlcard', '.vcard', '.permission-card')
        for selector in cards:
            await expect(page.locator('#stream > ' + selector)).to_have_count(1)
        # Since DREAM-231 a completed turn's timing card is the detail of the numbers in its reply's row: open it.
        await expect(page.locator('#stream > .turn-timing')).to_be_hidden()
        await page.locator('#stream .msg.dream .msg-meta').click()
        await expect(page.locator('#stream > .turn-timing')).to_be_visible()
        g = await page.evaluate(GEOMETRY)
        for selector in cards:
            await inside_column(page, '#stream > ' + selector, g)
        assert not g['pageScrolls'] and not g['paneScrolls'], g
        # The plan strip keeps its place in the composer (fix #88), inside the same column.
        strip = await page.locator('footer .composer > #plan-panel').bounding_box()
        assert strip['x'] >= g['composer']['x'] and strip['x'] + strip['width'] <= g['composer']['r'] + 0.5, (strip, g['composer'])
        # They still work: the tool card opens to its result, the thinking block to its text, the request is answered.
        tool = page.locator('#stream > .tool')
        if not await tool.evaluate('e => e.open'):
            await tool.locator('summary').click()
        await expect(tool.get_by_role('button', name='Inspect result', exact=False)).to_be_visible()
        await expect(page.locator('#stream > .think')).to_contain_text('The owner wants a page.')
        await expect(page.locator('#stream > .qform').get_by_text('Who is it for?')).to_be_visible()
        await page.get_by_role('button', name='Allow once', exact=True).click()
        assert await asyncio.wait_for(pending, 2) == 'y'
        await expect(page.locator('#stream > .permission-card')).to_have_count(0)
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def test_the_model_chip_sits_under_the_composer_and_opens_council(chat):
    # DREAM-242: no view shows the work bar; its first chip, the session's model, is the first of the chips under the
    # composer (it led the top bar, DREAM-228), and a click still opens Council.
    _, page, prompts, _ = chat
    await expect(page.locator('#dream-workbar')).to_be_hidden()
    chip = page.locator('#cn-model')
    await expect(chip).to_have_text('fixture')                               # the session's model, from hello
    first = await page.locator('footer .hint .cn-chip:visible').first.get_attribute('id')
    assert first == 'cn-model', first
    await expect(page.locator('#dream-council')).to_be_hidden()
    await chip.click()
    await expect(page.locator('#dream-council')).to_be_visible()             # where the main model changes between turns
    assert prompts == []
