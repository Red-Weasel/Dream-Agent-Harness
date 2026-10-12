"""In and Out are easy to find (the owner, 2026-10-10): wide rounded pills, In in the middle of a tile's top bar and
Out in the middle of its bottom bar; the cord runs from Out to In, and a message on the link runs along the cord as
lightning in reds, oranges and yellows, only while it travels."""
from playwright.async_api import expect

from test_deepsleep_ui import add, board_page  # noqa: F401  (board_page is the fixture)

BOX = """(el) => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return {x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2, radius: parseFloat(s.borderTopLeftRadius)}; }"""
# a cord's two ends in page coordinates
ENDS = """(path) => { const svg = path.ownerSVGElement, m = path.getScreenCTM(), n = path.getTotalLength();
  const at = d => { const p = path.getPointAtLength(d); return {x: p.x * m.a + m.e, y: p.y * m.d + m.f}; };
  return [at(0), at(n)]; }"""


async def two_tiles(page, api):
    await page.set_viewport_size({'width': 1500, 'height': 1000})
    for provider in ('anthropic', 'codex'):
        await add(page, provider)
    await expect(page.locator('.ds-node')).to_have_count(2)
    return page.locator('.ds-node').nth(0), page.locator('.ds-node').nth(1)


async def test_in_and_out_are_wide_pills_in_the_middle_of_the_top_and_bottom_bars(board_page):
    page, api, errors = board_page
    first, _ = await two_tiles(page, api)
    head, bar = first.locator('.ds-node-head'), first.locator('.ds-repo-bar')
    pin = first.get_by_role('button', name='Link to Claude', exact=True)
    pout = first.get_by_role('button', name='Link from Claude', exact=True)
    await expect(head.get_by_role('button', name='Link to Claude', exact=True)).to_be_visible()
    await expect(bar.get_by_role('button', name='Link from Claude', exact=True)).to_be_visible()
    for pill, row in ((pin, head), (pout, bar)):
        p, r = await pill.evaluate(BOX), await row.evaluate(BOX)
        assert abs(p['cx'] - r['cx']) <= 6, (p, r)                         # the middle of its bar
        assert p['w'] >= 60 and p['w'] >= 2 * p['h'] and p['radius'] >= p['h'] / 2 - 1, p   # a wide rounded pill
    assert not errors


async def test_the_cord_runs_from_out_to_in_and_a_collapsed_tile_keeps_its_cord(board_page):
    page, api, errors = board_page
    first, second = await two_tiles(page, api)
    await first.get_by_role('button', name='Link from Claude', exact=True).click()
    await second.get_by_role('button', name='Link to Codex', exact=True).click()
    wire = page.locator('.ds-wire')
    await expect(wire).to_have_count(1)
    start, end = await wire.evaluate(ENDS)
    out = await first.get_by_role('button', name='Link from Claude', exact=True).evaluate(BOX)
    into = await second.get_by_role('button', name='Link to Codex', exact=True).evaluate(BOX)
    assert abs(start['x'] - out['cx']) <= 2 and abs(start['y'] - (out['y'] + out['h'])) <= 2, (start, out)   # Out's bottom edge
    assert abs(end['x'] - into['cx']) <= 2 and abs(end['y'] - into['y']) <= 2, (end, into)                 # In's top edge
    await first.get_by_role('button', name='Collapse Claude to header').click()
    await expect(wire).to_have_count(1)                                     # the Out pill is hidden: the cord stays
    start, _ = await wire.evaluate(ENDS)
    head = await first.locator('.ds-node-head').evaluate(BOX)
    assert abs(start['y'] - (head['y'] + head['h'])) <= 2 and abs(start['x'] - head['cx']) <= 2
    assert not errors


async def test_lightning_runs_along_the_cord_only_while_a_message_travels(board_page):
    page, api, errors = board_page
    first, second = await two_tiles(page, api)
    await first.get_by_role('button', name='Link from Claude', exact=True).click()
    await second.get_by_role('button', name='Link to Codex', exact=True).click()
    await expect(page.locator('.ds-wire')).to_have_count(1)
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(page.locator('.ds-flow-pulse')).to_have_count(0)           # nothing travels: no lightning
    api.state['receipts'] = [{'id': 'direct-1', 'source': 'node-1', 'target': 'node-2', 'state': 'queued', 'result': '', 'error': ''}]
    await page.get_by_role('button', name='Refresh workspace').click()
    pulse = page.locator('.ds-flow-pulse')
    await expect(pulse).to_have_count(1)
    strokes = await pulse.locator('path').evaluate_all('(ps) => ps.map(p => getComputedStyle(p).stroke)')
    assert {'rgb(255, 59, 31)', 'rgb(255, 138, 28)', 'rgb(255, 228, 107)'} <= set(strokes), strokes   # red, orange, yellow
    bolt = await pulse.locator('.ds-flow-packet').get_attribute('d')
    assert bolt == await page.locator('.ds-wire').get_attribute('d')        # along the cord itself
    await expect(pulse).to_have_count(0, timeout=4000)                      # gone once the message has passed
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(pulse).to_have_count(0)                                    # and never replayed
    assert not errors


async def test_reduced_motion_shows_a_still_cord_glow_instead_of_moving_lightning(board_page):
    page, api, errors = board_page
    await page.emulate_media(reduced_motion='reduce')
    first, second = await two_tiles(page, api)
    await first.get_by_role('button', name='Link from Claude', exact=True).click()
    await second.get_by_role('button', name='Link to Codex', exact=True).click()
    await page.get_by_role('button', name='Refresh workspace').click()
    api.state['receipts'] = [{'id': 'direct-2', 'source': 'node-1', 'target': 'node-2', 'state': 'queued', 'result': '', 'error': ''}]
    await page.get_by_role('button', name='Refresh workspace').click()
    await expect(page.locator('.ds-flow-pulse')).to_have_count(1)            # the message still shows, standing still
    names = await page.locator('.ds-flow-pulse path').evaluate_all('(ps) => ps.map(p => getComputedStyle(p).animationName)')
    assert names and all(n == 'none' for n in names), names
    assert await page.locator('.ds-flow-pulse animate').count() == 0          # no crackle either
    assert not errors
