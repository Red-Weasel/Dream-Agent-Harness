"""DREAM-171 Skills workspace in real Chromium: assign by keyboard and by drag (never a move), Default takes nothing."""
import json

from playwright.async_api import expect
from test_desktop_chat import chat  # noqa: F401
from test_workspace_library_ui import library  # noqa: F401

from dream import presets


def _saved():
    return json.loads(presets.user_path().read_text())["presets"]


async def _open(page):
    await page.locator('#dream-nav-skills').click()
    await expect(page.get_by_label('Preset', exact=True)).to_have_value('Default')
    toggle = page.get_by_role('button', name='In Default: writing')
    await expect(toggle).to_be_disabled()                  # Default means everything: nothing to assign


async def test_keyboard_assigns_and_unassigns_in_the_chosen_preset(library):
    _, page, prompts, _, _ = library
    await _open(page)
    await page.get_by_label('Preset', exact=True).select_option('Legal')
    toggle = page.get_by_role('button', name='In Legal: writing')
    await expect(toggle).to_have_attribute('aria-pressed', 'true')
    await page.locator('[data-key=writing]').focus()
    await page.keyboard.press('Tab')
    await expect(toggle).to_be_focused()
    await page.keyboard.press('Enter')
    await expect(toggle).to_have_attribute('aria-pressed', 'false')
    await expect(toggle).to_be_focused()
    await expect(page.locator('.library-preset-col')).not_to_contain_text('writing')
    assert 'writing' not in _saved()['Legal']['skills'] and 'Legal' in _saved()      # an edited built-in, the owner's
    await page.keyboard.press('Enter')
    await expect(toggle).to_have_attribute('aria-pressed', 'true')
    await expect(page.locator('.library-preset-col')).to_contain_text('Built-in · edited by you')
    await page.get_by_role('button', name='Use for next session').click()
    await expect(page.get_by_role('button', name='Active for new sessions')).to_be_disabled()
    assert presets.active() == 'Legal' and prompts == []


async def test_drag_assigns_a_copy_and_default_takes_no_drop(library):
    _, page, prompts, _, _ = library
    await _open(page)
    row = page.locator('.library-row', has=page.locator('[data-key=writing]'))
    await row.drag_to(page.locator('.library-preset-col'))               # Default: refused, nothing written
    assert not presets.user_path().exists()
    await page.get_by_label('Preset', exact=True).select_option('')      # Add new…
    await page.get_by_label('New preset name').fill('Contracts')
    await page.get_by_role('button', name='Create preset').click()
    await expect(page.get_by_label('Preset', exact=True)).to_have_value('Contracts')
    await row.drag_to(page.locator('.library-preset-col'))
    await expect(page.get_by_role('button', name='Remove writing from Contracts')).to_be_visible()
    await expect(page.get_by_role('button', name='In Contracts: writing')).to_have_attribute('aria-pressed', 'true')
    assert _saved()['Contracts']['skills'] == ['writing'] and 'Legal' not in _saved()   # a copy; Legal untouched
    await page.get_by_label('Preset', exact=True).select_option('Legal')
    await expect(page.get_by_role('button', name='In Legal: writing')).to_have_attribute('aria-pressed', 'true')
    assert prompts == []


async def test_the_composer_select_and_the_workspace_share_one_choice(library):
    _, page, prompts, controls, _ = library
    composer = page.get_by_label('Skill preset for new sessions')
    await expect(composer).to_have_value('Default')
    await expect(page.locator('#preset-cost')).to_contain_text('skills ·')
    await composer.select_option('Finance')
    await expect(page.locator('#stream')).to_contain_text('Skill preset Finance applies to new sessions')
    assert presets.active() == 'Finance'
    await page.locator('#dream-nav-skills').click()
    await page.get_by_label('Preset', exact=True).select_option('Legal')
    await page.get_by_role('button', name='Use for next session').click()
    await expect(composer).to_have_value('Legal')                              # the workspace told the composer
    assert presets.active() == 'Legal' and prompts == [] and controls == [{'action': 'permission_mode_status'}] * (len(controls) > 0)


async def test_a_plugin_skill_opens_read_only_and_edit_a_copy_saves_your_version(library):
    _, page, prompts, _, writes = library
    plugin = {'name': 'greet-people', 'description': 'Greets people', 'source': 'plugin', 'origin': 'plugin',
              'managed': False, 'enabled': True, 'sha256': 'p1',
              'content': '---\nname: greet-people\ndescription: Greets people\n---\n\nSay hello.'}
    rows = [plugin]
    await page.route('**/api/skills', lambda r: r.fulfill(json={'skills': rows}))
    await page.route('**/api/skills/greet-people', lambda r: r.fulfill(json=plugin) if r.request.method == 'GET'
                     else (writes.append(r.request.post_data_json), r.fulfill(json={**plugin, 'origin': 'copy', 'managed': True,
                                                                                     'content': r.request.post_data_json['content'], 'sha256': 'c1'}))[1])
    await page.locator('#dream-nav-skills').click()
    await page.locator('[data-key=greet-people]').click()
    editor = page.get_by_label('Skill Markdown', exact=True)
    await expect(editor).to_have_attribute('readonly', '')
    await expect(page.get_by_role('button', name='Save skill')).to_be_hidden()
    await page.get_by_role('button', name='Edit a copy').click()
    await expect(editor).to_be_editable()
    await editor.fill(plugin['content'] + '\nMy way.')
    await page.get_by_role('button', name='Save skill').click()
    await expect(page.get_by_role('button', name='Remove your version')).to_be_visible()
    assert writes[-1] == {'name': 'greet-people', 'content': plugin['content'] + '\nMy way.', 'expected_sha256': 'p1'}
    assert prompts == []


async def test_the_preset_column_is_the_library_column_with_a_preset_on_top(library, monkeypatch):
    from dream.tools import registry
    monkeypatch.setattr(presets, "_CATALOG", list(registry._BASE_TOOLS))       # the tool groups a session would have
    _, page, prompts, _, _ = library
    await _open(page)
    left, middle = page.locator('#dream-skills-page .library-sidebar'), page.locator('.library-preset-col')
    shape = ("c => [...c.querySelectorAll('input,select,.library-count,.library-list .library-row')]"
             ".filter(e => !e.closest('.library-preset-head')).map(e => e.tagName + '.' + e.className)")
    await page.get_by_label('Preset', exact=True).select_option('Legal')
    await middle.get_by_label('Filter preset').select_option('tools')
    await page.get_by_label('Filter skills').select_option('tools')
    await expect(middle.locator('.library-list strong')).to_have_text(['export_tools', 'library_tools', 'show_to_user', 'vision', 'web'])
    mid = await middle.evaluate(shape)
    assert len(mid) == 9 and mid == (await left.evaluate(shape))[:9]            # search, Show, Sort, count, five rows
    row = middle.locator('.library-row').first
    assert await row.evaluate("r => [...r.children].map(e => e.className)") == ['', 'library-assign']
    await expect(row.locator('small')).to_have_count(2)                                    # description and badge
    await middle.get_by_label('Filter preset').select_option('all')
    await middle.get_by_label('Search preset').fill('writ')
    await expect(middle.locator('.library-list strong')).to_have_text(['writing'])
    await expect(page.locator('.library-preset-col .library-cost')).to_contain_text('tools')
    assert prompts == []
