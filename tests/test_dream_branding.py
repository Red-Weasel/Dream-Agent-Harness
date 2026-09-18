"""Reference-led chat composition, exercised with fixture output only."""
from playwright.async_api import expect
from dream.core.backends.base import Event
from test_desktop_chat import chat


async def test_welcome_actions_preserve_drafts_and_do_not_send(chat):
    _, page, prompts, _ = chat
    welcome = page.locator('#studio-interactions-empty')
    await welcome.get_by_role('button', name='Research', exact=True).click()
    await expect(page.locator('#input')).to_have_value('Help me research ')
    await page.locator('#input').fill('Keep this unsent idea')
    await welcome.get_by_role('button', name='Build', exact=True).click()
    await expect(page.locator('#input')).to_have_value('Keep this unsent idea')
    await expect(page.locator('#input')).to_be_focused()
    assert prompts == []


async def test_artwork_frames_conversation_and_composer_stays_below(chat):
    srv, page, _, _ = chat
    for width, height in [(1600, 1000), (720, 520), (390, 844)]:
        await page.set_viewport_size({'width': width, 'height': height})
        await expect(page.locator('#studio-interactions-empty')).to_be_visible()
        heading = await page.locator('#studio-interactions-empty h2').bounding_box()
        main = await page.locator('#main').bounding_box()
        assert heading['y'] >= main['y']
        assert heading['y'] + heading['height'] <= main['y'] + main['height']
        await page.screenshot(path=f'/tmp/dream-brand-welcome-{width}.png')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await srv._accept_prompt('Help me shape an idea for my project.')
    srv.bus.publish(Event('text_delta', '## Let’s make room for the idea\n\nTell me what you want to create. We can start with a sketch, explore the details, and build it together.'))
    srv.bus.publish(Event('result', {}))
    await expect(page.locator('#studio-interactions-empty')).to_be_hidden()
    for width, height in [(1600, 1000), (720, 520), (390, 844)]:
        await page.set_viewport_size({'width': width, 'height': height})
        main = await page.locator('#main').bounding_box()
        composer = await page.locator('.composer').bounding_box()
        message = await page.locator('#stream .dream').bounding_box()
        assert composer['y'] >= main['y'] + main['height'] - 1
        assert composer['y'] + composer['height'] <= height
        assert message['x'] > main['x']
        assert message['x'] + message['width'] < main['x'] + main['width']
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=f'/tmp/dream-brand-conversation-{width}.png')
    await page.reload()
    await expect(page.locator('#studio-interactions-empty')).to_be_hidden()
    await expect(page.locator('#stream .dream')).to_have_count(1)
