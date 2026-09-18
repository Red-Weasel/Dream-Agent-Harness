"""Pinned context reaches a fake provider without rewriting the user's log."""
from test_task_guidance_integration import engine  # noqa: F401
from dream.projects import ProjectWorkspace


async def test_engine_injects_bounded_pins_and_observable_names(engine):
    svc = ProjectWorkspace(engine.workspace)
    svc.pin(kind='constraint', text='Never load models.', expected_revision=0)
    svc.pin(kind='decision', text='Use CPU fixtures.', expected_revision=1)
    request = 'Hello there'
    events = [event async for event in engine.ask(request)]
    actual = engine.backend.prompts[0]
    assert actual.startswith(request)
    assert 'Never load models.' in actual and 'Use CPU fixtures.' in actual
    assert len(actual) - len(request) < 2100
    assert any(e.kind == 'system' and 'Loaded project context:' in str(e.data) for e in events)
    users = [turn for turn in engine.store.session_turns(engine.session_id) if turn['role'] == 'user']
    assert users[0]['content'] == request


async def test_engine_excludes_changed_file_and_reports_it(engine):
    source = engine.workspace / 'facts.md'; source.write_text('old fact')
    ProjectWorkspace(engine.workspace).pin(kind='file', path='facts.md', expected_revision=0)
    source.write_text('changed fact')
    events = [event async for event in engine.ask('Hello there')]
    assert engine.backend.prompts == ['Hello there']
    assert any('Changed file excluded' in str(event.data) for event in events)


def test_runtime_status_reports_capabilities_and_coordination_without_probes(engine):
    from dream.core.capabilities import capability_report
    engine.backend.capability_status = lambda: capability_report(provider_metadata={'vision': False})
    engine.backend.coordination_status = lambda: {'enabled': True, 'state': 'uncertain', 'request_id': 'fixture', 'can_reconcile': True}
    result = engine.runtime_status()
    assert result['capabilities']['vision']['known']
    assert result['capabilities']['vision']['value'] is False
    assert result['capabilities']['cancellation']['known'] is False
    assert result['coordination']['can_reconcile']
    def failed(): raise RuntimeError('fixture status failure')
    engine.backend.capability_status = failed
    engine.backend.coordination_status = failed
    result = engine.runtime_status()
    assert result['capabilities']['vision']['known'] is False
    assert 'fixture status failure' in result['capabilities']['warnings'][0]
    assert result['coordination']['state'] == 'unavailable'
    assert 'fixture status failure' in result['coordination']['reason']


async def test_saved_project_instructions_and_selected_markdown_reach_engine(engine):
    from dream.projects.library import ProjectLibrary
    from dream.projects.documents import ProjectDocuments
    project = ProjectLibrary().create('Engine project', str(engine.workspace), 'Use accessible UI components.')
    docs = ProjectDocuments(project['id'])
    docs.save({'title':'Render lesson','kind':'memory','content':'Probe actual render engines first.','include':True})
    docs.save({'title':'Unselected reference','kind':'reference','content':'UNSELECTED_REFERENCE','include':False})
    other = engine.workspace / 'other'; other.mkdir()
    unrelated = ProjectLibrary().create('Other project', str(other), 'OTHER_PROJECT_SECRET')
    ProjectDocuments(unrelated['id']).save({'title':'Other note','content':'OTHER_NOTE_SECRET','include':True})
    events = [event async for event in engine.ask('Continue the project.')]
    actual = engine.backend.prompts[0]
    assert 'Use accessible UI components.' in actual
    assert 'Probe actual render engines first.' in actual
    assert 'UNSELECTED_REFERENCE' not in actual
    assert 'OTHER_PROJECT_SECRET' not in actual and 'OTHER_NOTE_SECRET' not in actual
    assert any('selected notes' in str(e.data) for e in events if e.kind == 'system')
    users = [t for t in engine.store.session_turns(engine.session_id) if t['role']=='user']
    assert users[0]['content']=='Continue the project.'
