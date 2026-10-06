"""Dream Chat, batch B3 (DREAM-231): message actions. Under each finished reply a quiet row with Copy (the reply's
markdown source), the reply's speed and time with the turn's timing one click away, and "Try again" on a failed or
stopped turn; a Copy on the owner's own message. The last reply's row is always visible, older rows on hover and
on keyboard focus, all of them at a touch width. Real Chromium against an isolated StudioServer with synthetic
events; no model, no engine."""
from playwright.async_api import async_playwright, expect

from dream.core.backends.base import Event
from dream.gui.bus import EventBus
from dream.gui.server import StudioServer
from test_desktop_chat import chat  # noqa: F401
from test_turn_timing_ui import TIMING

SOURCE = ('## Plan\n\nUse **bold**, `code` and a list:\n\n1. one\n2. two\n\n```python\nprint("hi")\n```\n\n'
          '| a | b |\n|---|---|\n| 1 | 2 |\n\nSee https://example.org/docs.')
CLIPBOARD = 'navigator.clipboard.readText()'


async def turn(srv, page, said, reply, stats=None, timing=None):
    """One whole turn, as Dream publishes it: the owner's message, the reply, the result, the turn's end."""
    replies = await page.locator('#stream .msg.dream').count()
    await srv._accept_prompt(said)
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', reply))
    srv.bus.publish(Event('result', {'stats': stats} if stats else {}))
    if timing:
        srv.bus.publish(Event('turn_timing', timing))
    srv.bus.publish(Event('turn_end', {'interrupted': False}))
    await expect(page.locator('#stream .msg.dream')).to_have_count(replies + 1)
    await expect(page.locator('#stat')).to_have_text('Ready')
    return page.locator('#stream .msg.dream').nth(replies)


async def away(page):
    """The pointer rests on nothing in the conversation."""
    await page.mouse.move(2, 2)


async def test_copy_reply_copies_the_markdown_source(chat):
    srv, page, _, _ = chat
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    await srv._accept_prompt('Give me the plan.')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', SOURCE[:60]))
    reply = page.locator('#stream .msg.dream')
    await expect(reply.locator('.cursor')).to_have_count(1)
    await expect(reply.locator('.msg-foot')).to_have_count(0)               # a reply still streaming has no row
    srv.bus.publish(Event('text_delta', SOURCE[60:]))
    srv.bus.publish(Event('result', {}))
    srv.bus.publish(Event('turn_end', {}))
    await expect(reply.locator('.msg-foot').get_by_role('button', name='Copy', exact=True)).to_be_visible()   # finished: a row with Copy
    copy = reply.locator('.msg-foot .msg-copy')
    place = await reply.evaluate("""m => { const f = m.querySelector('.msg-foot').getBoundingClientRect(), b = m.querySelector('.body').getBoundingClientRect();
        return {under: f.y >= b.bottom - 1, left: Math.abs(f.x - b.x), inside: f.right <= b.right + 1}; }""")
    assert place == {'under': True, 'left': place['left'], 'inside': True} and place['left'] <= 1, place   # under the text, in the column
    await copy.click()
    await expect(copy).to_have_attribute('aria-label', 'Copied')                                # it confirms
    assert await page.evaluate(CLIPBOARD) == SOURCE                          # the markdown source, exactly
    await expect(copy).to_have_attribute('aria-label', 'Copy', timeout=4000)
    # The code block's own Copy still copies the code alone.
    await reply.locator('.codeblock .codecopy').click()
    assert await page.evaluate(CLIPBOARD) == 'print("hi")'
    # A refused clipboard is said as one.
    await page.evaluate("""() => { navigator.clipboard.writeText = () => Promise.reject(new Error('denied')); document.execCommand = () => false; }""")
    await copy.click()
    await expect(copy).to_have_attribute('aria-label', 'Copy failed')


async def test_each_block_of_a_turn_copies_its_own_text(chat):
    srv, page, _, _ = chat
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    await srv._accept_prompt('Read the notes and summarise them.')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', 'I will read the **notes** first.'))
    srv.bus.publish(Event('tool_use', {'id': 'r1', 'name': 'read_file', 'input': {'path': 'NOTES.md'}}))
    srv.bus.publish(Event('tool_result', {'id': 'r1', 'content': 'notes', 'is_error': False}))
    srv.bus.publish(Event('text_delta', 'The notes say:\n\n- ship on Friday'))
    srv.bus.publish(Event('result', {}))
    srv.bus.publish(Event('turn_end', {}))
    blocks = page.locator('#stream .msg.dream')
    await expect(blocks).to_have_count(2)
    await expect(blocks.locator('.msg-copy')).to_have_count(2)
    await blocks.nth(1).locator('.msg-copy').click()
    assert await page.evaluate(CLIPBOARD) == 'The notes say:\n\n- ship on Friday'
    await blocks.nth(0).locator('.body').hover()
    await blocks.nth(0).locator('.msg-copy').click()
    assert await page.evaluate(CLIPBOARD) == 'I will read the **notes** first.'


async def test_copy_user_message(chat):
    srv, page, _, _ = chat
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    said = 'What is the port?\nAnd is <b>this</b> text & nothing else?'
    await turn(srv, page, said, 'It is 8080.')
    you = page.locator('#stream .msg.you')
    await expect(you.get_by_role('button', name='Copy', exact=True)).to_have_count(1)
    copy = you.locator('.msg-copy')
    await away(page)
    await expect(copy).to_have_css('opacity', '0')                           # quiet until the pointer or the keyboard is there
    await you.locator('.body').hover()
    await expect(copy).to_have_css('opacity', '1')
    at = await you.evaluate("""m => { const c = m.querySelector('.msg-copy').getBoundingClientRect(), b = m.querySelector('.body').getBoundingClientRect(), r = m.getBoundingClientRect();
        return {under: c.y >= b.bottom - 1, gap: c.y - b.bottom, left: Math.abs(c.x - b.x), room: r.bottom - b.bottom}; }""")
    assert at['under'] and at['gap'] <= 4 and at['left'] <= 1 and at['room'] <= 1, at   # under the text's left edge (DREAM-240: no bubble), taking no room
    await copy.click()
    await expect(copy).to_have_attribute('aria-label', 'Copied')
    assert await page.evaluate(CLIPBOARD) == said                            # the message as typed
    assert await you.evaluate('m => m.textContent') == 'You' + said          # the control adds no word to the message's own text
    label = await copy.evaluate("b => [getComputedStyle(b, '::after').content, b.getBoundingClientRect().width > 30]")
    assert label == ['"Copied"', True], label                                # its word is drawn from its accessible name
    await away(page)
    await expect(copy).to_have_css('opacity', '0')                           # a click does not pin it open
    await expect(copy).to_have_attribute('aria-label', 'Copy', timeout=4000)
    await page.locator('#feed-detail').focus()                               # from the keyboard: Tab reaches it, and it shows
    await page.keyboard.press('Tab')
    await expect(copy).to_be_focused()
    await expect(copy).to_have_css('opacity', '1')
    await page.evaluate("navigator.clipboard.writeText('')")
    await page.keyboard.press('Enter')
    await expect(copy).to_have_attribute('aria-label', 'Copied')
    assert await page.evaluate(CLIPBOARD) == said


async def test_last_reply_row_is_visible_older_rows_on_hover_and_focus(chat):
    srv, page, _, _ = chat
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    older = await turn(srv, page, 'First question', 'The first answer.', stats={'gen_tps': 20.0, 'duration_s': 2.0})
    last = await turn(srv, page, 'Second question', 'The second answer.', stats={'gen_tps': 21.0, 'duration_s': 3.0})
    await away(page)
    for control in ('.msg-copy', '.msg-meta'):
        await expect(last.locator(control)).to_have_css('opacity', '1')      # the last reply's row is always visible
        await expect(older.locator(control)).to_have_css('opacity', '0')     # an earlier row waits
    # An earlier row takes no room: the next message starts one gap under the reply's text.
    gap = await older.evaluate("m => m.nextElementSibling.getBoundingClientRect().y - m.querySelector('.body').getBoundingClientRect().bottom")
    assert 20 <= gap <= 28, gap
    await older.locator('.body').hover()
    for control in ('.msg-copy', '.msg-meta'):
        await expect(older.locator(control)).to_have_css('opacity', '1')     # on hover
    await older.locator('.msg-copy').hover()                                 # the pointer can reach it
    await expect(older.locator('.msg-copy')).to_have_css('opacity', '1')
    await older.locator('.msg-copy').click()
    assert await page.evaluate(CLIPBOARD) == 'The first answer.'
    await away(page)
    await page.locator('#feed-detail').focus()
    await expect(older.locator('.msg-copy')).to_have_css('opacity', '0')
    # From the keyboard: Tab walks through the conversation's rows in order, and a focused row shows.
    reached = []
    for _ in range(12):
        await page.keyboard.press('Tab')
        reached.append(await page.evaluate("document.activeElement.closest('.msg')?.querySelector('.body').textContent + ' / ' + document.activeElement.className"))
        if reached[-1] == 'The first answer. / msg-copy':
            break
    assert reached == ['First question / msg-copy', 'The first answer. / msg-copy'], reached
    await expect(older.locator('.msg-copy')).to_have_css('opacity', '1')     # on keyboard focus
    await expect(older.locator('.msg-meta')).to_have_css('opacity', '1')
    outline = await older.locator('.msg-copy').evaluate("e => getComputedStyle(e).outlineStyle + ' ' + parseFloat(getComputedStyle(e).outlineWidth)")
    assert outline.startswith('solid') and float(outline.split()[1]) >= 2, outline   # and the focus is visible
    await page.keyboard.press('Tab')
    await page.keyboard.press('Tab')
    assert await page.evaluate("document.activeElement.closest('.msg')?.querySelector('.body').textContent + ' / ' + document.activeElement.className") == 'The second answer. / msg-copy'
    # A touch width: every row is visible without a pointer.
    await page.locator('#input').focus()
    await page.set_viewport_size({'width': 390, 'height': 844})
    await away(page)
    await expect(older.locator('.msg-copy')).to_have_css('opacity', '1')
    await expect(page.locator('#stream .msg.you .msg-copy').first).to_have_css('opacity', '1')
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth && document.getElementById('main').scrollWidth <= document.getElementById('main').clientWidth")
    overlap = await page.evaluate("""() => [...document.querySelectorAll('#stream .msg-foot')].map(f => { const r = f.getBoundingClientRect(), n = f.closest('.msg').nextElementSibling?.getBoundingClientRect();
        return !n || r.bottom <= n.y + 1; })""")
    assert all(overlap), overlap                                             # no row lies over the next message


async def test_speed_shows_only_when_reported(chat):
    srv, page, _, _ = chat
    plain = await turn(srv, page, 'One', 'Nothing was reported for this one.')
    await expect(plain.locator('.msg-copy')).to_have_count(1)
    await expect(plain.locator('.msg-meta')).to_have_count(0)                # no number is invented
    fast = await turn(srv, page, 'Two', 'This one has numbers.', stats={'gen_tps': 26.94, 'duration_s': 14.21})
    meta = fast.locator('.msg-meta')
    await expect(meta).to_have_text('26.9 tok/s · 14.2 s')
    at = await fast.evaluate("""m => { const f = m.querySelector('.msg-foot').getBoundingClientRect(), t = m.querySelector('.msg-meta').getBoundingClientRect(), c = m.querySelector('.msg-copy').getBoundingClientRect();
        return {right: f.right - t.right, afterCopy: t.x >= c.right, sameLine: Math.abs((t.y + t.height / 2) - (c.y + c.height / 2))}; }""")
    assert at['right'] <= 1 and at['afterCopy'] and at['sameLine'] <= 2, at  # at the right of its row
    await expect(page.locator('#stream > .sys', has_text='tok/s')).to_have_count(0)   # no longer a system line
    speed = await turn(srv, page, 'Three', 'Only a speed.', stats={'gen_tps': 12.0})
    await expect(speed.locator('.msg-meta')).to_have_text('12.0 tok/s')     # a time that was not reported is not shown
    # The turn's timing is one click away: the numbers open the timing card under the reply.
    timed = await turn(srv, page, 'Four', 'Timed.', stats={'gen_tps': 30.0, 'duration_s': 12.4}, timing=TIMING)
    button = timed.get_by_role('button', name='30.0 tok/s · 12.4 s', exact=True)
    card = page.locator('#stream > .turn-timing')
    await expect(card).to_have_count(1)
    await expect(card).to_be_hidden()
    await expect(button).to_have_attribute('aria-expanded', 'false')
    await button.click()
    await expect(card).to_be_visible()
    await expect(button).to_have_attribute('aria-expanded', 'true')
    await expect(card).to_contain_text('First answer 3.4 s')
    await expect(card.get_by_text('Measured phases', exact=True)).to_be_visible()   # the detail, open
    assert await card.evaluate("c => c.previousElementSibling.querySelector('.body').textContent") == 'Timed.'   # under its reply
    await button.click()
    await expect(card).to_be_hidden()
    # No speed reported: the turn's measured time alone.
    await turn(srv, page, 'Five', 'Timing only.', timing=TIMING)
    await expect(page.locator('#stream .msg.dream').nth(4).locator('.msg-meta')).to_have_text('12.4 s')
    # A turn that did not complete keeps its timing card in view, as before.
    await turn(srv, page, 'Six', 'Cut short.', timing=dict(TIMING, outcome='error'))
    await expect(page.locator('#stream > .turn-timing').last).to_be_visible()
    await expect(page.locator('#stream > .turn-timing').last).to_contain_text('Failed')
    # A turn with no reply text at its end: the speed stays a system line, the card stays in view.
    await srv._accept_prompt('Seven')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('tool_use', {'id': 'w1', 'name': 'write_file', 'input': {'path': 'a.txt', 'content': 'x'}}))
    srv.bus.publish(Event('tool_result', {'id': 'w1', 'content': 'ok', 'is_error': False}))
    srv.bus.publish(Event('result', {'stats': {'gen_tps': 9.5, 'duration_s': 1.0}}))
    srv.bus.publish(Event('turn_timing', TIMING))
    srv.bus.publish(Event('turn_end', {}))
    await expect(page.locator('#stream > .sys', has_text='9.5 tok/s')).to_have_count(1)
    await expect(page.locator('#stream > .turn-timing').last).to_be_visible()


async def test_failed_turn_offers_try_again_and_resends_once(chat):
    srv, page, prompts, _ = chat
    await srv._accept_prompt('Summarise the notes')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('error', 'ConnectError: the model did not answer'))
    srv.bus.publish(Event('result', {'is_error': True}))
    retry = page.get_by_role('button', name='Try again', exact=True)
    await expect(page.locator('#stream > .sys.error').first).to_have_text('ConnectError: the model did not answer')   # the error, in the column
    await expect(retry).to_have_count(0)                                     # not before the turn has ended
    srv.bus.publish(Event('turn_end', {}))
    await expect(retry).to_have_count(1)
    await expect(retry).to_be_visible()
    assert await retry.evaluate("b => { const e = [...document.querySelectorAll('#stream > .sys.error')].pop(); return !!(e.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) && b.closest('#stream') !== null; }")
    await retry.dblclick()                                                   # a double click sends once
    await expect(page.locator('#stream .msg.you')).to_have_count(2)
    assert prompts == ['Summarise the notes', 'Summarise the notes']         # the same message, as a new turn
    await expect(retry).to_have_count(0)                                     # the offer is gone with the new turn
    # A partial reply before the failure keeps its Copy; the offer follows the error.
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', 'The notes say'))
    srv.bus.publish(Event('error', 'ReadTimeout'))
    srv.bus.publish(Event('turn_end', {}))
    await expect(page.locator('#stream .msg.dream .msg-copy')).to_have_count(1)
    await expect(retry).to_have_count(1)
    # A send that fails keeps the offer.
    await page.route('**/api/prompt', lambda r: r.fulfill(status=503, json={'error': 'Model is unavailable'}))
    await retry.click()
    await expect(page.locator('#studio-error')).to_contain_text('Model is unavailable')
    await expect(retry).to_be_enabled()
    await page.unroute('**/api/prompt')
    assert len(prompts) == 2
    # A reply cut at the output limit keeps its own Continue button and gets no Try again.
    await srv._accept_prompt('Write a long report')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', 'A long report that'))
    srv.bus.publish(Event('result', {'subtype': 'length', 'is_error': True}))
    srv.bus.publish(Event('turn_end', {}))
    await expect(page.get_by_role('button', name='Continue answer', exact=True)).to_have_count(1)
    await expect(page.locator('#stat')).to_have_text('Ready')
    await expect(retry).to_have_count(0)
    # A failure that was not the owner's message (a project that did not open) offers nothing to send again.
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('error', 'The project could not be opened'))
    srv.bus.publish(Event('turn_end', {}))
    await expect(page.locator('#stream > .sys.error').last).to_have_text('The project could not be opened')
    await expect(page.locator('#stat')).to_have_text('Ready')
    await expect(retry).to_have_count(0)
    # A message queued while a turn runs is not that turn's message.
    await srv._accept_prompt('First of two')
    srv.bus.publish(Event('turn_start', {}))
    await srv._accept_prompt('Second, queued behind it')
    srv.bus.publish(Event('error', 'ConnectError'))
    srv.bus.publish(Event('turn_end', {}))
    await retry.click()
    await expect(page.locator('#stream .msg.you')).to_have_count(6)
    assert prompts[-1] == 'First of two', prompts


async def test_stopped_turn_offers_try_again(chat):
    srv, page, prompts, _ = chat
    await srv._accept_prompt('Write a poem')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', 'Roses are'))
    await expect(page.locator('#stream .msg.dream .cursor')).to_have_count(1)
    srv.bus.publish(Event('turn_end', {'interrupted': True}))
    row = page.locator('#stream .msg.dream .msg-foot')
    await expect(row.locator('.msg-stopped')).to_have_text('Stopped')
    assert await row.evaluate("f => [...f.children].map(c => c.getAttribute('aria-label') || c.textContent)") == ['Stopped', 'Copy', 'Try again']
    await row.get_by_role('button', name='Try again', exact=True).click()
    await expect(page.locator('#stream .msg.you')).to_have_count(2)
    assert prompts == ['Write a poem', 'Write a poem']
    await expect(page.get_by_role('button', name='Try again', exact=True)).to_have_count(0)
    await expect(row.locator('.msg-stopped')).to_have_text('Stopped')       # the mark stays on the stopped reply
    # Stopped before any text: the column says so and offers the same.
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('turn_end', {'interrupted': True}))
    line = page.locator('#stream > .sys.turn-stopped')
    await expect(line).to_contain_text('Stopped')
    await line.get_by_role('button', name='Try again', exact=True).click()
    await expect(page.locator('#stream .msg.you')).to_have_count(3)
    assert prompts == ['Write a poem'] * 3


async def test_actions_survive_reload(chat):
    srv, page, prompts, _ = chat
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    await turn(srv, page, 'First question', SOURCE, stats={'gen_tps': 26.9, 'duration_s': 14.2}, timing=TIMING)
    await srv._accept_prompt('Second question')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', 'A partial **answer**'))
    await expect(page.locator('#stream .msg.dream')).to_have_count(2)
    srv.bus.publish(Event('turn_end', {'interrupted': True}))
    await expect(page.locator('#stream .msg-stopped')).to_have_count(1)
    await page.reload()
    await expect(page.locator('#stat')).to_have_text('Ready')
    replies, you = page.locator('#stream .msg.dream'), page.locator('#stream .msg.you')
    await expect(replies).to_have_count(2)
    await expect(replies.locator('.msg-copy')).to_have_count(2)             # the rows are back
    await expect(you.locator('.msg-copy')).to_have_count(2)
    await replies.nth(0).locator('.body').hover()
    await expect(replies.nth(0).locator('.msg-meta')).to_have_text('26.9 tok/s · 14.2 s')
    await replies.nth(0).locator('.msg-copy').click()
    assert await page.evaluate(CLIPBOARD) == SOURCE                          # and Copy still has the source
    await replies.nth(0).locator('.msg-meta').click()
    await expect(page.locator('#stream > .turn-timing')).to_be_visible()    # the timing detail too
    await you.nth(1).locator('.body').hover()
    await you.nth(1).locator('.msg-copy').click()
    assert await page.evaluate(CLIPBOARD) == 'Second question'
    row = replies.nth(1).locator('.msg-foot')
    assert await row.evaluate("f => [...f.children].map(c => c.getAttribute('aria-label') || c.textContent)") == ['Stopped', 'Copy', 'Try again']
    await row.locator('.msg-copy').click()
    assert await page.evaluate(CLIPBOARD) == 'A partial **answer**'
    await row.get_by_role('button', name='Try again', exact=True).click()
    await expect(you).to_have_count(3)
    assert prompts == ['First question', 'Second question', 'Second question']


async def test_only_copy_retry_and_the_numbers_are_drawn(chat):
    srv, page, _, _ = chat
    await turn(srv, page, 'A question', 'An answer.', stats={'gen_tps': 26.9, 'duration_s': 14.2}, timing=TIMING)
    await srv._accept_prompt('Another')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', 'Partial'))
    await expect(page.locator('#stream .msg.dream')).to_have_count(2)
    srv.bus.publish(Event('turn_end', {'interrupted': True}))
    await expect(page.locator('#stream .msg-stopped')).to_have_count(1)
    controls = await page.evaluate("[...document.querySelectorAll('#stream .msg-foot button, #stream .msg-foot a, #stream .msg-foot [role=button]')].map(b => b.getAttribute('aria-label') || b.textContent)")
    assert sorted(controls) == sorted(['Copy', 'Copy', '26.9 tok/s · 14.2 s', 'Copy', 'Copy', 'Try again']), controls   # no thumbs, read aloud, share, edit or regenerate
    # The row is flat and quiet: no gradient, glow or blur.
    look = await page.locator('#stream .msg.dream .msg-copy').last.evaluate("""b => { const s = getComputedStyle(b);
        return {image: s.backgroundImage, shadow: s.boxShadow, filter: s.backdropFilter, size: parseFloat(s.fontSize), height: b.getBoundingClientRect().height}; }""")
    assert look['image'] == 'none' and look['shadow'] == 'none' and look['filter'] == 'none' and look['size'] <= 13 and look['height'] >= 24, look


async def test_the_plain_browser_page_keeps_its_speed_line(chat):
    """The plain browser page keeps its layout (spec default 2): no action rows, the speed as a system line."""
    srv, page, _, _ = chat
    await page.goto(srv.url)
    await expect(page.locator('#stat')).to_have_text('live')
    await srv._accept_prompt('A question')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', 'An answer.'))
    srv.bus.publish(Event('result', {'stats': {'gen_tps': 26.9, 'duration_s': 14.2}}))
    srv.bus.publish(Event('turn_timing', TIMING))
    srv.bus.publish(Event('turn_end', {}))
    await expect(page.locator('#stream > .sys', has_text='26.9 tok/s')).to_have_count(1)
    await expect(page.locator('#stream > .turn-timing')).to_be_visible()
    await expect(page.locator('#stream .msg-foot')).to_have_count(0)


async def test_try_again_sends_the_files_this_window_attached(tmp_path, monkeypatch):
    monkeypatch.delenv('DREAM_DESKTOP_SESSION_FILE', raising=False)
    prompts = []
    srv = StudioServer(EventBus(), on_prompt=prompts.append, session={'workspace': str(tmp_path), 'model': 'fixture', 'session_id': 'files'})
    url = await srv.start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=['--disable-gpu'])
            page = await browser.new_page(viewport={'width': 1100, 'height': 800})
            page.set_default_timeout(3000)
            await page.goto(url.replace('/#', '/?companion=1#'))
            await expect(page.locator('#stat')).to_have_text('Ready')
            await page.locator('#file-input').set_input_files({'name': 'notes.txt', 'mimeType': 'text/plain', 'buffer': b'Codename: Foxglove'})
            await expect(page.locator('#attachments')).to_contain_text('Ready')
            await page.locator('#input').fill('What is the codename?')
            await page.keyboard.press('Enter')
            await expect(page.locator('#stream .msg.you')).to_have_count(1)
            assert len(prompts) == 1 and 'notes.txt' in prompts[0] and 'read_file' in prompts[0]
            srv.bus.publish(Event('turn_start', {}))
            srv.bus.publish(Event('error', 'ConnectError'))
            srv.bus.publish(Event('turn_end', {}))
            await page.get_by_role('button', name='Try again', exact=True).click()
            await expect(page.locator('#stream .msg.you')).to_have_count(2)
            assert prompts[1] == prompts[0]                                  # the same message, with its file
            await expect(page.locator('#stream .msg.you .body').last).to_have_text('What is the codename?\n\nAttached files: notes.txt')
            # After a reload the page no longer holds the file: no offer it could not keep.
            srv.bus.publish(Event('turn_start', {}))
            srv.bus.publish(Event('error', 'ConnectError'))
            srv.bus.publish(Event('turn_end', {}))
            await expect(page.get_by_role('button', name='Try again', exact=True)).to_have_count(1)
            await page.reload()
            await expect(page.locator('#stat')).to_have_text('Ready')
            await expect(page.locator('#stream .msg.you')).to_have_count(2)
            await expect(page.locator('#stream > .sys.error')).to_have_count(2)
            await expect(page.get_by_role('button', name='Try again', exact=True)).to_have_count(0)
            await browser.close()
    finally:
        await srv.stop()
