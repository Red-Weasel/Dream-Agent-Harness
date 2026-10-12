"""Operator schedule flags validate before any provider or native account boots."""
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from dream.__main__ import main


@pytest.fixture
def launch(monkeypatch, tmp_path):
    from dream.tui import app
    observed = []
    loader = ModuleType('dream.core.phase_plan')
    def load(path, *, workspace):
        observed.append(('loaded', path, workspace))
        return SimpleNamespace(goal='Fixed operator goal', workspace=Path(workspace), project='fixture')
    loader.load_phase_plan = load
    monkeypatch.setitem(sys.modules, loader.__name__, loader)
    class FakeApp:
        def __init__(self, **kwargs): observed.append(('app', kwargs))
        async def run_phase_schedule(self, plan, **kwargs):
            observed.append(('run', plan.goal, self.mode, kwargs))
    monkeypatch.setattr(app, 'App', FakeApp)
    def invoke(*flags):
        monkeypatch.setattr(sys, 'argv', ['dream', '--provider', 'machx', '--workspace', str(tmp_path), *flags])
        main()
    return invoke, observed, loader


def test_phase_plan_loads_before_app_and_uses_captured_goal(launch):
    invoke, observed, _ = launch
    invoke('--phase-plan', 'operator.json')
    assert [event[0] for event in observed] == ['loaded', 'app', 'run']
    assert observed[-1][1:3] == ('Fixed operator goal', 'auto')


def test_schedule_resume_passes_only_explicit_reports(launch):
    invoke, observed, _ = launch
    action, check = 'a' * 32, 'b' * 32
    invoke('--phase-plan', 'operator.json', '--resume-schedule', 'schedule-fixture',
           '--resume-note', 'Owner inspected the existing boundary.',
           '--reconcile-action', action + '=Confirmed effect; do not replay.',
           '--reconcile-check', check + '=Confirmed interrupted check outcome.')
    kwargs = observed[-1][3]
    assert kwargs['resume_schedule_id'] == 'schedule-fixture'
    assert kwargs['reconciled_actions'] == {action: 'Confirmed effect; do not replay.'}
    assert kwargs['reconciled_checks'] == {check: 'Confirmed interrupted check outcome.'}


@pytest.mark.parametrize('flags', [
    ('--phase-plan', 'operator.json', '--loop', 'Different goal'),
    ('--phase-plan', 'operator.json', '--loop', ''),
    ('--phase-plan', 'operator.json', '--iterations', '2'),
    ('--phase-plan', 'operator.json', '--resume-run', 'child-fixture'),
    ('--phase-plan', 'operator.json', '--resume-run', ''),
    ('--resume-schedule', 'schedule-fixture'),
    ('--phase-plan', 'operator.json', '--reconcile-check', 'b' * 32 + '=Unknown check'),
    ('--phase-plan', 'operator.json', '--resume-note', 'Implicit resume'),
    ('--phase-plan', ''),
])
def test_invalid_schedule_flags_cannot_boot_provider(launch, flags):
    invoke, observed, _ = launch
    with pytest.raises(SystemExit): invoke(*flags)
    assert not any(event[0] == 'app' for event in observed)


def test_malformed_operator_plan_cannot_boot_provider(launch):
    invoke, observed, loader = launch
    def bad(*args, **kwargs): raise ValueError('Invalid phase acceptance declaration')
    loader.load_phase_plan = bad
    with pytest.raises(SystemExit): invoke('--phase-plan', 'operator.json')
    assert not observed


@pytest.mark.parametrize('schedule_id', ['../child', '', 'x' * 101])
def test_invalid_schedule_identity_cannot_boot_provider(launch, schedule_id):
    invoke, observed, _ = launch
    with pytest.raises(SystemExit):
        invoke('--phase-plan', 'operator.json', '--resume-schedule', schedule_id)
    assert not observed


@pytest.mark.parametrize('note', ['', '   ', 'x' * 8193])
def test_invalid_resume_report_cannot_boot_provider(launch, note):
    invoke, observed, _ = launch
    with pytest.raises(SystemExit):
        invoke('--phase-plan', 'operator.json', '--resume-schedule', 'schedule-fixture', '--resume-note', note)
    assert not any(event[0] == 'app' for event in observed)


@pytest.mark.parametrize('where', ['constructor', 'run'])
def test_cli_releases_loaded_plan_after_provider_run_fails(launch, where):
    invoke, observed, loader = launch
    def load(path, *, workspace):
        return SimpleNamespace(goal='Fixed goal', project='', workspace=Path(workspace),
            close=lambda: observed.append(('closed',)))
    loader.load_phase_plan = load
    from dream.tui import app
    class FailingApp:
        def __init__(self, **kwargs):
            if where == 'constructor': raise RuntimeError('Fixture startup failure')
        async def run_phase_schedule(self, *args, **kwargs):
            raise RuntimeError('Fixture startup failure')
    # This fixture uses launch's monkeypatch only for the scoped loader/argv;
    # the App replacement gets its own bounded restoration.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(app, 'App', FailingApp)
        with pytest.raises(RuntimeError, match='startup failure'):
            invoke('--phase-plan', 'operator.json')
    assert observed == [('closed',)]


def test_cli_releases_loaded_plan_if_application_import_fails(launch):
    invoke, observed, loader = launch
    def load(path, *, workspace):
        return SimpleNamespace(goal='Fixed goal', project='', workspace=Path(workspace),
            close=lambda: observed.append(('closed',)))
    loader.load_phase_plan = load
    unavailable_app = ModuleType('dream.tui.app')
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, 'dream.tui.app', unavailable_app)
        with pytest.raises(ImportError): invoke('--phase-plan', 'operator.json')
    assert observed == [('closed',)]


@pytest.mark.parametrize('provider', ['anthropic', 'codex', 'grok', 'gemini'])
def test_unqualified_native_schedule_provider_refused_before_boot(launch, provider):
    invoke, observed, _ = launch
    with pytest.raises(SystemExit): invoke('--provider', provider, '--phase-plan', 'operator.json')
    assert not any(event[0] == 'app' for event in observed)


@pytest.mark.parametrize('interrupted', [False, True])
async def test_headless_schedule_releases_scope_and_shuts_down_on_stop(monkeypatch, tmp_path, capsys, interrupted):
    from dream.tui.app import App
    from dream.core import phase_runner
    events = []
    app = App.__new__(App)
    app.engine = SimpleNamespace()
    app.interrupted = interrupted
    app._active_loop = None
    app._pending_activity = {}
    app._pending_changed_files = {}
    app._footer = lambda: ''
    app._loop_sink = lambda event: events.append(event)
    app.meter = SimpleNamespace(turn_start=lambda: events.append('meter_start'), turn_end=lambda: events.append('meter_end'))
    app.renderer = SimpleNamespace(**{name: (lambda *args, n=name: events.append(n))
        for name in ('live_begin', 'working', 'live_end', 'end_line', 'rule', 'info', 'system')})
    async def start(): events.append('start')
    async def seal(): events.append('seal')
    async def shutdown(*, allow_consolidation):
        assert allow_consolidation is False
        assert app._loop_workspace is None
        events.append('shutdown')
    async def turn(operation): return await operation
    app.start, app._seal_checkpoint, app._shutdown, app._run_turn = start, seal, shutdown, turn
    class Runner:
        def __init__(self, *args, **kwargs):
            assert kwargs['on_event'] is app._loop_sink
            self.active_loop = None
            self._journal = SimpleNamespace(run_id='schedule-fixture')
        async def run(self, **kwargs):
            self.active_loop = SimpleNamespace(workspace=tmp_path / 'owned-child')
            assert app._loop_workspace == tmp_path / 'owned-child'
            self.active_loop = None
            if interrupted: return None
            return SimpleNamespace(status='done', completed_phases=1, schedule_id='schedule-fixture',
                                   current_run_id=None, message='Checked fixture')
    monkeypatch.setattr(phase_runner, 'PhaseRunner', Runner)
    plan = SimpleNamespace(goal='Fixed plan', project='', phases=(), limits=None)
    await app.run_phase_schedule(plan)
    assert events[-1] == 'shutdown' and app._active_loop is None
    assert events.index('meter_end') < events.index('seal') < events.index('shutdown')
    output = capsys.readouterr().out
    assert 'DREAM_SCHEDULE_RESULT_JSON:' in output and 'schedule-fixture' in output
    assert ('"status": "stopped"' if interrupted else '"status": "done"') in output


async def test_partial_schedule_startup_is_cleaned_without_optional_model_work():
    from dream.tui.app import App
    app = App.__new__(App)
    events = []
    async def start():
        events.append('partial_start')
        raise OSError('Fixture Studio startup failure')
    async def shutdown(*, allow_consolidation):
        assert allow_consolidation is False
        events.append('shutdown')
    app.start, app._shutdown = start, shutdown
    with pytest.raises(OSError, match='Studio startup'):
        await app.run_phase_schedule(SimpleNamespace())
    assert events == ['partial_start', 'shutdown']


@pytest.mark.parametrize('foreign_project', [False, True])
def test_cli_uses_real_captured_plan_and_releases_its_checks(monkeypatch, tmp_path, foreign_project):
    import json
    from dream.core.phase_plan import load_phase_plan
    from dream.memory.project import project_key
    from dream.tui import app
    from dream.core import phase_plan
    source = tmp_path / 'operator.json'
    result = tmp_path / 'result.txt'
    result.write_text('checked result\n')
    declaration = {'version': 1, 'goal': 'Finish the fixed schedule',
        'limits': {'max_dispatches': 4, 'max_wall_seconds': 60},
        'phases': [{'id': 'first', 'goal': 'Write the result', 'contract': '- Exact result exists',
            'check_name': 'exact-result', 'acceptance': {'all': [
                {'path': 'result.txt', 'text': 'checked result\n', 'lines': 1}]}}]}
    if foreign_project: declaration['project'] = 'foreign'
    source.write_text(json.dumps(declaration))
    observed = []
    captured = []
    def load(path, *, workspace):
        plan = load_phase_plan(path, workspace=workspace)
        captured.append(plan)
        return plan
    monkeypatch.setattr(phase_plan, 'load_phase_plan', load)
    class FixtureApp:
        def __init__(self, **kwargs):
            observed.append('app')
            # Editing the selected file cannot replace the already captured checks.
            source.write_text('{}')
        async def run_phase_schedule(self, plan, **kwargs):
            assert plan.project == project_key(tmp_path)
            assert plan.phases[0].check() is True
            assert self.mode == 'auto'
            observed.append('run')
    monkeypatch.setattr(app, 'App', FixtureApp)
    monkeypatch.setattr(sys, 'argv', ['dream', '--provider', 'machx', '--workspace', str(tmp_path),
                                    '--phase-plan', str(source)])
    if foreign_project:
        with pytest.raises(SystemExit): main()
        assert observed == [] and captured == []
    else:
        main()
        assert observed == ['app', 'run'] and len(captured) == 1
        assert captured[0].phases[0].check() is False
