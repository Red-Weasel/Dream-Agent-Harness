"""Dream Chat, batch B1 (DREAM-228): the composer is one rounded box that grows and then scrolls; Enter sends and
Shift+Enter breaks the line; its round primary button is Send when idle and Stop while a turn runs, with Queue and
Steer kept beside Stop (their ids and words are pinned by test_live_steering_ui.py and test_plan_strip.py); the row
under it wraps instead of scrolling. Real Chromium, synthetic events, no model."""
import asyncio

import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_desktop_chat import chat  # noqa: F401

BOX = 'e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom}; }'
ROUND = "e => { const c = getComputedStyle(e), r = e.getBoundingClientRect(); return {w: r.width, h: r.height, radius: parseFloat(c.borderTopLeftRadius), image: c.backgroundImage, color: c.backgroundColor, font: parseFloat(c.fontSize)}; }"
FLAT = "e => { const c = getComputedStyle(e); return [c.backgroundImage, c.boxShadow, c.backdropFilter]; }"
PLAN = {'title': 'Page', 'updated_at': 0, 'phases': [
    {'name': 'Sketch', 'status': 'done', 'summary': 'done', 'steps': [{'name': 'layout', 'status': 'done'}]},
    {'name': 'Build', 'status': 'in_progress', 'summary': '', 'steps': [{'name': 'markup', 'status': 'pending'}]}]}


async def box(page, selector):
    return await page.locator(selector).evaluate(BOX)


async def test_composer_is_one_rounded_box_with_attach_inside_and_send_at_its_right(chat):
    _, page, _, _ = chat
    composer, attach, prompt, send = [await box(page, s) for s in ('footer .composer', '#attach', '#input', '#send')]
    assert await page.locator('footer .composer').evaluate('e => parseFloat(getComputedStyle(e).borderTopLeftRadius)') == 10   # the Nested view's box (DREAM-240)
    for inner in (attach, prompt, send):                                     # all three are inside the one box
        assert composer['x'] <= inner['x'] and inner['r'] <= composer['r'] and composer['y'] <= inner['y'] and inner['b'] <= composer['b'], (inner, composer)
    assert attach['r'] <= prompt['x'] and prompt['r'] <= send['x'], (attach, prompt, send)   # "+" at the left, the button at the right
    await expect(page.get_by_role('button', name='Attach files', exact=True)).to_be_visible()
    assert composer['h'] <= 72, composer                                     # one line of text: a compact box


async def test_box_grows_then_scrolls(chat):
    _, page, _, _ = chat
    prompt = page.locator('#input')
    one = await box(page, '#input')
    composer_one = await box(page, 'footer .composer')
    assert one['h'] <= 40, one                                               # one line
    await prompt.fill('first line\nsecond line\nthird line')
    three = await box(page, '#input')
    assert three['h'] >= one['h'] + 30, (one, three)                         # it grew with the text
    assert await prompt.evaluate('e => e.scrollHeight <= e.clientHeight + 1')   # and shows all of it
    await prompt.fill('\n'.join(f'line {n}' for n in range(60)))
    many = await box(page, '#input')
    assert 170 <= many['h'] <= 210, many                                     # capped at about 200 px
    assert await prompt.evaluate("e => e.scrollHeight > e.clientHeight + 100 && ['auto', 'scroll'].includes(getComputedStyle(e).overflowY)")   # then it scrolls
    composer = await box(page, 'footer .composer')
    assert composer['b'] <= 800 and composer['h'] > composer_one['h'] + 100, composer   # the box grew upward, still on screen
    send = await box(page, '#send')
    assert send['b'] <= composer['b'] and send['y'] >= composer['y'], (send, composer)
    await prompt.fill('')
    assert abs((await box(page, '#input'))['h'] - one['h']) <= 1             # back to one line


async def test_enter_sends_shift_enter_breaks_ime_enter_waits(chat):
    _, page, prompts, _ = chat
    prompt = page.locator('#input')
    await prompt.click()
    await page.keyboard.type('line one')
    await page.keyboard.press('Shift+Enter')
    await page.keyboard.type('line two')
    await expect(prompt).to_have_value('line one\nline two')                 # Shift+Enter made a new line
    assert prompts == []
    # An Enter that belongs to an input method (choosing a candidate) never sends.
    await prompt.evaluate("e => e.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true, cancelable: true}))")
    await expect(prompt).to_have_value('line one\nline two')
    assert prompts == []
    await page.keyboard.press('Enter')
    await expect(prompt).to_have_value('')
    assert prompts == ['line one\nline two']                                 # Enter sent it, once


async def test_round_button_is_send_idle_and_stop_while_working(chat):
    srv, page, _, controls = chat
    send = page.get_by_role('button', name='Send', exact=True)
    await expect(send).to_be_visible()
    await expect(send).to_have_attribute('id', 'send')
    await expect(page.get_by_role('button', name='Stop', exact=True)).to_have_count(0)
    assert await send.evaluate('e => parseFloat(getComputedStyle(e).opacity)') < 0.6     # nothing to send yet: dimmed
    await page.locator('#input').fill('a draft')
    assert await send.evaluate('e => getComputedStyle(e).opacity') == '1'
    idle = await send.evaluate(ROUND)
    # DREAM-240: the Nested view's Send -- it says Send, 32 px tall, the box's inner corner (2 px), no icon.
    assert idle['h'] == 32 and idle['w'] >= idle['h'] + 20 and idle['radius'] == 2 and idle['font'] >= 12, idle
    await expect(send).to_have_text('Send')
    assert await send.evaluate("e => getComputedStyle(e, '::before').content") == 'none'
    place = await box(page, '#send')
    srv.bus.publish(Event('turn_start', {}))
    stop = page.get_by_role('button', name='Stop', exact=True)
    await expect(stop).to_be_visible()
    await expect(page.get_by_role('button', name='Send', exact=True)).to_have_count(0)
    busy = await stop.evaluate(ROUND)
    # The owner (2026-10-03): [feedback] -- the round square alone was missed. At desktop widths the
    # primary button says Stop beside its square, the same height, corner and colour, where Send was.
    assert abs(busy['h'] - idle['h']) <= 0.5 and busy['w'] >= busy['h'] + 30 and busy['radius'] == idle['radius'], busy
    assert busy['font'] >= 12 and busy['color'] == idle['color'], (busy, idle)
    await expect(stop).to_have_text('Stop')
    assert await stop.evaluate("e => getComputedStyle(e, '::before').content") != 'none'   # the square stays beside the word
    now = await box(page, '#stop')
    assert abs(now['r'] - place['r']) <= 1 and abs(now['b'] - place['b']) <= 1, (now, place)   # where Send was
    await page.set_viewport_size({'width': 480, 'height': 800})              # under 600 px: the square icon alone, so the three fit
    narrow = await stop.evaluate(ROUND)
    assert abs(narrow['w'] - narrow['h']) <= 0.5 and narrow['font'] == 0 and narrow['w'] == 32, narrow
    await page.set_viewport_size({'width': 1100, 'height': 800})
    await stop.click()
    assert {'action': 'interrupt'} in controls                               # pressing it stops the turn
    srv.bus.publish(Event('turn_end', {'interrupted': True}))
    await expect(page.get_by_role('button', name='Send', exact=True)).to_be_visible()
    await expect(page.get_by_role('button', name='Stop', exact=True)).to_have_count(0)
    again = await page.locator('#send').evaluate(ROUND)
    assert abs(again['w'] - idle['w']) <= 0.5 and again['font'] == idle['font'], again


async def test_queue_and_steer_stay_reachable_while_working(chat):
    srv, page, prompts, _ = chat
    await expect(page.locator('#steer')).to_be_hidden()
    srv.bus.publish(Event('turn_start', {}))
    queue, steer, stop = page.locator('#send'), page.locator('#steer'), page.locator('#stop')
    await expect(queue).to_have_text('Queue')
    await expect(queue).to_be_visible()
    await expect(steer).to_have_text('Steer')
    await expect(steer).to_be_visible()
    await expect(page.get_by_role('button', name='Queue', exact=True)).to_be_visible()   # the words are readable, not icons
    await expect(page.get_by_role('button', name='Steer', exact=True)).to_be_visible()
    q, s, x, composer = [await box(page, sel) for sel in ('#send', '#steer', '#stop', 'footer .composer')]
    assert q['r'] <= s['x'] and s['r'] <= x['x'], (q, s, x)                   # Queue, Steer, then the round Stop
    assert q['w'] >= 40 and s['w'] >= 40                                      # readable words
    for selector in ('#send', '#steer'):                                      # quiet beside the primary: no fill of the action colour
        look = await page.locator(selector).evaluate(ROUND)
        assert look['image'] == 'none' and look['font'] >= 12 and look['color'] != (await stop.evaluate(ROUND))['color'], (selector, look)
    await page.locator('#input').fill('after this turn, tidy up')
    await page.keyboard.press('Tab')                                          # the keyboard reaches them in the order shown
    assert [await page.evaluate('document.activeElement.id')] == ['send']
    await page.keyboard.press('Tab')
    assert await page.evaluate('document.activeElement.id') == 'steer'
    await page.keyboard.press('Tab')
    assert await page.evaluate('document.activeElement.id') == 'stop'
    await queue.click()
    await expect(page.locator('#input')).to_have_value('')
    assert prompts == ['after this turn, tidy up']                            # Queue still queues the message
    for width in (390, 480, 800, 1280):
        await page.set_viewport_size({'width': width, 'height': 800})
        for selector in ('#send', '#steer', '#stop'):
            await expect(page.locator(selector)).to_be_visible()
            inner, composer = await box(page, selector), await box(page, 'footer .composer')
            assert composer['x'] <= inner['x'] and inner['r'] <= composer['r'], (width, selector, inner, composer)
        assert (await box(page, '#input'))['w'] >= 150, width                 # the prompt box keeps a usable width
        q, s, x = [await box(page, sel) for sel in ('#send', '#steer', '#stop')]
        assert q['r'] <= s['x'] and s['r'] <= x['x'] and abs(q['b'] - x['b']) <= 1, (width, q, s, x)   # the three stay together, in order
        if width >= 480:                                                      # from the 480 px desktop pane up: one line
            assert (await box(page, 'footer .composer'))['h'] <= 60, width
    srv.bus.publish(Event('turn_end', {}))
    await expect(steer).to_be_hidden()
    await expect(queue).to_have_text('Send')


@pytest.mark.parametrize('width', [390, 720, 800, 1100])
async def test_no_sideways_scroll_idle_and_busy_at_390_720_1100(chat, width):
    srv, page, _, _ = chat
    await page.set_viewport_size({'width': width, 'height': 800})
    await srv._accept_prompt('Build the page ' + 'with-an-unbroken-run-of-letters' * 12)
    srv.bus.publish(Event('text_delta', 'A reply with `' + 'code' * 60 + '` and https://example.org/' + 'a' * 300 + '\n\n```\n' + 'x' * 400 + '\n```'))
    srv.bus.publish(Event('result', {}))
    srv.bus.publish(Event('plan', PLAN))
    srv.bus.publish(Event('stats', {'pp_ms': 1200, 'pp_n': 4000, 'prompt_tokens': 4000, 'gen_ms': 5000, 'gen_n': 130, 'window': 262144, 'cached': 2000}))
    await expect(page.locator('#plan-panel')).to_be_visible()
    await expect(page.locator('#perf')).to_contain_text('decode')
    await page.locator('#input').fill('a long draft ' * 40)
    for state in ('idle', 'busy'):
        if state == 'busy':
            srv.bus.publish(Event('turn_start', {}))
            await expect(page.locator('#steer')).to_be_visible()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, state)
        for selector in ('#main', 'footer', 'footer .composer', 'footer .hint'):
            assert await page.locator(selector).evaluate('e => e.scrollWidth <= e.clientWidth + 1'), (width, state, selector)
        edge = await page.locator('footer').evaluate('e => e.getBoundingClientRect().right')
        rights = await page.locator('footer .hint > *:visible, footer .composer > *:visible').evaluate_all('els => els.map(e => e.getBoundingClientRect().right)')
        assert all(r <= edge + 0.5 for r in rights), (width, state, rights, edge)


async def test_the_row_under_the_composer_keeps_its_controls_and_wraps(chat):
    srv, page, _, controls = chat
    srv.bus.publish(Event('stats', {'pp_ms': 1200, 'pp_n': 4000, 'prompt_tokens': 4000, 'gen_ms': 5000, 'gen_n': 130, 'window': 262144, 'cached': 2000}))
    await expect(page.locator('#perf')).to_contain_text('decode 26.0 tok/s')
    row = page.locator('footer .hint')
    for selector in ('#chat-mode', '#read-twice', '#fresh-start', '#preset-select', '#perf'):   # the same controls as before
        await expect(row.locator(selector)).to_be_visible()
    assert await row.evaluate("e => getComputedStyle(e).flexWrap") == 'wrap'
    hint, composer = await box(page, 'footer .hint'), await box(page, 'footer .composer')
    assert hint['y'] >= composer['b'] - 0.5 and hint['x'] >= composer['x'] - 0.5 and hint['r'] <= composer['r'] + 0.5, (hint, composer)   # under the box, in its column
    await page.set_viewport_size({'width': 390, 'height': 844})
    assert await row.evaluate('e => e.scrollWidth <= e.clientWidth + 1')     # it wraps; it does not scroll
    assert (await box(page, 'footer .hint'))['h'] > hint['h'] + 10
    for selector in ('#chat-mode', '#read-twice', '#fresh-start', '#preset-select', '#perf'):
        await expect(row.locator(selector)).to_be_visible()
    # They still work: Read x2 toggles, the mode chip asks Dream for its mode.
    await page.locator('#read-twice').click()
    await expect(page.locator('#read-twice')).to_have_attribute('aria-pressed', 'true')
    before = len(controls)
    await page.locator('#chat-mode').click()
    for _ in range(60):
        if len(controls) > before:
            break
        await asyncio.sleep(0.05)
    assert controls[before:] == [{'action': 'permission_mode'}]


async def test_composer_bubble_and_primary_button_are_flat(chat):
    srv, page, _, _ = chat
    await srv._accept_prompt('A message from the owner')
    await page.locator('#input').fill('a draft')
    for selector in ('footer .composer', '#stream .you .body', '#send', '#attach'):
        assert await page.locator(selector).evaluate(FLAT) == ['none', 'none', 'none'], selector   # no gradient, glow or blur
    await page.locator('#input').focus()
    assert await page.locator('footer .composer').evaluate(FLAT) == ['none', 'none', 'none']        # nor on focus
    srv.bus.publish(Event('turn_start', {}))
    await expect(page.locator('#stop')).to_be_visible()
    for selector in ('#stop', '#send', '#steer'):
        assert await page.locator(selector).evaluate(FLAT) == ['none', 'none', 'none'], selector
