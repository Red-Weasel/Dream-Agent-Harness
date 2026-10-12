"""Header bridge drives the actual shipped DeepSleep controls, not a second API."""
from pathlib import Path

from playwright.async_api import expect
from test_deepsleep_ui import board_page
from test_deepsleep_ui import BoardAPI
from test_frontier_switches_ui import frontiers

STATIC = Path(__file__).resolve().parents[1] / 'dream/gui/static'


async def test_complete_shell_header_mobile_navigation_offset_and_pointer_controls(frontiers):
    page, _, _, errors = frontiers
    api = BoardAPI()
    await page.route('**/api/deepsleep', api.route)
    await page.route_web_socket('**/ws/deepsleep/*', api.socket)
    await page.set_viewport_size({'width': 1680, 'height': 1040})
    await page.locator('#dream-nav-deepsleep').click()
    header = page.locator('#dream-deepsleep-header')
    await expect(header).to_be_visible()
    for width in (390, 1680, 390):
        await page.set_viewport_size({'width': width, 'height': 844})
        await page.wait_for_function('''() => {
            const header = document.querySelector('body > header').getBoundingClientRect();
            const board = document.querySelector('#dream-deepsleep').getBoundingClientRect();
            return header.height > 0 && Math.abs(board.top - header.bottom) < 1;
        }''')
        if width == 390:
            nav = await page.locator('#dream-nav').bounding_box()
            top = await page.locator('body > header').bounding_box()
            assert top['y'] >= nav['y'] + nav['height'] - 1
        toggle = header.locator('#ds-character-toggle')
        await toggle.scroll_into_view_if_needed()
        box = await toggle.bounding_box()
        before = await toggle.get_attribute('aria-pressed')
        await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
        await expect(toggle).to_have_attribute('aria-pressed', 'false' if before == 'true' else 'true')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.locator('#dream-nav-chat').click()
    await expect(header).to_be_hidden()
    assert not errors


async def install_bridge(page):
    await page.evaluate('''() => {
        document.body.prepend(document.createElement('header'));
        window.DREAM_SESSION = {session_id: 'toolbar-fixture', workspace: '/fixture'};
        window.toolbarOriginal = document.querySelector('.ds-controls');
    }''')
    await page.add_style_tag(path=str(STATIC / 'workspace.css'))
    await page.add_script_tag(path=str(STATIC / 'deepsleep-toolbar.js'))


async def test_browser_header_moves_existing_controls_and_dispatches_actual_character_toggle(board_page):
    page, api, errors = board_page
    await install_bridge(page)
    assert await page.evaluate("document.querySelector('#dream-deepsleep-header .ds-controls') === toolbarOriginal")
    await expect(page.locator('#dream-deepsleep > .ds-toolbar')).to_be_hidden()
    state = await page.evaluate('DreamDeepSleepToolbar.state()')
    assert set(state['controls']) == {'provider','profile','model','layout','name','add','tile','group','connections','refresh','characters'}
    accepted = await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'characters'})")
    assert accepted
    await expect(page.locator('#ds-character-toggle')).to_have_attribute('aria-pressed', 'true')
    assert [option['value'] for option in state['controls']['layout']['options']] == ['terminal', 'conversation', 'activity']
    for layout in ('conversation', 'activity', 'terminal'):
        assert await page.evaluate('''value => DreamDeepSleepToolbar.dispatch({
            session_id:'toolbar-fixture',workspace:'/fixture',control:'layout',value})''', layout)
        await expect(page.locator('#ds-text-layout')).to_have_value(layout)
        await expect(page.locator('#ds-character-toggle')).to_have_attribute('aria-pressed', 'true')
    assert not await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'layout',value:'invalid'})")
    assert api.calls == []
    await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'provider',value:'codex'})")
    await expect(page.get_by_label('New terminal provider')).to_have_value('codex')
    await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'add'})")
    await expect(page.locator('.ds-node')).to_have_count(1)
    assert next(call for call in api.calls if call['action'] == 'create')['provider'] == 'codex'
    for layout in ('conversation', 'activity', 'terminal'):
        assert await page.evaluate('''value => DreamDeepSleepToolbar.dispatch({
            session_id:'toolbar-fixture',workspace:'/fixture',control:'layout',value})''', layout)
        await expect(page.locator('.ds-node')).to_have_attribute('data-text-layout', layout)
        await expect(page.locator('#ds-character-toggle')).to_have_attribute('aria-pressed', 'true')
    assert not errors


async def test_toolbar_rejects_stale_workspace_unknown_option_disabled_and_wrong_view(board_page):
    page, api, errors = board_page
    await install_bridge(page)
    for change in [{'workspace':'/other'}, {'session_id':'other'}, {'control':'shell'},
                   {'control':'provider','value':'not-a-provider'}, {'extra':'payload'}]:
        payload = {'session_id':'toolbar-fixture','workspace':'/fixture','control':'add', **change}
        assert not await page.evaluate('(payload) => DreamDeepSleepToolbar.dispatch(payload)', payload)
    await page.get_by_label('Add terminal', exact=True).evaluate('(node) => node.disabled = true')
    assert not await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'add'})")
    await page.evaluate("document.documentElement.dataset.dreamView='chat'")
    await expect(page.locator('#dream-deepsleep-header')).to_be_hidden()
    assert not await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'characters'})")
    assert api.calls == []
    assert not errors


async def test_native_header_source_survives_navigation_and_browser_small_width(board_page):
    page, api, errors = board_page
    await install_bridge(page)
    await page.evaluate("document.documentElement.classList.add('dream-native')")
    await expect(page.locator('#dream-deepsleep-header')).to_be_hidden()
    assert await page.evaluate("document.querySelector('#dream-deepsleep .ds-controls') === toolbarOriginal")
    assert await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'group'})")
    await expect(page.locator('.ds-group')).to_be_visible()
    await page.evaluate("document.documentElement.classList.remove('dream-native')")
    await page.set_viewport_size({'width':390,'height':844})
    await expect(page.locator('#dream-deepsleep-header')).to_be_visible()
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert api.calls == []
    assert not errors


async def test_terminal_name_roundtrip_bounds_and_create_payload(board_page):
    page, api, errors = board_page
    await install_bridge(page)
    name = page.get_by_label('New terminal name', exact=True)
    await name.fill('Claude · Research')
    state = await page.evaluate('DreamDeepSleepToolbar.state()')
    assert state['controls']['name']['value'] == 'Claude · Research'
    assert await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'name',value:'Claude · Review'})")
    await expect(name).to_have_value('Claude · Review')
    assert not await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'name',value:'x'.repeat(101)})")
    assert not await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'old',workspace:'/fixture',control:'name',value:'Stale'})")
    assert await page.evaluate("DreamDeepSleepToolbar.dispatch({session_id:'toolbar-fixture',workspace:'/fixture',control:'add'})")
    await expect(page.locator('.ds-node h2')).to_have_text('Claude · Review')
    assert next(call for call in api.calls if call['action']=='create')['title'] == 'Claude · Review'
    await expect(name).to_have_value('')
    assert not errors
