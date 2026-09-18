"""Working pages keep drafts and explicit project boundaries; no inference."""
import asyncio

import pytest
from playwright.async_api import expect
from test_desktop_chat import chat


@pytest.fixture
async def library(chat):
    srv, page, prompts, controls = chat
    skills = {'writing': {'name':'writing', 'description':'Write clearly', 'source':'bundled',
                         'content':'---\nname: writing\ndescription: Write clearly\n---\n\nFull instructions beyond the catalog.',
                         'sha256':'v1', 'managed':False}}
    projects = {'alpha': {'id':'alpha','name':'Alpha','workspace':'/projects/alpha',
                          'instructions':'Use accessible interfaces.','revision':1,'session_count':1}}
    writes = []

    async def skill_api(route):
        path=route.request.url.split('/api/skills',1)[1]
        if route.request.method=='GET':
            await route.fulfill(json=skills[path[1:]] if path else {'skills':list(skills.values())})
        else:
            body=route.request.post_data_json
            name=path[1:] or body['name']
            writes.append(body)
            skills[name]={'name':name,'description':'Saved skill','source':'managed','managed':True,
                          'content':body['content'],'sha256':'v2'}
            await route.fulfill(json=skills[name])

    async def project_api(route):
        path=route.request.url.split('/api/projects',1)[1]
        if path.endswith('/memory'):
            await route.fulfill(json={'memories':[],'summaries':[{'session_id':'session-a','title':'Landing page summary','summary':'Use accessible components.'}]})
        elif '/documents' in path:
            await route.fulfill(json={'documents':[]})
        elif '/sessions/' in path:
            await route.fulfill(json={'turns':[{'role':'user','content':'Build the landing page'},
                                             {'role':'assistant','content':'Saved a working first version.'}]})
        elif route.request.method=='GET':
            await route.fulfill(json={'project':projects[path[1:]],'sessions':[{'id':'session-a','title':'Landing page','turn_count':2}]} if path else {'projects':list(projects.values())})
        else:
            body=route.request.post_data_json;writes.append(body)
            ident=path[1:] or 'new-project';projects[ident]={**body,'id':ident,'revision':2}
            await route.fulfill(json={'project':projects[ident]})
    def control(payload):
        controls.append(payload)
        if payload.get('action')=='project_open':
            p=projects[payload['project_id']]
            return {'project_id':p['id'],'session':{'session_id':'opened-fixture','workspace':p['workspace']},'restoration':'saved_context'}
    srv._on_control=control
    await page.route('**/api/skills**',skill_api)
    await page.route('**/api/projects**',project_api)
    return srv,page,prompts,controls,writes


async def test_skills_full_page_edit_create_and_use(library):
    _,page,prompts,_,writes=library
    await page.locator('#dream-nav-skills').click()
    await expect(page.locator('#dream-skills-page')).to_be_visible()
    await expect(page.locator('dialog[open]')).to_have_count(0)
    await page.locator('#dream-skills-page .library-list button').first.click()
    editor=page.get_by_label('Skill Markdown',exact=True)
    assert 'Full instructions beyond the catalog.' in await editor.input_value()
    await editor.fill('---\nname: writing\ndescription: Clear writing\n---\n\nUpdated full instructions.')
    await page.get_by_role('button',name='Save skill',exact=True).click()
    await expect(page.locator('#dream-skills-page .library-status')).to_contain_text('Skill saved')
    assert writes[-1]['expected_sha256']=='v1'
    await page.get_by_role('button',name='Use in chat',exact=True).click()
    await expect(page.locator('#input')).to_have_value('Use the writing skill.')
    assert prompts==[]
    await page.locator('#dream-nav-skills').click()
    await page.get_by_role('button',name='New skill',exact=True).click()
    await page.get_by_label('Skill name',exact=True).fill('new-workflow')
    await page.get_by_role('button',name='Save skill',exact=True).click()
    await expect(page.locator('#dream-skills-page .library-status')).to_contain_text('Skill saved')
    assert writes[-1]['name']=='new-workflow'
    assert 'name: new-workflow' in writes[-1]['content']


async def test_skills_draft_survives_navigation_and_conflict(library):
    _,page,prompts,_,_=library
    await page.locator('#dream-nav-skills').click()
    await page.locator('#dream-skills-page .library-list button').first.click()
    await page.get_by_label('Skill Markdown',exact=True).fill('My unsaved edit')
    await page.locator('#dream-nav-projects').click()
    await page.locator('#dream-nav-skills').click()
    await expect(page.get_by_label('Skill Markdown',exact=True)).to_have_value('My unsaved edit')
    await page.route('**/api/skills/writing',lambda r:r.fulfill(status=409,json={'error':'Skill changed on disk. Reload before saving.'}))
    await page.get_by_role('button',name='Save skill',exact=True).click()
    await expect(page.locator('#dream-skills-page .library-status')).to_contain_text('changed on disk')
    await expect(page.get_by_label('Skill Markdown',exact=True)).to_have_value('My unsaved edit')
    assert prompts==[]


async def test_projects_history_and_draft_boundary(library):
    _,page,prompts,controls,_=library
    await page.locator('#input').fill('Keep this in the original workspace')
    await page.locator('#dream-nav-projects').click()
    await expect(page.locator('#dream-projects-page')).to_be_visible()
    await expect(page.locator('dialog[open]')).to_have_count(0)
    await page.locator('#dream-projects-page .library-list button').first.click()
    await page.get_by_role('button',name='Open conversation',exact=True).click()
    await expect(page.locator('#project-conversations')).to_contain_text('Saved a working first version.')
    await page.get_by_role('button',name='Continue conversation',exact=True).click()
    await expect(page.locator('#dream-projects-page .library-status')).to_contain_text('unsent draft')
    assert controls==[{'action':'permission_mode_status'}]
    await page.locator('#dream-nav-chat').click()
    await expect(page.locator('#input')).to_have_value('Keep this in the original workspace')
    await page.locator('#input').fill('')
    await page.locator('#dream-nav-projects').click()
    await page.get_by_role('button',name='Continue conversation',exact=True).click()
    await expect(page.locator('#dream-projects-page')).to_be_hidden()
    assert controls[-1]=={'action':'project_open','project_id':'alpha','session_id':'session-a'}
    assert prompts==[]


async def test_project_create_and_instruction_edit(library):
    _,page,prompts,_,writes=library
    await page.locator('#dream-nav-projects').click()
    await page.get_by_role('button',name='New project',exact=True).click()
    await page.get_by_label('Project name',exact=True).fill('My project')
    await page.get_by_label('Workspace folder',exact=True).fill('/projects/new')
    await page.get_by_label('Project instructions',exact=True).fill('Use semantic HTML.')
    await page.get_by_role('button',name='Save project',exact=True).click()
    await expect(page.locator('#dream-projects-page .library-detail h2')).to_have_text('My project')
    await page.get_by_role('button',name='Edit project',exact=True).click()
    await page.get_by_label('Project instructions',exact=True).fill('Use semantic HTML and keyboard navigation.')
    await page.get_by_role('button',name='Save project',exact=True).click()
    await expect(page.locator('#dream-projects-page .library-status')).to_contain_text('Project saved')
    assert writes[-1]['expected_revision']==2
    assert prompts==[]


async def test_page_cannot_hide_approval_and_keeps_editor_draft(library):
    srv,page,_,_,_=library
    await page.locator('#dream-nav-skills').click()
    await page.get_by_role('button',name='New skill',exact=True).click()
    await page.get_by_label('Skill Markdown',exact=True).fill('Draft during a running turn')
    pending=asyncio.create_task(srv.request_permission('run_bash',{'command':'pwd'},'Fixture review',{'y':'Allow once','n':'Deny'}))
    try:
        await expect(page.locator('#dream-skills-page')).to_be_hidden()
        await page.get_by_role('button',name='Deny',exact=True).click()
        assert await asyncio.wait_for(pending,2)=='n'
        await page.locator('#dream-nav-skills').click()
        await expect(page.get_by_label('Skill Markdown',exact=True)).to_have_value('Draft during a running turn')
    finally:
        pending.cancel();await asyncio.gather(pending,return_exceptions=True)


async def test_pages_responsive_and_markdown_is_inert(library):
    _,page,prompts,_,_=library
    await page.locator('#dream-nav-skills').click()
    await page.get_by_role('button',name='New skill',exact=True).click()
    await page.get_by_label('Skill Markdown',exact=True).fill('<script>window.badSkill=true</script>')
    for width,height in [(1280,800),(720,520),(390,844)]:
        await page.set_viewport_size({'width':width,'height':height})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=f'/tmp/dream-skills-page-{width}.png')
    assert await page.evaluate('window.badSkill === undefined')
    assert prompts==[]


async def test_real_project_documents_memory_and_skill_persist_across_reload(chat,tmp_path):
    from dream import config
    from dream.memory.store import MemoryStore
    srv,page,prompts,_=chat
    workspace=tmp_path/'project';workspace.mkdir()
    srv._session_source={'workspace':str(workspace),'session_id':'chat-test','provider':'test','model':'fixture'}
    store=MemoryStore(config.DB_PATH)
    store.start_session('chat-test','Saved fixture conversation')
    store.add_turn('chat-test','user','Build an accessible site.')
    store.end_session('chat-test','Use semantic buttons and keyboard navigation.')
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    await page.reload()
    await page.locator('#dream-nav-projects').click()
    await page.get_by_role('button',name='New project',exact=True).click()
    await page.get_by_label('Project name',exact=True).fill('Persistent fixture')
    await page.get_by_role('button',name='Save project',exact=True).click()
    await expect(page.get_by_role('heading',name='Persistent fixture',exact=True)).to_be_visible()
    await page.get_by_role('tab',name='Documents',exact=True).click()
    await page.get_by_role('button',name='New document',exact=True).click()
    await page.get_by_label('Document title',exact=True).fill('Verified lesson')
    await page.get_by_label('Document Markdown',exact=True).fill('Use semantic buttons.')
    await page.get_by_label('Include in project context',exact=True).check()
    await page.get_by_role('button',name='Save document',exact=True).click()
    await expect(page.locator('#dream-projects-page .library-status')).to_contain_text('document saved')
    await page.get_by_role('tab',name='Memory',exact=True).click()
    await expect(page.locator('#project-memory')).to_contain_text('Saved fixture conversation')
    await page.locator('#project-memory summary').first.click()
    await expect(page.locator('#project-memory')).to_contain_text('Use semantic buttons and keyboard navigation.')
    await page.reload()
    await page.locator('#dream-nav-projects').click()
    await page.locator('#dream-projects-page .library-list button').first.click()
    await page.get_by_role('tab',name='Documents',exact=True).click()
    await page.get_by_role('button',name='Edit document',exact=True).click()
    await expect(page.get_by_label('Document Markdown',exact=True)).to_have_value('Use semantic buttons.')
    for width,height in [(1280,800),(720,520),(390,844)]:
        await page.set_viewport_size({'width':width,'height':height})
        await page.screenshot(path=f'/tmp/dream-projects-documents-{width}.png')
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.locator('#dream-nav-skills').click()
    await page.get_by_role('button',name='New skill',exact=True).click()
    await page.get_by_label('Skill name',exact=True).fill('fixture-editor-persistence')
    await page.get_by_role('button',name='Save skill',exact=True).click()
    await expect(page.locator('#dream-skills-page .library-status')).to_contain_text('Skill saved')
    await page.reload()
    await page.locator('#dream-nav-skills').click()
    await page.get_by_label('Search skills',exact=True).fill('fixture-editor-persistence')
    await page.locator('#dream-skills-page .library-list button').first.click()
    assert 'name: fixture-editor-persistence' in await page.get_by_label('Skill Markdown',exact=True).input_value()
    assert prompts==[]
    assert errors==[]
    store.close()


async def test_late_skill_read_does_not_replace_new_typing(library):
    _,page,_,_,_=library
    await page.locator('#dream-nav-skills').click()
    await page.locator('#dream-skills-page .library-list button').first.click()
    ready=asyncio.Event();release=asyncio.Event()
    async def delayed(route):
        ready.set();await release.wait()
        await route.fulfill(json={'name':'writing','content':'New disk version','sha256':'v2'})
    await page.route('**/api/skills/writing',delayed)
    await page.locator('#dream-skills-page .library-list button').first.click()
    await asyncio.wait_for(ready.wait(),2)
    await page.get_by_label('Skill Markdown',exact=True).fill('Typed while reading')
    release.set()
    await expect(page.locator('#dream-skills-page .library-status')).to_contain_text('Save this draft')
    await expect(page.get_by_label('Skill Markdown',exact=True)).to_have_value('Typed while reading')


async def test_project_reset_clears_preview_and_expires_frame_requests(chat):
    from dream.core.backends.base import Event
    srv,page,_,_=chat
    event=Event('studio',{'op':'show','path':'old.html','content':'<!doctype html><h1>Old project preview</h1>'})
    srv.retain_show(event);srv.bus.publish(event)
    await expect(page.frame_locator('#artbody iframe').locator('h1')).to_have_text('Old project preview')
    srv._uploads['old-upload']={'path':'old-project/file'}
    srv.reset_project_view()
    await expect(page.locator('#artbody iframe')).to_have_count(0)
    assert srv._last_show is None and srv._uploads=={}
    await page.reload()
    await expect(page.locator('#artbody iframe')).to_have_count(0)


async def test_switch_pending_blocks_composer_and_file_additions(library):
    _,page,prompts,_,_=library
    await page.locator('#dream-nav-projects').click()
    await page.locator('#dream-projects-page .library-list button').first.click()
    ready=asyncio.Event();release=asyncio.Event()
    async def delayed(route):
        if route.request.post_data_json.get('action')!='project_open':
            return await route.continue_()
        ready.set();await release.wait()
        await route.fulfill(json={'result':{'project_id':'alpha','session':{'session_id':'new','workspace':'/projects/alpha'}}})
    await page.route('**/api/control',delayed)
    await page.get_by_role('button',name='New chat in project',exact=True).click()
    await asyncio.wait_for(ready.wait(),2)
    await page.locator('#dream-nav-chat').click()
    assert await page.locator('footer').evaluate('(el)=>el.inert')
    assert await page.locator('#input').evaluate('(el)=>el.readOnly')
    await page.locator('#file-input').set_input_files({'name':'blocked.txt','mimeType':'text/plain','buffer':b'fixture'})
    await expect(page.locator('#attachments .attachment')).to_have_count(0)
    release.set()
    await expect(page.locator('#dream-project-opening')).to_have_count(0)
    assert not await page.locator('footer').evaluate('(el)=>el.inert')
    await expect(page.locator('#input')).to_have_value('')
    assert prompts==[]
