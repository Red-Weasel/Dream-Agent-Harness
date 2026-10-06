"""Dream Chat (DREAM-237), the owner's notes of 2026-10-03: the thinking box follows the text being written
("it should auto scroll down so youre always at the bottom of the message seeing the text generate") unless the
reader scrolled up inside it, and a file dropped anywhere on the window joins the draft ("drop anywhere
attachments"). Real Chromium, synthetic events, no model."""
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_desktop_chat import chat  # noqa: F401

GAP = "e => e.scrollHeight - e.scrollTop - e.clientHeight"
DRAG = """([selector, withFile]) => {
  const dt = new DataTransfer();
  if(withFile) dt.items.add(new File(['hello'], 'notes.txt', {type: 'text/plain'})); else dt.setData('text/plain', 'just text');
  const target = document.querySelector(selector);
  for(const type of ['dragenter', 'dragover']) target.dispatchEvent(new DragEvent(type, {dataTransfer: dt, bubbles: true, cancelable: true}));
  const during = document.body.classList.contains('file-drag-page');
  const drop = new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true});
  target.dispatchEvent(drop);
  return {during, after: document.body.classList.contains('file-drag-page'), taken: drop.defaultPrevented};
}"""


async def test_the_thinking_box_follows_its_newest_line_until_the_reader_scrolls_up(chat):
    srv, page, _, _ = chat
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('thinking_delta', ''.join(f'step {n}: weighing the layout\n' for n in range(60))))
    body = page.locator('#stream details.think > div').last
    # DREAM-240: the desktop starts a thinking block as one line that shows the end of the text; opened while the text
    # still arrives, its box (capped) shows the newest line and follows from there.
    await expect(page.locator('#stream details.think .think-tail')).to_contain_text('step 59: weighing the layout')
    await page.locator('#stream details.think > summary').last.click()
    await expect(body).to_be_visible()
    assert await body.evaluate("e => e.scrollHeight > e.clientHeight + 200"), "the reasoning is longer than its box"
    assert await body.evaluate(GAP) <= 2                                     # at the end: the newest line shows
    srv.bus.publish(Event('thinking_delta', ''.join(f'step {n}: more\n' for n in range(60, 90))))
    await expect(body).to_contain_text('step 89: more')
    assert await body.evaluate(GAP) <= 2                                     # still following
    await body.evaluate("e => { e.scrollTop = 0; }")                         # the reader scrolls up to read
    srv.bus.publish(Event('thinking_delta', ''.join(f'step {n}: later\n' for n in range(90, 120))))
    await expect(body).to_contain_text('step 119: later')
    assert await body.evaluate("e => e.scrollTop") == 0                      # and is not pulled away
    await body.evaluate("e => { e.scrollTop = e.scrollHeight; }")            # back at the end: it follows again
    srv.bus.publish(Event('thinking_delta', ''.join(f'step {n}: last\n' for n in range(120, 150))))
    await expect(body).to_contain_text('step 149: last')
    assert await body.evaluate(GAP) <= 2
    srv.bus.publish(Event('turn_end', {}))


async def test_a_file_dropped_anywhere_on_the_window_joins_the_draft(chat):
    _, page, _, _ = chat
    for selector in ('#main', 'header'):                                     # the conversation, the top bar: not only the composer
        seen = await page.evaluate(DRAG, [selector, True])
        assert seen == {'during': True, 'after': False, 'taken': True}, (selector, seen)   # the veil shows, the drop is taken, the veil goes
    names = page.locator('#attachments .attachment-name')
    await expect(names).to_have_count(2)
    await expect(names.first).to_have_text('notes.txt')
    # A drag that carries no file (selected text, a skill card) is not this page's business.
    seen = await page.evaluate(DRAG, ['#main', False])
    assert seen == {'during': False, 'after': False, 'taken': False}, seen
    await expect(names).to_have_count(2)
