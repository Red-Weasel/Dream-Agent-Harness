"""Chat and Artifacts show no bars above the work area (DREAM-240): what the page's header and work bar held is the
More menu under the composer. A test that clicked a bar's control there goes the owner's way, through the menu."""


async def more(page, control_id):
    """Open More and click the row that clicks the page's own control of this id."""
    await page.locator("#chat-menu > summary").click()
    await page.locator(f'#chat-menu [data-proxy="{control_id}"]').click()
