"""S2 polish: one round-action family, one top-bar pill family, a list gutter the scrollbar cannot cover, no
overflow at 1100 and 390 px, and WCAG AA text contrast for the shared theme tokens."""
import re
from pathlib import Path

from playwright.async_api import expect
from test_desktop_chat import chat  # noqa: F401
from test_workspace_library_ui import library  # noqa: F401

THEME = Path(__file__).resolve().parents[1] / 'dream/gui/static/theme.css'
SHAPE = "e => {const c = getComputedStyle(e); return [c.width, c.height, c.borderRadius, c.borderTopWidth, c.fontSize, c.fontFamily]}"
PILL = "e => {const c = getComputedStyle(e); return [c.height, c.borderRadius, c.borderTopColor, c.backgroundColor, c.fontSize, c.fontWeight, c.paddingLeft]}"


def _tokens():
    block = re.search(r'html\.dream-design\{(--bg0:[^}]*)\}', THEME.read_text()).group(1)
    return dict(re.findall(r'--([\w-]+):(#[0-9a-f]{6})', block))


def _ratio(a, b):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted([lum(a), lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_theme_text_tokens_meet_wcag_aa():
    t = _tokens()
    surfaces = ['bg0', 'bg', 'bg2', 'panel', 'raised', 'plum']
    for text in ['fg', 'dim', 'faint', 'ember', 'ember-soft', 'violet', 'violet-soft']:
        for s in surfaces:
            assert _ratio(t[text], t[s]) >= 4.5, (text, s, round(_ratio(t[text], t[s]), 2))
    assert _ratio(t['ember-ink'], t['ember']) >= 4.5                  # dark text on the ember primary button


async def test_round_actions_and_pills_are_one_family_and_the_gutter_holds(library):
    _, page, prompts, _, _ = library
    await page.locator('#dream-nav-skills').click()
    await page.get_by_label('Preset', exact=True).select_option('Legal')
    remove = page.get_by_role('button', name='Remove writing from Legal')
    await expect(remove).to_be_visible()
    assigned = page.get_by_role('button', name='In Legal: writing')
    x_shape = await remove.evaluate(SHAPE)
    assert await assigned.evaluate(SHAPE) == x_shape                        # assigned (✓) and remove (×)
    await assigned.click()                                                  # now it is the add (+) state
    await expect(assigned).to_have_attribute('aria-pressed', 'false')
    assert await assigned.evaluate(SHAPE) == x_shape                        # add (+) and remove (×)
    await assigned.click()
    await expect(remove).to_be_visible()
    for width, height in [(2549, 1337), (1100, 800), (390, 844)]:
        await page.set_viewport_size({'width': width, 'height': height})
        gap = await page.locator('.library-preset-col .library-list').evaluate(
            """l => {const r = l.getBoundingClientRect(), pad = parseFloat(getComputedStyle(l).paddingRight),
                     bar = l.offsetWidth - l.clientWidth, x = l.querySelector('.library-assign').getBoundingClientRect();
                     return {pad, clear: r.right - bar - x.right}}""")
        assert gap['pad'] >= 10 and gap['clear'] >= 10, (width, gap)     # an overlay scrollbar lands in the gutter
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        assert await page.locator('#dream-skills-page').evaluate('e => e.scrollWidth <= e.clientWidth'), width
    # DREAM-242: no view shows the page's bars; their pill family is the row of chips under the composer now.
    await page.locator('#dream-nav-chat').click()
    await page.set_viewport_size({'width': 2549, 'height': 1337})
    pills = page.locator('footer .hint :is(#chat-mode, #read-twice, #fresh-start, .cn-chip):visible')
    shapes = [await pills.nth(i).evaluate(PILL) for i in range(await pills.count())]
    assert len(shapes) >= 5 and all(s == shapes[0] for s in shapes), shapes
    for width, height in [(2549, 1337), (1100, 800), (390, 844)]:
        await page.set_viewport_size({'width': width, 'height': height})
        for bar in ('body > header', '#dream-workbar', '#dream-workspace-tools>summary'):
            await expect(page.locator(bar)).to_be_hidden()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
    assert prompts == []
