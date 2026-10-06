"""Dream Chat, batch B2 (DREAM-230): streaming polish. A reply paints at most once a frame and keeps the elements
of its finished blocks, the cursor sits at the end of the streaming text, the view follows the stream only while
the reader is at the end, a round "Jump to latest" button returns there, and a stopped reply keeps its text and
says so. Real Chromium against an isolated StudioServer with synthetic events; no model, no engine."""
import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_desktop_chat import chat  # noqa: F401

LONG = '\n\n'.join(f'Paragraph {n} of a long reply, with enough words to fill a line of the column.' for n in range(80))
GAP = "() => { const m = document.getElementById('main'); return m.scrollHeight - m.scrollTop - m.clientHeight; }"
TOP = "() => document.getElementById('main').scrollTop"
FRAMES = "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(done))))"
CODE = 'for item in queue:\n    handle(item)'


async def at_latest(page):
    await page.wait_for_function("""() => { const m = document.getElementById('main');
        return m.scrollHeight > m.clientHeight && m.scrollHeight - m.scrollTop - m.clientHeight <= 2; }""")


async def streaming(srv, page, text=LONG):
    """A turn that is running: the owner's message, then the reply so far."""
    await srv._accept_prompt('Tell me everything.')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', text))
    await expect(page.locator('#stream .msg.dream')).to_have_count(1)
    await expect(page.locator('#stream .msg.dream .cursor')).to_have_count(1)
    return page.locator('#stream .msg.dream .body')


async def wheel_up(page, pixels):
    before = await page.evaluate(TOP)
    await page.locator('#main').hover(position={'x': 5, 'y': 60})
    await page.mouse.wheel(0, -pixels)
    await page.wait_for_function('before => document.getElementById("main").scrollTop <= before - 20', arg=before)
    await page.evaluate(FRAMES)
    return await page.evaluate(TOP)


async def test_jump_to_latest_shows_when_scrolled_up_and_resumes_following(chat):
    srv, page, _, _ = chat
    await streaming(srv, page)
    await at_latest(page)
    jump = page.get_by_role('button', name='Jump to latest', exact=True)
    await expect(jump).to_be_hidden()                                        # at the end: no button
    reading = await wheel_up(page, 600)
    await expect(jump).to_be_visible()                                       # scrolled up by more than 120 px
    box = await jump.evaluate("""e => { const r = e.getBoundingClientRect(), c = document.querySelector('footer .composer').getBoundingClientRect(),
        m = document.getElementById('main').getBoundingClientRect(), s = getComputedStyle(e);
        return {w: r.width, h: r.height, mid: r.x + r.width / 2, bottom: r.bottom, top: r.y, radius: parseFloat(s.borderTopLeftRadius), font: parseFloat(s.fontSize),
                composerMid: c.x + c.width / 2, composerTop: c.y, paneTop: m.y, icon: getComputedStyle(e, '::before').content}; }""")
    assert abs(box['w'] - box['h']) <= 0.5 and 32 <= box['w'] <= 40 and box['radius'] >= box['w'] / 2, box   # round
    assert abs(box['mid'] - box['composerMid']) <= 1.5, box                  # centered on the composer
    assert box['bottom'] <= box['composerTop'] and box['top'] > box['paneTop'], box   # above it, over the conversation
    assert box['font'] == 0 and box['icon'] != 'none', box                   # an icon; its words are the accessible name
    # New text never moves a reader who scrolled up, and the button stays.
    srv.bus.publish(Event('text_delta', '\n\n' + LONG))
    await expect(page.locator('#stream .msg.dream .body')).to_contain_text('Paragraph 79 of a long reply, with enough words to fill a line of the column.Paragraph 0')
    await page.evaluate(FRAMES)
    assert abs(await page.evaluate(TOP) - reading) <= 2
    await expect(jump).to_be_visible()
    # Pressing it returns to the end, and the view follows the stream again.
    await jump.click()
    await at_latest(page)
    await expect(jump).to_be_hidden()
    srv.bus.publish(Event('text_delta', '\n\nThe newest paragraph.' * 30))
    await expect(page.locator('#stream .msg.dream .body')).to_contain_text('The newest paragraph.')
    await at_latest(page)
    await expect(jump).to_be_hidden()
    # From the keyboard too.
    await wheel_up(page, 600)
    await expect(jump).to_be_visible()
    await jump.focus()
    await page.keyboard.press('Enter')
    await at_latest(page)
    await expect(jump).to_be_hidden()
    await expect(page.locator('#input')).to_be_focused()                     # the focus is not left on a hidden button


async def test_button_is_absent_at_the_bottom_and_in_short_chats(chat):
    srv, page, _, _ = chat
    jump = page.locator('#to-latest')
    await expect(jump).to_be_hidden()                                        # an empty chat
    await srv._accept_prompt('Hello')
    srv.bus.publish(Event('text_delta', 'A short answer.'))
    srv.bus.publish(Event('result', {}))
    await expect(page.locator('#stream .msg.dream')).to_have_count(1)
    await page.evaluate(FRAMES)
    assert await page.evaluate("() => { const m = document.getElementById('main'); return m.scrollHeight <= m.clientHeight; }")
    await expect(jump).to_be_hidden()                                        # a chat shorter than the pane
    srv.bus.publish(Event('text_delta', LONG))
    srv.bus.publish(Event('result', {}))
    await at_latest(page)
    await expect(jump).to_be_hidden()                                        # a long chat, at the end
    await wheel_up(page, 60)
    assert 20 <= await page.evaluate(GAP) <= 120
    await expect(jump).to_be_hidden()                                        # within 120 px of the end
    await wheel_up(page, 400)
    await expect(jump).to_be_visible()
    await page.locator('#main').evaluate("e => e.scrollTo({top: e.scrollHeight, behavior: 'instant'})")
    await expect(jump).to_be_hidden()                                        # the reader came back by hand


async def test_a_small_scroll_up_stops_following_at_once(chat):
    srv, page, _, _ = chat
    await streaming(srv, page)
    await at_latest(page)
    reading = await wheel_up(page, 60)                                       # less than the button's 120 px
    assert await page.evaluate(GAP) >= 20
    srv.bus.publish(Event('text_delta', '\n\nMore text arrives.' * 40))
    await expect(page.locator('#stream .msg.dream .body')).to_contain_text('More text arrives.')
    await page.evaluate(FRAMES)
    assert abs(await page.evaluate(TOP) - reading) <= 2                      # the reader was not pulled back
    await expect(page.locator('#to-latest')).to_be_visible()                 # and the stream ran on past 120 px
    # Returning to the end by hand resumes following.
    await page.locator('#main').evaluate("e => e.scrollTo({top: e.scrollHeight, behavior: 'instant'})")
    await page.evaluate(FRAMES)
    srv.bus.publish(Event('text_delta', '\n\nAnd the last words.' * 40))
    await expect(page.locator('#stream .msg.dream .body')).to_contain_text('And the last words.')
    await at_latest(page)


async def test_a_burst_paints_once_and_the_text_is_exact(chat):
    srv, page, _, _ = chat
    # 500 deltas inside one frame: one render, and the text is their concatenation.
    burst = await page.evaluate("""() => new Promise(done => {
        const render = window.md; let renders = 0;
        window.md = source => { renders++; return render(source); };
        const pieces = Array.from({length: 500}, (_, n) => 'w' + n + ' ');
        for (const piece of pieces) handle({kind: 'text_delta', data: piece});
        const during = renders;
        requestAnimationFrame(() => requestAnimationFrame(() => { window.md = render;
            done({during, renders, shown: document.querySelector('#stream .msg.dream .body').textContent.trim(), sent: pieces.join('').trim(),
                  cursors: document.querySelectorAll('#stream .cursor').length}); })); })""")
    assert burst['during'] == 0 and burst['renders'] == 1, burst            # nothing painted inside the burst; one paint after it
    assert burst['shown'] == burst['sent'] and burst['cursors'] == 1, burst
    # The same from the server: never more than one render a frame, and no piece lost.
    await page.evaluate("""() => { const render = window.md; window.__renders = 0; window.__frames = 0;
        window.md = source => { window.__renders++; return render(source); };
        const tick = () => { window.__frames++; requestAnimationFrame(tick); }; requestAnimationFrame(tick); }""")
    more = [f'x{n} ' for n in range(500)]
    for piece in more:
        srv.bus.publish(Event('text_delta', piece))
    srv.bus.publish(Event('result', {}))
    body = page.locator('#stream .msg.dream .body')
    await expect(body.locator('.cursor')).to_have_count(0)
    counts = await page.evaluate('[window.__renders, window.__frames]')
    assert counts[0] <= counts[1] + 2, counts                                # one a frame at most (and the final one)
    assert (await body.evaluate('e => e.textContent')).strip() == (burst['sent'] + ' ' + ''.join(more)).strip()


async def test_cursor_sits_at_the_end_of_the_streaming_text(chat):
    srv, page, _, _ = chat
    body = await streaming(srv, page, 'The next step is to run the **checks** and then')
    place = """b => { const c = b.querySelector('.cursor'), r = c.getBoundingClientRect(), host = c.parentElement, h = host.getBoundingClientRect();
        const range = document.createRange(); range.selectNodeContents(host); const lines = [...range.getClientRects()];
        const last = lines.filter(l => l.width > 1).pop() || h;
        return {host: host.tagName, x: r.x, y: r.y, bottom: r.bottom, hostX: h.x, hostBottom: h.bottom, lastTop: last.top, lastBottom: last.bottom, lastRight: last.right,
                position: getComputedStyle(c).display}; }"""
    at = await body.evaluate(place)
    assert at['host'] == 'P', at                                             # inside the paragraph, not after it
    assert at['x'] > at['hostX'] + 100 and at['y'] >= at['lastTop'] - 2 and at['bottom'] <= at['hostBottom'] + 2, at   # on the text's last line, after its last word
    await expect(body.locator('> .cursor')).to_have_count(0)
    # In a list it is in the last item; in a code block still open, at the end of the code.
    srv.bus.publish(Event('text_delta', ' report:\n\n1. first\n2. second'))
    await expect(body.locator('ol > li').last.locator('.cursor')).to_have_count(1)
    srv.bus.publish(Event('text_delta', '\n\n```python\nprint(1)\nprint(2'))
    await expect(body.locator('.codeblock pre code .cursor')).to_have_count(1)
    await expect(body.locator('.cursor')).to_have_count(1)
    assert await body.locator('.codeblock pre code').evaluate('e => e.textContent') == 'print(1)\nprint(2'
    # A table: in its last cell. After a closed code block or a rule nothing is open: the cursor waits under it.
    srv.bus.publish(Event('text_delta', ')\n```\n\n| a | b |\n|---|---|\n| 1 | 2'))
    await expect(body.locator('table tbody td').last.locator('.cursor')).to_have_count(1)
    srv.bus.publish(Event('text_delta', ' |\n\n---\n'))
    await expect(body.locator('> .cursor')).to_have_count(1)
    srv.bus.publish(Event('result', {}))
    await expect(body.locator('.cursor')).to_have_count(0)                   # the finished reply has none
    await expect(body.locator('p').first).to_have_text('The next step is to run the checks and then report:')


async def test_a_copy_click_during_a_stream_is_never_lost(chat):
    srv, page, _, _ = chat
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    body = await streaming(srv, page, f'First the loop:\n\n```python\n{CODE}\n```\n\nThen the explanation, which is still')
    copy = body.locator('.codeblock').nth(0).locator('.codecopy')
    await copy.evaluate('e => { e.__mine = true; }')                         # this very element
    # The button is pressed, the reply goes on (several frames), and the button is released.
    await copy.hover()
    await page.mouse.down()
    for n in range(6):
        srv.bus.publish(Event('text_delta', f' streaming word{n}'))
        await page.evaluate(FRAMES)
    await expect(body).to_contain_text('streaming word5')
    await page.mouse.up()
    await expect(copy).to_have_text('Copied')                                # the click arrived
    assert await page.evaluate('navigator.clipboard.readText()') == CODE
    assert await copy.evaluate('e => e.__mine === true')                     # the finished block kept its elements
    # The confirmation outlives the frames that follow.
    for n in range(4):
        srv.bus.publish(Event('text_delta', f' later word{n}'))
        await page.evaluate(FRAMES)
    await expect(body).to_contain_text('later word3')
    await expect(copy).to_have_text('Copied')
    await expect(copy).to_have_text('Copy', timeout=4000)
    # A block still being written: the same, with the code as it stood at the click.
    srv.bus.publish(Event('text_delta', '\n\n```js\nlet a = 1;\n'))
    open_copy = body.locator('.codeblock').nth(1).locator('.codecopy')
    await expect(open_copy).to_have_count(1)
    await open_copy.evaluate('e => { e.__mine = true; }')
    await open_copy.hover()
    await page.mouse.down()
    srv.bus.publish(Event('text_delta', 'let b = 2;\n'))
    await expect(body.locator('.codeblock').nth(1).locator('pre code')).to_contain_text('let b = 2;')
    await page.evaluate(FRAMES)
    await page.mouse.up()
    await expect(open_copy).to_have_text('Copied')
    assert await page.evaluate('navigator.clipboard.readText()') == 'let a = 1;\nlet b = 2;'
    assert await open_copy.evaluate('e => e.__mine === true')
    srv.bus.publish(Event('text_delta', 'let c = 3;\n```\n\nDone.'))
    srv.bus.publish(Event('result', {}))
    await expect(body.locator('.cursor')).to_have_count(0)
    assert await copy.evaluate('e => e.__mine === true') and await open_copy.evaluate('e => e.__mine === true')   # and the finished reply keeps them
    assert await body.locator('.codeblock').nth(1).locator('pre code').evaluate('e => e.textContent') == 'let a = 1;\nlet b = 2;\nlet c = 3;'
    await expect(body.locator('p').last).to_have_text('Done.')


async def test_stopped_reply_keeps_its_text_and_says_stopped(chat):
    srv, page, _, controls = chat
    body = await streaming(srv, page, 'A partial answer that was still being')
    await page.get_by_role('button', name='Stop', exact=True).click()
    assert {'action': 'interrupt'} in controls
    srv.bus.publish(Event('turn_end', {'interrupted': True}))               # what Dream publishes when the turn is interrupted
    await expect(page.locator('#stat')).to_have_text('Ready')
    await expect(body).to_have_text('A partial answer that was still being')   # the partial reply stays
    await expect(body.locator('.cursor')).to_have_count(0)
    mark = page.locator('#stream .msg.dream .msg-stopped')
    await expect(mark).to_have_count(1)
    await expect(mark).to_have_text('Stopped')
    look = await mark.evaluate("""e => { const r = e.getBoundingClientRect(), b = e.closest('.msg').querySelector('.body').getBoundingClientRect(), s = getComputedStyle(e);
        return {below: r.y >= b.bottom - 1, left: Math.abs(r.x - b.x), size: parseFloat(s.fontSize), animation: s.animationName}; }""")
    assert look['below'] and look['left'] <= 2 and look['size'] <= 13 and look['animation'] == 'none', look   # a quiet mark under the text
    # It survives a reload.
    await page.reload()
    await expect(page.locator('#stat')).to_have_text('Ready')
    await expect(page.locator('#stream .msg.dream .body')).to_have_text('A partial answer that was still being')
    await expect(page.locator('#stream .msg.dream .msg-stopped')).to_have_text('Stopped')
    # A turn that ran to its end has no mark; one stopped before any text says so in the column.
    await srv._accept_prompt('Again')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('text_delta', 'A whole answer.'))
    srv.bus.publish(Event('result', {}))
    srv.bus.publish(Event('turn_end', {'interrupted': False}))
    await expect(page.locator('#stream .msg.dream')).to_have_count(2)
    await expect(page.locator('#stat')).to_have_text('Ready')
    await expect(page.locator('#stream .msg-stopped')).to_have_count(1)
    await srv._accept_prompt('And once more')
    srv.bus.publish(Event('turn_start', {}))
    srv.bus.publish(Event('turn_end', {'interrupted': True}))
    await expect(page.locator('#stream > .sys.turn-stopped .msg-stopped')).to_have_text('Stopped')
    await expect(page.locator('#stream .msg.dream')).to_have_count(2)


async def test_sending_returns_to_the_latest(chat):
    srv, page, prompts, _ = chat
    await srv._accept_prompt('Tell me everything.')
    srv.bus.publish(Event('text_delta', LONG))
    srv.bus.publish(Event('result', {}))
    await at_latest(page)
    await wheel_up(page, 900)
    await expect(page.locator('#to-latest')).to_be_visible()
    await page.locator('#input').fill('A follow-up from far up the page')
    await page.keyboard.press('Enter')
    await expect(page.locator('#stream .msg.you')).to_have_count(2)
    assert prompts[-1] == 'A follow-up from far up the page'
    await at_latest(page)                                                    # the new message is in view
    await expect(page.locator('#to-latest')).to_be_hidden()
    srv.bus.publish(Event('text_delta', 'The reply to it. ' * 200))
    await expect(page.locator('#stream .msg.dream')).to_have_count(2)
    await at_latest(page)                                                    # and the view follows its reply


@pytest.mark.parametrize('width', [1100, 390])
async def test_status_line_sits_in_the_column_above_the_composer(chat, width):
    srv, page, _, _ = chat
    await page.set_viewport_size({'width': width, 'height': 800})
    await srv._accept_prompt('Think about it')
    srv.bus.publish(Event('turn_start', {}))
    status = page.locator('#chat-progress')
    await expect(status).to_be_visible()
    await expect(status).to_have_text('Waiting for first text…')
    at = await status.evaluate("""e => { const r = e.getBoundingClientRect(), c = document.querySelector('footer .composer').getBoundingClientRect(), s = getComputedStyle(e);
        return {x: r.x, right: r.right, bottom: r.bottom, cx: c.x, cright: c.right, ctop: c.y, animation: s.animationName, role: e.getAttribute('role')}; }""")
    assert at['x'] >= at['cx'] - 0.5 and at['right'] <= at['cright'] + 0.5, at   # in the column
    assert 0 <= at['ctop'] - at['bottom'] <= 12, at                          # directly above the composer
    assert at['animation'] == 'none' and at['role'] == 'status', at          # no perpetual animation
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    srv.bus.publish(Event('text_delta', 'The first words'))
    await expect(status).to_have_text('Writing reply')                      # it no longer says it waits for the text
    srv.bus.publish(Event('turn_end', {}))
    await expect(status).to_be_hidden()


async def test_the_plain_browser_page_follows_the_same_way_without_the_button(chat):
    """The plain browser page keeps its layout (spec default 2): the same streaming and following, no new control."""
    srv, page, _, _ = chat
    await page.goto(srv.url)
    await expect(page.locator('#stat')).to_have_text('live')
    body = await streaming(srv, page)
    await expect(body.locator('p').last.locator('.cursor')).to_have_count(1)   # the cursor at the end of the text
    await at_latest(page)
    reading = await wheel_up(page, 600)
    srv.bus.publish(Event('text_delta', '\n\nMore text arrives.' * 40))
    await expect(body).to_contain_text('More text arrives.')
    await page.evaluate(FRAMES)
    assert abs(await page.evaluate(TOP) - reading) <= 2                      # the reader stays where they are
    await expect(page.locator('#to-latest')).to_be_hidden()                  # and no button is drawn here
    srv.bus.publish(Event('turn_end', {'interrupted': True}))
    await expect(page.locator('#stream .msg.dream .msg-stopped')).to_have_text('Stopped')


async def test_composer_is_one_line_when_the_page_loads_narrow(chat):
    """Found while taking this batch's 390 px screenshots (a batch B1 left-over): the draft restore sizes the prompt
    box before the page's last stylesheets apply, and a page LOADED at a phone width kept a two-line empty box."""
    srv, page, _, _ = chat
    await page.set_viewport_size({'width': 390, 'height': 844})
    await page.reload()
    await expect(page.locator('#stat')).to_have_text('Ready')
    box = await page.evaluate("""() => { const r = id => document.getElementById(id).getBoundingClientRect();
        return {input: r('input').height, composer: document.querySelector('footer .composer').getBoundingClientRect().height,
                attachY: r('attach').y, inputY: r('input').y, sendY: r('send').y}; }""")
    assert box['input'] <= 37 and box['composer'] <= 56, box                 # one line
    assert abs(box['attachY'] - box['inputY']) <= 1 and abs(box['sendY'] - box['inputY']) <= 1, box   # "+", the text and Send in it
