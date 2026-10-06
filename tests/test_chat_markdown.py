"""Dream Chat, batch B1 (DREAM-228): `md()` renders what a chat reply uses -- tables, numbered and nested lists,
rules, strikethrough, bare links, block quotes, four heading sizes, and code blocks with a language header and a
Copy button -- and model text is still escaped before any markup. Real Chromium, synthetic events, no model."""
import pytest
from playwright.async_api import expect

from dream.core.backends.base import Event
from test_desktop_chat import chat  # noqa: F401

CODE = 'def greet(name: str) -> str:\n    if name < "m" and name > \'a\':\n        return f"<b>Hello & welcome, {name}</b>"\n\n    return "bye"'
INSIDE = """e => { const box = e.getBoundingClientRect(), col = document.getElementById('stream').getBoundingClientRect();
  return box.x >= col.x - 0.5 && box.right <= col.right + 0.5; }"""


async def say(srv, page, text, done=True):
    """One reply from the model; returns its body."""
    count = await page.locator('#stream .msg.dream').count()
    srv.bus.publish(Event('text_delta', text))
    if done:
        srv.bus.publish(Event('result', {}))
    await expect(page.locator('#stream .msg.dream')).to_have_count(count + 1)
    body = page.locator('#stream .msg.dream .body').last
    if done:
        await expect(body.locator('.cursor')).to_have_count(0)
    return body


async def no_sideways_scroll(page):
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth && document.getElementById('main').scrollWidth <= document.getElementById('main').clientWidth")


async def test_tables_render_and_scroll_inside_the_column(chat):
    srv, page, _, _ = chat
    body = await say(srv, page, 'Speeds:\n\n| Model | Prefill | Decode |\n|:------|--------:|:------:|\n'
                                '| **Flash** | 686 tok/s | `26.9` |\n| Large | 300 tok/s | 12.0 \\| 12.4 |\n\nAfter the table.')
    await expect(body.locator('table')).to_have_count(1)
    assert await body.locator('thead th').all_text_contents() == ['Model', 'Prefill', 'Decode']    # the header row
    await expect(body.locator('tbody tr')).to_have_count(2)
    assert await body.locator('tbody tr').nth(0).locator('td').all_text_contents() == ['Flash', '686 tok/s', '26.9']
    await expect(body.locator('tbody td strong')).to_have_text('Flash')                              # inline markup inside a cell
    await expect(body.locator('tbody td code')).to_have_text('26.9')
    await expect(body.locator('tbody tr').nth(1).locator('td').nth(2)).to_have_text('12.0 | 12.4')   # an escaped pipe is text
    assert [await body.locator('thead th').nth(i).evaluate('e => getComputedStyle(e).textAlign') for i in range(3)] == ['left', 'right', 'center']
    await expect(body.locator('p').last).to_have_text('After the table.')
    assert '|' not in await body.locator('p').first.inner_text()
    # A table wider than the column scrolls sideways inside it; the page does not.
    wide = await say(srv, page, '| ' + ' | '.join(f'Column number {n} heading' for n in range(14)) + ' |\n|' + '---|' * 14 + '\n| '
                     + ' | '.join(f'value-{n}-without-a-break' for n in range(14)) + ' |')
    scroller = wide.locator('.md-table')
    await expect(scroller.locator('table')).to_have_count(1)
    assert await scroller.evaluate('e => e.scrollWidth > e.clientWidth + 50 && getComputedStyle(e).overflowX') == 'auto'
    assert await scroller.evaluate(INSIDE)
    await no_sideways_scroll(page)
    # A line with pipes and no delimiter row under it is not a table.
    plain = await say(srv, page, 'Use a | b to pipe, or c | d.')
    await expect(plain.locator('table')).to_have_count(0)
    await expect(plain.locator('p')).to_have_text('Use a | b to pipe, or c | d.')


async def test_ordered_and_nested_lists(chat):
    srv, page, _, _ = chat
    body = await say(srv, page, 'Steps:\n\n1. Install the package\n2. Configure it\n   - set the port\n   - set the host\n'
                                '     - IPv4 only\n3. Run the *checks*\n\nThen:\n\n- a bullet\n- another')
    numbered = body.locator(':scope > ol')
    await expect(numbered).to_have_count(1)
    await expect(numbered.locator(':scope > li')).to_have_count(3)
    assert await numbered.evaluate('e => getComputedStyle(e).listStyleType') == 'decimal'            # numbered, not bullets
    await expect(numbered.locator(':scope > li').nth(0)).to_have_text('Install the package')
    nested = numbered.locator(':scope > li').nth(1).locator(':scope > ul')
    await expect(nested.locator(':scope > li')).to_have_count(2)                                     # depth 2
    await expect(nested.locator(':scope > li').nth(1).locator(':scope > ul > li')).to_have_text('IPv4 only')   # depth 3
    await expect(numbered.locator(':scope > li').nth(2).locator('em')).to_have_text('checks')
    indent = [await body.locator(s).first.evaluate('e => e.getBoundingClientRect().x') for s in ('ol > li', 'ol > li > ul > li', 'ol > li > ul > li > ul > li')]
    assert indent[0] < indent[1] < indent[2], indent
    await expect(body.locator(':scope > ul > li')).to_have_count(2)
    # A list that starts at 3 keeps its number; blank lines between items do not split the list.
    later = await say(srv, page, '3. third\n4. fourth\n\n5. fifth')
    await expect(later.locator('ol')).to_have_count(1)
    await expect(later.locator('ol')).to_have_attribute('start', '3')
    await expect(later.locator('ol > li')).to_have_count(3)
    # A code block between two numbered steps stays inside its step.
    steps = await say(srv, page, '1. Install:\n   ```bash\n   pip install dream\n   ```\n2. Run it')
    await expect(steps.locator('ol')).to_have_count(1)
    await expect(steps.locator('ol > li')).to_have_count(2)
    await expect(steps.locator('ol > li').first.locator('pre code')).to_have_text('pip install dream')


async def test_rule_strikethrough_and_bare_links(chat):
    srv, page, _, _ = chat
    body = await say(srv, page, 'Above the rule.\n\n---\n\nThis is ~~wrong~~ right. See https://example.org/docs?a=1&b=2. '
                                'Or [the guide](https://example.org/guide), or `https://example.org/in-code`.')
    await expect(body.locator('hr')).to_have_count(1)
    await expect(body.locator('del')).to_have_text('wrong')
    bare = body.locator('a').nth(0)
    await expect(bare).to_have_attribute('href', 'https://example.org/docs?a=1&b=2')                # the sentence's full stop is not in it
    await expect(bare).to_have_text('https://example.org/docs?a=1&b=2')
    await expect(bare).to_have_attribute('target', '_blank')
    await expect(bare).to_have_attribute('rel', 'noopener noreferrer')
    await expect(body.locator('a').nth(1)).to_have_text('the guide')
    await expect(body.locator('a').nth(1)).to_have_attribute('href', 'https://example.org/guide')
    await expect(body.locator('a')).to_have_count(2)                                                 # a URL inside code is not a link
    await expect(body.locator('code')).to_have_text('https://example.org/in-code')


async def test_headings_block_quotes_and_inline_code(chat):
    srv, page, _, _ = chat
    body = await say(srv, page, '# One\n\n## Two\n\n### Three\n\n#### Four\n\nBody text with `inline code` and **bold**.\n\n'
                                '> A quoted line\n> and its second line.\n\nAfter.')
    sizes = [await body.locator(f'h{n}').evaluate('e => parseFloat(getComputedStyle(e).fontSize)') for n in (1, 2, 3, 4)]
    assert sizes[0] > sizes[1] > sizes[2] > sizes[3], sizes                                          # headings 1 to 4 differ in size
    assert [await body.locator(f'h{n}').inner_text() for n in (1, 2, 3, 4)] == ['One', 'Two', 'Three', 'Four']
    await expect(body.locator('blockquote')).to_have_count(1)                                        # one quote, not one per line
    await expect(body.locator('blockquote')).to_contain_text('A quoted line')
    await expect(body.locator('blockquote')).to_contain_text('and its second line.')
    await expect(body.locator('p code')).to_have_text('inline code')
    await expect(body.locator('p strong')).to_have_text('bold')


async def test_code_block_has_language_and_copy(chat):
    srv, page, _, _ = chat
    body = await say(srv, page, f'Here:\n\n```python\n{CODE}\n```\n\nAnd plain:\n\n```\nno language here\n```\n\n```text\n' + 'long ' * 200 + '\n```')
    blocks = body.locator('.codeblock')
    await expect(blocks).to_have_count(3)
    await expect(blocks.nth(0).locator('.codehead .codelang')).to_have_text('python')                # the header names the language
    await expect(blocks.nth(1).locator('.codehead .codelang')).to_have_text('text')                  # "text" when none is given
    for i in range(3):
        await expect(blocks.nth(i).get_by_role('button', name='Copy', exact=True)).to_be_visible()
        head, pre = await blocks.nth(i).locator('.codehead').bounding_box(), await blocks.nth(i).locator('pre').bounding_box()
        assert head['y'] + head['height'] <= pre['y'] + 0.5, (head, pre)                              # the header sits above the code
    assert await blocks.nth(0).locator('pre code').evaluate('e => e.textContent') == CODE
    assert await blocks.nth(2).locator('pre').evaluate('e => e.scrollWidth > e.clientWidth + 50')    # a long line scrolls inside the block
    for i in range(3):
        assert await blocks.nth(i).evaluate(INSIDE)
    await no_sideways_scroll(page)


async def test_copy_puts_the_exact_code_on_the_clipboard(chat):
    srv, page, _, _ = chat
    await page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    body = await say(srv, page, f'First:\n\n```python\n{CODE}\n```\n\nSecond:\n\n```\nother block\n```')
    copy = body.locator('.codeblock').nth(0).get_by_role('button')
    await expect(copy).to_have_text('Copy')
    await copy.click()
    await expect(copy).to_have_text('Copied')                                                        # it confirms
    assert await page.evaluate('navigator.clipboard.readText()') == CODE                             # exactly the code
    await expect(body.locator('.codeblock').nth(1).get_by_role('button')).to_have_text('Copy')       # only the pressed one
    await expect(copy).to_have_text('Copy', timeout=4000)                                            # and reads Copy again after two seconds
    await body.locator('.codeblock').nth(1).get_by_role('button').click()
    assert await page.evaluate('navigator.clipboard.readText()') == 'other block'


async def test_a_refused_clipboard_says_so(chat):
    srv, page, _, _ = chat
    await page.evaluate("""() => { navigator.clipboard.writeText = () => Promise.reject(new Error('denied'));
                                   document.execCommand = () => false; }""")
    body = await say(srv, page, '```\nsecret\n```')
    copy = body.locator('.codeblock').get_by_role('button')
    await copy.click()
    await expect(copy).to_have_text('Copy failed')                                                   # never a false "Copied"


async def test_an_open_fence_streams_as_code(chat):
    srv, page, _, _ = chat
    body = await say(srv, page, 'Here is the script:\n\n```python\nprint(1)\n', done=False)
    await expect(body.locator('.codeblock')).to_have_count(1)                                        # already a code block
    await expect(body.locator('.codeblock .codelang')).to_have_text('python')
    assert await body.locator('.codeblock pre code').evaluate('e => e.textContent') == 'print(1)'
    await expect(body.locator('p').first).to_have_text('Here is the script:')
    assert '```' not in await body.inner_text()
    srv.bus.publish(Event('text_delta', 'print(2)\n```\n\nDone.'))
    srv.bus.publish(Event('result', {}))
    await expect(body.locator('p').last).to_have_text('Done.')
    await expect(body.locator('.codeblock')).to_have_count(1)
    assert await body.locator('.codeblock pre code').evaluate('e => e.textContent') == 'print(1)\nprint(2)'


async def test_model_html_stays_text(chat):
    srv, page, _, _ = chat
    hostile = ('<img src=x onerror="window.pwned=1"> and <script>window.pwned=2</script>\n\n'
               '| <img src=x onerror="window.pwned=3"> | b |\n|---|---|\n| <b>cell</b> | <i>x</i> |\n\n'
               '1. <img src=x onerror="window.pwned=4">\n   - <svg onload="window.pwned=5">\n\n'
               '> <iframe src="javascript:window.pwned=6"></iframe>\n\n'
               '[<b>label</b>](https://example.org/"onmouseover="window.pwned=7) [x](javascript:window.pwned=8)\n\n'
               '~~<u>struck</u>~~ https://example.org/"><img src=x onerror="window.pwned=9">\n\n'
               '```"><img src=x onerror="window.pwned=10">\n<script>window.pwned=11</script>\n```\n')
    body = await say(srv, page, hostile)
    await expect(body.locator('table')).to_have_count(1)
    await expect(body.locator('.codeblock')).to_have_count(1)
    assert await body.locator('img, script, iframe, svg, b, i, u').count() == 0                      # none became an element
    assert await page.evaluate('window.pwned') is None                                               # and none ran
    text = await body.inner_text()
    for literal in ('<img src=x onerror="window.pwned=1">', '<script>window.pwned=2</script>', '<b>cell</b>', '<svg onload=', '<iframe src=', '<u>struck</u>'):
        assert literal in text, literal                                                              # the HTML shows as text
    hrefs = await body.locator('a').evaluate_all('els => els.map(a => a.getAttribute("href"))')
    assert hrefs and all(h.startswith('https://example.org/') for h in hrefs), hrefs                 # only http(s) links
    assert await body.locator('a').evaluate_all('els => els.every(a => a.attributes.length === 3)')  # href, target, rel: no injected attribute
    # The same while it streams (each delta re-renders the message).
    live = await say(srv, page, '<img src=x onerror="window.pwned=12">\n\n```html\n<script>window.pwned=13</script>\n', done=False)
    assert await live.locator('img, script').count() == 0 and await page.evaluate('window.pwned') is None


@pytest.mark.parametrize('desktop', [True, False])
async def test_the_plain_browser_page_gets_the_same_markdown(chat, desktop):
    """The browser page keeps its own layout (spec default 2) and gains the new markdown."""
    srv, page, _, _ = chat
    if not desktop:
        await page.goto(srv.url)
        await expect(page.locator('#stat')).to_have_text('live')
    body = await say(srv, page, '1. one\n2. two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n---\n\n~~old~~\n\n```js\nlet x = 1;\n```')
    await expect(body.locator('ol > li')).to_have_count(2)
    await expect(body.locator('table tbody td')).to_have_count(2)
    await expect(body.locator('hr')).to_have_count(1)
    await expect(body.locator('del')).to_have_text('old')
    await expect(body.locator('.codeblock .codelang')).to_have_text('js')
    await expect(body.locator('.codeblock').get_by_role('button', name='Copy', exact=True)).to_be_visible()
    # Both boxes in one reading: the plain page slides a new message in for 180 ms, and two readings a few
    # milliseconds apart inside that differ by more than the half pixel allowed here.
    head, pre = await body.locator('.codeblock').evaluate("b => ['.codehead', 'pre'].map(s => { const r = b.querySelector(s).getBoundingClientRect(); return {y: r.y, height: r.height}; })")
    assert head['y'] + head['height'] <= pre['y'] + 0.5, (head, pre)
