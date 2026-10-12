"""Owner-controlled reconciliation entrypoints with synthetic App/RunState."""
from types import SimpleNamespace
import pytest
from dream.core.run_resume import reconciliation_actions, resume_arguments

ACTION = 'a' * 32


def test_legacy_notes_preserved_and_explicit_actions_distinct():
    assert resume_arguments("run-1 owner's note") == ('run-1', "owner's note", {})
    assert resume_arguments(f'run-1 --reconcile {ACTION}="inspected the file; do not replay" --note "review only"') == (
        'run-1', 'review only', {ACTION: 'inspected the file; do not replay'})
    assert resume_arguments('run-1 --note "checked progress"') == ('run-1', 'checked progress', {})


@pytest.mark.parametrize('values', [['missing report'], ['a=report'], [ACTION + '='], [ACTION + '=x', ACTION + '=x'], [ACTION + '=' + 'x'*8193]])
def test_invalid_or_duplicate_action_reports_refused(values):
    with pytest.raises(ValueError):
        reconciliation_actions(values)


@pytest.mark.parametrize('text', ['run-1 --reconcile', 'run-1 --note first --note second', 'run-1 --note "unterminated', 'run-1 --note ok --unknown x'])
def test_invalid_command_is_not_downgraded_to_a_note(text):
    with pytest.raises(ValueError):
        resume_arguments(text)


def test_headless_resume_forwards_explicit_action_reports(monkeypatch):
    import dream.__main__ as entry
    seen = {}
    class Journal:
        state = {'goal': 'recorded goal', 'worker_workspace': '/fixture/workspace'}
        def __init__(self, *args):pass
        def close(self):pass
    class App:
        def __init__(self, **args):self.mode = 'ask'
        async def run_autonomous(self, goal, iterations, **options):seen.update(goal=goal, **options)
    monkeypatch.setattr('dream.core.run_state.RunState', Journal)
    monkeypatch.setattr('dream.tui.app.App', App)
    monkeypatch.setattr('sys.argv', ['dream', '--resume-run', 'fixture-run', '--resume-note', 'review only',
                                   '--reconcile-action', ACTION+'=inspected effect; do not replay'])
    entry.main()
    assert seen == {'goal':'recorded goal','resume_run_id':'fixture-run','resume_note':'review only',
                    'reconciled_actions':{ACTION:'inspected effect; do not replay'}}


def test_reconciliation_requires_resume_before_app_constructed(monkeypatch):
    import dream.__main__ as entry
    def forbidden(**kwargs):pytest.fail('App must not start on invalid reconciliation')
    monkeypatch.setattr('dream.tui.app.App', forbidden)
    monkeypatch.setattr('sys.argv', ['dream', '--loop', 'goal', '--reconcile-action', ACTION+'=report'])
    with pytest.raises(SystemExit) as exc:entry.main()
    assert exc.value.code == 2


async def test_tui_resume_forwards_action_reports_from_actual_command(monkeypatch):
    from dream.tui.app import App
    seen = {}
    class Journal:
        state = {'goal':'recorded goal'}
        def __init__(self,*args):pass
        def close(self):pass
    async def run(goal, **options):seen.update(goal=goal,**options)
    fake = SimpleNamespace(engine=SimpleNamespace(store=None),renderer=SimpleNamespace(console=None,info=lambda *a:None,error=lambda message:pytest.fail(message)),_loop=run)
    monkeypatch.setattr('dream.core.run_state.RunState', Journal)
    await App._command(fake, f'/loop-resume fixture-run --reconcile {ACTION}="inspected effect" --note "review only"')
    assert seen == {'goal':'recorded goal','resume_run_id':'fixture-run','resume_note':'review only','reconciled_actions':{ACTION:'inspected effect'}}

from test_managed_loop import worker, loop, tool_bundle

def test_actual_cli_reconciles_failed_handler_and_resumes_review_without_replay(worker,tmp_path,monkeypatch):
    import asyncio
    import dream.__main__ as entry
    from dream import config
    effects=[]
    async def effect(args):
        effects.append('effect happened')
        return {'is_error':True,'content':[{'type':'text','text':'failure after effect'}]}
    tool=tool_bundle(effect)
    async def act():await tool.handler({})
    worker.action=act
    driver=loop(worker,tmp_path)
    first=asyncio.run(driver.run('original goal',acceptance_criteria='- inspected criterion'))
    assert first.status=='need_input' and len(effects)==1
    action=next(iter(driver.checkpoints.unresolved_actions))
    seen=[]
    class App:
        def __init__(self,**kwargs):self.mode='ask'
        async def run_autonomous(self,goal,iterations,**options):
            seen.append(await driver.run(goal,**options))
    monkeypatch.setattr(config,'LOOP_DIR',driver.state_dir)
    monkeypatch.setattr('dream.tui.app.App',App)
    monkeypatch.setattr('sys.argv',['dream','--resume-run',first.run_id,'--resume-note','review only',
                                   '--reconcile-action',action+'=inspected completed effect; do not replay'])
    entry.main()
    assert seen[0].status=='done' and worker.calls==1 and len(effects)==1
