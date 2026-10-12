"""The owner, 2026-10-10: DeepSleep's Connections button is named Message Board, and its window can be resized."""
from playwright.async_api import expect

from test_deepsleep_channel_ui import channel_setup
from test_deepsleep_ui import board_page  # noqa: F401  (the fixture)

GRIP = 'Resize message board; arrow keys resize, Home resets'


async def test_the_button_and_its_window_are_named_message_board(board_page):
    page, api, errors = board_page
    button = page.locator('.ds-controls button', has_text='Message Board')
    await expect(button).to_have_count(1)
    assert (await button.get_attribute('aria-label')).startswith('Message Board')    # the spoken name starts as it reads
    await expect(page.locator('.ds-controls button', has_text='Connections')).to_have_count(0)
    await channel_setup(page, api)
    await expect(page.locator('.ds-activity h2')).to_have_text('Message Board')
    assert not errors


async def test_the_corner_grip_resizes_the_window_and_the_size_is_kept(board_page):
    page, api, errors = board_page
    await page.set_viewport_size({'width': 1500, 'height': 900})
    await channel_setup(page, api)
    panel = page.locator('.ds-activity')
    before = await panel.bounding_box()
    grip = await page.get_by_role('button', name=GRIP).bounding_box()
    assert abs(grip['x'] - before['x']) <= 2 and abs(grip['y'] + grip['height'] - before['y'] - before['height']) <= 2  # bottom-left
    await page.mouse.move(grip['x'] + grip['width'] / 2, grip['y'] + grip['height'] / 2)
    await page.mouse.down()
    await page.mouse.move(grip['x'] + grip['width'] / 2 - 200, grip['y'] + grip['height'] / 2 - 250, steps=5)
    await page.mouse.up()
    after = await panel.bounding_box()
    assert abs(after['width'] - (before['width'] + 200)) <= 2, (before, after)        # wider to the left
    assert abs(after['height'] - (before['height'] - 250)) <= 2, (before, after)       # shorter from the bottom
    assert abs(after['x'] + after['width'] - before['x'] - before['width']) <= 2        # the right edge stays put
    await page.get_by_role('button', name='Close board channel').click()
    await page.get_by_role('button', name='Message Board').click()
    kept = await panel.bounding_box()
    assert abs(kept['width'] - after['width']) <= 1 and abs(kept['height'] - after['height']) <= 1
    send = await page.get_by_role('button', name='Send board channel message').bounding_box()
    assert send['y'] + send['height'] <= kept['y'] + kept['height']                     # nothing cut off
    await page.get_by_role('button', name=GRIP).press('Home')                           # back to the default size
    reset = await panel.bounding_box()
    assert abs(reset['width'] - before['width']) <= 1 and abs(reset['height'] - before['height']) <= 1
    assert not errors


async def test_the_grip_works_from_the_keyboard_and_stops_at_a_usable_size(board_page):
    page, api, errors = board_page
    await page.set_viewport_size({'width': 1500, 'height': 900})
    await channel_setup(page, api)
    panel = page.locator('.ds-activity')
    grip = page.get_by_role('button', name=GRIP)
    before = await panel.bounding_box()
    await grip.press('ArrowLeft')
    await grip.press('ArrowUp')
    after = await panel.bounding_box()
    assert abs(after['width'] - before['width'] - 10) <= 1 and abs(before['height'] - after['height'] - 10) <= 1
    for _ in range(40):
        await grip.press('Shift+ArrowUp')
        await grip.press('Shift+ArrowRight')
    small = await panel.bounding_box()
    assert small['width'] >= 259 and small['height'] >= 250, small
    send = await page.get_by_role('button', name='Send board channel message').bounding_box()
    head = await page.get_by_role('button', name='Close board channel').bounding_box()
    assert send['y'] + send['height'] <= small['y'] + small['height'] and head['y'] >= small['y']
    assert not errors
