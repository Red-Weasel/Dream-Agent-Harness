"""Real App turn/queue lifecycle driven by CPU fixture events."""
import asyncio
from types import SimpleNamespace
import pytest
from dream.tui.app import App
from dream.core.backends.base import Event
from dream.workflows import WorkflowService


class FixtureEngine:
    def __init__(self, events, output=None):self.events=events;self.prompts=[];self.output=output
    async def ask(self,prompt):
        self.prompts.append(prompt)
        if self.output:self.output()
        for event in self.events:
            if isinstance(event,BaseException):raise event
            yield event
    async def interrupt(self):pass


def app_fixture(tmp_path,monkeypatch,events,output=None):
    app=App(workspace=tmp_path,gui=False)
    app.engine=FixtureEngine(events,output)
    monkeypatch.setattr(app,'_render_event',lambda _:None)
    monkeypatch.setattr(app.renderer,'live_begin',lambda *_:None)
    monkeypatch.setattr(app.renderer,'live_end',lambda *_:None)
    monkeypatch.setattr(app.renderer,'working',lambda *_:None)
    async def seal():pass
    monkeypatch.setattr(app,'_seal_checkpoint',seal)
    return app


async def queue_task(app,tmp_path):
    assert hasattr(app,'_queue_workflow'), 'App workflow dispatch is missing'
    svc=WorkflowService(tmp_path);task=svc.create('report',{'goal':'Report observations'})
    await svc.start(task['id'],1,'start',app._queue_workflow)
    return svc,task,app._gui_prompts.get_nowait()


@pytest.mark.asyncio
@pytest.mark.parametrize('events,expected',[
    ([Event('assistant_done','I finished')],'unknown'),
    ([Event('result',{'is_error':False})],'needs_attention'),
    ([Event('result',{'is_error':True,'subtype':'failure'})],'failed'),
    ([Event('error','Backend failed')],'failed'),
    ([RuntimeError('fixture failed')],'failed'),
    ([asyncio.CancelledError()],'interrupted'),
])
async def test_turn_events_do_not_invent_completion(tmp_path,monkeypatch,events,expected):
    app=app_fixture(tmp_path,monkeypatch,events)
    svc,task,queued=await queue_task(app,tmp_path)
    try:await app._ask(queued.text,workflow=queued)
    except (RuntimeError,asyncio.CancelledError):pass
    assert svc.get(task['id'])['status']==expected


@pytest.mark.asyncio
async def test_completed_turn_checks_artifact_and_preserves_metadata(tmp_path,monkeypatch):
    app=app_fixture(tmp_path,monkeypatch,[Event('tool_use',{'name':'write_file'}),Event('tool_result',{'name':'write_file'}),Event('result',{'is_error':False})])
    svc,task,queued=await queue_task(app,tmp_path)
    output=tmp_path/task['output_path'];output.parent.mkdir(parents=True)
    app.engine.output=lambda:output.write_text('# Report\n\nHere are observations supplied by the fixture.\n')
    await app._ask(queued.text,workflow=queued)
    finished=svc.get(task['id'])
    assert finished['status']=='ready_for_review'
    assert any('write_file' in step['message'] for step in finished['steps'])
    assert queued.workflow_task_id==task['id'] and len(app.engine.prompts)==1


@pytest.mark.asyncio
async def test_recovered_queued_task_never_reaches_engine(tmp_path,monkeypatch):
    app=app_fixture(tmp_path,monkeypatch,[Event('result',{'is_error':False})])
    svc,task,queued=await queue_task(app,tmp_path)
    state=svc.get(task['id']);svc.recover(task['id'],state['version'])
    await app._ask(queued.text,workflow=queued)
    assert not app.engine.prompts
    assert svc.get(task['id'])['status']=='interrupted'


@pytest.mark.asyncio
async def test_foreign_workspace_queue_rejected_and_plain_prompt_preserved(tmp_path,monkeypatch):
    app=app_fixture(tmp_path,monkeypatch,[Event('result',{'is_error':False})])
    assert hasattr(app,'_queue_workflow'), 'App workflow dispatch is missing'
    with pytest.raises(ValueError):app._queue_workflow('text',{'workspace':'/tmp/foreign','workflow_task_id':'x','workflow_attempt':1})
    await app._ask('ordinary text')
    assert app.engine.prompts==['ordinary text']


@pytest.mark.asyncio
async def test_reconcile_control_requires_explicit_idle_confirmation(tmp_path,monkeypatch):
    app=app_fixture(tmp_path,monkeypatch,[])
    calls=[]
    def reconcile(request_id,*,confirmed_idle):
        calls.append((request_id,confirmed_idle));return {'state':'idle'}
    app.engine.backend=SimpleNamespace(reconcile_local_request=reconcile)
    for confirmation in (None,False,'true',1):
        with pytest.raises(ValueError):
            await app._runtime_control({'action':'reconcile_local_request','request_id':'owned-request','confirmed_idle':confirmation})
    result=await app._runtime_control({'action':'reconcile_local_request','request_id':'owned-request','confirmed_idle':True})
    assert result['state']=='idle' and calls==[('owned-request',True)]


@pytest.mark.asyncio
async def test_queue_bound_and_simultaneous_terminal_input_preserve_task(tmp_path,monkeypatch):
    app=app_fixture(tmp_path,monkeypatch,[])
    svc,task,queued=await queue_task(app,tmp_path)
    app._queue_gui_prompt(queued)
    app.studio=object()
    async def typed(*_):return 'terminal text'
    app.session=SimpleNamespace(prompt_async=typed)
    first=await app._next_input()
    second=await app._next_input()
    assert first=='terminal text' and second is queued
    for i in range(32):app._queue_gui_prompt(str(i))
    with pytest.raises(ValueError,match='full'):app._queue_gui_prompt('overflow')
    assert app._gui_prompts.qsize()==32


@pytest.mark.asyncio
async def test_run_unwraps_workflow_without_turning_it_into_a_command(tmp_path,monkeypatch):
    app=app_fixture(tmp_path,monkeypatch,[Event('result',{'is_error':False})])
    svc,task,queued=await queue_task(app,tmp_path)
    async def noop():pass
    inputs=[queued]
    async def next_input():
        if inputs:return inputs.pop()
        raise EOFError()
    monkeypatch.setattr(app,'start',noop)
    monkeypatch.setattr(app,'_shutdown',noop)
    monkeypatch.setattr(app,'_next_input',next_input)
    await app.run()
    assert app.engine.prompts==[queued.text]
    assert svc.get(task['id'])['status']=='needs_attention'


@pytest.mark.asyncio
async def test_changed_workspace_and_revised_attempt_never_execute_stale_queue(tmp_path,monkeypatch):
    app=app_fixture(tmp_path,monkeypatch,[Event('result',{'is_error':False})])
    svc,task,queued=await queue_task(app,tmp_path)
    current=svc.get(task['id']);stopped=svc.recover(task['id'],current['version']);revised=svc.revise(task['id'],stopped['version'])
    await app._ask(queued.text,workflow=queued)
    assert not app.engine.prompts and svc.get(task['id'])['attempt']==2
    await svc.start(task['id'],revised['version'],'second',app._queue_workflow)
    next_queued=app._gui_prompts.get_nowait()
    other=tmp_path/'other';other.mkdir();app.workspace=other
    await app._ask(next_queued.text,workflow=next_queued)
    assert not app.engine.prompts and svc.get(task['id'])['status']=='interrupted'


@pytest.mark.asyncio
async def test_start_does_not_block_eventloop_during_workspace_io(tmp_path,monkeypatch):
    import threading
    svc=WorkflowService(tmp_path);task=svc.create('report',{'goal':'Read a source'})
    entered=threading.Event();release=threading.Event()
    original=svc.store.get
    def blocked(*args,**kwargs):
        entered.set();release.wait(1);return original(*args,**kwargs)
    monkeypatch.setattr(svc.store,'get',blocked)
    dispatched=[]
    task_start=asyncio.create_task(svc.start(task['id'],1,'blocking',lambda *_:dispatched.append(1)))
    await asyncio.sleep(.05)
    responsive=entered.is_set() and not task_start.done()
    release.set();await task_start
    assert responsive, 'Workspace I/O blocked the event loop'
    assert dispatched==[1]


@pytest.mark.asyncio
async def test_cancel_while_queue_transaction_runs_never_dispatches(tmp_path,monkeypatch):
    import threading
    svc=WorkflowService(tmp_path);task=svc.create('report',{'goal':'Fixture'})
    entered=threading.Event();release=threading.Event();original=svc._enqueue
    def blocked(*args):
        entered.set();release.wait(2);return original(*args)
    monkeypatch.setattr(svc,'_enqueue',blocked)
    dispatched=[]
    pending=asyncio.create_task(svc.start(task['id'],1,'cancel-thread',lambda *_:dispatched.append(1)))
    assert await asyncio.to_thread(entered.wait,1)
    pending.cancel();release.set()
    with pytest.raises(asyncio.CancelledError):await pending
    assert not dispatched and svc.get(task['id'])['status']=='unknown'
